"""
Fix indentation issues in all split module files.

The split_modules.py script copied some lines (like 'import io') that were
originally inside an if-block, preserving their indentation. This script
strips leading whitespace from header lines (imports, from-imports, comments)
that appear BEFORE the first function def or top-level assignment in each module.
"""

import re, os

MODULES = [
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

def fix_module(filepath):
    if not os.path.exists(filepath):
        print(f"  SKIP {filepath} (not found)")
        return False

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    changed = False
    fixed_lines = []

    # Find where the "real code" starts (first def, class, or non-import assignment)
    header_end = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Skip blank lines, comments, imports, from-imports
        if (stripped == "" or
            stripped.startswith("#") or
            stripped.startswith("import ") or
            stripped.startswith("from ") or
            stripped.startswith('"""') or
            stripped.startswith("'''")):
            continue
        # This is the first line of real code
        header_end = i
        break

    for i, line in enumerate(lines):
        if i < header_end:
            stripped = line.lstrip()
            # Only fix import/from lines and comments that have leading whitespace
            if (stripped.startswith("import ") or
                stripped.startswith("from ") or
                (stripped.startswith("#") and line[0] == " ")):
                if line != stripped + "\n" and line != stripped:
                    fixed_lines.append(stripped if stripped.endswith("\n") else stripped + "\n")
                    changed = True
                    continue
        fixed_lines.append(line)

    if changed:
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(fixed_lines)
        print(f"  FIXED {filepath}")
        # Show first 15 lines
        for j, line in enumerate(fixed_lines[:15], 1):
            print(f"    {j:3d}: {line.rstrip()}")
    else:
        print(f"  OK   {filepath} (no changes needed)")

    return changed


def main():
    print("=" * 60)
    print("Fixing indentation in all split modules")
    print("=" * 60)

    any_changed = False
    for mod in MODULES:
        print(f"\n  Checking {mod}...")
        if fix_module(mod):
            any_changed = True

    if any_changed:
        print("\n" + "=" * 60)
        print("FIXES APPLIED - ready to push")
        print("=" * 60)
    else:
        print("\n  No fixes needed!")


if __name__ == "__main__":
    main()
