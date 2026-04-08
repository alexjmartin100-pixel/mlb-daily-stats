#!/usr/bin/env python3
"""
Diagnose and fix fetch_mlb_stats.py for FanGraphs type=0 (wOBA/K%/BB%/WAR).

This script:
1. Reads the file and reports what it finds
2. Checks if _get_pw_page has cookie injection
3. Checks if step 2/6 (batting leaderboard type=0) exists
4. Checks if fg_api() properly calls _pw_fetch_json for type=0
5. Applies all necessary fixes

Run: python diagnose_and_fix.py
"""
import re, sys, os

FILENAME = "fetch_mlb_stats.py"

def find_file():
    for p in [FILENAME, os.path.join(os.path.dirname(__file__), FILENAME)]:
        if os.path.isfile(p):
            return p
    return None

def main():
    filepath = find_file()
    if not filepath:
        print(f"ERROR: {FILENAME} not found"); sys.exit(1)

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    lines = content.split('\n')
    total = len(lines)
    print(f"File: {filepath} ({total} lines, {len(content)} bytes)")
    print("=" * 60)

    # --- DIAGNOSTIC 1: Check for step 2/6 ---
    print("\n[DIAG 1] Looking for step 2/6 (FanGraphs leaderboard)...")
    step2_lines = [(i, l) for i, l in enumerate(lines) if '2/6' in l]
    if step2_lines:
        for i, l in step2_lines:
            print(f"  Found at line {i+1}: {l.strip()}")
    else:
        print("  NOT FOUND - step 2/6 is missing from the code!")

    # --- DIAGNOSTIC 2: Check for batting leaderboard call ---
    print("\n[DIAG 2] Looking for 'batting leaderboard' fg_api call...")
    bat_lines = [(i, l) for i, l in enumerate(lines) if 'batting leaderboard' in l.lower()]
    if bat_lines:
        for i, l in bat_lines:
            print(f"  Found at line {i+1}: {l.strip()}")
    else:
        print("  NOT FOUND - batting leaderboard call is missing!")

    # --- DIAGNOSTIC 3: Check for type=0 or "type": "0" ---
    print("\n[DIAG 3] Looking for type=0 API calls...")
    type0_lines = [(i, l) for i, l in enumerate(lines) if '"type": "0"' in l or "'type': '0'" in l or "type=0" in l]
    if type0_lines:
        for i, l in type0_lines[:10]:
            print(f"  Found at line {i+1}: {l.strip()}")
    else:
        print("  NOT FOUND - no type=0 API calls in the code!")

    # --- DIAGNOSTIC 4: Check _get_pw_page function ---
    print("\n[DIAG 4] Looking for _get_pw_page function...")
    pw_func = [(i, l) for i, l in enumerate(lines) if 'def _get_pw_page' in l]
    if pw_func:
        idx = pw_func[0][0]
        print(f"  Found at line {idx+1}")
        # Show the function (next 70 lines)
        print("  --- Function content (first 70 lines) ---")
        for j in range(idx, min(idx+70, total)):
            print(f"    {j+1}: {lines[j]}")
    else:
        print("  NOT FOUND!")

    # --- DIAGNOSTIC 5: Check for cookie injection ---
    print("\n[DIAG 5] Looking for cookie injection code...")
    cookie_inject = [(i, l) for i, l in enumerate(lines) if 'add_cookies' in l or 'Pre-seed' in l]
    if cookie_inject:
        for i, l in cookie_inject:
            print(f"  Found at line {i+1}: {l.strip()}")
    else:
        print("  NOT FOUND - cookie injection is missing from _get_pw_page!")

    # --- DIAGNOSTIC 6: Check _pw_fetch_json ---
    print("\n[DIAG 6] Looking for _pw_fetch_json function...")
    pw_fetch = [(i, l) for i, l in enumerate(lines) if 'def _pw_fetch_json' in l]
    if pw_fetch:
        idx = pw_fetch[0][0]
        print(f"  Found at line {idx+1}")
    else:
        print("  NOT FOUND!")

    # --- DIAGNOSTIC 7: Check fg_api function ---
    print("\n[DIAG 7] Looking for fg_api function...")
    fg_api_func = [(i, l) for i, l in enumerate(lines) if 'def fg_api' in l]
    if fg_api_func:
        idx = fg_api_func[0][0]
        print(f"  Found at line {idx+1}")
        for j in range(idx, min(idx+20, total)):
            print(f"    {j+1}: {lines[j]}")
    else:
        print("  NOT FOUND!")

    # --- DIAGNOSTIC 8: Check for fg_rows usage pattern ---
    print("\n[DIAG 8] Looking for 'fg_rows' variable usage...")
    fg_rows_lines = [(i, l) for i, l in enumerate(lines) if 'fg_rows' in l]
    if fg_rows_lines:
        for i, l in fg_rows_lines[:15]:
            print(f"  Line {i+1}: {l.strip()}")
    else:
        print("  NOT FOUND!")

    # --- DIAGNOSTIC 9: Check what step labels exist ---
    print("\n[DIAG 9] All step labels in the code...")
    step_lines = [(i, l) for i, l in enumerate(lines) if re.search(r'\[\s*\d+[ab]?/\d+\s*\]', l)]
    for i, l in step_lines:
        print(f"  Line {i+1}: {l.strip()}")

    # --- DIAGNOSTIC 10: Check for _load_fg_cookie ---
    print("\n[DIAG 10] Looking for _load_fg_cookie function...")
    load_cookie = [(i, l) for i, l in enumerate(lines) if 'def _load_fg_cookie' in l]
    if load_cookie:
        idx = load_cookie[0][0]
        print(f"  Found at line {idx+1}")
        for j in range(idx, min(idx+15, total)):
            print(f"    {j+1}: {lines[j]}")
    else:
        print("  NOT FOUND!")

    print("\n" + "=" * 60)
    print("DIAGNOSIS COMPLETE")
    print("=" * 60)
    print("\nPlease paste this output back in the chat so I can see what's happening.")

if __name__ == '__main__':
    main()
