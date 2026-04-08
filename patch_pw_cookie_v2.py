#!/usr/bin/env python3
"""
Robust patcher for fetch_mlb_stats.py
Injects cookie pre-seeding into the Playwright _get_pw_page() function.

Run from the repo folder:
    python patch_pw_cookie_v2.py

Then push:
    git add fetch_mlb_stats.py
    git commit -m "fix: inject fg_cookie.txt cookies into Playwright browser context"
    git push
"""
import re, sys, os

FILENAME = "fetch_mlb_stats.py"

# The code block to insert (cookie injection)
COOKIE_BLOCK = '''
        # --- Pre-seed Playwright with fg_cookie.txt cookies ---
        try:
            _cs = _load_fg_cookie()
            if _cs:
                _ck = []
                for _p in _cs.split(';'):
                    _p = _p.strip()
                    if '=' in _p:
                        _n, _v = _p.split('=', 1)
                        _ck.append({"name": _n.strip(), "value": _v.strip(),
                                    "domain": ".fangraphs.com", "path": "/"})
                if _ck:
                    ctx.add_cookies(_ck)
                    print(f"  Pre-seeded {len(_ck)} cookie(s) from fg_cookie.txt")
        except Exception as _ce:
            print(f"  Cookie pre-seed skipped: {_ce}")
'''

def patch():
    # Find the file - check current dir and common locations
    filepath = None
    candidates = [
        FILENAME,
        os.path.join(os.path.dirname(__file__), FILENAME),
    ]
    for c in candidates:
        if os.path.isfile(c):
            filepath = c
            break

    if not filepath:
        print(f"ERROR: Cannot find {FILENAME}")
        print(f"  Searched: {candidates}")
        print(f"  Current dir: {os.getcwd()}")
        sys.exit(1)

    print(f"Reading {filepath} ...")
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.split('\n')
    total = len(lines)
    print(f"  {total} lines read")

    # Check if already patched
    if 'Pre-seed Playwright with fg_cookie' in content:
        print("ALREADY PATCHED - cookie injection code is already present.")
        print("No changes needed.")
        sys.exit(0)

    # STRATEGY: Find the _get_pw_page function, then find 'new_page()' within it
    # and insert the cookie block right after that line.

    # Step 1: Find def _get_pw_page
    func_start = -1
    for i, line in enumerate(lines):
        if 'def _get_pw_page' in line:
            func_start = i
            print(f"  Found _get_pw_page at line {i+1}: {line.strip()}")
            break

    if func_start < 0:
        print("ERROR: Could not find 'def _get_pw_page' in the file!")
        print("  The function may have been renamed or removed.")
        sys.exit(1)

    # Step 2: Find 'new_page()' within the next 80 lines (should be within the function)
    new_page_line = -1
    for i in range(func_start, min(func_start + 80, total)):
        if 'new_page()' in lines[i]:
            new_page_line = i
            print(f"  Found new_page() at line {i+1}: {lines[i].strip()}")
            break

    if new_page_line < 0:
        # Fallback: look for 'new_context' and insert after that block
        for i in range(func_start, min(func_start + 80, total)):
            if 'new_context' in lines[i]:
                # Find the end of the new_context call (could span multiple lines)
                j = i
                paren_depth = 0
                while j < min(i + 20, total):
                    paren_depth += lines[j].count('(') - lines[j].count(')')
                    if paren_depth <= 0 and j > i:
                        break
                    j += 1
                # Look for 'page =' after new_context
                for k in range(j, min(j + 5, total)):
                    if 'page' in lines[k] and '=' in lines[k]:
                        new_page_line = k
                        print(f"  Found page assignment at line {k+1}: {lines[k].strip()}")
                        break
                break

    if new_page_line < 0:
        print("ERROR: Could not find 'new_page()' or page assignment in _get_pw_page!")
        print("  Showing lines around the function for debugging:")
        for i in range(func_start, min(func_start + 40, total)):
            print(f"    {i+1}: {lines[i]}")
        sys.exit(1)

    # Step 3: Find where to insert - right after the new_page line,
    # but BEFORE the stealth section or any page.goto
    insert_after = new_page_line
    print(f"  Will insert cookie block after line {insert_after+1}")

    # Step 4: Build the patched content
    before = lines[:insert_after + 1]
    after = lines[insert_after + 1:]

    patched_lines = before + [COOKIE_BLOCK.rstrip()] + after
    patched_content = '\n'.join(patched_lines)

    # Step 5: Write back
    print(f"  Writing patched file...")
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(patched_content)

    new_total = len(patched_content.split('\n'))
    print(f"  Done! {total} -> {new_total} lines ({new_total - total} lines added)")
    print()
    print("SUCCESS! Now push to GitHub:")
    print("  git add fetch_mlb_stats.py")
    print('  git commit -m "fix: inject fg_cookie.txt cookies into Playwright browser context"')
    print("  git push")

if __name__ == '__main__':
    patch()
