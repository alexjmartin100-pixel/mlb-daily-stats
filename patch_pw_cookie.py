"""
Patch fetch_mlb_stats.py to inject fg_cookie.txt cookies into the
Playwright browser context BEFORE navigating to FanGraphs.

This fixes the issue where FanGraphs type=0 API (wOBA, K%, BB%, WAR)
fails because Cloudflare blocks the Playwright browser and the
cf_clearance cookie from fg_cookie.txt was never being injected
into the browser context.
"""
import re, sys, os

SCRIPT = os.path.join(os.path.dirname(__file__), "fetch_mlb_stats.py")

with open(SCRIPT, "r", encoding="utf-8") as f:
    code = f.read()

# ── The anchor: the line right after ctx.new_page() ──────────────
OLD = "        page = ctx.new_page()\n\n        # \u2014 Stealth"

NEW = """        page = ctx.new_page()

        # \u2014 Pre-seed Playwright with fg_cookie.txt cookies \u2014
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

        # \u2014 Stealth"""

if OLD not in code:
    print("ERROR: Could not find the anchor text in fetch_mlb_stats.py")
    print("The file may have already been patched or the code has changed.")
    sys.exit(1)

count = code.count(OLD)
if count != 1:
    print(f"ERROR: Found {count} matches (expected 1). Aborting.")
    sys.exit(1)

code = code.replace(OLD, NEW, 1)

with open(SCRIPT, "w", encoding="utf-8") as f:
    f.write(code)

print("SUCCESS: Patched _get_pw_page() to inject fg_cookie.txt into Playwright context")
print("  wOBA, K%, BB%, and WAR should now populate on the Season Leaders tab")
