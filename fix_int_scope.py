"""
Fix the '_int' free variable scoping error in batting_leaderboard.py.

The problem: on line ~44, a generator expression uses _int() before it's
defined on line ~54. Python 3 treats _int as a local variable in the
enclosing scope (because it's assigned later), so the generator can't
access it yet.

Fix approach: Find the line with max_g = max((_int(...) and replace the
_int() call with an inline equivalent, since _int is just:
    def _int(v):
        try: return int(float(v))
        except: return 0
"""

import re

def fix_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Check if the problem exists
    if "_int(r.get" not in content and "_int(row.get" not in content:
        print(f"  {filepath}: No _int generator pattern found — skipping")
        return False

    lines = content.split("\n")
    changed = False

    for i, line in enumerate(lines):
        # Look for: max_g = max((_int(r.get('G')) for r in fg_rows), default=1)
        # or similar patterns where _int is used in a generator before being defined
        if "max(" in line and "_int(" in line and "for " in line:
            # Replace _int(x) with inline equivalent: int(float(x or 0))
            old = line
            # Pattern: _int(r.get('G')) or _int(r.get("G", 0))
            new = re.sub(
                r'_int\(([^)]+)\)',
                r'(lambda v: (int(float(v)) if v is not None else 0))(\1)',
                line
            )
            if new != old:
                lines[i] = new
                changed = True
                print(f"  {filepath} L{i+1}: Fixed inline")
                print(f"    OLD: {old.strip()}")
                print(f"    NEW: {new.strip()}")

    if changed:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return True

    print(f"  {filepath}: Pattern not matched")
    return False


def verify(filepath):
    """Try importing the module to see if the scoping error is gone."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            compile(f.read(), filepath, "exec")
        print(f"  {filepath}: Syntax OK")
        return True
    except SyntaxError as e:
        print(f"  {filepath}: SYNTAX ERROR at line {e.lineno}: {e.msg}")
        return False


print("=" * 60)
print("Fixing _int free variable scoping error")
print("=" * 60)

for f in ["batting_leaderboard.py", "pitching_leaderboard.py"]:
    try:
        fix_file(f)
    except Exception as e:
        print(f"  {f}: ERROR - {e}")
        import traceback
        traceback.print_exc()

print("\nVerifying syntax...")
for f in ["batting_leaderboard.py", "pitching_leaderboard.py"]:
    verify(f)

# Now test if the actual function works
print("\nTesting fetch_season_batting_leaderboard...")
try:
    # Force reimport
    import importlib
    import batting_leaderboard
    importlib.reload(batting_leaderboard)

    players = batting_leaderboard.fetch_season_batting_leaderboard(2026)
    with_woba = sum(1 for p in players if p.get("woba") is not None)
    with_kpct = sum(1 for p in players if p.get("k_pct") is not None)
    with_war = sum(1 for p in players if p.get("war") is not None)
    print(f"  {len(players)} players")
    print(f"  woba:  {with_woba}/{len(players)}")
    print(f"  k_pct: {with_kpct}/{len(players)}")
    print(f"  war:   {with_war}/{len(players)}")

    if with_woba > 0:
        print("  SUCCESS! FanGraphs stats are populating!")
    else:
        print("  STILL FAILING — need deeper investigation")
        # Print the actual error
        print("\n  Retrying with verbose error...")
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            players2 = batting_leaderboard.fetch_season_batting_leaderboard(2026)
        output = buf.getvalue()
        # Show lines with "failed" or "error"
        for line in output.split("\n"):
            if "fail" in line.lower() or "error" in line.lower() or "exception" in line.lower():
                print(f"    {line}")
except Exception as e:
    print(f"  ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
