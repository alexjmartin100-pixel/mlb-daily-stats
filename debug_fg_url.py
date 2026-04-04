"""
debug_fg_url.py  —  Use Playwright to open a real FanGraphs pitcher game-log
page, intercept all /api/ network requests, and save them so we can find the
correct URL + parameters for Stuff+/Loc+ data.

Run:  python debug_fg_url.py
Output:  fg_api_calls.txt  (list of every API URL the page called)
         fg_gamelog_response.json  (the raw JSON from the game-log API)
"""

import json, os, sys, time

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    from playwright.sync_api import sync_playwright

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
API_LOG  = os.path.join(OUT_DIR, "fg_api_calls.txt")
JSON_OUT = os.path.join(OUT_DIR, "fg_gamelog_response.json")

# Gerrit Cole FanGraphs ID = 13125  (change to any starter you want to test)
PLAYER_ID   = 13125
PLAYER_NAME = "gerrit-cole"
SEASON      = 2026

page_url = (
    f"https://www.fangraphs.com/players/{PLAYER_NAME}/{PLAYER_ID}"
    f"/game-log?position=P&type=8&season={SEASON}"
)

print("=" * 60)
print("FanGraphs API URL interceptor")
print(f"Target page: {page_url}")
print("=" * 60)
print("A Chrome window will open — do NOT close it until this script finishes.")
print()

api_calls   = []
gl_response = None     # store raw JSON from the game-log API call

def on_response(response):
    global gl_response
    url = response.url
    if "fangraphs.com/api/" in url:
        api_calls.append(url)
        print(f"  API ← {url[:120]}")
        # Capture game-log responses (any type)
        if "game-log" in url and "dates" not in url:
            try:
                data = response.json()
                rows = data if isinstance(data, list) else data.get("data", [])
                n = len(rows)
                print(f"    ↳ game-log JSON: {n} rows")
                if n > 0:
                    print(f"    ↳ keys: {list(rows[0].keys())}")
                    stuff_keys = [k for k in rows[0].keys()
                                  if any(s in k.lower() for s in ("stuff","loc","pitching","model"))]
                    print(f"    ↳ Stuff+/Loc+ related keys: {stuff_keys}")
                # Keep the most data-rich response
                if gl_response is None or n > len(
                        gl_response if isinstance(gl_response, list)
                        else gl_response.get("data", [])):
                    gl_response = data
            except Exception as e:
                print(f"    ↳ could not parse JSON: {e}")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    )
    page = ctx.new_page()

    # Apply stealth if available
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
        print("Stealth patches applied")
    except Exception:
        pass

    page.on("response", on_response)

    # Step 1: homepage to get cf_clearance cookie
    print("\nStep 1: loading fangraphs.com homepage (Cloudflare challenge)…")
    page.goto("https://www.fangraphs.com/", wait_until="load", timeout=45_000)
    page.wait_for_timeout(9_000)
    cookies = [c["name"] for c in ctx.cookies()]
    print(f"  Cookies after homepage: {cookies}")

    # Step 2: navigate to the game-log page
    print(f"\nStep 2: loading game-log page…")
    page.goto(page_url, wait_until="load", timeout=45_000)
    page.wait_for_timeout(8_000)   # let all AJAX calls complete

    # Step 3: directly call the API for each type number so we see column names
    print(f"\nStep 3: probing game-log API types 0,6,8,23 directly…")
    for t in (0, 6, 8, 23):
        api_url = f"https://www.fangraphs.com/api/players/game-log?playerid={PLAYER_ID}&position=P&type={t}"
        try:
            result = page.evaluate(f"""
                async () => {{
                    const r = await fetch({json.dumps(api_url)}, {{
                        credentials: 'include',
                        headers: {{'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}}
                    }});
                    if (!r.ok) return {{'_err': r.status}};
                    return await r.json();
                }}
            """)
            if isinstance(result, dict) and "_err" in result:
                print(f"  type={t}: HTTP {result['_err']}")
            else:
                rows = result if isinstance(result, list) else (result or {}).get("data", [])
                print(f"  type={t}: {len(rows)} rows")
                if rows:
                    keys = list(rows[0].keys())
                    stuff = [k for k in keys if any(s in k.lower() for s in ("stuff","loc","model","pitching+"))]
                    print(f"    all keys:   {keys}")
                    print(f"    stuff/loc:  {stuff}")
                    # Save this type's data
                    out = os.path.join(OUT_DIR, f"fg_gamelog_type{t}.json")
                    with open(out, "w") as f:
                        json.dump(result, f, indent=2)
                    print(f"    saved → {out}")
        except Exception as e:
            print(f"  type={t}: error — {e}")

    print(f"\n{'='*60}")
    print(f"Total API calls intercepted: {len(api_calls)}")
    print(f"{'='*60}")
    for u in api_calls:
        print(f"  {u}")

    # Save API call list
    with open(API_LOG, "w") as f:
        f.write("\n".join(api_calls))
    print(f"\nSaved API call list → {API_LOG}")

    # Save game-log JSON if captured
    if gl_response is not None:
        with open(JSON_OUT, "w") as f:
            json.dump(gl_response, f, indent=2)
        print(f"Saved game-log JSON → {JSON_OUT}")
        # Show first row keys
        first = gl_response[0] if isinstance(gl_response, list) and gl_response else gl_response
        if isinstance(first, dict):
            print(f"\nGame-log JSON keys: {list(first.keys())}")
            stuff_keys = [k for k in first.keys() if "stuff" in k.lower() or "loc" in k.lower()]
            print(f"Stuff+/Loc+ keys: {stuff_keys}")
    else:
        print("\nNo game-log JSON captured — check fg_api_calls.txt for URLs to test manually")

    browser.close()

print("\nDone! Open fg_api_calls.txt and fg_gamelog_response.json to inspect results.")
input("Press Enter to exit…")
