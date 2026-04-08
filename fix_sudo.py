#!/usr/bin/env python3
"""
Fix GHA: 'sudo: playwright: command not found'

The problem: 'sudo playwright install-deps' fails because sudo resets PATH
and can't find the pip-installed playwright binary.

The fix: playwright install-deps handles sudo internally — it runs
'sudo apt-get' on its own. We just call it without sudo.
Also combine into 'playwright install --with-deps chromium' which does
both browser install + system deps in one step.

Run: python fix_sudo.py
Then: push_fix_sudo.bat
"""
import os

REPO = os.path.dirname(os.path.abspath(__file__))


def fix_workflow():
    yml_path = os.path.join(REPO, ".github", "workflows", "daily.yml")
    if not os.path.exists(yml_path):
        print(f"ERROR: {yml_path} not found")
        return False

    with open(yml_path, 'r') as f:
        content = f.read()

    original = content

    # Replace the three separate commands with one combined command + xvfb
    # Old (broken):
    #   playwright install chromium
    #   sudo playwright install-deps
    #   sudo apt-get install -y xvfb
    # New (fixed):
    #   playwright install --with-deps chromium
    #   sudo apt-get install -y xvfb

    # Handle the case with sudo
    old_block = """          playwright install chromium
          sudo playwright install-deps
          sudo apt-get install -y xvfb"""

    new_block = """          playwright install --with-deps chromium
          sudo apt-get install -y xvfb"""

    if old_block in content:
        content = content.replace(old_block, new_block)
        print("[workflow] Replaced playwright commands (sudo variant)")
    else:
        # Try without sudo variant too
        old_block2 = """          playwright install chromium
          playwright install-deps
          sudo apt-get install -y xvfb"""

        if old_block2 in content:
            content = content.replace(old_block2, new_block)
            print("[workflow] Replaced playwright commands (no-sudo variant)")
        else:
            print("[workflow] Could not find expected block to replace")
            print("           Attempting line-by-line fix...")

            # Fallback: just fix the sudo line
            if 'sudo playwright install-deps' in content:
                content = content.replace(
                    'sudo playwright install-deps',
                    'playwright install-deps'
                )
                print("[workflow] Removed sudo from playwright install-deps")
            else:
                print("[workflow] No changes needed or pattern not found")

    if content != original:
        with open(yml_path, 'w') as f:
            f.write(content)
        print("[workflow] daily.yml saved")
        return True
    else:
        print("[workflow] No changes made")
        return False


def main():
    print("=" * 50)
    print("Fix: sudo: playwright: command not found")
    print("=" * 50)
    print()

    ok = fix_workflow()

    print()
    if ok:
        print("FIX APPLIED SUCCESSFULLY")
        print()
        print("Now run: push_fix_sudo.bat")
    else:
        print("NO CHANGES MADE — check output above")


if __name__ == "__main__":
    main()
