"""
Fix ALL cross-module dependency issues in one pass.

For each module:
1. Parse with ast to find all top-level definitions
2. Parse to find all referenced names
3. Identify names that are referenced but not locally defined
4. Find which other module defines them
5. Add explicit imports

This also ensures __all__ lists are complete.
"""

import ast, os, re, sys
from collections import defaultdict

MODULES = [
    "config.py",
    "utils.py",
    "fangraphs.py",
    "data_fetch.py",
    "batting_leaderboard.py",
    "player_cards.py",
    "pitching_leaderboard.py",
    "html_template.py",
    "fantasy.py",
    "fetch_mlb_stats.py",
]

# Standard library + third party modules that should NOT be resolved
BUILTIN_NAMES = {
    "True", "False", "None", "print", "len", "range", "str", "int", "float",
    "list", "dict", "set", "tuple", "bool", "type", "super", "object",
    "isinstance", "issubclass", "hasattr", "getattr", "setattr", "delattr",
    "enumerate", "zip", "map", "filter", "sorted", "reversed", "min", "max",
    "sum", "abs", "round", "any", "all", "open", "Exception", "ValueError",
    "TypeError", "KeyError", "IndexError", "AttributeError", "RuntimeError",
    "StopIteration", "FileNotFoundError", "OSError", "IOError",
    "NotImplementedError", "ImportError", "ModuleNotFoundError",
    "ZeroDivisionError", "OverflowError", "NameError",
    "id", "repr", "hash", "callable", "iter", "next", "input",
    "format", "chr", "ord", "hex", "oct", "bin", "pow", "divmod",
    "staticmethod", "classmethod", "property",
    "bytes", "bytearray", "memoryview", "complex", "frozenset",
    "breakpoint", "compile", "eval", "exec", "globals", "locals", "vars",
    "dir", "help", "ascii",
    "__name__", "__file__", "__doc__", "__all__", "__builtins__",
    # Common imports that resolve to packages
    "subprocess", "sys", "os", "json", "unicodedata", "time", "re",
    "datetime", "date", "timedelta", "io", "math", "traceback",
    "pathlib", "Path", "collections", "defaultdict", "functools",
    "itertools", "copy", "shutil", "tempfile", "hashlib", "base64",
    "urllib", "html", "csv", "string", "textwrap", "warnings",
    "logging", "threading", "multiprocessing", "signal", "atexit",
    "contextlib", "abc", "zoneinfo", "ZoneInfo",
    # Third-party
    "pybaseball", "pd", "np", "requests", "statsapi", "cloudscraper",
    "pandas", "numpy",
    "playwright", "sync_playwright",
    # pandas/numpy common
    "DataFrame", "Series", "NaT", "Timestamp",
}


def get_definitions(filepath):
    """Get all top-level definitions from a module."""
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()

    defs = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Fallback: regex
        for line in source.split("\n"):
            m = re.match(r'^(def|class)\s+(\w+)', line)
            if m:
                defs.add(m.group(2))
            m = re.match(r'^(\w+)\s*=', line)
            if m:
                defs.add(m.group(1))
        return defs

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.add(node.name)
        elif isinstance(node, ast.ClassDef):
            defs.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defs.add(target.id)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            defs.add(elt.id)
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name):
                defs.add(node.target.id)

    return defs


def get_all_names_used(filepath):
    """Get ALL names used (referenced) in a module, recursively through all nodes."""
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()

    names = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return names

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)

    return names


def get_star_imports(filepath):
    """Get list of modules imported via 'from X import *'."""
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()

    modules = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return modules

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom):
            if node.names and node.names[0].name == '*':
                modules.append(node.module)

    return modules


def get_explicit_imports(filepath):
    """Get all names explicitly imported."""
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()

    names = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return names

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != '*':
                    names.add(alias.asname or alias.name)

    return names


def main():
    print("=" * 60)
    print("Fixing cross-module dependencies")
    print("=" * 60)

    # Step 1: Build a map of {name: module} for all top-level definitions
    name_to_module = {}
    module_defs = {}
    for mod in MODULES:
        if not os.path.exists(mod):
            continue
        defs = get_definitions(mod)
        module_defs[mod] = defs
        for d in defs:
            if d not in name_to_module:  # First module wins
                name_to_module[d] = mod
        print(f"  {mod}: {len(defs)} definitions")

    # Step 2: For each module, find what it needs but doesn't have
    fixes_needed = defaultdict(set)  # module -> set of (name, source_module)

    for mod in MODULES:
        if not os.path.exists(mod):
            continue

        used = get_all_names_used(mod)
        local_defs = module_defs.get(mod, set())
        explicit_imports = get_explicit_imports(mod)
        star_modules = get_star_imports(mod)

        # Names available from star imports (considering __all__)
        star_available = set()
        for sm in star_modules:
            sm_file = sm + ".py"
            if sm_file in module_defs:
                star_available |= module_defs[sm_file]

        available = local_defs | explicit_imports | BUILTIN_NAMES | star_available

        missing = used - available
        for name in missing:
            if name in name_to_module and name_to_module[name] != mod:
                source = name_to_module[name]
                fixes_needed[mod].add((name, source))

    # Step 3: Apply fixes
    total_fixes = 0
    for mod, needed in sorted(fixes_needed.items()):
        if not needed:
            continue

        # Group by source module
        by_source = defaultdict(list)
        for name, source in needed:
            by_source[source].append(name)

        print(f"\n  {mod} needs:")
        for source, names in sorted(by_source.items()):
            source_mod = source.replace(".py", "")
            print(f"    from {source_mod}: {', '.join(sorted(names))}")

        # Read the file and add imports
        with open(mod, "r", encoding="utf-8") as f:
            content = f.read()

        lines = content.split("\n")

        # Find insertion point (after existing imports, before code)
        insert_at = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if (stripped.startswith("import ") or
                stripped.startswith("from ") or
                stripped.startswith("#") or
                stripped == "" or
                stripped.startswith("__all__")):
                insert_at = i + 1
            else:
                break

        # Build import lines
        new_imports = []
        for source, names in sorted(by_source.items()):
            source_mod = source.replace(".py", "")
            # Check if we already have a star import from this module
            if f"from {source_mod} import *" in content:
                # Star import exists but __all__ might not include these names
                # Add explicit import for the missing names
                names_str = ", ".join(sorted(names))
                new_imports.append(f"from {source_mod} import {names_str}")
            else:
                names_str = ", ".join(sorted(names))
                new_imports.append(f"from {source_mod} import {names_str}")

        if new_imports:
            for imp in new_imports:
                lines.insert(insert_at, imp)
                insert_at += 1
            lines.insert(insert_at, "")

            with open(mod, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))

            total_fixes += len(needed)
            print(f"    -> Added {len(new_imports)} import line(s)")

    # Step 4: Ensure __all__ in every module includes ALL defs
    print("\n" + "-" * 60)
    print("Updating __all__ lists...")
    for mod in MODULES:
        if mod == "fetch_mlb_stats.py":
            continue
        if not os.path.exists(mod):
            continue

        defs = get_definitions(mod)
        underscore = [d for d in defs if d.startswith('_')]
        if not underscore:
            continue

        with open(mod, "r", encoding="utf-8") as f:
            content = f.read()

        # Remove old __all__ if exists
        content_no_all = re.sub(r'__all__\s*=\s*\[.*?\]\n*', '', content, flags=re.DOTALL)

        # Re-read defs after removing __all__
        all_names = sorted(defs - {'__all__'})
        all_line = "__all__ = " + repr(all_names) + "\n\n"

        # Find insertion point
        lines = content_no_all.split("\n")
        insert_at = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if (stripped.startswith("import ") or
                stripped.startswith("from ") or
                stripped.startswith("#") or
                stripped == ""):
                insert_at = i + 1
            else:
                break

        lines.insert(insert_at, all_line)
        with open(mod, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"  Updated __all__ in {mod}: {len(all_names)} names ({len(underscore)} underscore)")

    print(f"\n{'=' * 60}")
    print(f"Done! Fixed {total_fixes} missing references.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
