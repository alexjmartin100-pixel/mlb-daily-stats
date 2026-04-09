"""
Fix: move _pct, _int, _flt helper definitions BEFORE their first use.

Problem: In batting_leaderboard.py (and possibly pitching_leaderboard.py),
the helper functions _pct, _int, _flt are defined AFTER a line that uses
them in a generator expression. Python 3's closure scoping doesn't allow
referencing a free variable before it's assigned in the enclosing scope.

Fix: Move the helper defs to right after the fg_rows check, before max_g.
"""

import re

FILES_TO_CHECK = ["batting_leaderboard.py", "pitching_leaderboard.py"]


def fix_helper_order(filepath):
    """Move _pct/_int/_flt definitions before their first use."""
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Find the helper function blocks
    helpers_start = None
    helpers_end = None
    helper_lines = []
    helper_names = set()

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        # Look for def _pct, def _int, def _flt
        if stripped.startswith("def _pct(") or stripped.startswith("def _int(") or stripped.startswith("def _flt("):
            if helpers_start is None:
                helpers_start = i

            # Capture this entire function def (until next non-indented or blank-then-def)
            func_name = stripped.split("(")[0].replace("def ", "")
            helper_names.add(func_name)
            helper_lines.append(lines[i])
            j = i + 1
            while j < len(lines):
                # If we hit another def at the same indent level, stop
                next_stripped = lines[j].strip()
                if next_stripped.startswith("def ") and not next_stripped.startswith("def _pct") and \
                   not next_stripped.startswith("def _int") and not next_stripped.startswith("def _flt"):
                    break
                # If it's a non-empty line at base indent (not more indented than the def), stop
                if next_stripped and not lines[j].startswith("        ") and not lines[j].startswith("\t\t"):
                    # Check if it's another helper
                    if next_stripped.startswith("def _pct(") or next_stripped.startswith("def _int(") or next_stripped.startswith("def _flt("):
                        break
                    # Could be the for loop or other code at the same indent
                    if not next_stripped.startswith("def ") and lines[j][0:1] not in (" ", "\t", "\n"):
                        break
                helper_lines.append(lines[j])
                j += 1

            helpers_end = j
            i = j
            continue
        i += 1

    if helpers_start is None:
        print(f"  {filepath}: No _pct/_int/_flt helpers found — skipping")
        return False

    print(f"  {filepath}: Found helpers at lines {helpers_start+1}-{helpers_end}")
    print(f"    Helpers: {', '.join(sorted(helper_names))}")

    # Find where they're first used (look for _int( or _pct( or _flt( before helpers_start)
    first_use = None
    for i in range(helpers_start):
        line = lines[i]
        for name in helper_names:
            if name + "(" in line:
                first_use = i
                break
        if first_use is not None:
            break

    if first_use is None:
        print(f"  {filepath}: Helpers aren't used before their definition — no fix needed")
        return False

    print(f"  {filepath}: First use at line {first_use+1}: {lines[first_use].strip()[:60]}")

    # Strategy: extract the helper block, remove from original position, insert before first use
    # We need to find a good insertion point — right after the try: and fg_rows check
    # Look for "if not fg_rows:" or "raise ValueError" before first_use
    insert_at = first_use  # Default: right before first use
    for i in range(first_use - 1, -1, -1):
        stripped = lines[i].strip()
        if "raise ValueError" in stripped or "if not fg_rows" in stripped:
            insert_at = i + 1
            break
        if "qual_pa" in stripped:
            # Put before qual_pa since it also uses the helpers
            insert_at = i
            break

    # Get the indentation of the first_use line to match
    indent = ""
    for ch in lines[first_use]:
        if ch in (" ", "\t"):
            indent += ch
        else:
            break

    # Build the helper block with correct indentation
    # Determine the indentation of the original helper defs
    orig_indent = ""
    for ch in helper_lines[0]:
        if ch in (" ", "\t"):
            orig_indent += ch
        else:
            break

    # Re-indent helpers to match the insertion point indent
    reindented = []
    for hl in helper_lines:
        reindented.append(hl)
    reindented.append("\n")  # blank line after helpers

    # Remove helpers from their original position
    new_lines = lines[:helpers_start] + lines[helpers_end:]

    # Adjust insert_at if it was after the removed block
    if insert_at > helpers_start:
        insert_at -= (helpers_end - helpers_start)

    # Insert helpers at the new position
    for j, hl in enumerate(reindented):
        new_lines.insert(insert_at + j, hl)

    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"  {filepath}: Moved helpers to line {insert_at+1}")
    return True


def main():
    print("=" * 60)
    print("Fixing helper function ordering")
    print("=" * 60)

    changed = []
    for f in FILES_TO_CHECK:
        try:
            if fix_helper_order(f):
                changed.append(f)
        except Exception as e:
            print(f"  {f}: ERROR - {e}")
            import traceback
            traceback.print_exc()

    if changed:
        print(f"\nFixed: {', '.join(changed)}")
    else:
        print("\nNo changes needed")

    # Verify the fix by trying to compile each file
    print("\nVerifying syntax...")
    for f in FILES_TO_CHECK:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                compile(fh.read(), f, "exec")
            print(f"  {f}: OK")
        except SyntaxError as e:
            print(f"  {f}: SYNTAX ERROR at line {e.lineno}: {e.msg}")


if __name__ == "__main__":
    main()
