"""
Fix star imports for underscore-prefixed names.

Problem: `from module import *` skips names starting with '_'.
Solution: Add __all__ to each module listing ALL top-level names.
"""

import ast, os, sys

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
]


def get_top_level_names(filepath):
    """Parse a Python file and return all top-level names."""
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()

    names = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        print(f"  SYNTAX ERROR in {filepath}: {e}")
        # Fallback: regex-based extraction
        import re
        for line in source.split("\n"):
            m = re.match(r'^(def|class)\s+(\w+)', line)
            if m:
                names.append(m.group(2))
            m = re.match(r'^(\w+)\s*=', line)
            if m and not m.group(1).startswith('__'):
                names.append(m.group(1))
        return names

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
        elif isinstance(node, ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    # Skip __all__ itself and dunder names
                    if not target.id.startswith('__'):
                        names.append(target.id)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name) and not elt.id.startswith('__'):
                            names.append(elt.id)
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and not node.target.id.startswith('__'):
                names.append(node.target.id)

    return names


def add_all_to_module(filepath):
    """Add __all__ to a module file."""
    names = get_top_level_names(filepath)

    # Filter: only need __all__ if there are underscore-prefixed names
    underscore_names = [n for n in names if n.startswith('_')]
    if not underscore_names:
        print(f"  SKIP {filepath} (no underscore names)")
        return False

    print(f"  {filepath}: {len(names)} names ({len(underscore_names)} underscore)")
    for n in underscore_names:
        print(f"    _ {n}")

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Check if __all__ already exists
    if "__all__" in content:
        print(f"  SKIP {filepath} (__all__ already exists)")
        return False

    # Build __all__ line
    all_list = repr(names)
    all_line = f"__all__ = {all_list}\n\n"

    # Insert after the last top-level import line (before first def/class/assignment)
    lines = content.split("\n")
    insert_at = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (stripped.startswith("import ") or
            stripped.startswith("from ") or
            stripped == "" or
            stripped.startswith("#")):
            insert_at = i + 1
        else:
            break

    lines.insert(insert_at, all_line)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"  ADDED __all__ at line {insert_at + 1}")
    return True


def main():
    print("=" * 60)
    print("Adding __all__ to modules for star import compatibility")
    print("=" * 60)

    changed = []
    for mod in MODULES:
        if not os.path.exists(mod):
            print(f"  MISSING {mod}")
            continue
        if add_all_to_module(mod):
            changed.append(mod)

    if changed:
        print(f"\nFixed {len(changed)} module(s): {', '.join(changed)}")
    else:
        print("\nNo changes needed.")


if __name__ == "__main__":
    main()
