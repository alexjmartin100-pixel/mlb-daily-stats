import subprocess, sys, os, json, unicodedata, time
from datetime import date, timedelta, datetime
import pybaseball          # type: ignore
import pandas as pd
import numpy as np
import requests
import statsapi            # type: ignore  (MLB-StatsAPI)

import io

from config import *
from utils import *

_PW_INSTANCE = None
_PW_BROWSER  = None
_PW_PAGE     = None

def _get_pw_page():
    """
    Return a live Playwright page pre-loaded on FanGraphs.
    Opens Chromium once; reuses the same page for all subsequent calls.
    fetch() calls made via page.evaluate() run inside the browser and
    carry the Cloudflare cf_clearance cookie automatically.

    Key details:
    - wait_until="load" (not "networkidle") — FanGraphs has permanent
      background connections that prevent networkidle from ever firing.
    - After load we pause 5 s so Cloudflare's JS challenge can execute
      and set the cf_clearance cookie.
    - _PW_PAGE is only assigned AFTER successful navigation so that a
      failed init doesn't leave a stale unloaded page in the global.
    """
    global _PW_INSTANCE, _PW_BROWSER, _PW_PAGE
    if _PW_PAGE is not None:
        return _PW_PAGE
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        print("  Launching Chromium (Playwright) to bypass Cloudflare…")
        _PW_INSTANCE = sync_playwright().start()
        # headless=False: visible Chrome window is far harder for Cloudflare
        # to fingerprint as a bot than headless mode.  The window opens for
        # ~6 seconds while the Cloudflare challenge runs, then stays open
        # (hidden in taskbar) for the duration of the script.
        _PW_BROWSER  = _PW_INSTANCE.chromium.launch(headless=False)
        ctx = _PW_BROWSER.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = ctx.new_page()

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

        # ── Stealth: patch JS properties that reveal headless automation ──
        try:
            from playwright_stealth import stealth_sync  # type: ignore
            stealth_sync(page)
            print("  Stealth patches applied (navigator.webdriver hidden)")
        except Exception as se:
            print(f"  playwright-stealth unavailable ({se}), proceeding without")

        # "load" fires when DOM + resources finish; FanGraphs never reaches
        # "networkidle" due to persistent WebSocket connections.
        page.goto("https://www.fangraphs.com/", wait_until="load", timeout=45_000)
        # Give Cloudflare's JS challenge time to complete and set cf_clearance
        # 9 s is safer than 6 s — slower machines need more time
        page.wait_for_timeout(9_000)
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

        _PW_PAGE = page   # only set here, after successful navigation
        return _PW_PAGE
    except Exception as e:
        print(f"  Playwright init failed: {e}")
        _close_pw()       # clean up any partial state
        return None

def _close_pw():
    """Shut down Playwright browser and free resources."""
    global _PW_INSTANCE, _PW_BROWSER, _PW_PAGE
    try:
        if _PW_BROWSER:
            _PW_BROWSER.close()
        if _PW_INSTANCE:
            _PW_INSTANCE.stop()
    except Exception:
        pass
    _PW_INSTANCE = _PW_BROWSER = _PW_PAGE = None

def _load_fg_cookie() -> str | None:
    """
    Read the full Cookie header string from fg_cookie.txt (same folder as
    this script).  The user copies the entire Cookie header value from Chrome
    DevTools → Network tab → any fangraphs.com request → Headers → Cookie.
    Returns the raw string, or None if the file doesn't exist / is empty.
    """
    cookie_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "fg_cookie.txt")
    if os.path.exists(cookie_path):
        val = open(cookie_path).read().strip()
        if val:
            return val
    return None


def _fg_session_from_cookie(cookie_str: str) -> requests.Session:
    """
    Build a requests.Session from a full Cookie header string.
    Supports two formats:
      • Full header: 'key1=val1; key2=val2; ...'  (from Network tab copy)
      • Single value: just the cf_clearance token   (legacy)
    """
    sess = requests.Session()
    if ";" in cookie_str or "=" in cookie_str:
        # Parse semicolon-separated key=value pairs
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                key, _, val = part.partition("=")
                sess.cookies.set(key.strip(), val.strip(), domain=".fangraphs.com")
    else:
        # Legacy: bare cf_clearance value
        sess.cookies.set("cf_clearance", cookie_str, domain=".fangraphs.com")
    sess.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":           "application/json, text/javascript, */*; q=0.01",
        "Accept-Language":  "en-US,en;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Referer":          "https://www.fangraphs.com/leaders/major-league",
    })
    return sess


_FG_DIRECT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":           "application/json, text/javascript, */*; q=0.01",
    "Accept-Language":  "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer":          "https://www.fangraphs.com/leaders/major-league",
    "Origin":           "https://www.fangraphs.com",
    "Connection":       "keep-alive",
    "Sec-Fetch-Dest":   "empty",
    "Sec-Fetch-Mode":   "cors",
    "Sec-Fetch-Site":   "same-origin",
}

def _pw_fetch_json(url: str, params: dict | None = None) -> object:
    """
    Fetch JSON from a FanGraphs API endpoint.

    Priority order:
      1. Direct requests   — plain HTTP with browser-like headers (fastest, no deps)
      2. cloudscraper      — auto-solves Cloudflare JS challenges (no IP restriction)
      3. fg_cookie.txt     — user-supplied cf_clearance cookie (most reliable fallback)
      4. Playwright        — headless+stealth Chrome (last resort)

    Returns parsed JSON or None on failure.
    """
    from urllib.parse import urlencode
    full_url = (url + "?" + urlencode(params)) if params else url

    # ── Option A: direct requests with browser-like headers ──────────────
    try:
        r = requests.get(full_url, headers=_FG_DIRECT_HEADERS, timeout=20)
        if r.status_code == 200:
            data = r.json()
            # Cloudflare challenge pages return 200 but with HTML, not JSON
            if isinstance(data, (dict, list)):
                return data
        elif r.status_code not in (403, 429, 503):
            print(f"    Direct request returned {r.status_code} for {url}")
    except Exception as e:
        print(f"    Direct request failed: {e}")

    # ── Option A2: cloudscraper — solves Cloudflare JS challenge automatically ──
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        r = scraper.get(full_url, timeout=30)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, (dict, list)):
                return data
        elif r.status_code not in (403, 429, 503):
            print(f"    cloudscraper returned {r.status_code}")
    except Exception as e:
        print(f"    cloudscraper failed: {e}")

    # ── Option B: manual cookie file ─────────────────────────────────────
    cf_cookie = _load_fg_cookie()
    if cf_cookie:
        try:
            sess = _fg_session_from_cookie(cf_cookie)
            r = sess.get(full_url, timeout=20)
            if r.status_code == 200:
                return r.json()
            print(f"    fg_cookie.txt returned {r.status_code} — "
                  f"cookie expired or IP mismatch; falling through to Playwright…")
        except Exception as e:
            print(f"    Cookie request error: {e}; falling through to Playwright…")
    # NOTE: intentionally fall through to Playwright even when cookie file exists —
    # cookie is IP-bound and may be stale; Playwright is the authoritative fallback.

    # ── Option C: Playwright stealth browser ──────────────────────────────
    # _get_pw_page() launches a visible Chromium, applies stealth patches, and
    # pre-navigates to fangraphs.com so Cloudflare issues a cf_clearance cookie.
    # We then try two sub-methods:
    #   C1 — page.evaluate(fetch())  : runs inside the browser, carries cookies
    #   C2 — direct page.goto()      : navigate the browser tab to the API URL,
    #                                  then read the raw JSON body text
    page = _get_pw_page()
    if page is None:
        return None

    # C1: fetch() from within the browser context (same-origin, cookies included)
    try:
        result = page.evaluate(f"""
            async () => {{
                try {{
                    const r = await fetch({json.dumps(full_url)}, {{
                        credentials: 'include',
                        headers: {{
                            'Accept': 'application/json, text/javascript, */*; q=0.01',
                            'X-Requested-With': 'XMLHttpRequest',
                            'Referer': 'https://www.fangraphs.com/leaders/major-league'
                        }}
                    }});
                    if (!r.ok) return {{'_err': r.status}};
                    return await r.json();
                }} catch(e) {{
                    return {{'_err': String(e)}};
                }}
            }}
        """)
        if isinstance(result, (dict, list)) and not (
                isinstance(result, dict) and "_err" in result):
            return result
        if isinstance(result, dict) and "_err" in result:
            err_val = result['_err']
            print(f"    Playwright fetch() error: {err_val}")
            # 404 = URL/playerid is wrong — C2 navigation will also 404 (and show
            # a visible 404 page to the user), so skip it entirely.
            if err_val == 404 or err_val == "404":
                return None
    except Exception as e:
        print(f"    Playwright evaluate error: {e}")

    # C2: navigate the browser tab directly to the API URL and parse the body
    try:
        print(f"    Playwright: navigating browser to API URL…")
        page.goto(full_url, wait_until="load", timeout=25_000)
        page.wait_for_timeout(3_000)   # let any secondary CF check finish
        body = page.inner_text("body")
        if body:
            data = json.loads(body)
            if isinstance(data, (dict, list)):
                print(f"    Playwright direct-navigate succeeded")
                return data
    except Exception as e:
        print(f"    Playwright direct-navigate error: {e}")

    return None

# Keep FG_HEADERS alias for any remaining direct references
FG_HEADERS: dict = {}

def fg_api(params: dict, label: str) -> list:
    try:
        data = _pw_fetch_json(
            "https://www.fangraphs.com/api/leaders/major-league/data", params)
        if data is None:
            raise ValueError("no data")
        rows = data.get("data", data if isinstance(data, list) else [])
        print(f"    FanGraphs {label}: {len(rows)} row(s)")
        return rows
    except Exception as e:
        print(f"    FanGraphs {label} warning: {e}")
        return []

def detect_per_pitch_cols(sample: dict, stat_key: str) -> dict:
    """
    Detect per-pitch stat columns. stat_key='stuff' or 'loc'.
    Returns {statcast_pitch_code: column_name}
    """
    mapping = {}
    for col in sample:
        cl = col.lower().replace(" ", "").replace("+", "plus").replace("(", "").replace(")", "").replace("-", "")
        if stat_key == "stuff" and "stuff" not in cl:
            continue
        if stat_key == "loc" and "loc" not in cl and "location" not in cl:
            continue
        for sc_code, aliases in SC_TO_FG_ALIASES.items():
            if sc_code in mapping:
                continue
            for alias in aliases:
                if alias in cl and len(alias) >= 2:
                    mapping[sc_code] = col
                    break
    return mapping

# ── FanGraphs: per-game Stuff+ & Location+ (game log API) ────────────────
_GL_URL    = "https://www.fangraphs.com/api/players/game-log"
_FG_SEARCH = "https://www.fangraphs.com/api/players"

def _unwrap_gl(raw) -> list:
    """Extract the list of game rows from a FanGraphs game-log API response.
    Confirmed format: {'mlb': [...rows...], 'mlb_default_season': [...], ...}
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        # "mlb" is the confirmed key from network interception (March 2026)
        for key in ("mlb", "mlbgamelog", "data", "gamelog", "games", "playerGameLog"):
            val = raw.get(key)
            if isinstance(val, list) and val:
                return val
    return []

def _gl_date_matches(row_date: str, target: str) -> bool:
    """Return True if a FanGraphs date string matches target YYYY-MM-DD."""
    rd = str(row_date).strip()
    if target in rd:
        return True
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%b %d, %Y"):
        try:
            if datetime.strptime(rd, fmt).strftime("%Y-%m-%d") == target:
                return True
        except Exception:
            pass
    return False

def _detect_stuff_loc_cols(sample: dict):
    """
    Detect the column names for overall Stuff+ and Location+ in a game-log row.
    Returns (stuff_col, loc_col) — either may be None if not found.
    """
    _PT_LOWER = [p.lower() for p in ALL_PT_CODES]

    def _is_overall(col: str, kw: str) -> bool:
        cl = col.lower()
        if kw not in cl:
            return False
        bare = cl.replace("+","").replace(" ","").replace("-","") \
                  .replace("(","").replace(")","")
        return not any(
            bare.startswith(pt) or bare.endswith(pt)
            for pt in _PT_LOWER if len(pt) == 2
        )

    # Exact-name candidates first
    # sp_stuff / sp_location confirmed from network interception (FanGraphs, March 2026)
    stuff_col = next((c for c in sample
                      if c in ("sp_stuff", "Stuff+", "Stuff+ (pfx)", "xStuff+",
                                "SP_stuff", "stuff_plus", "stuffplus")), None)
    if not stuff_col:
        stuff_col = next((c for c in sample if _is_overall(c, "stuff")), None)

    loc_col = next((c for c in sample
                    if c in ("sp_location", "Location+", "Location+ (pfx)", "xLocation+",
                              "SP_loc", "location_plus")), None)
    if not loc_col:
        loc_col = next((c for c in sample
                        if _is_overall(c, "location")), None)

    return stuff_col, loc_col

def _fg_search_player_id(name: str) -> int | None:
    """
    Query FanGraphs player search API to get a player's numeric playerid.
    Used when pybaseball's ID is missing or stale (fg_id = -1 or None).
    Tries plain HTTP first (fast), then Playwright (CF-protected fallback).
    """
    def _parse_players(raw):
        players = raw if isinstance(raw, list) else raw.get("data", raw.get("players", []))
        for p in (players if isinstance(players, list) else []):
            for k in ("playerid", "id", "PlayerID", "fgid"):
                pid = p.get(k)
                if pid:
                    parsed = _parse_fg_id(pid)
                    if parsed is not None:
                        return parsed
        return None

    # NOTE: keep params minimal — "type": "data" and "season": "" are not valid
    # search params for /api/players and caused the endpoint to return no results.
    search_params = {"pos": "all", "stats": "pit", "q": name}

    # Option 1: plain HTTP (fast, works if not CF-blocked)
    for url in (_FG_SEARCH, "https://www.fangraphs.com/api/players/search"):
        try:
            r = requests.get(url, params=search_params, headers=_FG_DIRECT_HEADERS, timeout=10)
            if r.status_code == 200:
                result = _parse_players(r.json())
                if result:
                    return result
        except Exception:
            pass

    # Option 2: Playwright (uses cached browser with CF cookies — fast after first open)
    raw = _pw_fetch_json(_FG_SEARCH, search_params)
    if raw is not None:
        result = _parse_players(raw)
        if result:
            return result

    return None

def _call_game_log(player_id, year: int) -> list:
    """
    Fetch a pitcher's season game log via Playwright.
    Uses the exact URL format confirmed by network interception:
      /api/players/game-log?playerid=X&position=P&type=N
    No 'season' or 'z' params — those cause 404s.

    type=8  → Pitch Modeling tab (Stuff+, Location+)
    type=23 → alternate pitch modeling type (try if 8 is empty)
    type=0  → Standard stats (no Stuff+, but proves connectivity)
    """
    # type=0 returns ALL columns including sp_stuff/sp_location (confirmed March 2026).
    # No 'season' param — it causes 404s. FanGraphs returns the current season
    # automatically for active players, which is what we want.
    # No 'z' param either — also causes 404s.
    from urllib.parse import urlencode
    _params = {"playerid": player_id, "position": "P", "type": 0}
    print(f"    _call_game_log → {_GL_URL}?{urlencode(_params)}")
    data = _pw_fetch_json(
        _GL_URL,
        _params,
    )
    if data is not None:
        rows = _unwrap_gl(data)
        if rows:
            return rows
    return []


def fetch_fg_game_stuff(date_str: str, year: int,
                        pitchers: list, p_info: dict,
                        name_to_fgid: dict) -> dict:
    """
    Fetch per-game Stuff+ and Location+ for each starter using FanGraphs'
    per-pitcher game-log API.
    Tries (in order): pybaseball fg_id → season-velo name map → FG search by name → MLBAM id.
    Returns {fg_id | norm_name | mlbam_id: {stuff_plus, location_plus, pitch_stuff:{}}}
    """
    print(f"  Fetching FanGraphs Stuff+/Loc+ (game-log API) for {date_str}…")

    result      : dict = {}
    cols_logged : bool = False
    stuff_col   : str | None = None
    loc_col     : str | None = None
    found_ct    : int  = 0

    # Process pitchers in priority order so players with known FG IDs are fetched
    # before Cloudflare starts rate-limiting the session (~85-90 requests in).
    # Priority: FG_ID_OVERRIDES first → known pybaseball fg_id → everyone else.
    def _pitcher_priority(p):
        mid = p["id"]
        if mid in FG_ID_OVERRIDES:
            return 0
        fg = p_info.get(mid, {}).get("fg_id")
        parsed = _parse_fg_id(fg)
        if parsed is not None:
            return 1
        nm = norm_name(p_info.get(mid, {}).get("name", ""))
        if nm in name_to_fgid:
            return 2
        return 3

    pitchers = sorted(pitchers, key=_pitcher_priority)

    for p in pitchers:
        mlbam = p["id"]
        info  = p_info.get(mlbam, {})
        fg_id = info.get("fg_id")
        name  = info.get("name", f"#{mlbam}")
        nm    = norm_name(name)

        # Build ordered list of candidate player IDs to try.
        # fg_id may be int (standard FG numeric ID) or str (e.g. "sa3017880" sa-prefix).
        candidates: list = []
        # Apply FG_ID_OVERRIDES directly here as the highest-priority source
        override_id = FG_ID_OVERRIDES.get(mlbam)
        if override_id and override_id != fg_id:
            candidates.append(("override", override_id))
            fg_id = override_id   # also update fg_id so result keying works below
        if fg_id and fg_id != -1 and fg_id != 0:
            # Accept both int IDs (> 0) and string sa-prefix IDs
            if (isinstance(fg_id, str) and fg_id.startswith("sa")) or \
               (isinstance(fg_id, int) and fg_id > 0):
                if not any(c[1] == fg_id for c in candidates):
                    candidates.append(("pybaseball", fg_id))
        nm_fgid = name_to_fgid.get(nm)
        if nm_fgid and nm_fgid != fg_id:
            candidates.append(("velo_map", nm_fgid))
        # NOTE: MLBAM IDs are NOT FanGraphs IDs — never add as candidate.
        # Fallback to FG name search happens below instead.

        rows      = []
        found_id  = None
        for src, cand_id in candidates:
            rows = _call_game_log(cand_id, year)
            time.sleep(2.0)   # avoid Cloudflare rate-limiting after rapid-fire requests
            if rows:
                found_id = cand_id
                print(f"    {name}: got {len(rows)} game-log rows via {src} id={cand_id}")
                break

        # If all pre-known IDs failed, try FanGraphs name search as last resort
        if not rows:
            searched_id = _fg_search_player_id(name)
            if searched_id and searched_id not in [c[1] for c in candidates]:
                rows = _call_game_log(searched_id, year)
                time.sleep(2.0)
                if rows:
                    found_id = searched_id
                    print(f"    {name}: got {len(rows)} game-log rows via FG search id={searched_id}")

        if not rows:
            print(f"    {name}: no game-log data found (tried {[c[1] for c in candidates]})")
            continue

        # Log column names once so we can debug column detection
        if not cols_logged:
            sample = rows[0]
            print(f"      Column names: {list(sample.keys())}")
            stuff_col, loc_col = _detect_stuff_loc_cols(sample)
            print(f"      Stuff+ col={stuff_col!r}  Loc+ col={loc_col!r}")
            cols_logged = True

        # Find row matching our target date (fall back to last row)
        target_row = next(
            (row for row in rows if _gl_date_matches(str(row.get("Date","")), date_str)),
            rows[-1],
        )
        row_date = target_row.get("Date", "")
        sp  = safe_float(target_row.get(stuff_col)  if stuff_col else None, 0)
        lp  = safe_float(target_row.get(loc_col)    if loc_col   else None, 0)
        if sp is not None:
            print(f"    {name} ({row_date}): Stuff+={sp}  Loc+={lp}")

        entry = {"stuff_plus": sp, "location_plus": lp, "pitch_stuff": {}}
        result[nm]    = entry
        result[mlbam] = entry          # key by MLBAM so attach_fg_data can find it
        if found_id:
            result[found_id] = entry
        if fg_id and fg_id != found_id:
            result[fg_id] = entry
        if entry["stuff_plus"] is not None:
            found_ct += 1

    print(f"    Stuff+ attached: {found_ct}/{len(pitchers)} starter(s)")
    return result

# ── FanGraphs: season-average velocity per pitch type ─────────────────────
def fetch_fg_season_velo(year: int) -> tuple:
    """
    Season-avg velocity per pitch type.
    Returns (velo_dict, name_to_fgid, mlbam_to_fgid) where:
      velo_dict    = {norm_name | fg_id: {pitch_code: avg_velo}}
      name_to_fgid = {norm_name: fg_id}   (fallback for pitchers missing from Chadwick)
      mlbam_to_fgid= {mlbam_id: fg_id}    (built from xMLBAMID column; direct MLBAM→FG map)
    """
    print(f"  Fetching FanGraphs season velocity ({year})…")
    base = {
        "pos": "all", "stats": "pit", "lg": "all", "qual": "0",
        "season": year, "season1": year,
        "month": "0", "team": "0",
        "pageitems": "2000", "pagenum": "1", "ind": "0",
    }
    rows = []
    for type_val in ("velocity", "11", "7"):
        rows = fg_api({**base, "type": type_val}, f"season velo (type={type_val})")
        if rows and any(k in rows[0] for k in FG_VELO_COL_MAP):
            break
        rows = []
    velo_dict     = {}
    name_to_fgid  = {}
    mlbam_to_fgid = {}

    def _extract_mlbam(row, parsed_fg):
        """Extract xMLBAMID from a leaderboard row and add to mlbam_to_fgid."""
        raw = row.get("xMLBAMID")
        if raw:
            try:
                mid = int(float(raw))
                if mid > 0:
                    mlbam_to_fgid[mid] = parsed_fg
            except (ValueError, TypeError):
                pass

    if not rows:
        # Velocity leaderboard returned no usable rows — skip velo but still
        # fall through to the type=8 fetch below to build the MLBAM→FG ID map.
        print("    Could not retrieve season velocity — will still build MLBAM→FG map from Stuff+ leaderboard.")
    else:
        sample_v = rows[0]
        matched_vcols = [c for c in sample_v if c in FG_VELO_COL_MAP]
        print(f"    Season velo: {len(rows)} rows, matched velo columns: {matched_vcols or 'none'}")

    for row in rows:
        name  = row.get("PlayerName", row.get("Name", "")).strip()
        fg_id = row.get("playerid")
        if name and fg_id:
            parsed = _parse_fg_id(fg_id)
            if parsed is not None:
                name_to_fgid[norm_name(name)] = parsed
                _extract_mlbam(row, parsed)
        velo = {}
        seen_codes: set = set()
        for col, pt_code in FG_VELO_COL_MAP.items():
            if pt_code in seen_codes:
                continue
            if col in row:
                v = safe_float(row[col], 1)
                if v and v > 0:
                    velo[pt_code] = v
                    seen_codes.add(pt_code)
        if velo:
            if name:
                velo_dict[norm_name(name)] = velo
            if fg_id:
                try:
                    velo_dict[int(float(fg_id))] = velo
                except Exception:
                    pass

    # If the velocity leaderboard had no xMLBAMID column, also fetch the Stuff+
    # leaderboard (type=8) which always includes xMLBAMID.  This gives us a direct
    # MLBAM→FG map for every pitcher FanGraphs has ever tracked — including recent
    # debutants whose Chadwick key_fangraphs field is still blank.
    if not mlbam_to_fgid:
        type8_rows = fg_api({**base, "type": "8"}, "Stuff+ leaderboard (type=8, for MLBAM→FG map)")
        for row in (type8_rows or []):
            fg_id = row.get("playerid")
            if not fg_id:
                continue
            parsed = _parse_fg_id(fg_id)
            if parsed is None:
                continue
            _extract_mlbam(row, parsed)
            # Also supplement name_to_fgid with any names not already present
            name = row.get("Name", row.get("PlayerName", "")).strip()
            if name:
                name_to_fgid.setdefault(norm_name(name), parsed)
        if mlbam_to_fgid:
            print(f"    Built {len(mlbam_to_fgid)} MLBAM→FG ID mappings from Stuff+ leaderboard")

    return velo_dict, name_to_fgid, mlbam_to_fgid

# ── MLB Stats API: stolen bases ───────────────────────────────────────────
