#!/usr/bin/env python3
"""
Fix GHA exit code 127: playwright not in requirements.txt
Also fixes playwright install-deps to use sudo.

Run: python fix_workflow_127.py
Then: push_fix_127.bat
"""
import os

REPO = os.path.dirname(os.path.abspath(__file__))

def fix_requirements():
    req_path = os.path.join(REPO, "requirements.txt")
    with open(req_path, 'r') as f:
        content = f.read()

    if 'playwright' in content.lower():
        print("[requirements] playwright already listed — skipping")
        return True

    # Add playwright to requirements
    content = content.rstrip('\n') + '\nplaywright\n'
    with open(req_path, 'w') as f:
        f.write(content)
    print("[requirements] Added 'playwright' to requirements.txt")
    return True


def fix_workflow():
    yml_path = os.path.join(REPO, ".github", "workflows", "daily.yml")
    if not os.path.exists(yml_path):
        print(f"ERROR: {yml_path} not found")
        return False

    with open(yml_path, 'r') as f:
        content = f.read()

    changed = False

    # Fix 1: playwright install-deps needs sudo
    if 'playwright install-deps' in content and 'sudo playwright install-deps' not in content:
        content = content.replace(
            'playwright install-deps',
            'sudo playwright install-deps'
        )
        print("[workflow] Added sudo to playwright install-deps")
        changed = True

    # Fix 2: Remove redundant 'sudo apt-get install -y xvfb' since
    # playwright install-deps already installs xvfb and all needed libs.
    # But keep it as a safety net — it's harmless.

    if changed:
        with open(yml_path, 'w') as f:
            f.write(content)
        print("[workflow] daily.yml updated")
    else:
        print("[workflow] No changes needed")

    return True


def main():
    print("=" * 50)
    print("Fixing exit code 127 (playwright not found)")
    print("=" * 50)

    ok1 = fix_requirements()
    print()
    ok2 = fix_workflow()

    print()
    if ok1 and ok2:
        print("ALL FIXES APPLIED")
        print()
        print("Now run: push_fix_127.bat")
    else:
        print("SOME FIXES FAILED — check output above")


if __name__ == "__main__":
    main()
