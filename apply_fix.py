#!/usr/bin/env python3
"""
Fix FanGraphs type=0 (wOBA/K%/BB%/WAR) by:
1. Updating daily.yml to install Playwright browsers + use xvfb-run
2. Updating _get_pw_page() to verify cf_clearance and retry if missing

Run: python apply_fix.py
Then: git add -A && git commit -m "fix: add xvfb + playwright install + cf_clearance retry" && git push
"""
import os, sys

REPO = os.path.dirname(os.path.abspath(__file__))

# ═══════════════════════════════════════════════════════════════
# FIX 1: Update daily.yml workflow
# ═══════════════════════════════════════════════════════════════
def fix_workflow():
    yml_path = os.path.join(REPO, ".github", "workflows", "daily.yml")
    if not os.path.exists(yml_path):
        print(f"ERROR: {yml_path} not found")
        return False

    content = open(yml_path, 'r').read()

    # Check if already fixed
    if 'xvfb-run' in content:
        print("[workflow] Already has xvfb-run — skipping")
        return True

    # Fix 1a: Add playwright install + xvfb to Install dependencies step
    old_install = "- name: Install dependencies\n        run: pip install -r requirements.txt"
    new_install = """- name: Install dependencies
        run: |
          pip install -r requirements.txt
          playwright install chromium
          playwright install-deps
          sudo apt-get install -y xvfb"""

    if old_install in content:
        content = content.replace(old_install, new_install)
        print("[workflow] Added playwright install + xvfb to Install dependencies")
    else:
        print("[workflow] WARNING: Could not find Install dependencies step to patch")
        print("           Looking for alternative patterns...")
        # Try a more flexible match
        if 'pip install -r requirements.txt' in content and 'playwright install' not in content:
            content = content.replace(
                'pip install -r requirements.txt',
                'pip install -r requirements.txt\n          playwright install chromium\n          playwright install-deps\n          sudo apt-get install -y xvfb'
            )
            print("[workflow] Added playwright install + xvfb (flexible match)")
        else:
            print("[workflow] ERROR: Could not patch Install dependencies")
            return False

    # Fix 1b: Wrap python command with xvfb-run
    old_run = "run: python fetch_mlb_stats.py"
    new_run = "run: xvfb-run python fetch_mlb_stats.py"

    if old_run in content and 'xvfb-run' not in content:
        content = content.replace(old_run, new_run)
        print("[workflow] Wrapped fetch_mlb_stats.py with xvfb-run")
    else:
        print("[workflow] WARNING: Could not find 'run: python fetch_mlb_stats.py' to wrap")

    with open(yml_path, 'w') as f:
        f.write(content)
    print("[workflow] ✓ daily.yml updated")
    return True


# ═══════════════════════════════════════════════════════════════
# FIX 2: Update _get_pw_page() to verify cf_clearance + retry
# ═══════════════════════════════════════════════════════════════
def fix_python():
    py_path = os.path.join(REPO, "fetch_mlb_stats.py")
    if not os.path.exists(py_path):
        print(f"ERROR: {py_path} not found")
        return False

    content = open(py_path, 'r', encoding='utf-8').read()

    # Check if already fixed
    if 'cf_clearance' in content and 'retry' in content.lower() and 'RETRY' in content:
        print("[python] Already has cf_clearance retry — skipping")
        return True

    # Find the section after page.wait_for_timeout(9_000) where cookies are checked
    # Current code:
    #   page.wait_for_timeout(9_000)
    #   cookies = [c["name"] for c in ctx.cookies()]
    #   print(f"  Playwright ready — cookies: {cookies}")
    #   _PW_PAGE = page

    old_cookie_check = '''        page.wait_for_timeout(9_000)
        cookies = [c["name"] for c in ctx.cookies()]
        print(f"  Playwright ready — cookies: {cookies}")
        _PW_PAGE = page'''

    new_cookie_check = '''        page.wait_for_timeout(9_000)
        cookies = [c["name"] for c in ctx.cookies()]
        print(f"  Playwright ready — cookies: {cookies}")

        # ── RETRY: verify cf_clearance was obtained ──────────────────
        if "cf_clearance" not in cookies:
            print("  ⚠ cf_clearance NOT found — retrying with page reload…")
            for _attempt in range(3):
                page.reload(wait_until="load", timeout=30_000)
                page.wait_for_timeout(12_000)  # longer wait for CF challenge
                cookies = [c["name"] for c in ctx.cookies()]
                print(f"  Retry {_attempt+1}/3 — cookies: {cookies}")
                if "cf_clearance" in cookies:
                    print("  ✓ cf_clearance obtained on retry!")
                    break
            else:
                print("  ⚠ cf_clearance still missing after 3 retries — "
                      "API calls may get 403")
        else:
            print("  ✓ cf_clearance present")

        _PW_PAGE = page'''

    if old_cookie_check in content:
        content = content.replace(old_cookie_check, new_cookie_check)
        print("[python] Added cf_clearance verification + retry loop")
    else:
        print("[python] WARNING: Could not find exact cookie check block")
        # Try a simpler match
        simple_old = 'cookies = [c["name"] for c in ctx.cookies()]\n        print(f"  Playwright ready'
        if simple_old in content:
            print("[python] Found simpler pattern — patching...")
            # Insert after the print line
            old_line = 'print(f"  Playwright ready — cookies: {cookies}")\n        _PW_PAGE = page'
            new_line = '''print(f"  Playwright ready — cookies: {cookies}")

        # ── RETRY: verify cf_clearance was obtained ──────────────────
        if "cf_clearance" not in cookies:
            print("  ⚠ cf_clearance NOT found — retrying with page reload…")
            for _attempt in range(3):
                page.reload(wait_until="load", timeout=30_000)
                page.wait_for_timeout(12_000)
                cookies = [c["name"] for c in ctx.cookies()]
                print(f"  Retry {_attempt+1}/3 — cookies: {cookies}")
                if "cf_clearance" in cookies:
                    print("  ✓ cf_clearance obtained on retry!")
                    break
            else:
                print("  ⚠ cf_clearance still missing after 3 retries")
        else:
            print("  ✓ cf_clearance present")

        _PW_PAGE = page'''
            content = content.replace(old_line, new_line)
            print("[python] ✓ Patched with simpler match")
        else:
            print("[python] ERROR: Could not find cookie check pattern to patch")
            return False

    with open(py_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("[python] ✓ fetch_mlb_stats.py updated")
    return True


def main():
    print("=" * 60)
    print("Applying FanGraphs type=0 fix")
    print("=" * 60)

    ok1 = fix_workflow()
    print()
    ok2 = fix_python()

    print()
    print("=" * 60)
    if ok1 and ok2:
        print("✓ ALL FIXES APPLIED")
        print()
        print("Now push to GitHub:")
        print('  git add -A')
        print('  git commit -m "fix: add xvfb + playwright install + cf_clearance retry"')
        print('  git push')
    else:
        print("⚠ Some fixes failed — see messages above")
    print("=" * 60)


if __name__ == '__main__':
    main()
