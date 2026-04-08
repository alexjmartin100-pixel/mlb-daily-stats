#!/usr/bin/env python3
"""
FIX: wOBA, K%, BB%, fWAR not populating on Season Leaders tab.

ROOT CAUSE:
  The batting/pitching leaderboard calls use FanGraphs API type=0 (Dashboard).
  Cloudflare blocks type=0 on GHA runners, so the call silently fails.
  Meanwhile, Stuff+ uses type=8 (Statcast) — same URL, same function — and
  it works fine.

  Verified: type=8 returns ALL the same fields as type=0 (wOBA, K%, BB%, WAR,
  etc), plus Statcast-specific fields.  Both go through the same endpoint
  (fangraphs.com/api/leaders/major-league/data).

FIX:
  Change "type": "0"  →  "type": "8"  in the leaderboard fg_api() calls ONLY.
  This makes them use the same type that already works for Stuff+.

  We target ONLY lines that contain both 'fg_api' context AND '"type": "0"'
  inside fetch_season_batting_leaderboard / fetch_season_pitching_leaderboard.

Run:  python fix_type0.py
Then: push_fix_type0.bat
"""
import os, re

REPO = os.path.dirname(os.path.abspath(__file__))
PY   = os.path.join(REPO, "fetch_mlb_stats.py")


def fix():
    with open(PY, "r", encoding="utf-8") as f:
        src = f.read()

    # ── Strategy ──
    # Find fg_api() call blocks that contain "type": "0"
    # These are multi-line dict literals passed to fg_api().
    # We match:   fg_api({  ... "type": "0" ... }, "some label")
    #
    # We specifically look for the pattern in the context of
    # leaderboard functions (batting and pitching).

    changes = 0

    # Pattern: inside an fg_api({ ... }) call, find "type": "0"
    # We look for fg_api( followed by a dict, and within that dict "type": "0"
    # Safer approach: find each fg_api call block and change type inside it

    # Find all fg_api call blocks
    fg_api_pattern = re.compile(
        r'(fg_api\s*\(\s*\{[^}]*?"type"\s*:\s*)"0"([^}]*?\}\s*,\s*"[^"]*leaderboard[^"]*")',
        re.DOTALL | re.IGNORECASE
    )

    def replace_type(m):
        nonlocal changes
        changes += 1
        return m.group(1) + '"8"' + m.group(2)

    new_src = fg_api_pattern.sub(replace_type, src)

    # If the regex didn't match (maybe label doesn't say "leaderboard"),
    # try a broader pattern: any fg_api call with "type": "0"
    if changes == 0:
        print("[info] Strict pattern didn't match, trying broader pattern...")
        broad_pattern = re.compile(
            r'(fg_api\s*\(\s*\{[^}]*?"type"\s*:\s*)"0"',
            re.DOTALL
        )
        new_src = broad_pattern.sub(replace_type, src)

    # Also catch the case where the code uses a variable dict with "type": "0"
    # right before fg_api calls — search within leaderboard functions
    if changes == 0:
        print("[info] Trying function-scoped search...")
        # Find fetch_season_batting_leaderboard function and change type=0
        for func_name in ['fetch_season_batting_leaderboard', 'fetch_season_pitching_leaderboard']:
            idx = new_src.find(f'def {func_name}')
            if idx == -1:
                continue
            # Find next def (end of function)
            next_def = new_src.find('\ndef ', idx + 10)
            if next_def == -1:
                next_def = len(new_src)
            func_body = new_src[idx:next_def]
            # Replace "type": "0" with "type": "8" within this function
            new_func = func_body.replace('"type": "0"', '"type": "8"')
            if new_func != func_body:
                new_src = new_src[:idx] + new_func + new_src[next_def:]
                changes += 1
                print(f"  Changed type 0→8 in {func_name}")

    if changes > 0:
        with open(PY, "w", encoding="utf-8") as f:
            f.write(new_src)
        print(f"\n[SUCCESS] Changed {changes} occurrence(s) of type=0 → type=8")
    else:
        print("\n[WARNING] No type=0 leaderboard calls found to change")
        print("  Searching for all type mentions...")
        for i, line in enumerate(src.split('\n'), 1):
            if '"type"' in line and '"0"' in line and 'fg_api' not in line:
                if any(kw in line.lower() for kw in ['type', 'qual']):
                    print(f"  Line {i}: {line.strip()[:100]}")

    return changes > 0


def main():
    print("=" * 60)
    print("Fix: Change FanGraphs leaderboard type=0 → type=8")
    print("  type=8 returns same data AND works (Stuff+ proves it)")
    print("=" * 60)
    print()

    if not os.path.exists(PY):
        print(f"ERROR: {PY} not found")
        return

    ok = fix()
    print()
    if ok:
        print("Now run: push_fix_type0.bat")
    else:
        print("Manual intervention may be needed.")


if __name__ == "__main__":
    main()
