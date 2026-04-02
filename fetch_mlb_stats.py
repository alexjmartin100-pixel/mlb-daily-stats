#!/usr/bin/env python3
"""
MLB Daily Stats Dashboard Generator  v3
=========================================
Data sources:
  • Baseball Savant (Statcast)  — pitch-by-pitch game data
  • FanGraphs                   — single-game Stuff+/Location+/per-pitch Stuff+
                                  season-avg velocity per pitch type
  • MLB Stats API (statsapi)    — SB / CS box-score data

Run once each morning; mlb_daily_stats.html is updated in the same folder.
"""

import subprocess, sys, os, json, unicodedata, time
from datetime import date, timedelta, datetime

# Fix Unicode output on Windows (cp1252 can't handle checkmarks etc.)
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Auto-install (skipped on GitHub Actions / CI where requirements.txt is used) ──
if not os.environ.get("SKIP_AUTO_INSTALL"):
    print("Checking dependencies…")
    _pip_flags = ["--break-system-packages"] if sys.platform != "win32" else []
    for _pkg in ("pybaseball", "pandas", "numpy", "requests", "cloudscraper", "MLB-StatsAPI", "playwright", "playwright-stealth"):
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", _pkg, "-q"] + _pip_flags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    print("  Installing Playwright Chromium (cached after first run)…")
    subprocess.check_call(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

import pybaseball          # type: ignore
import pandas as pd
import numpy as np
import requests
import statsapi            # type: ignore  (MLB-StatsAPI)

pybaseball.cache.enable()

# ── Constants ──────────────────────────────────────────────────────────────
PITCH_NAMES = {
    "FF": "4-Seam",  "SI": "Sinker",   "FC": "Cutter",
    "SL": "Slider",  "ST": "Sweeper",  "SV": "Slurve",
    "CH": "Change",  "FS": "Splitter", "FO": "Fork",
    "CU": "Curve",   "KC": "K-Curve",  "CS": "Slow Curve",
    "KN": "Knuckle", "EP": "Eephus",   "FA": "Fastball",
    "SC": "Screw",
}
PITCH_COLORS = {
    "FF": "#e74c3c", "SI": "#c0392b", "FA": "#e74c3c",
    "FC": "#e67e22",
    "SL": "#f1c40f", "ST": "#f39c12", "SV": "#d35400",
    "CH": "#2ecc71", "FS": "#27ae60", "FO": "#16a085",
    "CU": "#3498db", "KC": "#2980b9", "CS": "#1abc9c",
    "KN": "#9b59b6", "EP": "#8e44ad", "SC": "#95a5a6",
}
OUT_WEIGHTS = {
    "strikeout": 1,                  "strikeout_double_play": 2,
    "field_out": 1,                  "grounded_into_double_play": 2,
    "double_play": 2,                "force_out": 1,
    "fielders_choice_out": 1,        "fielders_choice": 1,
    "sac_fly": 1,                    "sac_bunt": 1,
    "other_out": 1,                  "triple_play": 3,
    "sac_fly_double_play": 2,
}
WHIFF_DESC     = frozenset({"swinging_strike", "swinging_strike_blocked", "foul_tip"})
HIT_EVENTS     = frozenset({"single", "double", "triple", "home_run"})
FASTBALL_TYPES = frozenset({"FF", "SI", "FC", "FA"})
ALL_PT_CODES   = ["FF","FA","SI","FC","SL","ST","SV","CH","FS",
                  "CU","KC","KN","SC","FO","EP","CS"]

# ── Team Alex roster ───────────────────────────────────────────────────────
# Normalized (lowercase, no diacritics, no periods) for matching
TEAM_ALEX_NAMES = {
    "jose ramirez", "vladimir guerrero jr", "gunnar henderson",
    "wyatt langford", "zach neto", "eury perez",
    "freddy peralta", "matt chapman", "kyle bradish",
    "tyler soderstrom", "bryson stott", "adley rutschman",
    "taylor ward", "ryan pepiot", "ryan helsley",
    "shane baz", "ian happ", "tanner bibee",
    "ryan weathers", "mackenzie gore", "griffin jax",
    "max meyer", "reid detmers", "matt brash",
}

# ── FanGraphs ID overrides ─────────────────────────────────────────────────
# Maps MLBAM player ID → FanGraphs player ID for pitchers whose fg_id is
# missing or wrong in pybaseball's Chadwick register (stale for recent debuts).
# Values can be int (standard FG numeric ID) or str (e.g. "sa..." minor-league IDs).
# Add entries here whenever a pitcher regularly pitches but has no Stuff+ shown.
FG_ID_OVERRIDES: dict = {
    # Only needed for players whose Chadwick entry has a WRONG (not missing) FG ID.
    # Players with a blank Chadwick key_fangraphs are handled automatically via the
    # xMLBAMID→playerid mapping extracted from the FanGraphs leaderboard at startup.
    691725: 30091,   # Andrew Painter (PHI) — Chadwick has stale minor-league ID sa3017880
}

def ta_norm(name: str) -> str:
    """Normalize a player name for Team Alex matching."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_s = "".join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_s.lower().replace(".", "").strip()


# Statcast pitch code → FanGraphs name aliases (for column detection)
SC_TO_FG_ALIASES = {
    "FF": ["ff", "fa", "4seam", "fourseam"],
    "FA": ["fa", "ff", "4seam"],
    "SI": ["si", "ft", "sinker", "twoseam"],
    "FC": ["fc", "cutter"],
    "SL": ["sl", "slider"],
    "ST": ["st", "sweeper"],
    "SV": ["sv", "slurve"],
    "CH": ["ch", "changeup", "change"],
    "FS": ["fs", "splitter", "split"],
    "CU": ["cu", "cb", "curve", "curveball"],
    "KC": ["kc", "knucklecurve"],
    "KN": ["kn", "knuckleball"],
}

# FanGraphs velocity column → Statcast pitch code
FG_VELO_COL_MAP = {
    "vFA (pfx)": "FF", "vFA": "FF",
    "vFF (pfx)": "FF", "vFF": "FF",
    "vSI (pfx)": "SI", "vSI": "SI",
    "vFT (pfx)": "SI", "vFT": "SI",
    "vFC (pfx)": "FC", "vFC": "FC",
    "vSL (pfx)": "SL", "vSL": "SL",
    "vST (pfx)": "ST", "vST": "ST",
    "vCH (pfx)": "CH", "vCH": "CH",
    "vFS (pfx)": "FS", "vFS": "FS",
    "vCB (pfx)": "CU", "vCB": "CU",
    "vCU (pfx)": "CU", "vCU": "CU",
    "vKC (pfx)": "KC", "vKC": "KC",
    "vKN (pfx)": "KN", "vKN": "KN",
}

# ── Utility helpers ────────────────────────────────────────────────────────
def outs_to_ip(outs: int) -> str:
    return f"{outs // 3}.{outs % 3}"

def ip_to_float(ip_str: str) -> float:
    p = ip_str.split(".")
    return int(p[0]) + int(p[1]) / 3.0 if len(p) == 2 else float(p[0])

def last_events(df: pd.DataFrame) -> pd.Series:
    return (df.sort_values("pitch_number")
              .groupby("at_bat_number")["events"].last().dropna())

def calc_outs(df: pd.DataFrame) -> int:
    return sum(OUT_WEIGHTS.get(e, 0) for e in last_events(df))

def team_for_batter(row: pd.Series):
    top = row.get("inning_topbot") == "Top"
    return (str(row.get("away_team", "")).upper() if top else str(row.get("home_team", "")).upper(),
            str(row.get("home_team", "")).upper() if top else str(row.get("away_team", "")).upper())

def team_for_pitcher(row: pd.Series):
    top = row.get("inning_topbot") == "Top"
    return (str(row.get("home_team", "")).upper() if top else str(row.get("away_team", "")).upper(),
            str(row.get("away_team", "")).upper() if top else str(row.get("home_team", "")).upper())

def title_name(s: str) -> str:
    return " ".join(w.capitalize() for w in s.split())

def norm_name(s: str) -> str:
    return s.strip().lower().replace(".", "").replace("-", " ")

def safe_float(v, prec=1):
    if v is None:
        return None
    try:
        f = float(v)
        return None if (pd.isna(f) or np.isinf(f) or f == 0) else round(f, prec)
    except Exception:
        return None

def safe_barrels(batted: pd.DataFrame) -> int:
    if "barrel" in batted.columns:
        ct = int(pd.to_numeric(batted["barrel"], errors="coerce").fillna(0).sum())
        if ct > 0:
            return ct
    ev = pd.to_numeric(batted.get("launch_speed", pd.Series(dtype=float)), errors="coerce")
    la = pd.to_numeric(batted.get("launch_angle", pd.Series(dtype=float)), errors="coerce")
    count = 0
    for e, a in zip(ev, la):
        if pd.isna(e) or pd.isna(a) or e < 98:
            continue
        lo, hi = max(26 - (e - 98), 8), min(30 + (e - 98), 50)
        if lo <= a <= hi:
            count += 1
    return count

def calc_runs_allowed(df: pd.DataFrame) -> int:
    r0 = df.iloc[0]
    if r0.get("inning_topbot") == "Top":
        pre_col, post_col = "away_score", "post_away_score"
    else:
        pre_col, post_col = "home_score", "post_home_score"
    if pre_col not in df.columns:
        return 0
    pre  = pd.to_numeric(df[pre_col],  errors="coerce").fillna(0)
    post = pd.to_numeric(df[post_col], errors="coerce").fillna(0)
    return int((post - pre).clip(lower=0).sum())

# ── Playwright: real Chromium browser to bypass Cloudflare on FanGraphs ──────
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
def fetch_mlb_sb(date_str: str) -> dict:
    """Returns {(mlbam_id, game_pk): [sb, cs]} from official MLB box scores."""
    try:
        print(f"  Fetching SB data via MLB Stats API for {date_str}…")
        games  = statsapi.schedule(date=date_str)
        sb_map: dict = {}
        for g in games:
            gpk = g.get("game_id")
            if not gpk:
                continue
            status = g.get("status", "")
            if "Final" not in status and "Completed" not in status:
                continue
            try:
                bs = statsapi.boxscore_data(int(gpk))
            except Exception:
                continue
            for side in ("home", "away"):
                for pk, pd_ in bs.get(side, {}).get("players", {}).items():
                    if not pk.startswith("ID"):
                        continue
                    try:
                        mid = int(pd_["person"]["id"])
                    except Exception:
                        continue
                    bat = pd_.get("stats", {}).get("batting", {})
                    sb  = int(bat.get("stolenBases", 0))
                    cs  = int(bat.get("caughtStealing", 0))
                    if sb or cs:
                        sb_map[(mid, int(gpk))] = [sb, cs]
        total = sum(v[0] + v[1] for v in sb_map.values())
        print(f"    {total} steal attempt(s) for {len(sb_map)} player(s)")
        return sb_map
    except Exception as e:
        print(f"  MLB Stats API SB warning: {e}")
        return {}

# ── Statcast fetching ──────────────────────────────────────────────────────
def fetch_statcast(date_str: str) -> pd.DataFrame:
    print(f"  Downloading Statcast data for {date_str}…")
    try:
        df = pybaseball.statcast(start_dt=date_str, end_dt=date_str, verbose=False)
        if df is None or df.empty:
            print("  No data returned.")
            return pd.DataFrame()
        print(f"  {len(df):,} pitches across {df['game_pk'].nunique()} game(s)")
        sc_stuff_cols = [c for c in df.columns if "stuff" in c.lower() or "loc_avg" in c.lower()]
        print(f"  Stuff+ columns in Statcast data: {sc_stuff_cols if sc_stuff_cols else 'none found'}")
        return df
    except Exception as e:
        print(f"  ERROR: {e}")
        return pd.DataFrame()

def identify_starters(df: pd.DataFrame) -> set:
    starters: set = set()
    for gpk in df["game_pk"].unique():
        g = df[df["game_pk"] == gpk].sort_values("at_bat_number")
        for side in ("Top", "Bot"):
            sdf = g[g["inning_topbot"] == side]
            if len(sdf):
                starters.add(int(sdf.iloc[0]["pitcher"]))
    return starters

def fetch_pitcher_box_data(date_str: str) -> dict:
    """Returns {mlbam_id: {w, sv, hld, bs}} from official MLB box scores."""
    try:
        print(f"  Fetching pitcher box score data via MLB Stats API for {date_str}…")
        games = statsapi.schedule(date=date_str)
        box_map: dict = {}
        for g in games:
            gpk = g.get("game_id")
            if not gpk:
                continue
            status = g.get("status", "")
            if "Final" not in status and "Completed" not in status:
                continue
            try:
                boxscore = statsapi.boxscore_data(int(gpk))
            except Exception:
                continue

            # Per-pitcher stats: W, HLD, BS (saves unreliable here — handled below)
            for side in ("home", "away"):
                for pk, pd_ in boxscore.get(side, {}).get("players", {}).items():
                    if not pk.startswith("ID"):
                        continue
                    try:
                        mid = int(pd_["person"]["id"])
                    except Exception:
                        continue
                    pit = pd_.get("stats", {}).get("pitching", {})
                    w      = int(pit.get("wins", 0))
                    hld    = int(pit.get("holds", 0))
                    bs_val = int(pit.get("blownSaves", 0))
                    if w or hld or bs_val:
                        prev = box_map.get(mid, {"w": 0, "sv": 0, "hld": 0, "bs": 0})
                        box_map[mid] = {"w": prev["w"]+w, "sv": prev["sv"], "hld": prev["hld"]+hld, "bs": prev["bs"]+bs_val}

            # Saves from game decisions endpoint — more reliable than per-pitcher boxscore
            try:
                game_data = statsapi.get("game", {"gamePk": int(gpk)})
                save_info = game_data.get("liveData", {}).get("decisions", {}).get("save", {})
                save_id   = save_info.get("id")
                if save_id:
                    prev = box_map.get(int(save_id), {"w": 0, "sv": 0, "hld": 0, "bs": 0})
                    box_map[int(save_id)] = {**prev, "sv": prev["sv"] + 1}
                    print(f"      Save: {save_info.get('fullName','?')} (id={save_id})")
            except Exception as e:
                print(f"      Decisions fetch warning for {gpk}: {e}")

        total = len(box_map)
        print(f"    Pitcher box data: {total} pitcher(s) with W/SV/HLD/BS")
        return box_map
    except Exception as e:
        print(f"  MLB Stats API pitcher box warning: {e}")
        return {}

def _parse_fg_id(raw):
    """
    Convert a raw FanGraphs ID value to a usable form.
    - Numeric IDs (int/float/numeric-string) → int
    - sa-prefix IDs like "sa3017880"          → str (kept as-is)
    - Missing / NaN                            → None
    """
    if raw is None:
        return None
    try:
        if pd.isna(raw):
            return None
    except Exception:
        pass
    s = str(raw).strip()
    if not s or s in ("nan", "None", ""):
        return None
    if s.startswith("sa"):   # FanGraphs minor-league / prospect ID (e.g. "sa3017880")
        return s
    try:
        v = int(float(s))
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None

def get_player_info(ids: list) -> dict:
    if not ids:
        return {}
    result = {}
    try:
        lkp = pybaseball.playerid_reverse_lookup(list(set(ids)), key_type="mlbam")
        for _, r in lkp.iterrows():
            mlbam  = int(r["key_mlbam"])
            raw_fg = r.get("key_fangraphs")
            fg_id  = _parse_fg_id(raw_fg)
            result[mlbam] = {
                "name":  title_name(f"{r['name_first']} {r['name_last']}"),
                "fg_id": fg_id,
            }
    except Exception as e:
        print(f"  pybaseball lookup warning: {e}")

    # Fallback: MLB Stats API for any IDs not found above
    missing = [pid for pid in set(ids) if pid not in result]
    if missing:
        print(f"  Looking up {len(missing)} missing player(s) via MLB Stats API…")
        for mid in missing:
            try:
                data = statsapi.get("person", {"personId": mid})
                people = data.get("people", [])
                if people:
                    full = people[0].get("fullName", "")
                    if full:
                        result[mid] = {"name": title_name(full), "fg_id": None}
            except Exception:
                pass

    # Apply hardcoded overrides (players missing from or wrong in Chadwick register)
    for mlbam, override_fg_id in FG_ID_OVERRIDES.items():
        if mlbam in result:
            if result[mlbam].get("fg_id") in (None, -1):
                result[mlbam]["fg_id"] = override_fg_id
                print(f"  FG_ID_OVERRIDES: {result[mlbam]['name']} (mlbam={mlbam}) → fg_id={override_fg_id}")
        # If player not found via pybaseball yet, they'll still get the override applied
        # in fetch_fg_game_stuff via a direct FG_ID_OVERRIDES check

    return result

# ── Stat aggregation ───────────────────────────────────────────────────────
def build_hitter_stats(df: pd.DataFrame, sb_map: dict) -> list:
    rows = []
    for (bid, gpk), gdf in df.groupby(["batter", "game_pk"]):
        r0           = gdf.iloc[0]
        bat_t, opp_t = team_for_batter(r0)
        evts         = last_events(gdf)
        batted       = gdf[gdf["type"] == "X"]
        bev          = batted[batted["launch_speed"].notna()]
        sb_data      = sb_map.get((int(bid), int(gpk)), [0, 0])
        # Detect grand slams: HR where on_1b, on_2b, on_3b all occupied
        last_rows  = gdf.sort_values("pitch_number").groupby("at_bat_number").last()
        hr_rows    = last_rows[last_rows["events"] == "home_run"]
        grand_slam = any(
            pd.notna(row.get("on_1b")) and
            pd.notna(row.get("on_2b")) and
            pd.notna(row.get("on_3b"))
            for _, row in hr_rows.iterrows()
        )
        rows.append({
            "id":         int(bid),
            "game_pk":    int(gpk),
            "team":       bat_t,
            "opp":        opp_t,
            "hr":         int((evts == "home_run").sum()),
            "grand_slam": grand_slam,
            "k":          int((evts == "strikeout").sum()),
            "bb":         int(evts.isin(["walk", "intent_walk"]).sum()),
            "sb":         sb_data[0],
            "cs":         sb_data[1],
            "sba":        sb_data[0] + sb_data[1],
            "hard_hits":  int((bev["launch_speed"] >= 95).sum()) if len(bev) else 0,
            "barrels":    safe_barrels(batted),
            "max_ev":     round(float(bev["launch_speed"].max()), 1) if len(bev) else None,
            "bip":        int(len(bev)),
        })
    return rows

def build_pitcher_stats(df: pd.DataFrame, starters: set, box_data: dict = None) -> list:
    rows = []
    sp_df = df[df["pitcher"].isin(starters)]
    for (pid, gpk), gdf in sp_df.groupby(["pitcher", "game_pk"]):
        r0           = gdf.iloc[0]
        pit_t, opp_t = team_for_pitcher(r0)
        evts         = last_events(gdf)
        outs         = calc_outs(gdf)
        batted       = gdf[gdf["type"] == "X"]
        bev          = batted[batted["launch_speed"].notna()]
        bf = gdf["at_bat_number"].nunique()
        k  = int((evts == "strikeout").sum())
        bb = int(evts.isin(["walk", "intent_walk"]).sum())

        # Get W, SV, HLD, BS from box score data (keyed by player_id only)
        w, sv, hld, bs = 0, 0, 0, 0
        if box_data:
            pdata = box_data.get(int(pid), {})
            w  = pdata.get("w", 0)
            sv = pdata.get("sv", 0)
            hld = pdata.get("hld", 0)
            bs  = pdata.get("bs", 0)

        total_p = len(gdf)
        ptypes  = []
        for pt, ptdf in gdf.groupby("pitch_type"):
            pt_str = str(pt).strip()
            if not pt_str or pt_str in ("nan", "PO"):
                continue
            ct = len(ptdf)
            vs = ptdf["release_speed"].dropna()
            wh = int(ptdf["description"].isin(WHIFF_DESC).sum())
            ptypes.append({
                "code":        pt_str,
                "name":        PITCH_NAMES.get(pt_str, pt_str),
                "color":       PITCH_COLORS.get(pt_str, "#95a5a6"),
                "count":       ct,
                "pct":         round(ct / total_p * 100, 1) if total_p else 0,
                "velo":        round(float(vs.mean()), 1) if len(vs) else None,
                "season_velo": None,
                "game_stuff":  None,
                "velo_alert":  False,
                "whiffs":      wh,
            })
        ptypes.sort(key=lambda x: x["count"], reverse=True)

        # ── Stuff+ / Location+ from Statcast columns ──────────────────────
        def _sc_metric(col: str):
            if col not in gdf.columns:
                return None
            vals = pd.to_numeric(gdf[col], errors="coerce").dropna()
            return round(float(vals.mean()), 0) if len(vals) else None

        sc_stuff    = _sc_metric("stuff_plus_stuff_avg")
        sc_location = _sc_metric("stuff_plus_loc_avg")
        # Diagnostic: print once per build so we know what the data contains
        if not rows:   # only on first pitcher row
            sp_present = "stuff_plus_stuff_avg" in gdf.columns
            lp_present = "stuff_plus_loc_avg" in gdf.columns
            sp_nulls   = gdf["stuff_plus_stuff_avg"].isna().all() if sp_present else True
            print(f"  [Statcast Stuff+ cols] "
                  f"stuff_plus_stuff_avg: present={sp_present}, all-null={sp_nulls} | "
                  f"stuff_plus_loc_avg: present={lp_present}")

        ip_str = outs_to_ip(outs)
        rows.append({
            "id":            int(pid),
            "game_pk":       int(gpk),
            "team":          pit_t,
            "opp":           opp_t,
            "ip":            ip_str,
            "ip_float":      round(ip_to_float(ip_str), 3),
            "hits":          int(evts.isin(HIT_EVENTS).sum()),
            "r":             calc_runs_allowed(gdf),
            "bb":            bb,
            "k":             k,
            "whiffs":        int(gdf["description"].isin(WHIFF_DESC).sum()),
            "hard_hits":     int((bev["launch_speed"] >= 95).sum()) if len(bev) else 0,
            "barrels":       safe_barrels(batted),
            "k_bb_pct":      round((k - bb) / bf * 100, 1) if bf else 0.0,
            "w":             w,
            "sv":            sv,
            "hld":           hld,
            "bs":            bs,
            "pitch_types":   ptypes,
            "total_pitches": total_p,
            "stuff_plus":    sc_stuff,
            "location_plus": sc_location,
        })
    return rows

# Mapping from Baseball Savant CSV pitch prefix → Statcast pitch code
_SAVANT_PT_PREFIX = {
    "ff": "FF", "si": "SI", "sl": "SL", "ch": "CH", "cu": "CU",
    "fc": "FC", "fs": "FS", "st": "ST", "sv": "SV", "kc": "KC",
    "sc": "SC", "cs": "CS", "fa": "FA", "kn": "KN", "ep": "EP",
}

def fetch_savant_season_velo(year: int, mlbam_ids: set) -> dict:
    """Returns {mlbam_id: {pitch_code: avg_speed}} from Baseball Savant arsenal CSV."""
    from io import StringIO
    print(f"  Fetching Baseball Savant season velocity ({year})…")
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    url  = "https://baseballsavant.mlb.com/leaderboard/pitch-arsenals"
    try:
        r = requests.get(url,
            params={"year": year, "min": 0, "type": "avg_speed",
                    "hand": "", "pos": "P", "teamId": "", "csv": "true"},
            headers=hdrs, timeout=25)
        r.raise_for_status()
        df = pd.read_csv(StringIO(r.text))
        df["pitcher"] = pd.to_numeric(df["pitcher"], errors="coerce")
        result = {}
        for _, row in df.iterrows():
            try:
                pid = int(row["pitcher"])
            except (ValueError, TypeError):
                continue
            velo_by_code = {}
            for pfx, code in _SAVANT_PT_PREFIX.items():
                col = f"{pfx}_avg_speed"
                if col in row.index and pd.notna(row[col]):
                    velo_by_code[code] = round(float(row[col]), 1)
            if velo_by_code:
                result[pid] = velo_by_code
        hits = sum(1 for pid in mlbam_ids if pid in result)
        print(f"  ✓ Savant season velo: {len(result)} pitchers, {hits}/{len(mlbam_ids)} matched")
        return result
    except Exception as e:
        print(f"  Savant season velo failed: {e}")
        return {}

def attach_fg_data(pitchers: list, p_info: dict,
                   game_stuff: dict, season_velo: dict,
                   savant_velo: dict = None) -> None:
    """Attach FanGraphs-sourced Stuff+ and velocity data to pitcher rows."""
    for p in pitchers:
        mlbam     = p["id"]
        fg_id     = p_info.get(mlbam, {}).get("fg_id")
        name_norm = norm_name(p.get("name", ""))

        # Look up game stuff: try MLBAM id first (new), then fg_id, then name
        fg_game = (
            game_stuff.get(mlbam) or
            (game_stuff.get(fg_id) if fg_id else None) or
            game_stuff.get(name_norm) or
            {}
        )
        fg_svelo = (season_velo.get(fg_id)
                    if fg_id and fg_id in season_velo
                    else season_velo.get(name_norm, {}))
        # Fallback to Baseball Savant season velocity if FanGraphs unavailable
        if not fg_svelo and savant_velo:
            fg_svelo = savant_velo.get(mlbam, {})

        # Only override Statcast-derived Stuff+/Loc+ if FanGraphs has a value
        if fg_game.get("stuff_plus") is not None:
            p["stuff_plus"]    = fg_game.get("stuff_plus")
        if fg_game.get("location_plus") is not None:
            p["location_plus"] = fg_game.get("location_plus")

        pp_stuff = fg_game.get("pitch_stuff", {})
        for pt in p["pitch_types"]:
            code = pt["code"]
            svelo = fg_svelo.get(code)
            gs    = pp_stuff.get(code)
            pt["season_velo"] = svelo
            pt["game_stuff"]  = gs
            if code in FASTBALL_TYPES and pt["velo"] and svelo:
                pt["velo_alert"] = abs(pt["velo"] - svelo) > 1.0
            else:
                pt["velo_alert"] = False

# ── Season Batting Leaderboard ─────────────────────────────────────────────
def fetch_season_batting_leaderboard(year: int) -> list:
    """
    Fetch season batting leaderboard combining FanGraphs + Savant data.
    Returns a list of player dicts sorted by HR desc, each with 'qualified' flag.
    Stats: R, HR, RBI, SB, SBA, OBP, wOBA, xwOBA, Chase%, Whiff%, K%, SO, BB%,
           Hard Hit%, Barrel%, Barrels, Sweet Spot%, Avg EV, Max EV, Bat Speed, Sprint Speed.
    """
    from io import StringIO
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    players = {}  # mlbam_id -> stat dict

    # ── Step 1: FanGraphs batting stats (all batters ≥1 PA) ──────────────────
    print("  [LB] FanGraphs batting stats…")
    qual_pa = 50  # fallback threshold
    try:
        fg = pybaseball.batting_stats(year, qual=1)
        # Build FG playerid → MLBAM via Chadwick register
        fg_to_mlbam = {}
        try:
            chad = pybaseball.chadwick_register()
            for _, cr in chad.iterrows():
                fgk = cr.get("key_fangraphs")
                mk  = cr.get("key_mlbam")
                if pd.notna(fgk) and pd.notna(mk):
                    try:
                        fg_to_mlbam[int(fgk)] = int(mk)
                    except (ValueError, TypeError):
                        pass
        except Exception as e2:
            print(f"  [LB] Chadwick register failed: {e2}")

        max_g = int(fg["G"].max()) if "G" in fg.columns and not fg.empty else 1
        qual_pa = max(5, round(max_g * 3.1))

        def _pct(v):
            try:
                f = float(str(v).replace("%", ""))
                return round(f * 100, 1) if f < 1.5 else round(f, 1)
            except (ValueError, TypeError):
                return None

        def _int(v):
            try:
                return int(float(v))
            except (ValueError, TypeError):
                return 0

        def _flt(v, p=3):
            try:
                return round(float(v), p)
            except (ValueError, TypeError):
                return None

        for _, row in fg.iterrows():
            try:
                fg_id = int(row.get("playerid") or row.get("IDfg") or 0)
            except (ValueError, TypeError):
                continue
            if fg_id == 0:
                continue
            mlbam = fg_to_mlbam.get(fg_id)
            if mlbam is None:
                continue
            pa  = _int(row.get("PA", 0))
            sb  = _int(row.get("SB", 0))
            cs  = _int(row.get("CS", 0))
            players[mlbam] = {
                "id":      mlbam,
                "name":    str(row.get("Name", "")).strip(),
                "team":    str(row.get("Team", "")).strip(),
                "pa":      pa,
                "qualified": pa >= qual_pa,
                "r":       _int(row.get("R",   0)),
                "hr":      _int(row.get("HR",  0)),
                "rbi":     _int(row.get("RBI", 0)),
                "sb":      sb,
                "sba":     sb + cs,
                "obp":     _flt(row.get("OBP"),  3),
                "woba":    _flt(row.get("wOBA"), 3),
                "k_pct":   _pct(row.get("K%")),
                "bb_pct":  _pct(row.get("BB%")),
                "so":      _int(row.get("SO", 0)),
                "xwoba": None, "chase_pct": None, "whiff_pct": None,
                "hard_hit_pct": None, "barrel_pct": None, "barrels": None,
                "sweet_spot_pct": None, "avg_ev": None, "max_ev": None,
                "bat_speed": None, "sprint_speed": None,
            }
        print(f"  [LB] FG: {len(players)} hitters, qual ≥{qual_pa} PA")
    except Exception as e:
        print(f"  [LB] FanGraphs batting stats failed: {e}")

    # ── Step 2: Savant EV / Barrel stats ─────────────────────────────────────
    print("  [LB] Savant EV/Barrel…")
    try:
        ev = pybaseball.statcast_batter_exitvelo_barrels(year, minBBE=1)
        for _, row in ev.iterrows():
            try:
                mid = int(row["player_id"])
            except (ValueError, TypeError):
                continue
            if mid not in players:
                continue
            p = players[mid]
            def _sv(c, df=ev):
                try:
                    return round(float(row[c]), 1) if c in df.columns and pd.notna(row[c]) else None
                except (ValueError, TypeError):
                    return None
            p["avg_ev"]         = _sv("avg_hit_speed")
            p["max_ev"]         = _sv("max_hit_speed")
            p["sweet_spot_pct"] = _sv("anglesweetspotpercent")
            p["hard_hit_pct"]   = _sv("ev95percent")
            p["barrel_pct"]     = _sv("brl_percent")
            try:
                p["barrels"] = int(row["barrels"]) if "barrels" in ev.columns and pd.notna(row["barrels"]) else None
            except (ValueError, TypeError):
                p["barrels"] = None
        print(f"  [LB] ✓ EV/Barrel {len(ev)} rows")
    except Exception as e:
        print(f"  [LB] Savant EV/Barrel failed: {e}")

    # ── Step 3: Savant xwOBA / Chase% / Whiff% ───────────────────────────────
    print("  [LB] Savant xwOBA/Chase/Whiff…")
    try:
        r3 = requests.get(
            "https://baseballsavant.mlb.com/leaderboard/custom",
            params={"year": year, "type": "batter", "filter": "",
                    "sort": "4", "sortDir": "desc", "min": "1",
                    "selections": "xwoba,oz_swing_percent,whiff_percent",
                    "csv": "true"},
            headers=hdrs, timeout=30)
        r3.raise_for_status()
        sv3 = pd.read_csv(StringIO(r3.text))
        mid_col3 = next((c for c in ["player_id", "batter"] if c in sv3.columns), None)
        for _, row in sv3.iterrows():
            if mid_col3 is None:
                break
            try:
                mid = int(row[mid_col3])
            except (ValueError, TypeError):
                continue
            if mid not in players:
                continue
            p = players[mid]
            try:
                if "xwoba" in sv3.columns and pd.notna(row.get("xwoba")):
                    p["xwoba"] = round(float(row["xwoba"]), 3)
            except (ValueError, TypeError):
                pass
            for sv_col, p_key in [("oz_swing_percent", "chase_pct"), ("whiff_percent", "whiff_pct")]:
                try:
                    if sv_col in sv3.columns and pd.notna(row.get(sv_col)):
                        p[p_key] = round(float(row[sv_col]), 1)
                except (ValueError, TypeError):
                    pass
        print(f"  [LB] ✓ xwOBA/Chase/Whiff {len(sv3)} rows")
    except Exception as e:
        print(f"  [LB] Savant xwOBA/Chase/Whiff failed: {e}")

    # ── Step 4: Savant bat speed ──────────────────────────────────────────────
    # Try bat-tracking leaderboard first, then fall back to custom leaderboard
    print("  [LB] Savant bat speed…")
    bat_speed_ok = False
    for bs_url, bs_params in [
        ("https://baseballsavant.mlb.com/leaderboard/bat-tracking",
         {"year": year, "type": "batter", "min": "1", "csv": "true"}),
        ("https://baseballsavant.mlb.com/leaderboard/custom",
         {"year": year, "type": "batter", "filter": "", "sort": "4",
          "sortDir": "desc", "min": "1",
          "selections": "bat_speed,fast_swing_rate", "csv": "true"}),
    ]:
        try:
            r4 = requests.get(bs_url, params=bs_params, headers=hdrs, timeout=30)
            r4.raise_for_status()
            bt = pd.read_csv(StringIO(r4.text))
            mid_col4 = next((c for c in ["player_id","batter_id","mlbam_id","batter","id"] if c in bt.columns), None)
            bs_col   = next((c for c in ["bat_speed","avg_bat_speed","mean_bat_speed"] if c in bt.columns), None)
            if not mid_col4 or not bs_col:
                print(f"  [LB] bat speed: cols not found in {bs_url.split('/')[-1]} — got: {list(bt.columns[:10])}")
                continue
            matched = 0
            for _, row in bt.iterrows():
                try:
                    mid = int(row[mid_col4])
                except (ValueError, TypeError):
                    continue
                if mid not in players:
                    continue
                v = row.get(bs_col)
                try:
                    players[mid]["bat_speed"] = round(float(v), 1) if pd.notna(v) else None
                    matched += 1
                except (ValueError, TypeError):
                    pass
            print(f"  [LB] ✓ bat speed ({bs_url.split('/')[-1]}): {len(bt)} rows, {matched} matched")
            bat_speed_ok = True
            break
        except Exception as e:
            print(f"  [LB] bat speed attempt failed ({bs_url.split('/')[-1]}): {e}")
    if not bat_speed_ok:
        print("  [LB] bat speed: all attempts failed")

    # ── Step 5: Sprint speed ──────────────────────────────────────────────────
    # Use min_opp=1 (pybaseball default is 10, too high early season)
    print("  [LB] Savant sprint speed…")
    sprint_ok = False
    for ss_min in [1, 0]:
        try:
            ss = pybaseball.statcast_sprint_speed(year, min_opp=ss_min)
            if ss.empty:
                continue
            id_col = next((c for c in ["player_id","mlbam_id","batter"] if c in ss.columns), None)
            if not id_col:
                print(f"  [LB] sprint speed: no ID column found — got: {list(ss.columns[:8])}")
                break
            matched = 0
            for _, row in ss.iterrows():
                try:
                    mid = int(row[id_col])
                except (ValueError, TypeError):
                    continue
                if mid not in players:
                    continue
                v = row.get("sprint_speed")
                try:
                    players[mid]["sprint_speed"] = round(float(v), 1) if pd.notna(v) else None
                    matched += 1
                except (ValueError, TypeError):
                    pass
            print(f"  [LB] ✓ sprint speed (min_opp={ss_min}): {len(ss)} rows, {matched} matched")
            sprint_ok = True
            break
        except Exception as e:
            print(f"  [LB] sprint speed attempt (min_opp={ss_min}) failed: {e}")
    if not sprint_ok:
        # Final fallback: Savant sprint speed CSV directly
        try:
            rs = requests.get(
                "https://baseballsavant.mlb.com/leaderboard/sprint_speed",
                params={"year": year, "position": "", "team": "", "min": "0", "csv": "true"},
                headers=hdrs, timeout=30)
            rs.raise_for_status()
            ss2 = pd.read_csv(StringIO(rs.text))
            id_col = next((c for c in ["player_id","mlbam_id"] if c in ss2.columns), None)
            matched = 0
            if id_col and "sprint_speed" in ss2.columns:
                for _, row in ss2.iterrows():
                    try:
                        mid = int(row[id_col])
                    except (ValueError, TypeError):
                        continue
                    if mid not in players:
                        continue
                    v = row.get("sprint_speed")
                    try:
                        players[mid]["sprint_speed"] = round(float(v), 1) if pd.notna(v) else None
                        matched += 1
                    except (ValueError, TypeError):
                        pass
            print(f"  [LB] ✓ sprint speed (CSV fallback): {len(ss2)} rows, {matched} matched")
        except Exception as e:
            print(f"  [LB] sprint speed CSV fallback failed: {e}")

    out = sorted(players.values(), key=lambda x: (x.get("hr") or 0), reverse=True)
    q = sum(1 for p in out if p["qualified"])
    print(f"  [LB] Done: {len(out)} total players, {q} qualified (≥{qual_pa} PA)")
    return out


# ── Season Pitching Leaderboard ────────────────────────────────────────────
def fetch_season_pitching_leaderboard(year: int) -> dict:
    """
    Fetch season pitching leaderboard combining FanGraphs + Savant.
    Returns {'starters': [...], 'relievers': [...]}.
    Starters: IP, W, ERA, WHIP, xERA, SIERA, Stuff+, Loc+, K, K%, BB%,
              Chase%, Whiff%, Barrel%, Hard Hit%, GB%, wOBA, xwOBA, Avg EV, FB Velo
    Relievers: same + SV/SVO, Holds
    """
    from io import StringIO
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    starters_d  = {}   # mlbam_id -> dict
    relievers_d = {}   # mlbam_id -> dict

    def _pct(v):
        try:
            f = float(str(v).replace("%", ""))
            return round(f * 100, 1) if f < 1.5 else round(f, 1)
        except (ValueError, TypeError):
            return None

    def _int(v):
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return 0

    def _flt(v, p=2):
        try:
            return round(float(v), p)
        except (ValueError, TypeError):
            return None

    # ── Step 1: FanGraphs pitching stats ──────────────────────────────────────
    print("  [PLB] FanGraphs pitching stats…")
    qual_sp_ip = 10.0
    qual_rp_ip = 3.0
    try:
        fg = pybaseball.pitching_stats(year, qual=1)
        # Build FG playerid → MLBAM via Chadwick register
        fg_to_mlbam = {}
        try:
            chad = pybaseball.chadwick_register()
            for _, cr in chad.iterrows():
                fgk = cr.get("key_fangraphs")
                mk  = cr.get("key_mlbam")
                if pd.notna(fgk) and pd.notna(mk):
                    try:
                        fg_to_mlbam[int(fgk)] = int(mk)
                    except (ValueError, TypeError):
                        pass
        except Exception as e2:
            print(f"  [PLB] Chadwick register failed: {e2}")

        max_g = int(fg["G"].max()) if "G" in fg.columns and not fg.empty else 1
        qual_sp_ip = max(3.0, round(max_g * 1.0, 1))
        qual_rp_ip = max(1.0, round(max_g * 0.5, 1))

        for _, row in fg.iterrows():
            try:
                fg_id = int(row.get("playerid") or row.get("IDfg") or 0)
            except (ValueError, TypeError):
                continue
            if fg_id == 0:
                continue
            mlbam = fg_to_mlbam.get(fg_id)
            if mlbam is None:
                continue

            try:
                ip_val = float(row.get("IP", 0) or 0)
            except (ValueError, TypeError):
                ip_val = 0.0

            try:
                gs = int(float(row.get("GS", 0) or 0))
                g  = int(float(row.get("G",  1) or 1))
            except (ValueError, TypeError):
                gs, g = 0, 1

            is_sp = gs > 0 and (gs / max(g, 1)) >= 0.5

            # Try to get Stuff+ / Loc+ from FG columns
            stuff_plus = None
            loc_plus   = None
            for sc in ["Stuff+", "stuff_plus", "StuffPlus", "Stf+"]:
                if sc in fg.columns and pd.notna(row.get(sc)):
                    try:
                        stuff_plus = int(round(float(row[sc])))
                    except (ValueError, TypeError):
                        pass
                    break
            for lc in ["Location+", "location_plus", "Loc+", "LocationPlus"]:
                if lc in fg.columns and pd.notna(row.get(lc)):
                    try:
                        loc_plus = int(round(float(row[lc])))
                    except (ValueError, TypeError):
                        pass
                    break

            try:
                sv = int(float(row.get("SV", 0) or 0))
            except (ValueError, TypeError):
                sv = 0
            try:
                bs = int(float(row.get("BS", 0) or 0))
            except (ValueError, TypeError):
                bs = 0
            try:
                hld = int(float(row.get("HLD", 0) or 0))
            except (ValueError, TypeError):
                hld = 0

            # FanGraphs also carries O-Swing%, SwStr%, Swing%, xERA,
            # Barrel%, HardHit%, EV, FBv — use as baseline; Savant overwrites later
            fg_chase   = _pct(row.get("O-Swing%"))
            fg_xera    = _flt(row.get("xERA"), 3)
            fg_barrel  = _pct(row.get("Barrel%"))
            fg_hh      = _pct(row.get("HardHit%"))
            fg_ev      = _flt(row.get("EV"), 1)
            fg_fbv     = _flt(row.get("FBv"), 1)
            # Whiff% (per swing) = SwStr% / Swing% — different from SwStr% alone
            fg_whiff   = None
            try:
                swstr = float(str(row.get("SwStr%", "")).replace("%",""))
                swing = float(str(row.get("Swing%", "")).replace("%",""))
                if swing > 0:
                    # Convert both to fractions if they look like percents (>1.5)
                    if swstr > 1.5: swstr /= 100
                    if swing > 1.5: swing /= 100
                    fg_whiff = round(swstr / swing * 100, 1)
            except (ValueError, TypeError, ZeroDivisionError):
                pass

            rec = {
                "id":          mlbam,
                "name":        str(row.get("Name", "")).strip(),
                "team":        str(row.get("Team", "")).strip(),
                "ip_f":        round(ip_val, 1),
                "w":           _int(row.get("W", 0)),
                "sv":          sv,
                "sv_opp":      sv + bs,
                "hld":         hld,
                "era":         _flt(row.get("ERA"), 2),
                "whip":        _flt(row.get("WHIP"), 2),
                "siera":       _flt(row.get("SIERA") or row.get("Sierra"), 2),
                "stuff_plus":  stuff_plus,
                "loc_plus":    loc_plus,
                "k":           _int(row.get("SO") or row.get("K") or 0),
                "k_pct":       _pct(row.get("K%")),
                "bb_pct":      _pct(row.get("BB%")),
                "gb_pct":      _pct(row.get("GB%")),
                "is_sp":       is_sp,
                "qualified":   ip_val >= (qual_sp_ip if is_sp else qual_rp_ip),
                # FanGraphs baseline (Savant will overwrite where available)
                "xera":        fg_xera,
                "chase_pct":   fg_chase,
                "whiff_pct":   fg_whiff,
                "barrel_pct":  fg_barrel,
                "hard_hit_pct":fg_hh,
                "avg_ev":      fg_ev,
                "fb_velo":     fg_fbv,
                # Savant-only (no FG equivalent)
                "xwoba": None, "woba": None,
            }
            if is_sp:
                starters_d[mlbam] = rec
            else:
                relievers_d[mlbam] = rec

        qs = sum(1 for p in starters_d.values()  if p["qualified"])
        qr = sum(1 for p in relievers_d.values() if p["qualified"])
        print(f"  [PLB] FG: {len(starters_d)} SP ({qs} qual ≥{qual_sp_ip} IP), "
              f"{len(relievers_d)} RP ({qr} qual ≥{qual_rp_ip} IP)")
    except Exception as e:
        print(f"  [PLB] FanGraphs pitching stats failed: {e}")

    all_pitchers_d = {**starters_d, **relievers_d}

    # ── Step 2: Savant pitcher stats (xERA, xwOBA, wOBA, Chase%, Whiff%, Barrel%, Hard Hit%, Avg EV) ──
    print("  [PLB] Savant pitcher stats…")
    try:
        r2 = requests.get(
            "https://baseballsavant.mlb.com/leaderboard/custom",
            params={"year": year, "type": "pitcher", "filter": "",
                    "sort": "4", "sortDir": "desc", "min": "1",
                    "selections": "xera,xwoba,woba,oz_swing_percent,whiff_percent,"
                                  "brl_percent,ev95percent,avg_hit_speed",
                    "csv": "true"},
            headers=hdrs, timeout=30)
        r2.raise_for_status()
        sv2 = pd.read_csv(StringIO(r2.text))
        mid_col = next((c for c in ["player_id", "pitcher", "mlbam_id", "id"] if c in sv2.columns), None)
        print(f"  [PLB] Savant pitcher CSV: {len(sv2)} rows, id_col={mid_col}, cols={list(sv2.columns[:12])}")
        if mid_col:
            col_map = {
                "xera":              ("xera",         3),
                "xwoba":             ("xwoba",        3),
                "woba":              ("woba",         3),
                "oz_swing_percent":  ("chase_pct",    1),
                "whiff_percent":     ("whiff_pct",    1),
                "brl_percent":       ("barrel_pct",   1),
                "ev95percent":       ("hard_hit_pct", 1),
                "avg_hit_speed":     ("avg_ev",       1),
            }
            matched = 0
            for _, row in sv2.iterrows():
                try:
                    mid = int(row[mid_col])
                except (ValueError, TypeError):
                    continue
                if mid not in all_pitchers_d:
                    continue
                p = all_pitchers_d[mid]
                for sv_col, (p_key, prec) in col_map.items():
                    if sv_col in sv2.columns and pd.notna(row.get(sv_col)):
                        try:
                            p[p_key] = round(float(row[sv_col]), prec)
                        except (ValueError, TypeError):
                            pass
                matched += 1
            print(f"  [PLB] ✓ Savant pitcher stats {len(sv2)} rows, {matched} matched")
        else:
            print(f"  [PLB] Savant pitcher stats: no ID col — got {list(sv2.columns[:8])}")
    except Exception as e:
        print(f"  [PLB] Savant pitcher stats failed: {e}")

    # ── Step 2b: Savant pitcher exit velo barrels (Barrel%, Hard Hit%, Avg EV) ─
    # The custom CSV (Step 2) often lacks these cols for pitchers; use dedicated endpoint
    print("  [PLB] Savant pitcher exit velo / barrels…")
    try:
        pev = pybaseball.statcast_pitcher_exitvelo_barrels(year, minBBE=1)
        mid_col_ev = next((c for c in ["pitcher","player_id","mlbam_id","id"] if c in pev.columns), None)
        if mid_col_ev:
            ev_col_map = {
                "avg_hit_speed": ("avg_ev",       1),
                "ev95percent":   ("hard_hit_pct", 1),
                "brl_percent":   ("barrel_pct",   1),
            }
            matched = 0
            for _, row in pev.iterrows():
                try:
                    mid = int(row[mid_col_ev])
                except (ValueError, TypeError):
                    continue
                if mid not in all_pitchers_d:
                    continue
                p = all_pitchers_d[mid]
                for ev_col, (p_key, prec) in ev_col_map.items():
                    if ev_col in pev.columns and pd.notna(row.get(ev_col)):
                        try:
                            p[p_key] = round(float(row[ev_col]), prec)
                        except (ValueError, TypeError):
                            pass
                matched += 1
            print(f"  [PLB] ✓ Pitcher exit velo barrels: {len(pev)} rows, {matched} matched")
        else:
            print(f"  [PLB] Pitcher exit velo: no ID col — got {list(pev.columns[:8])}")
    except Exception as e:
        print(f"  [PLB] Pitcher exit velo barrels failed: {e}")

    # ── Step 3: Savant pitch arsenals (avg FB velo) ───────────────────────────
    print("  [PLB] Savant FB velo…")
    fb_velo_ok = False
    # Attempt 1: pitch-arsenals endpoint (individual pitch type, includes avg speed)
    for pa_type in ["n_ff", "n_si", "n_fc"]:
        try:
            r3 = requests.get(
                "https://baseballsavant.mlb.com/leaderboard/pitch-arsenals",
                params={"year": year, "min": "1", "type": pa_type,
                        "hand": "", "csv": "true"},
                headers=hdrs, timeout=30)
            r3.raise_for_status()
            sv3 = pd.read_csv(StringIO(r3.text))
            mid_col = next((c for c in ["player_id", "pitcher", "id"] if c in sv3.columns), None)
            if not mid_col:
                print(f"  [PLB] pitch-arsenals({pa_type}): no ID col — got {list(sv3.columns[:10])}")
                continue
            # Velocity column candidates: may vary by Savant version
            velo_cols = [c for c in sv3.columns if any(kw in c.lower() for kw in
                         ["avg_speed","velocity","mph","speed"])]
            if not velo_cols:
                print(f"  [PLB] pitch-arsenals({pa_type}): no velo cols — got {list(sv3.columns[:15])}")
                continue
            matched = 0
            for _, row in sv3.iterrows():
                try:
                    mid = int(row[mid_col])
                except (ValueError, TypeError):
                    continue
                if mid not in all_pitchers_d:
                    continue
                if all_pitchers_d[mid].get("fb_velo") is not None:
                    continue  # already have FF velo from earlier pitch type
                for vc in velo_cols:
                    if pd.notna(row.get(vc)):
                        try:
                            all_pitchers_d[mid]["fb_velo"] = round(float(row[vc]), 1)
                            matched += 1
                            break
                        except (ValueError, TypeError):
                            pass
            print(f"  [PLB] pitch-arsenals({pa_type}): {len(sv3)} rows, {matched} matched, velo_cols={velo_cols}")
            if matched > 0:
                fb_velo_ok = True
        except Exception as e:
            print(f"  [PLB] pitch-arsenals({pa_type}) failed: {e}")
    # Attempt 2: Savant custom leaderboard with fastball speed selections
    if not fb_velo_ok:
        try:
            r3b = requests.get(
                "https://baseballsavant.mlb.com/leaderboard/custom",
                params={"year": year, "type": "pitcher", "filter": "",
                        "sort": "4", "sortDir": "desc", "min": "1",
                        "selections": "n_ff_formatted,n_si_formatted,ff_avg_speed,si_avg_speed,fc_avg_speed",
                        "csv": "true"},
                headers=hdrs, timeout=30)
            r3b.raise_for_status()
            sv3b = pd.read_csv(StringIO(r3b.text))
            mid_col = next((c for c in ["player_id","pitcher","id"] if c in sv3b.columns), None)
            speed_cols = [c for c in sv3b.columns if "avg_speed" in c or "mph" in c]
            print(f"  [PLB] custom-fb-velo: cols={list(sv3b.columns[:15])}, speed_cols={speed_cols}")
            if mid_col and speed_cols:
                matched = 0
                for _, row in sv3b.iterrows():
                    try:
                        mid = int(row[mid_col])
                    except (ValueError, TypeError):
                        continue
                    if mid not in all_pitchers_d:
                        continue
                    velo = None
                    for sc in speed_cols:
                        if pd.notna(row.get(sc)):
                            try:
                                v = float(row[sc])
                                if velo is None or v > velo:
                                    velo = v
                            except (ValueError, TypeError):
                                pass
                    if velo is not None:
                        all_pitchers_d[mid]["fb_velo"] = round(velo, 1)
                        matched += 1
                print(f"  [PLB] custom-fb-velo: {len(sv3b)} rows, {matched} matched")
                if matched > 0:
                    fb_velo_ok = True
        except Exception as e:
            print(f"  [PLB] custom-fb-velo failed: {e}")
    if not fb_velo_ok:
        print("  [PLB] FB velo: all attempts failed")

    sp_out = sorted(starters_d.values(),  key=lambda x: (x.get("ip_f") or 0), reverse=True)
    rp_out = sorted(relievers_d.values(), key=lambda x: (-(x.get("era") or 99), x.get("ip_f") or 0), reverse=False)
    print(f"  [PLB] Done: {len(sp_out)} SP, {len(rp_out)} RP")
    return {"starters": sp_out, "relievers": rp_out}


# ── HTML Template ──────────────────────────────────────────────────────────
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MLB Daily Stats · __DATE_DISPLAY__</title>

<!-- PWA: installable as app icon on iOS & Android -->
<link rel="manifest" href="manifest.json">
<meta name="theme-color" content="#e31837">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="MLB Stats">
<link rel="apple-touch-icon" href="icon-192.png">
<script>
// Register service worker (enables PWA install prompt on Android/Chrome)
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function() {
    navigator.serviceWorker.register('sw.js').catch(function(){});
  });
}
</script>

<script>
// Auto-refresh: reload the page at 10:15 AM each day (5 min after script runs).
// Works for both local HTML files and hosted pages.
(function(){
  function msUntil(h, m) {
    var now = new Date();
    var target = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m, 0, 0);
    if (target <= now) target.setDate(target.getDate() + 1);
    return target - now;
  }
  function scheduleRefresh() {
    var ms = msUntil(10, 15);
    var hrs = Math.floor(ms / 3600000);
    var min = Math.floor((ms % 3600000) / 60000);
    var el = document.getElementById('refresh-status');
    if (el) el.textContent = 'Auto-refresh in ' + hrs + 'h ' + min + 'm';
    // Update countdown every minute
    setTimeout(function(){ scheduleRefresh(); }, 60000);
    // Reload at target time
    setTimeout(function(){ location.reload(true); }, ms);
  }
  document.addEventListener('DOMContentLoaded', scheduleRefresh);
})();
</script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0f1923;--card:#182130;--card2:#1e2c3d;--border:#243447;
  --text:#dce8f0;--muted:#6b8599;--accent:#e31837;--gold:#f0c040;
  --green:#2ecc71;--orange:#e8832a;--blue:#3d9be9;--red:#e74c3c;
  --radius:8px;
}
html{font-size:14px;background:var(--bg);color:var(--text);
     font-family:system-ui,-apple-system,'Segoe UI',sans-serif;line-height:1.4}
.site-header{
  background:linear-gradient(135deg,#080e14 0%,#140609 100%);
  border-bottom:3px solid var(--accent);
  padding:15px 26px;display:flex;align-items:center;gap:15px;
}
.hdr-logo{font-size:1.9rem;line-height:1}
.hdr-title{font-size:1.35rem;font-weight:800;color:#fff}
.hdr-badge{background:var(--accent);color:#fff;font-size:.6rem;font-weight:800;
  padding:2px 7px;border-radius:99px;letter-spacing:.7px;margin-left:7px;vertical-align:middle;}
.hdr-meta{font-size:.74rem;color:var(--muted);margin-top:3px}
.hdr-meta strong{color:#aabcc8}
.tab-bar{display:flex;align-items:flex-end;background:var(--card);
  border-bottom:2px solid var(--border);padding:0 26px;}
.tab-btn{background:none;border:none;color:var(--muted);
  padding:12px 24px 10px;font-size:.88rem;font-weight:600;cursor:pointer;
  border-bottom:3px solid transparent;margin-bottom:-2px;
  transition:color .15s,border-color .15s;display:flex;align-items:center;gap:6px;}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{color:#fff;border-bottom-color:var(--accent)}
.tab-count{background:rgba(255,255,255,.1);border-radius:99px;
  padding:1px 7px;font-size:.63rem;font-weight:700;letter-spacing:.4px;}
.tab-btn.active .tab-count{background:var(--accent)}
.tab-panel{display:none;padding:20px 18px 40px}
.tab-panel.active{display:block}
main{max-width:1900px;margin:0 auto}
.controls{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.controls input{background:var(--card2);border:1px solid var(--border);color:var(--text);
  border-radius:var(--radius);padding:6px 11px;font-size:.8rem;width:210px;
  outline:none;transition:border-color .2s;}
.controls input:focus{border-color:var(--blue)}
.row-count{font-size:.73rem;color:var(--muted)}
.sort-hint{font-size:.7rem;color:var(--muted);font-style:italic}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:10px;
  padding:7px 11px;background:var(--card);border-radius:var(--radius);
  border:1px solid var(--border);}
.leg-item{display:flex;align-items:center;gap:4px;font-size:.69rem;color:var(--muted)}
.leg-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.note{font-size:.7rem;color:var(--muted);padding:5px 11px;
  border-left:3px solid var(--blue);background:rgba(52,152,219,.07);
  border-radius:0 var(--radius) var(--radius) 0;margin-bottom:11px;}
.table-wrap{overflow-x:auto;border-radius:var(--radius);border:1px solid var(--border)}
table{width:100%;border-collapse:collapse;font-size:.79rem}
thead th{background:var(--card2);color:var(--muted);text-transform:uppercase;
  font-size:.63rem;letter-spacing:.9px;font-weight:700;padding:9px 9px;
  text-align:left;white-space:nowrap;border-bottom:1px solid var(--border);
  user-select:none;position:sticky;top:0;z-index:1;}
thead th.sortable{cursor:pointer;transition:color .15s}
thead th.sortable:hover{color:#fff}
thead th.sort-asc::after{content:" ▲";color:var(--accent);font-size:.58rem}
thead th.sort-desc::after{content:" ▼";color:var(--accent);font-size:.58rem}
thead th.r{text-align:right}
thead th.sc{color:#5d9bc8 !important}
tbody tr{border-bottom:1px solid var(--border);transition:background .1s}
tbody tr:last-child{border-bottom:none}
tbody tr:hover{background:rgba(0,0,0,.04)}
tbody tr:nth-child(even){background:rgba(0,0,0,.025)}
tbody td{padding:8px 9px;vertical-align:middle;color:#e8f2ff}
tbody td.r{text-align:right;font-variant-numeric:tabular-nums;color:#e8f2ff;font-weight:500}
td.nm{font-weight:600;white-space:nowrap;color:var(--text);font-size:.83rem;
  position:sticky;left:0;z-index:2;background:var(--bg);}
tbody tr:nth-child(even) td.nm{background:color-mix(in srgb,var(--card) 40%,var(--bg));}
tbody tr:hover td.nm{background:color-mix(in srgb,var(--card2) 60%,var(--bg));}
thead th:first-child{position:sticky;left:0;z-index:3;background:var(--card2);}
.tm{display:inline-block;border-radius:4px;padding:1px 6px;font-size:.65rem;font-weight:800;
  letter-spacing:.5px;white-space:nowrap;border:1px solid transparent;}
.c-barrel{color:var(--gold);font-weight:700}
.c-great{color:var(--green);font-weight:600}
.c-good{color:#27ae60}
.c-warn{color:var(--orange)}
.c-neg{color:var(--red)}
.c-dim{color:#8aa0ae}
.c-blue{color:#1a6699}
/* Arsenal */
.arsenal{display:flex;flex-direction:column;gap:4px;min-width:230px}
.pt-row{display:grid;grid-template-columns:52px 32px 1fr auto;
  gap:5px;align-items:center;font-size:.72rem;line-height:1.3}
.pt-badge{display:inline-block;font-size:.59rem;font-weight:700;letter-spacing:.3px;
  padding:1px 5px;border-radius:3px;text-align:center;background:rgba(255,255,255,.06);}
.pt-pct{color:var(--muted);text-align:right;font-size:.68rem}
.pt-velo{white-space:nowrap;display:flex;align-items:center;gap:2px}
.pt-stuff{color:var(--muted);font-size:.68rem;text-align:right;white-space:nowrap}
.va{color:var(--red) !important;font-weight:700}
.vn{color:var(--text)}
.c-gold{color:var(--gold);font-weight:700}
.vd{color:var(--muted)}
.sv{color:#2e6e9e;font-size:.67rem}
.gs{color:#1a5a80}
.empty{text-align:center;padding:48px;color:var(--muted)}
.empty .ico{font-size:2.2rem;margin-bottom:8px}
footer{text-align:center;padding:18px;color:var(--muted);font-size:.69rem;
  border-top:1px solid var(--border);margin-top:40px;}
.ta-section-hdr{font-size:.82rem;font-weight:700;color:var(--muted);
  text-transform:uppercase;letter-spacing:.9px;padding:10px 2px 6px;
  border-bottom:1px solid var(--border);margin-bottom:10px;
  display:flex;align-items:center;gap:7px}
.tab-btn.ta-btn{color:#a07800}
.tab-btn.ta-btn.active{color:var(--gold);border-bottom-color:var(--gold)}
.tab-btn.ta-btn.active .tab-count{background:var(--gold);color:#0f1923}
.tab-btn.lb-btn{color:#1a6699}
.tab-btn.lb-btn.active{color:#155080;border-bottom-color:#1a6699}
.tab-btn.lb-btn.active .tab-count{background:#1a6699;color:#fff}
/* Toggle group (pitcher type / TA view) */
.toggle-group{display:flex;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;width:fit-content;margin-bottom:14px;}
.tgl-btn{background:transparent;border:none;border-right:1px solid var(--border);color:var(--muted);padding:6px 18px;font-size:.82rem;font-weight:600;cursor:pointer;transition:background .15s,color .15s;}
.tgl-btn:last-child{border-right:none}
.tgl-btn.active{background:var(--accent);color:#fff;}
/* Leaderboard */
#lb-panel .note{margin-bottom:9px}
#lb-panel .controls{margin-bottom:11px}
#lb-panel .qual-toggle{display:flex;align-items:center;gap:7px;font-size:.77rem;color:var(--muted);cursor:pointer;user-select:none;}
#lb-panel .qual-toggle input{cursor:pointer;accent-color:var(--accent)}
.lb-th-inv{} /* lower-is-better marker — no visual distinction */
@media(max-width:640px){
  .site-header{padding:11px 13px}.hdr-title{font-size:1rem}
  .tab-panel{padding:13px 8px}.tab-btn{padding:10px 12px;font-size:.78rem}
}
</style>
</head>
<body>

<header class="site-header">
  <div class="hdr-logo">⚾</div>
  <div>
    <div class="hdr-title">MLB Daily Stats <span class="hdr-badge">STATCAST</span></div>
    <div class="hdr-meta">
      <strong>__DATE_DISPLAY__</strong>
      &nbsp;·&nbsp; __N_GAMES__ game(s)
      &nbsp;·&nbsp; Updated __TS__
      &nbsp;·&nbsp; <span id="refresh-status" style="color:var(--muted);font-size:0.85em">⟳ Auto-refresh</span>
    </div>
  </div>
</header>

<div class="tab-bar">
  <button class="tab-btn active" onclick="showTab('hitters',this)">
    🏏 Hitters <span class="tab-count" id="h-tc">—</span>
  </button>
  <button class="tab-btn" onclick="showTab('pitchers',this)">
    ⚾ Pitchers <span class="tab-count" id="p-tc">—</span>
  </button>
  <button class="tab-btn ta-btn" onclick="showTab('teamalex',this)">
    👑 Team Alex <span class="tab-count" id="ta-tc">—</span>
  </button>
  <button class="tab-btn lb-btn" onclick="showTab('leaderboard',this)">
    📊 Season Leaders <span class="tab-count" id="lb-tc">—</span>
  </button>
</div>

<main>

<!-- ══ HITTERS ══ -->
<div id="hitters-panel" class="tab-panel active">
  <div class="legend">
    <div class="leg-item"><span class="leg-dot" style="background:var(--gold)"></span>Leader in category</div>
    <div class="leg-item"><span class="leg-dot" style="background:#e74c3c"></span>Max EV: high (red) → low (blue)</div>
    <div class="leg-item"><span class="leg-dot" style="background:#2ecc71"></span>HR = grand slam</div>
  </div>
  <div class="controls">
    <input id="h-search" type="text" placeholder="Search player or team…" oninput="filterH()">
    <span class="row-count" id="h-cnt"></span>
    <span class="sort-hint">Click headers to sort</span>
  </div>
  <div class="table-wrap">
    <table id="h-tbl">
      <thead><tr>
        <th class="sortable"   data-k="name"      onclick="srtH(this,'name')">Player</th>
        <th class="sortable"   data-k="team"      onclick="srtH(this,'team')">Team</th>
        <th class="sortable"   data-k="opp"       onclick="srtH(this,'opp')">Opp</th>
        <th class="sortable r" data-k="hr"        onclick="srtH(this,'hr')">HR</th>
        <th class="sortable r" data-k="bb"        onclick="srtH(this,'bb')">BB</th>
        <th class="sortable r" data-k="k"         onclick="srtH(this,'k')">K</th>
        <th class="sortable r" data-k="sb"        onclick="srtH(this,'sb')">SB</th>
        <th class="sortable r" data-k="sba"       onclick="srtH(this,'sba')">SBA</th>
        <th class="sortable r" data-k="hard_hits" onclick="srtH(this,'hard_hits')">Hard Hits</th>
        <th class="sortable r" data-k="barrels"   onclick="srtH(this,'barrels')">Barrels</th>
        <th class="sortable r" data-k="max_ev"    onclick="srtH(this,'max_ev')">Max EV</th>
      </tr></thead>
      <tbody id="h-body"></tbody>
    </table>
  </div>
</div>

<!-- ══ PITCHERS (Starters + Relievers) ══ -->
<div id="pitchers-panel" class="tab-panel">
  <div class="note">
    ⓘ &nbsp;<strong>Stuff+</strong> and <strong>Loc+</strong> are per-game values from FanGraphs (season avg when unavailable).
    Arsenal: game velocity <span class="vd">(season avg)</span> —
    fastball shown in <span style="color:var(--red);font-weight:700">red</span> if &gt;1 mph below season avg.
    <span class="gs">S+</span> = game Stuff+ for that pitch type.
    <strong>SV</strong> = Saves, <strong>HLD</strong> = Holds, <strong>BS</strong> = Blown Saves.
  </div>
  <div class="legend">
    <div class="leg-item"><span class="leg-dot" style="background:var(--gold)"></span>Leader in category</div>
  </div>
  <div class="toggle-group">
    <button class="tgl-btn active" id="pitch-sp-btn" onclick="showPitchType('sp',this)">⚾ Starters <span id="p-sp-tc" style="opacity:.6;font-size:.75em"></span></button>
    <button class="tgl-btn" id="pitch-rp-btn" onclick="showPitchType('rp',this)">🔥 Relievers <span id="p-rp-tc" style="opacity:.6;font-size:.75em"></span></button>
  </div>
  <div class="controls">
    <input id="p-search" type="text" placeholder="Search pitcher or team…" oninput="filterP()">
    <span class="row-count" id="p-cnt"></span>
    <span class="sort-hint">Click headers to sort</span>
  </div>

  <!-- Starters table -->
  <div id="p-sp-wrap" class="table-wrap">
    <table id="sp-tbl">
      <thead><tr>
        <th class="sortable"      data-k="name"          onclick="srtSP(this,'name')">Pitcher</th>
        <th class="sortable"      data-k="team"          onclick="srtSP(this,'team')">Team</th>
        <th class="sortable"      data-k="opp"           onclick="srtSP(this,'opp')">Opp</th>
        <th class="sortable r"    data-k="ip_float"      onclick="srtSP(this,'ip_float')">IP</th>
        <th class="sortable r"    data-k="hits"          onclick="srtSP(this,'hits')">H</th>
        <th class="sortable r"    data-k="r"             onclick="srtSP(this,'r')">R</th>
        <th class="sortable r"    data-k="bb"            onclick="srtSP(this,'bb')">BB</th>
        <th class="sortable r"    data-k="k"             onclick="srtSP(this,'k')">K</th>
        <th class="sortable r"    data-k="w"             onclick="srtSP(this,'w')">W</th>
        <th class="sortable r"    data-k="whiffs"        onclick="srtSP(this,'whiffs')">Whiffs</th>
        <th class="sortable r"    data-k="hard_hits"     onclick="srtSP(this,'hard_hits')">Hard Hits</th>
        <th class="sortable r"    data-k="barrels"       onclick="srtSP(this,'barrels')">Barrels</th>
        <th class="sortable r"    data-k="k_bb_pct"      onclick="srtSP(this,'k_bb_pct')">K-BB%</th>
        <th class="sortable r sc" data-k="stuff_plus"    onclick="srtSP(this,'stuff_plus')">Stuff+</th>
        <th class="sortable r sc" data-k="location_plus" onclick="srtSP(this,'location_plus')">Loc+</th>
        <th>Arsenal</th>
      </tr></thead>
      <tbody id="sp-body"></tbody>
    </table>
  </div>

  <!-- Relievers table (hidden by default) -->
  <div id="p-rp-wrap" class="table-wrap" style="display:none">
    <table id="rp-tbl">
      <thead><tr>
        <th class="sortable"      data-k="name"          onclick="srtRP(this,'name')">Pitcher</th>
        <th class="sortable"      data-k="team"          onclick="srtRP(this,'team')">Team</th>
        <th class="sortable"      data-k="opp"           onclick="srtRP(this,'opp')">Opp</th>
        <th class="sortable r"    data-k="ip_float"      onclick="srtRP(this,'ip_float')">IP</th>
        <th class="sortable r"    data-k="hits"          onclick="srtRP(this,'hits')">H</th>
        <th class="sortable r"    data-k="r"             onclick="srtRP(this,'r')">R</th>
        <th class="sortable r"    data-k="bb"            onclick="srtRP(this,'bb')">BB</th>
        <th class="sortable r"    data-k="k"             onclick="srtRP(this,'k')">K</th>
        <th class="sortable r"    data-k="sv"            onclick="srtRP(this,'sv')">SV</th>
        <th class="sortable r"    data-k="hld"           onclick="srtRP(this,'hld')">HLD</th>
        <th class="sortable r"    data-k="bs"            onclick="srtRP(this,'bs')">BS</th>
        <th class="sortable r"    data-k="w"             onclick="srtRP(this,'w')">W</th>
        <th class="sortable r"    data-k="whiffs"        onclick="srtRP(this,'whiffs')">Whiffs</th>
        <th class="sortable r"    data-k="hard_hits"     onclick="srtRP(this,'hard_hits')">Hard Hits</th>
        <th class="sortable r"    data-k="barrels"       onclick="srtRP(this,'barrels')">Barrels</th>
        <th class="sortable r sc" data-k="stuff_plus"    onclick="srtRP(this,'stuff_plus')">Stuff+</th>
        <th class="sortable r sc" data-k="location_plus" onclick="srtRP(this,'location_plus')">Loc+</th>
        <th>Arsenal</th>
      </tr></thead>
      <tbody id="rp-body"></tbody>
    </table>
  </div>
</div>

<!-- ══ TEAM ALEX ══ -->
<div id="teamalex-panel" class="tab-panel">
  <div style="display:flex;align-items:center;gap:11px;margin-bottom:18px">
    <span style="font-size:1.6rem">👑</span>
    <div>
      <div style="font-size:1.05rem;font-weight:800;color:var(--gold)">Team Alex</div>
      <div style="font-size:.72rem;color:var(--muted)">24-player roster</div>
    </div>
  </div>

  <!-- Hitters section with Yesterday / Season toggle -->
  <div class="ta-section-hdr">🏏 Hitters <span class="tab-count" id="ta-h-tc">—</span></div>
  <div class="toggle-group" style="margin-bottom:10px">
    <button class="tgl-btn active" id="ta-h-yday-btn" onclick="showTAHView('yday',this)">Yesterday</button>
    <button class="tgl-btn" id="ta-h-season-btn" onclick="showTAHView('season',this)">Season</button>
  </div>

  <!-- Yesterday game stats table -->
  <div id="ta-h-yday-wrap" class="table-wrap" style="margin-bottom:24px">
    <table id="ta-h-tbl">
      <thead><tr>
        <th class="sortable"   data-k="name"      onclick="srtTA(this,'h','name')">Player</th>
        <th class="sortable"   data-k="team"      onclick="srtTA(this,'h','team')">Team</th>
        <th class="sortable"   data-k="opp"       onclick="srtTA(this,'h','opp')">Opp</th>
        <th class="sortable r" data-k="hr"        onclick="srtTA(this,'h','hr')">HR</th>
        <th class="sortable r" data-k="bb"        onclick="srtTA(this,'h','bb')">BB</th>
        <th class="sortable r" data-k="k"         onclick="srtTA(this,'h','k')">K</th>
        <th class="sortable r" data-k="sb"        onclick="srtTA(this,'h','sb')">SB</th>
        <th class="sortable r" data-k="sba"       onclick="srtTA(this,'h','sba')">SBA</th>
        <th class="sortable r" data-k="hard_hits" onclick="srtTA(this,'h','hard_hits')">Hard Hits</th>
        <th class="sortable r" data-k="barrels"   onclick="srtTA(this,'h','barrels')">Barrels</th>
        <th class="sortable r" data-k="max_ev"    onclick="srtTA(this,'h','max_ev')">Max EV</th>
      </tr></thead>
      <tbody id="ta-h-body"></tbody>
    </table>
  </div>

  <!-- Season stats table (hidden by default) -->
  <div id="ta-h-season-wrap" class="table-wrap" style="display:none;margin-bottom:24px">
    <table id="ta-lb-tbl">
      <thead><tr>
        <th class="sortable"   data-k="name"           onclick="srtTALB(this,'name')">Player</th>
        <th class="sortable r" data-k="r"              onclick="srtTALB(this,'r')">R</th>
        <th class="sortable r" data-k="hr"             onclick="srtTALB(this,'hr')">HR</th>
        <th class="sortable r" data-k="rbi"            onclick="srtTALB(this,'rbi')">RBI</th>
        <th class="sortable r" data-k="sb"             onclick="srtTALB(this,'sb')">SB</th>
        <th class="sortable r" data-k="obp"            onclick="srtTALB(this,'obp')">OBP</th>
        <th class="sortable r" data-k="woba"           onclick="srtTALB(this,'woba')">wOBA</th>
        <th class="sortable r" data-k="xwoba"          onclick="srtTALB(this,'xwoba')">xwOBA</th>
        <th class="sortable r lb-th-inv" data-k="chase_pct"    onclick="srtTALB(this,'chase_pct')">Chase%</th>
        <th class="sortable r lb-th-inv" data-k="whiff_pct"    onclick="srtTALB(this,'whiff_pct')">Whiff%</th>
        <th class="sortable r lb-th-inv" data-k="k_pct"        onclick="srtTALB(this,'k_pct')">K%</th>
        <th class="sortable r lb-th-inv" data-k="so"           onclick="srtTALB(this,'so')">SO</th>
        <th class="sortable r" data-k="bb_pct"         onclick="srtTALB(this,'bb_pct')">BB%</th>
        <th class="sortable r" data-k="hard_hit_pct"   onclick="srtTALB(this,'hard_hit_pct')">Hard Hit%</th>
        <th class="sortable r" data-k="barrel_pct"     onclick="srtTALB(this,'barrel_pct')">Barrel%</th>
        <th class="sortable r" data-k="barrels"        onclick="srtTALB(this,'barrels')">Barrels</th>
        <th class="sortable r" data-k="sweet_spot_pct" onclick="srtTALB(this,'sweet_spot_pct')">Swt Spot%</th>
        <th class="sortable r" data-k="avg_ev"         onclick="srtTALB(this,'avg_ev')">Avg EV</th>
        <th class="sortable r" data-k="max_ev"         onclick="srtTALB(this,'max_ev')">Max EV</th>
        <th class="sortable r" data-k="bat_speed"      onclick="srtTALB(this,'bat_speed')">Bat Spd</th>
        <th class="sortable r" data-k="sprint_speed"   onclick="srtTALB(this,'sprint_speed')">Sprt Spd</th>
      </tr></thead>
      <tbody id="ta-lb-body"></tbody>
    </table>
  </div>

  <div class="ta-section-hdr">⚾ Starting Pitchers <span class="tab-count" id="ta-sp-tc">—</span></div>
  <div class="toggle-group" style="margin-bottom:10px">
    <button class="tgl-btn active" id="ta-sp-yday-btn" onclick="showTASPView('yday',this)">Yesterday</button>
    <button class="tgl-btn" id="ta-sp-season-btn" onclick="showTASPView('season',this)">Season</button>
  </div>
  <!-- Yesterday game stats -->
  <div id="ta-sp-yday-wrap" class="table-wrap" style="margin-bottom:24px">
    <table id="ta-sp-tbl">
      <thead><tr>
        <th class="sortable"      data-k="name"          onclick="srtTA(this,'sp','name')">Pitcher</th>
        <th class="sortable"      data-k="team"          onclick="srtTA(this,'sp','team')">Team</th>
        <th class="sortable"      data-k="opp"           onclick="srtTA(this,'sp','opp')">Opp</th>
        <th class="sortable r"    data-k="ip_float"      onclick="srtTA(this,'sp','ip_float')">IP</th>
        <th class="sortable r"    data-k="hits"          onclick="srtTA(this,'sp','hits')">H</th>
        <th class="sortable r"    data-k="r"             onclick="srtTA(this,'sp','r')">R</th>
        <th class="sortable r"    data-k="bb"            onclick="srtTA(this,'sp','bb')">BB</th>
        <th class="sortable r"    data-k="k"             onclick="srtTA(this,'sp','k')">K</th>
        <th class="sortable r"    data-k="w"             onclick="srtTA(this,'sp','w')">W</th>
        <th class="sortable r"    data-k="whiffs"        onclick="srtTA(this,'sp','whiffs')">Whiffs</th>
        <th class="sortable r"    data-k="hard_hits"     onclick="srtTA(this,'sp','hard_hits')">Hard Hits</th>
        <th class="sortable r"    data-k="barrels"       onclick="srtTA(this,'sp','barrels')">Barrels</th>
        <th class="sortable r"    data-k="k_bb_pct"      onclick="srtTA(this,'sp','k_bb_pct')">K-BB%</th>
        <th class="sortable r sc" data-k="stuff_plus"    onclick="srtTA(this,'sp','stuff_plus')">Stuff+</th>
        <th class="sortable r sc" data-k="location_plus" onclick="srtTA(this,'sp','location_plus')">Loc+</th>
        <th>Arsenal</th>
      </tr></thead>
      <tbody id="ta-sp-body"></tbody>
    </table>
  </div>
  <!-- Season stats -->
  <div id="ta-sp-season-wrap" class="table-wrap" style="display:none;margin-bottom:24px">
    <table id="ta-sp-lb-tbl">
      <thead><tr>
        <th class="sortable"   data-k="name"         onclick="srtTASPLB(this,'name')">Pitcher</th>
        <th class="sortable r" data-k="ip_f"         onclick="srtTASPLB(this,'ip_f')">IP</th>
        <th class="sortable r" data-k="w"            onclick="srtTASPLB(this,'w')">W</th>
        <th class="sortable r lb-th-inv" data-k="era"  onclick="srtTASPLB(this,'era')">ERA</th>
        <th class="sortable r lb-th-inv" data-k="whip" onclick="srtTASPLB(this,'whip')">WHIP</th>
        <th class="sortable r lb-th-inv" data-k="xera" onclick="srtTASPLB(this,'xera')">xERA</th>
        <th class="sortable r lb-th-inv" data-k="siera" onclick="srtTASPLB(this,'siera')">SIERA</th>
        <th class="sortable r" data-k="stuff_plus"   onclick="srtTASPLB(this,'stuff_plus')">Stf+</th>
        <th class="sortable r" data-k="loc_plus"     onclick="srtTASPLB(this,'loc_plus')">Loc+</th>
        <th class="sortable r" data-k="k"            onclick="srtTASPLB(this,'k')">K</th>
        <th class="sortable r" data-k="k_pct"        onclick="srtTASPLB(this,'k_pct')">K%</th>
        <th class="sortable r lb-th-inv" data-k="bb_pct" onclick="srtTASPLB(this,'bb_pct')">BB%</th>
        <th class="sortable r" data-k="chase_pct"    onclick="srtTASPLB(this,'chase_pct')">Chase%</th>
        <th class="sortable r" data-k="whiff_pct"    onclick="srtTASPLB(this,'whiff_pct')">Whiff%</th>
        <th class="sortable r lb-th-inv" data-k="barrel_pct"   onclick="srtTASPLB(this,'barrel_pct')">Barrel%</th>
        <th class="sortable r lb-th-inv" data-k="hard_hit_pct" onclick="srtTASPLB(this,'hard_hit_pct')">Hard Hit%</th>
        <th class="sortable r" data-k="gb_pct"       onclick="srtTASPLB(this,'gb_pct')">GB%</th>
        <th class="sortable r lb-th-inv" data-k="woba"  onclick="srtTASPLB(this,'woba')">wOBA</th>
        <th class="sortable r lb-th-inv" data-k="xwoba" onclick="srtTASPLB(this,'xwoba')">xwOBA</th>
        <th class="sortable r lb-th-inv" data-k="avg_ev" onclick="srtTASPLB(this,'avg_ev')">Avg EV</th>
        <th class="sortable r" data-k="fb_velo"      onclick="srtTASPLB(this,'fb_velo')">FB Velo</th>
      </tr></thead>
      <tbody id="ta-sp-lb-body"></tbody>
    </table>
  </div>

  <div class="ta-section-hdr">🔥 Relief Pitchers <span class="tab-count" id="ta-rp-tc">—</span></div>
  <div class="toggle-group" style="margin-bottom:10px">
    <button class="tgl-btn active" id="ta-rp-yday-btn" onclick="showTARPView('yday',this)">Yesterday</button>
    <button class="tgl-btn" id="ta-rp-season-btn" onclick="showTARPView('season',this)">Season</button>
  </div>
  <!-- Yesterday game stats -->
  <div id="ta-rp-yday-wrap" class="table-wrap" style="margin-bottom:24px">
    <table id="ta-rp-tbl">
      <thead><tr>
        <th class="sortable"      data-k="name"          onclick="srtTA(this,'rp','name')">Pitcher</th>
        <th class="sortable"      data-k="team"          onclick="srtTA(this,'rp','team')">Team</th>
        <th class="sortable"      data-k="opp"           onclick="srtTA(this,'rp','opp')">Opp</th>
        <th class="sortable r"    data-k="ip_float"      onclick="srtTA(this,'rp','ip_float')">IP</th>
        <th class="sortable r"    data-k="hits"          onclick="srtTA(this,'rp','hits')">H</th>
        <th class="sortable r"    data-k="r"             onclick="srtTA(this,'rp','r')">R</th>
        <th class="sortable r"    data-k="bb"            onclick="srtTA(this,'rp','bb')">BB</th>
        <th class="sortable r"    data-k="k"             onclick="srtTA(this,'rp','k')">K</th>
        <th class="sortable r"    data-k="sv"            onclick="srtTA(this,'rp','sv')">SV</th>
        <th class="sortable r"    data-k="hld"           onclick="srtTA(this,'rp','hld')">HLD</th>
        <th class="sortable r"    data-k="bs"            onclick="srtTA(this,'rp','bs')">BS</th>
        <th class="sortable r"    data-k="w"             onclick="srtTA(this,'rp','w')">W</th>
        <th class="sortable r"    data-k="whiffs"        onclick="srtTA(this,'rp','whiffs')">Whiffs</th>
        <th class="sortable r"    data-k="hard_hits"     onclick="srtTA(this,'rp','hard_hits')">Hard Hits</th>
        <th class="sortable r"    data-k="barrels"       onclick="srtTA(this,'rp','barrels')">Barrels</th>
        <th class="sortable r sc" data-k="stuff_plus"    onclick="srtTA(this,'rp','stuff_plus')">Stuff+</th>
        <th class="sortable r sc" data-k="location_plus" onclick="srtTA(this,'rp','location_plus')">Loc+</th>
        <th>Arsenal</th>
      </tr></thead>
      <tbody id="ta-rp-body"></tbody>
    </table>
  </div>
  <!-- Season stats -->
  <div id="ta-rp-season-wrap" class="table-wrap" style="display:none;margin-bottom:24px">
    <table id="ta-rp-lb-tbl">
      <thead><tr>
        <th class="sortable"   data-k="name"         onclick="srtTARPLB(this,'name')">Pitcher</th>
        <th class="sortable r" data-k="ip_f"         onclick="srtTARPLB(this,'ip_f')">IP</th>
        <th class="sortable r" data-k="w"            onclick="srtTARPLB(this,'w')">W</th>
        <th class="sortable r" data-k="sv"           onclick="srtTARPLB(this,'sv')">SV/SVO</th>
        <th class="sortable r" data-k="hld"          onclick="srtTARPLB(this,'hld')">HLD</th>
        <th class="sortable r lb-th-inv" data-k="era"  onclick="srtTARPLB(this,'era')">ERA</th>
        <th class="sortable r lb-th-inv" data-k="whip" onclick="srtTARPLB(this,'whip')">WHIP</th>
        <th class="sortable r lb-th-inv" data-k="xera" onclick="srtTARPLB(this,'xera')">xERA</th>
        <th class="sortable r lb-th-inv" data-k="siera" onclick="srtTARPLB(this,'siera')">SIERA</th>
        <th class="sortable r" data-k="stuff_plus"   onclick="srtTARPLB(this,'stuff_plus')">Stf+</th>
        <th class="sortable r" data-k="loc_plus"     onclick="srtTARPLB(this,'loc_plus')">Loc+</th>
        <th class="sortable r" data-k="k"            onclick="srtTARPLB(this,'k')">K</th>
        <th class="sortable r" data-k="k_pct"        onclick="srtTARPLB(this,'k_pct')">K%</th>
        <th class="sortable r lb-th-inv" data-k="bb_pct" onclick="srtTARPLB(this,'bb_pct')">BB%</th>
        <th class="sortable r" data-k="chase_pct"    onclick="srtTARPLB(this,'chase_pct')">Chase%</th>
        <th class="sortable r" data-k="whiff_pct"    onclick="srtTARPLB(this,'whiff_pct')">Whiff%</th>
        <th class="sortable r lb-th-inv" data-k="barrel_pct"   onclick="srtTARPLB(this,'barrel_pct')">Barrel%</th>
        <th class="sortable r lb-th-inv" data-k="hard_hit_pct" onclick="srtTARPLB(this,'hard_hit_pct')">Hard Hit%</th>
        <th class="sortable r" data-k="gb_pct"       onclick="srtTARPLB(this,'gb_pct')">GB%</th>
        <th class="sortable r lb-th-inv" data-k="woba"  onclick="srtTARPLB(this,'woba')">wOBA</th>
        <th class="sortable r lb-th-inv" data-k="xwoba" onclick="srtTARPLB(this,'xwoba')">xwOBA</th>
        <th class="sortable r lb-th-inv" data-k="avg_ev" onclick="srtTARPLB(this,'avg_ev')">Avg EV</th>
        <th class="sortable r" data-k="fb_velo"      onclick="srtTARPLB(this,'fb_velo')">FB Velo</th>
      </tr></thead>
      <tbody id="ta-rp-lb-body"></tbody>
    </table>
  </div>
  <div class="note" style="margin-top:14px">
    Yesterday view: only roster members who played yesterday. Season view: all roster members with stats. Season cell colors = league rank among all qualified pitchers.
  </div>
</div>

<!-- ══ SEASON LEADERBOARD ══ -->
<div id="leaderboard-panel" class="tab-panel">
  <div class="toggle-group" style="margin-bottom:14px">
    <button class="tgl-btn active" onclick="showLBType('h',this)">🏏 Hitters</button>
    <button class="tgl-btn" onclick="showLBType('sp',this)">⚾ SP</button>
    <button class="tgl-btn" onclick="showLBType('rp',this)">🔥 RP</button>
  </div>

  <!-- ── Hitters view ── -->
  <div id="lb-h-wrap">
    <div class="note">
      ⓘ &nbsp;Season batting leaderboard — FanGraphs + Baseball Savant.
      <strong>Default view:</strong> qualified hitters only (≥3.1 PA/team game).
      Use search to find any player.
      Cell colors = league rank among qualified hitters:
      <span style="color:#f0c040;font-weight:700">Gold</span> = #1 &nbsp;·&nbsp;
      <span style="color:#c0392b;font-weight:700">Dark red</span> = top &nbsp;·&nbsp;
      <span style="color:#1a3a8a;font-weight:700">Dark blue</span> = bottom.
    </div>
    <div class="controls">
      <input id="lb-search" type="text" placeholder="Search any player or team…" oninput="filterLB()">
      <label class="qual-toggle" id="lb-qual-lbl">
        <input type="checkbox" id="lb-qual-chk" checked onchange="filterLB()"> Qualified only
      </label>
      <span class="row-count" id="lb-cnt"></span>
      <span class="sort-hint">Click headers to sort</span>
    </div>
    <div class="table-wrap">
      <table id="lb-tbl">
        <thead><tr>
          <th class="sortable"   data-k="name"           onclick="srtLB(this,'name')">Player</th>
          <th class="sortable r" data-k="r"              onclick="srtLB(this,'r')">R</th>
          <th class="sortable r" data-k="hr"             onclick="srtLB(this,'hr')">HR</th>
          <th class="sortable r" data-k="rbi"            onclick="srtLB(this,'rbi')">RBI</th>
          <th class="sortable r" data-k="sb"             onclick="srtLB(this,'sb')">SB</th>
          <th class="sortable r" data-k="obp"            onclick="srtLB(this,'obp')">OBP</th>
          <th class="sortable r" data-k="woba"           onclick="srtLB(this,'woba')">wOBA</th>
          <th class="sortable r" data-k="xwoba"          onclick="srtLB(this,'xwoba')">xwOBA</th>
          <th class="sortable r lb-th-inv" data-k="chase_pct"    onclick="srtLB(this,'chase_pct')">Chase%</th>
          <th class="sortable r lb-th-inv" data-k="whiff_pct"    onclick="srtLB(this,'whiff_pct')">Whiff%</th>
          <th class="sortable r lb-th-inv" data-k="k_pct"        onclick="srtLB(this,'k_pct')">K%</th>
          <th class="sortable r lb-th-inv" data-k="so"           onclick="srtLB(this,'so')">SO</th>
          <th class="sortable r" data-k="bb_pct"         onclick="srtLB(this,'bb_pct')">BB%</th>
          <th class="sortable r" data-k="hard_hit_pct"   onclick="srtLB(this,'hard_hit_pct')">Hard Hit%</th>
          <th class="sortable r" data-k="barrel_pct"     onclick="srtLB(this,'barrel_pct')">Barrel%</th>
          <th class="sortable r" data-k="barrels"        onclick="srtLB(this,'barrels')">Barrels</th>
          <th class="sortable r" data-k="sweet_spot_pct" onclick="srtLB(this,'sweet_spot_pct')">Swt Spot%</th>
          <th class="sortable r" data-k="avg_ev"         onclick="srtLB(this,'avg_ev')">Avg EV</th>
          <th class="sortable r" data-k="max_ev"         onclick="srtLB(this,'max_ev')">Max EV</th>
          <th class="sortable r" data-k="bat_speed"      onclick="srtLB(this,'bat_speed')">Bat Spd</th>
          <th class="sortable r" data-k="sprint_speed"   onclick="srtLB(this,'sprint_speed')">Sprt Spd</th>
        </tr></thead>
        <tbody id="lb-body"></tbody>
      </table>
    </div>
  </div>

  <!-- ── SP view ── -->
  <div id="lb-sp-wrap" style="display:none">
    <div class="note">
      ⓘ &nbsp;Season SP leaderboard — FanGraphs + Baseball Savant.
      <strong>Default view:</strong> qualified starters only (≥1 IP/team game).
      Cell colors = league rank among qualified starters.
    </div>
    <div class="controls">
      <input id="lb-sp-search" type="text" placeholder="Search pitcher or team…" oninput="filterLBSP()">
      <label class="qual-toggle" id="lb-sp-qual-lbl">
        <input type="checkbox" id="lb-sp-qual-chk" checked onchange="filterLBSP()"> Qualified only
      </label>
      <span class="row-count" id="lb-sp-cnt"></span>
      <span class="sort-hint">Click headers to sort</span>
    </div>
    <div class="table-wrap">
      <table id="lb-sp-tbl">
        <thead><tr>
          <th class="sortable"   data-k="name"         onclick="srtLBSP(this,'name')">Pitcher</th>
          <th class="sortable r" data-k="ip_f"         onclick="srtLBSP(this,'ip_f')">IP</th>
          <th class="sortable r" data-k="w"            onclick="srtLBSP(this,'w')">W</th>
          <th class="sortable r lb-th-inv" data-k="era"  onclick="srtLBSP(this,'era')">ERA</th>
          <th class="sortable r lb-th-inv" data-k="whip" onclick="srtLBSP(this,'whip')">WHIP</th>
          <th class="sortable r lb-th-inv" data-k="xera" onclick="srtLBSP(this,'xera')">xERA</th>
          <th class="sortable r lb-th-inv" data-k="siera" onclick="srtLBSP(this,'siera')">SIERA</th>
          <th class="sortable r" data-k="stuff_plus"   onclick="srtLBSP(this,'stuff_plus')">Stf+</th>
          <th class="sortable r" data-k="loc_plus"     onclick="srtLBSP(this,'loc_plus')">Loc+</th>
          <th class="sortable r" data-k="k"            onclick="srtLBSP(this,'k')">K</th>
          <th class="sortable r" data-k="k_pct"        onclick="srtLBSP(this,'k_pct')">K%</th>
          <th class="sortable r lb-th-inv" data-k="bb_pct" onclick="srtLBSP(this,'bb_pct')">BB%</th>
          <th class="sortable r" data-k="chase_pct"    onclick="srtLBSP(this,'chase_pct')">Chase%</th>
          <th class="sortable r" data-k="whiff_pct"    onclick="srtLBSP(this,'whiff_pct')">Whiff%</th>
          <th class="sortable r lb-th-inv" data-k="barrel_pct"   onclick="srtLBSP(this,'barrel_pct')">Barrel%</th>
          <th class="sortable r lb-th-inv" data-k="hard_hit_pct" onclick="srtLBSP(this,'hard_hit_pct')">Hard Hit%</th>
          <th class="sortable r" data-k="gb_pct"       onclick="srtLBSP(this,'gb_pct')">GB%</th>
          <th class="sortable r lb-th-inv" data-k="woba"  onclick="srtLBSP(this,'woba')">wOBA</th>
          <th class="sortable r lb-th-inv" data-k="xwoba" onclick="srtLBSP(this,'xwoba')">xwOBA</th>
          <th class="sortable r lb-th-inv" data-k="avg_ev" onclick="srtLBSP(this,'avg_ev')">Avg EV</th>
          <th class="sortable r" data-k="fb_velo"      onclick="srtLBSP(this,'fb_velo')">FB Velo</th>
        </tr></thead>
        <tbody id="lb-sp-body"></tbody>
      </table>
    </div>
  </div>

  <!-- ── RP view ── -->
  <div id="lb-rp-wrap" style="display:none">
    <div class="note">
      ⓘ &nbsp;Season RP leaderboard — FanGraphs + Baseball Savant.
      <strong>Default view:</strong> qualified relievers only (≥0.5 IP/team game).
      Cell colors = league rank among qualified relievers.
    </div>
    <div class="controls">
      <input id="lb-rp-search" type="text" placeholder="Search pitcher or team…" oninput="filterLBRP()">
      <label class="qual-toggle" id="lb-rp-qual-lbl">
        <input type="checkbox" id="lb-rp-qual-chk" checked onchange="filterLBRP()"> Qualified only
      </label>
      <span class="row-count" id="lb-rp-cnt"></span>
      <span class="sort-hint">Click headers to sort</span>
    </div>
    <div class="table-wrap">
      <table id="lb-rp-tbl">
        <thead><tr>
          <th class="sortable"   data-k="name"         onclick="srtLBRP(this,'name')">Pitcher</th>
          <th class="sortable r" data-k="ip_f"         onclick="srtLBRP(this,'ip_f')">IP</th>
          <th class="sortable r" data-k="w"            onclick="srtLBRP(this,'w')">W</th>
          <th class="sortable r" data-k="sv"           onclick="srtLBRP(this,'sv')">SV/SVO</th>
          <th class="sortable r" data-k="hld"          onclick="srtLBRP(this,'hld')">HLD</th>
          <th class="sortable r lb-th-inv" data-k="era"  onclick="srtLBRP(this,'era')">ERA</th>
          <th class="sortable r lb-th-inv" data-k="whip" onclick="srtLBRP(this,'whip')">WHIP</th>
          <th class="sortable r lb-th-inv" data-k="xera" onclick="srtLBRP(this,'xera')">xERA</th>
          <th class="sortable r lb-th-inv" data-k="siera" onclick="srtLBRP(this,'siera')">SIERA</th>
          <th class="sortable r" data-k="stuff_plus"   onclick="srtLBRP(this,'stuff_plus')">Stf+</th>
          <th class="sortable r" data-k="loc_plus"     onclick="srtLBRP(this,'loc_plus')">Loc+</th>
          <th class="sortable r" data-k="k"            onclick="srtLBRP(this,'k')">K</th>
          <th class="sortable r" data-k="k_pct"        onclick="srtLBRP(this,'k_pct')">K%</th>
          <th class="sortable r lb-th-inv" data-k="bb_pct" onclick="srtLBRP(this,'bb_pct')">BB%</th>
          <th class="sortable r" data-k="chase_pct"    onclick="srtLBRP(this,'chase_pct')">Chase%</th>
          <th class="sortable r" data-k="whiff_pct"    onclick="srtLBRP(this,'whiff_pct')">Whiff%</th>
          <th class="sortable r lb-th-inv" data-k="barrel_pct"   onclick="srtLBRP(this,'barrel_pct')">Barrel%</th>
          <th class="sortable r lb-th-inv" data-k="hard_hit_pct" onclick="srtLBRP(this,'hard_hit_pct')">Hard Hit%</th>
          <th class="sortable r" data-k="gb_pct"       onclick="srtLBRP(this,'gb_pct')">GB%</th>
          <th class="sortable r lb-th-inv" data-k="woba"  onclick="srtLBRP(this,'woba')">wOBA</th>
          <th class="sortable r lb-th-inv" data-k="xwoba" onclick="srtLBRP(this,'xwoba')">xwOBA</th>
          <th class="sortable r lb-th-inv" data-k="avg_ev" onclick="srtLBRP(this,'avg_ev')">Avg EV</th>
          <th class="sortable r" data-k="fb_velo"      onclick="srtLBRP(this,'fb_velo')">FB Velo</th>
        </tr></thead>
        <tbody id="lb-rp-body"></tbody>
      </table>
    </div>
  </div>
</div>

</main>

<footer>
  FanGraphs (Stuff+/Loc+) · Statcast · MLB Stats API (SB) &nbsp;·&nbsp;
  Hard Hit = EV ≥ 95 mph &nbsp;·&nbsp; Starters only &nbsp;·&nbsp; Generated __TS__
</footer>

<script>
const HITTERS    = __HITTERS_JSON__;
const ALL_PITCHERS = __ALL_PITCHERS_JSON__;
const STARTERS   = ALL_PITCHERS.filter(p=>p.ip_float>=3||p.is_starter);
const RELIEVERS  = ALL_PITCHERS.filter(p=>p.ip_float<3&&!p.is_starter);
const TA_HITTERS = __TA_H_JSON__;
const TA_STARTERS= __TA_SP_JSON__;
const TA_RELIEVERS=__TA_RP_JSON__;
const TA_ROSTER_NORMS=new Set(__TA_NAMES_JSON__);

// ── Category leaders (gold highlight) ─────────────────────────────────────
const H_LEAD_COLS=['hr','bb','k','sb','sba','hard_hits','barrels','max_ev'];
const SP_LEAD_COLS=['ip_float','k','w','whiffs','hard_hits','barrels','k_bb_pct','stuff_plus','location_plus'];
const RP_LEAD_COLS=['ip_float','k','sv','hld','bs','w','whiffs','hard_hits','barrels','stuff_plus','location_plus'];
function maxOf(arr,col){
  let m=-Infinity;
  arr.forEach(r=>{if(r[col]!=null&&!isNaN(r[col])&&r[col]>m)m=r[col];});
  return m>0?m:null;
}
function minOf(arr,col){
  let m=Infinity;
  arr.forEach(r=>{if(r[col]!=null&&!isNaN(r[col])&&r[col]<m)m=r[col];});
  return m<Infinity?m:null;
}
const hL={};H_LEAD_COLS.forEach(c=>hL[c]=maxOf(HITTERS,c));
const spL={};SP_LEAD_COLS.forEach(c=>spL[c]=maxOf(STARTERS,c));
const rpL={};RP_LEAD_COLS.forEach(c=>rpL[c]=maxOf(RELIEVERS,c));
// Pitcher H/R/BB/hard_hits/barrels: lower is better → gold goes to lowest
const spMin={hits:minOf(STARTERS,'hits'),r:minOf(STARTERS,'r'),bb:minOf(STARTERS,'bb'),hard_hits:minOf(STARTERS,'hard_hits'),barrels:minOf(STARTERS,'barrels')};
const rpMin={hits:minOf(RELIEVERS,'hits'),r:minOf(RELIEVERS,'r'),bb:minOf(RELIEVERS,'bb'),hard_hits:minOf(RELIEVERS,'hard_hits'),barrels:minOf(RELIEVERS,'barrels')};
// Gold if value equals category leader
const gl=(v,max)=>(max!=null&&v!=null&&v===max)?`<span class="c-gold">${v}</span>`:null;
const glMin=(v,min)=>(min!=null&&v!=null&&v===min)?`<span class="c-gold">${v}</span>`:null;
// EV gradient: red (high) → blue (low)
const _evVals=HITTERS.filter(h=>h.max_ev!=null&&h.bip>0).map(h=>h.max_ev);
const _evMin=_evVals.length?Math.min(..._evVals):90, _evMax=_evVals.length?Math.max(..._evVals):115;

// Inverted-sort columns (lower = better): first click → ascending
const LB_INV_SORT=new Set(['k_pct','chase_pct','whiff_pct','so']);

// Pitchers tab counts
document.getElementById('p-tc').textContent=STARTERS.length+RELIEVERS.length;
document.getElementById('p-sp-tc').textContent=STARTERS.length;
document.getElementById('p-rp-tc').textContent=RELIEVERS.length;

let hD=[...HITTERS], spD=[...STARTERS], rpD=[...RELIEVERS];
let hSC='barrels', hSD=-1, spSC='ip_float', spSD=-1, rpSC='sv', rpSD=-1;
let pitchType='sp';  // current pitcher sub-view

function showTab(nm,btn){
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(nm+'-panel').classList.add('active');
}

function showPitchType(type,btn){
  pitchType=type;
  document.querySelectorAll('#pitchers-panel .tgl-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('p-sp-wrap').style.display=type==='sp'?'':'none';
  document.getElementById('p-rp-wrap').style.display=type==='rp'?'':'none';
  // re-apply current search to the newly visible table
  filterP();
}

function filterP(){
  const q=document.getElementById('p-search').value.toLowerCase().trim();
  if(pitchType==='sp'){
    spD=q?STARTERS.filter(p=>p.name.toLowerCase().includes(q)||p.team.toLowerCase().includes(q)||p.opp.toLowerCase().includes(q)):[...STARTERS];
    if(spSC)spD.sort((a,b)=>cmp(a,b,spSC,spSD));
    document.getElementById('p-cnt').textContent=`${spD.length} starter${spD.length===1?'':'s'}`;
    renderSP();
  } else {
    rpD=q?RELIEVERS.filter(p=>p.name.toLowerCase().includes(q)||p.team.toLowerCase().includes(q)||p.opp.toLowerCase().includes(q)):[...RELIEVERS];
    if(rpSC)rpD.sort((a,b)=>cmp(a,b,rpSC,rpSD));
    document.getElementById('p-cnt').textContent=`${rpD.length} reliever${rpD.length===1?'':'s'}`;
    renderRP();
  }
}

const D  = ()=>'<span class="c-dim">—</span>';
// MLB team color map: abbreviation → [primaryColor, textColor]
const TEAM_COLORS = {
  ARI:['#A71930','#fff'], ATL:['#CE1141','#fff'], BAL:['#DF4601','#fff'],
  BOS:['#BD3039','#fff'], CHC:['#0E3386','#fff'], CWS:['#27251F','#fff'],
  CIN:['#C6011F','#fff'], CLE:['#00385D','#fff'], COL:['#33006F','#fff'],
  DET:['#0C2340','#fff'], HOU:['#002D62','#fff'], KC:['#004687','#fff'],
  LAA:['#BA0021','#fff'], LAD:['#005A9C','#fff'], MIA:['#00A3E0','#fff'],
  MIL:['#12284B','#fff'], MIN:['#002B5C','#fff'], NYM:['#002D72','#fff'],
  NYY:['#003087','#fff'], OAK:['#003831','#fff'], PHI:['#E81828','#fff'],
  PIT:['#FDB827','#1a1a1a'], SD:['#2F241D','#fff'], SEA:['#0C2C56','#fff'],
  SF:['#FD5A1E','#fff'],  STL:['#C41E3A','#fff'], TB:['#092C5C','#fff'],
  TEX:['#003278','#fff'], TOR:['#134A8E','#fff'], WSH:['#AB0003','#fff'],
};
const tm = t => {
  const tc = TEAM_COLORS[t];
  if(tc) return `<span class="tm" style="background:${tc[0]};color:${tc[1]};border-color:${tc[0]}44">${t}</span>`;
  return `<span class="tm" style="background:rgba(255,255,255,.12);color:var(--text)">${t}</span>`;
};

// Counting stats: show 0 when absent; rate/measurement stats: keep dash
const fHR  = v=>v>0?`${v}`:'0';
const fK_h = v=>v>0?`${v}`:'0';
const fBB_h= v=>v>0?`${v}`:'0';
const fSB  = v=>v>0?`${v}`:'0';
const fHrd = v=>v>0?`${v}`:'0';
const fBar = v=>v>0?`${v}`:'0';
const fEV  = (v,b)=>{if(v==null||b===0)return D();const t=Math.max(0,Math.min(1,(v-_evMin)/(_evMax-_evMin||1)));const r=Math.round(50+205*t),g=Math.round(120-55*t),bl=Math.round(255-200*t);return `<span style="color:rgb(${r},${g},${bl});font-weight:600">${v}</span>`;};
const fIP  = (v,s)=>s;
const fH_p = v=>v>0?`${v}`:'0';
const fR   = v=>v>0?`${v}`:'0';
const fBB_p= v=>v>0?`${v}`:'0';
const fK_p = v=>v>0?`${v}`:'0';
const fWh  = v=>v>0?`${v}`:'0';
const fKBB = v=>`${v}%`;
const fSP  = v=>v==null?D():`${v}`;
const fLP  = v=>v==null?D():`${v}`;
const glIP = (v,s,max)=>(max!=null&&v!=null&&v===max)?`<span class="c-gold">${s}</span>`:null;

function pitchArsenal(types){
  if(!types||!types.length) return D();
  return '<div class="arsenal">'+types.map(pt=>{
    const c=pt.color||'#888';
    let veloHtml='<span class="vd">—</span>';
    if(pt.velo!=null){
      const gvCls=pt.velo_alert?'va':'vn';
      const gv=`<span class="${gvCls}">${pt.velo}</span>`;
      const sv=pt.season_velo!=null?`<span class="vd sv"> (${pt.season_velo})</span>`:'';
      veloHtml=`${gv}${sv}<span class="vd" style="font-size:.65rem"> mph</span>`;
    }
    const stuffHtml=pt.game_stuff!=null?`<span class="gs">S+:${pt.game_stuff}</span>`:'';
    return `<div class="pt-row">
      <span class="pt-badge" style="color:${c};border:1px solid ${c}44">${pt.name}</span>
      <span class="pt-pct">${pt.pct}%</span>
      <span class="pt-velo">${veloHtml}</span>
      <span class="pt-stuff">${stuffHtml}</span>
    </div>`;
  }).join('')+'</div>';
}

function renderH(){
  const tb=document.getElementById('h-body');
  const ct=document.getElementById('h-cnt');
  document.getElementById('h-tc').textContent=hD.length;
  if(!hD.length){tb.innerHTML='<tr><td colspan="11"><div class="empty"><div class="ico">😴</div><p>No data.</p></div></td></tr>';ct.textContent='';return;}
  ct.textContent=`${hD.length} player${hD.length===1?'':'s'}`;
  tb.innerHTML=hD.map(h=>`<tr>
    <td class="nm">${h.name}</td>
    <td>${tm(h.team)}</td>
    <td><span class="c-dim" style="font-size:.7rem">vs</span> ${tm(h.opp)}</td>
    <td class="r">${h.grand_slam&&h.hr>0?`<span style="color:#2ecc71;font-weight:700">${h.hr}</span>`:gl(h.hr,hL.hr)||fHR(h.hr)}</td>
    <td class="r">${gl(h.bb,hL.bb)||fBB_h(h.bb)}</td>
    <td class="r">${gl(h.k,hL.k)||fK_h(h.k)}</td>
    <td class="r">${gl(h.sb,hL.sb)||fSB(h.sb)}</td>
    <td class="r">${gl(h.sba,hL.sba)||(h.sba>0?`${h.sba}`:'0')}</td>
    <td class="r">${gl(h.hard_hits,hL.hard_hits)||fHrd(h.hard_hits)}</td>
    <td class="r">${gl(h.barrels,hL.barrels)||fBar(h.barrels)}</td>
    <td class="r">${gl(h.max_ev,hL.max_ev)||fEV(h.max_ev,h.bip)}</td>
  </tr>`).join('');
}

function renderSP(){
  const tb=document.getElementById('sp-body');
  if(!spD.length){tb.innerHTML='<tr><td colspan="16"><div class="empty"><div class="ico">😴</div><p>No data.</p></div></td></tr>';return;}
  tb.innerHTML=spD.map(p=>`<tr>
    <td class="nm">${p.name}</td>
    <td>${tm(p.team)}</td>
    <td><span class="c-dim" style="font-size:.7rem">vs</span> ${tm(p.opp)}</td>
    <td class="r">${glIP(p.ip_float,p.ip,spL.ip_float)||p.ip}</td>
    <td class="r">${glMin(p.hits,spMin.hits)||fH_p(p.hits)}</td>
    <td class="r">${glMin(p.r,spMin.r)||fR(p.r)}</td>
    <td class="r">${glMin(p.bb,spMin.bb)||fBB_p(p.bb)}</td>
    <td class="r">${gl(p.k,spL.k)||fK_p(p.k)}</td>
    <td class="r">${gl(p.w,spL.w)||(p.w>0?`${p.w}`:'0')}</td>
    <td class="r">${gl(p.whiffs,spL.whiffs)||fWh(p.whiffs)}</td>
    <td class="r">${glMin(p.hard_hits,spMin.hard_hits)||fHrd(p.hard_hits)}</td>
    <td class="r">${glMin(p.barrels,spMin.barrels)||fBar(p.barrels)}</td>
    <td class="r">${gl(p.k_bb_pct,spL.k_bb_pct)||fKBB(p.k_bb_pct)}</td>
    <td class="r">${gl(p.stuff_plus,spL.stuff_plus)||fSP(p.stuff_plus)}</td>
    <td class="r">${gl(p.location_plus,spL.location_plus)||fLP(p.location_plus)}</td>
    <td>${pitchArsenal(p.pitch_types)}</td>
  </tr>`).join('');
}

function renderRP(){
  const tb=document.getElementById('rp-body');
  if(!rpD.length){tb.innerHTML='<tr><td colspan="18"><div class="empty"><div class="ico">😴</div><p>No relief data.</p></div></td></tr>';return;}
  tb.innerHTML=rpD.map(p=>`<tr>
    <td class="nm">${p.name}</td>
    <td>${tm(p.team)}</td>
    <td><span class="c-dim" style="font-size:.7rem">vs</span> ${tm(p.opp)}</td>
    <td class="r">${glIP(p.ip_float,p.ip,rpL.ip_float)||p.ip}</td>
    <td class="r">${glMin(p.hits,rpMin.hits)||fH_p(p.hits)}</td>
    <td class="r">${glMin(p.r,rpMin.r)||fR(p.r)}</td>
    <td class="r">${glMin(p.bb,rpMin.bb)||fBB_p(p.bb)}</td>
    <td class="r">${gl(p.k,rpL.k)||fK_p(p.k)}</td>
    <td class="r">${gl(p.sv,rpL.sv)||(p.sv>0?`${p.sv}`:'0')}</td>
    <td class="r">${gl(p.hld,rpL.hld)||(p.hld>0?`${p.hld}`:'0')}</td>
    <td class="r">${gl(p.bs,rpL.bs)||(p.bs>0?`${p.bs}`:'0')}</td>
    <td class="r">${gl(p.w,rpL.w)||(p.w>0?`${p.w}`:'0')}</td>
    <td class="r">${gl(p.whiffs,rpL.whiffs)||fWh(p.whiffs)}</td>
    <td class="r">${glMin(p.hard_hits,rpMin.hard_hits)||fHrd(p.hard_hits)}</td>
    <td class="r">${glMin(p.barrels,rpMin.barrels)||fBar(p.barrels)}</td>
    <td class="r">${gl(p.stuff_plus,rpL.stuff_plus)||fSP(p.stuff_plus)}</td>
    <td class="r">${gl(p.location_plus,rpL.location_plus)||fLP(p.location_plus)}</td>
    <td>${pitchArsenal(p.pitch_types)}</td>
  </tr>`).join('');
}

function cmp(a,b,col,dir){
  let av=a[col],bv=b[col];
  if(av==null)av=-Infinity;if(bv==null)bv=-Infinity;
  return typeof av==='string'?dir*av.localeCompare(bv):dir*(av-bv);
}
function clrSort(id){document.querySelectorAll(`#${id} thead th`).forEach(t=>t.classList.remove('sort-asc','sort-desc'));}
function srtH(th,col){if(hSC===col)hSD*=-1;else{hSC=col;hSD=-1;}clrSort('h-tbl');th.classList.add(hSD===1?'sort-asc':'sort-desc');hD.sort((a,b)=>cmp(a,b,col,hSD));renderH();}
function srtSP(th,col){if(spSC===col)spSD*=-1;else{spSC=col;spSD=-1;}clrSort('sp-tbl');th.classList.add(spSD===1?'sort-asc':'sort-desc');spD.sort((a,b)=>cmp(a,b,col,spSD));document.getElementById('p-cnt').textContent=`${spD.length} starter${spD.length===1?'':'s'}`;renderSP();}
function srtRP(th,col){if(rpSC===col)rpSD*=-1;else{rpSC=col;rpSD=-1;}clrSort('rp-tbl');th.classList.add(rpSD===1?'sort-asc':'sort-desc');rpD.sort((a,b)=>cmp(a,b,col,rpSD));document.getElementById('p-cnt').textContent=`${rpD.length} reliever${rpD.length===1?'':'s'}`;renderRP();}

function filterH(){
  const q=document.getElementById('h-search').value.toLowerCase().trim();
  hD=q?HITTERS.filter(h=>h.name.toLowerCase().includes(q)||h.team.toLowerCase().includes(q)||h.opp.toLowerCase().includes(q)):[...HITTERS];
  if(hSC)hD.sort((a,b)=>cmp(a,b,hSC,hSD));renderH();
}
function filterSP(){filterP();}
function filterRP(){filterP();}

hD.sort((a,b)=>cmp(a,b,'barrels',-1));
spD.sort((a,b)=>cmp(a,b,'ip_float',-1));
rpD.sort((a,b)=>cmp(a,b,'sv',-1));
document.querySelector('#h-tbl th[data-k="barrels"]')?.classList.add('sort-desc');
document.querySelector('#sp-tbl th[data-k="ip_float"]')?.classList.add('sort-desc');
document.querySelector('#rp-tbl th[data-k="sv"]')?.classList.add('sort-desc');
renderH();renderSP();renderRP();

// ── Team Alex ─────────────────────────────────────────────────────────────
let taHD=[...TA_HITTERS], taSPD=[...TA_STARTERS], taRPD=[...TA_RELIEVERS];
let taHSC='barrels', taHSD=-1, taSPSC='ip_float', taSPSD=-1, taRPSC='sv', taRPSD=-1;
let taHView='yday';  // 'yday' or 'season'

document.getElementById('ta-tc').textContent=TA_HITTERS.length+TA_STARTERS.length+TA_RELIEVERS.length;

// taNorm defined here; TA_LB/taLBD initialized below after LB_ALL is defined
function taNorm(s){
  return (s||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/\./g,'').trim();
}
// Placeholder declarations so srtTALB / renderTALB can reference them
let taLBD=[], taLBSC='hr', taLBSD=-1;

function showTAHView(view,btn){
  taHView=view;
  document.querySelectorAll('#teamalex-panel .toggle-group .tgl-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('ta-h-yday-wrap').style.display=view==='yday'?'':'none';
  document.getElementById('ta-h-season-wrap').style.display=view==='season'?'':'none';
  if(view==='season') renderTALB();
}

function renderTALB(){
  const tb=document.getElementById('ta-lb-body');
  if(!taLBD.length){
    tb.innerHTML='<tr><td colspan="21"><div class="empty"><div class="ico">📊</div><p>No season data for Team Alex yet.</p></div></td></tr>';return;
  }
  tb.innerHTML=taLBD.map(p=>`<tr>
    <td class="nm">${p.name} ${p.team?tm(p.team):''}${!p.qualified?'<span class="c-dim" style="font-size:.65rem;margin-left:4px">[NQ]</span>':''}</td>
    <td class="r">${fmtInt('r',   p.r)}</td>
    <td class="r">${fmtInt('hr',  p.hr)}</td>
    <td class="r">${fmtInt('rbi', p.rbi)}</td>
    <td class="r">${fmtSB(p)}</td>
    <td class="r">${fmtRate('obp',   p.obp)}</td>
    <td class="r">${fmtRate('woba',  p.woba)}</td>
    <td class="r">${fmtRate('xwoba', p.xwoba)}</td>
    <td class="r">${fmtPct('chase_pct',    p.chase_pct)}</td>
    <td class="r">${fmtPct('whiff_pct',    p.whiff_pct)}</td>
    <td class="r">${fmtPct('k_pct',        p.k_pct)}</td>
    <td class="r">${fmtInt('so',           p.so)}</td>
    <td class="r">${fmtPct('bb_pct',       p.bb_pct)}</td>
    <td class="r">${fmtPct('hard_hit_pct', p.hard_hit_pct)}</td>
    <td class="r">${fmtPct('barrel_pct',   p.barrel_pct)}</td>
    <td class="r">${fmtInt('barrels',      p.barrels)}</td>
    <td class="r">${fmtPct('sweet_spot_pct',p.sweet_spot_pct)}</td>
    <td class="r">${fmtEV( 'avg_ev',       p.avg_ev)}</td>
    <td class="r">${fmtEV( 'max_ev',       p.max_ev)}</td>
    <td class="r">${fmtSpd('bat_speed',    p.bat_speed)}</td>
    <td class="r">${fmtSpd('sprint_speed', p.sprint_speed)}</td>
  </tr>`).join('');
}

function srtTALB(th,col){
  if(taLBSC===col)taLBSD*=-1;else{taLBSC=col;taLBSD=LB_INV_SORT.has(col)?1:-1;}
  clrSort('ta-lb-tbl');th.classList.add(taLBSD===1?'sort-asc':'sort-desc');
  taLBD.sort((a,b)=>cmp(a,b,col,taLBSD));renderTALB();
}

function renderTASPLB(){
  const tb=document.getElementById('ta-sp-lb-body');
  if(!taSPLBD.length){
    tb.innerHTML='<tr><td colspan="21"><div class="empty"><div class="ico">📊</div><p>No season SP data for Team Alex yet.</p></div></td></tr>';return;
  }
  const D=plCellSP;
  tb.innerHTML=taSPLBD.map(p=>`<tr>
    <td class="nm">${p.name} ${p.team?tm(p.team):''}${!p.qualified?'<span class="c-dim" style="font-size:.65rem;margin-left:4px">[NQ]</span>':''}</td>
    <td class="r">${D('ip_f',p.ip_f,p.ip_f!=null?p.ip_f.toFixed(1):null)}</td>
    <td class="r">${D('w',p.w,p.w)}</td>
    <td class="r">${D('era',p.era,p.era!=null?p.era.toFixed(2):null)}</td>
    <td class="r">${D('whip',p.whip,p.whip!=null?p.whip.toFixed(2):null)}</td>
    <td class="r">${D('xera',p.xera,p.xera!=null?p.xera.toFixed(2):null)}</td>
    <td class="r">${D('siera',p.siera,p.siera!=null?p.siera.toFixed(2):null)}</td>
    <td class="r">${D('stuff_plus',p.stuff_plus,p.stuff_plus)}</td>
    <td class="r">${D('loc_plus',p.loc_plus,p.loc_plus)}</td>
    <td class="r">${D('k',p.k,p.k)}</td>
    <td class="r">${p.k_pct!=null?D('k_pct',p.k_pct,p.k_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.bb_pct!=null?D('bb_pct',p.bb_pct,p.bb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.chase_pct!=null?D('chase_pct',p.chase_pct,p.chase_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.whiff_pct!=null?D('whiff_pct',p.whiff_pct,p.whiff_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.barrel_pct!=null?D('barrel_pct',p.barrel_pct,p.barrel_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.hard_hit_pct!=null?D('hard_hit_pct',p.hard_hit_pct,p.hard_hit_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.gb_pct!=null?D('gb_pct',p.gb_pct,p.gb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.woba!=null?D('woba',p.woba,p.woba.toFixed(3).replace('0.','.')):D2()}</td>
    <td class="r">${p.xwoba!=null?D('xwoba',p.xwoba,p.xwoba.toFixed(3).replace('0.','.')):D2()}</td>
    <td class="r">${D('avg_ev',p.avg_ev,p.avg_ev!=null?p.avg_ev.toFixed(1):null)}</td>
    <td class="r">${D('fb_velo',p.fb_velo,p.fb_velo!=null?p.fb_velo.toFixed(1):null)}</td>
  </tr>`).join('');
}

function srtTASPLB(th,col){
  if(taSPLBSC===col)taSPLBSD*=-1;else{taSPLBSC=col;taSPLBSD=PL_INV_SORT.has(col)?1:-1;}
  clrSort('ta-sp-lb-tbl');th.classList.add(taSPLBSD===1?'sort-asc':'sort-desc');
  taSPLBD.sort((a,b)=>cmp(a,b,col,taSPLBSD));renderTASPLB();
}

function renderTARPLB(){
  const tb=document.getElementById('ta-rp-lb-body');
  if(!taRPLBD.length){
    tb.innerHTML='<tr><td colspan="23"><div class="empty"><div class="ico">📊</div><p>No season RP data for Team Alex yet.</p></div></td></tr>';return;
  }
  const D=plCellRP;
  tb.innerHTML=taRPLBD.map(p=>`<tr>
    <td class="nm">${p.name} ${p.team?tm(p.team):''}${!p.qualified?'<span class="c-dim" style="font-size:.65rem;margin-left:4px">[NQ]</span>':''}</td>
    <td class="r">${D('ip_f',p.ip_f,p.ip_f!=null?p.ip_f.toFixed(1):null)}</td>
    <td class="r">${D('w',p.w,p.w)}</td>
    <td class="r">${p.sv_opp>0?D('sv',p.sv,p.sv+'/'+p.sv_opp):D('sv',p.sv,p.sv)}</td>
    <td class="r">${D('hld',p.hld,p.hld)}</td>
    <td class="r">${D('era',p.era,p.era!=null?p.era.toFixed(2):null)}</td>
    <td class="r">${D('whip',p.whip,p.whip!=null?p.whip.toFixed(2):null)}</td>
    <td class="r">${D('xera',p.xera,p.xera!=null?p.xera.toFixed(2):null)}</td>
    <td class="r">${D('siera',p.siera,p.siera!=null?p.siera.toFixed(2):null)}</td>
    <td class="r">${D('stuff_plus',p.stuff_plus,p.stuff_plus)}</td>
    <td class="r">${D('loc_plus',p.loc_plus,p.loc_plus)}</td>
    <td class="r">${D('k',p.k,p.k)}</td>
    <td class="r">${p.k_pct!=null?D('k_pct',p.k_pct,p.k_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.bb_pct!=null?D('bb_pct',p.bb_pct,p.bb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.chase_pct!=null?D('chase_pct',p.chase_pct,p.chase_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.whiff_pct!=null?D('whiff_pct',p.whiff_pct,p.whiff_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.barrel_pct!=null?D('barrel_pct',p.barrel_pct,p.barrel_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.hard_hit_pct!=null?D('hard_hit_pct',p.hard_hit_pct,p.hard_hit_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.gb_pct!=null?D('gb_pct',p.gb_pct,p.gb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.woba!=null?D('woba',p.woba,p.woba.toFixed(3).replace('0.','.')):D2()}</td>
    <td class="r">${p.xwoba!=null?D('xwoba',p.xwoba,p.xwoba.toFixed(3).replace('0.','.')):D2()}</td>
    <td class="r">${D('avg_ev',p.avg_ev,p.avg_ev!=null?p.avg_ev.toFixed(1):null)}</td>
    <td class="r">${D('fb_velo',p.fb_velo,p.fb_velo!=null?p.fb_velo.toFixed(1):null)}</td>
  </tr>`).join('');
}

function srtTARPLB(th,col){
  if(taRPLBSC===col)taRPLBSD*=-1;else{taRPLBSC=col;taRPLBSD=PL_INV_SORT.has(col)?1:-1;}
  clrSort('ta-rp-lb-tbl');th.classList.add(taRPLBSD===1?'sort-asc':'sort-desc');
  taRPLBD.sort((a,b)=>cmp(a,b,col,taRPLBSD));renderTARPLB();
}

function renderTAH(){
  const tb=document.getElementById('ta-h-body');
  document.getElementById('ta-h-tc').textContent=taHD.length;
  if(!taHD.length){
    tb.innerHTML='<tr><td colspan="11"><div class="empty"><div class="ico">😴</div><p>No Team Alex hitters appeared yesterday.</p></div></td></tr>';
    return;
  }
  tb.innerHTML=taHD.map(h=>`<tr>
    <td class="nm">${h.name}</td>
    <td>${tm(h.team)}</td>
    <td><span class="c-dim" style="font-size:.7rem">vs</span> ${tm(h.opp)}</td>
    <td class="r">${h.grand_slam&&h.hr>0?`<span style="color:#2ecc71;font-weight:700">${h.hr}</span>`:gl(h.hr,hL.hr)||fHR(h.hr)}</td>
    <td class="r">${gl(h.bb,hL.bb)||fBB_h(h.bb)}</td>
    <td class="r">${gl(h.k,hL.k)||fK_h(h.k)}</td>
    <td class="r">${gl(h.sb,hL.sb)||fSB(h.sb)}</td>
    <td class="r">${gl(h.sba,hL.sba)||(h.sba>0?`${h.sba}`:'0')}</td>
    <td class="r">${gl(h.hard_hits,hL.hard_hits)||fHrd(h.hard_hits)}</td>
    <td class="r">${gl(h.barrels,hL.barrels)||fBar(h.barrels)}</td>
    <td class="r">${gl(h.max_ev,hL.max_ev)||fEV(h.max_ev,h.bip)}</td>
  </tr>`).join('');
}

function renderTASP(){
  const tb=document.getElementById('ta-sp-body');
  document.getElementById('ta-sp-tc').textContent=taSPD.length;
  if(!taSPD.length){
    tb.innerHTML='<tr><td colspan="16"><div class="empty"><div class="ico">😴</div><p>No Team Alex starters pitched yesterday.</p></div></td></tr>';
    return;
  }
  tb.innerHTML=taSPD.map(p=>`<tr>
    <td class="nm">${p.name}</td>
    <td>${tm(p.team)}</td>
    <td><span class="c-dim" style="font-size:.7rem">vs</span> ${tm(p.opp)}</td>
    <td class="r">${glIP(p.ip_float,p.ip,spL.ip_float)||p.ip}</td>
    <td class="r">${glMin(p.hits,spMin.hits)||fH_p(p.hits)}</td>
    <td class="r">${glMin(p.r,spMin.r)||fR(p.r)}</td>
    <td class="r">${glMin(p.bb,spMin.bb)||fBB_p(p.bb)}</td>
    <td class="r">${gl(p.k,spL.k)||fK_p(p.k)}</td>
    <td class="r">${gl(p.w,spL.w)||(p.w>0?`${p.w}`:'0')}</td>
    <td class="r">${gl(p.whiffs,spL.whiffs)||fWh(p.whiffs)}</td>
    <td class="r">${glMin(p.hard_hits,spMin.hard_hits)||fHrd(p.hard_hits)}</td>
    <td class="r">${glMin(p.barrels,spMin.barrels)||fBar(p.barrels)}</td>
    <td class="r">${gl(p.k_bb_pct,spL.k_bb_pct)||fKBB(p.k_bb_pct)}</td>
    <td class="r">${gl(p.stuff_plus,spL.stuff_plus)||fSP(p.stuff_plus)}</td>
    <td class="r">${gl(p.location_plus,spL.location_plus)||fLP(p.location_plus)}</td>
    <td>${pitchArsenal(p.pitch_types)}</td>
  </tr>`).join('');
}

function renderTARP(){
  const tb=document.getElementById('ta-rp-body');
  document.getElementById('ta-rp-tc').textContent=taRPD.length;
  if(!taRPD.length){
    tb.innerHTML='<tr><td colspan="18"><div class="empty"><div class="ico">😴</div><p>No Team Alex relievers pitched yesterday.</p></div></td></tr>';
    return;
  }
  tb.innerHTML=taRPD.map(p=>`<tr>
    <td class="nm">${p.name}</td>
    <td>${tm(p.team)}</td>
    <td><span class="c-dim" style="font-size:.7rem">vs</span> ${tm(p.opp)}</td>
    <td class="r">${glIP(p.ip_float,p.ip,rpL.ip_float)||p.ip}</td>
    <td class="r">${glMin(p.hits,rpMin.hits)||fH_p(p.hits)}</td>
    <td class="r">${glMin(p.r,rpMin.r)||fR(p.r)}</td>
    <td class="r">${glMin(p.bb,rpMin.bb)||fBB_p(p.bb)}</td>
    <td class="r">${gl(p.k,rpL.k)||fK_p(p.k)}</td>
    <td class="r">${gl(p.sv,rpL.sv)||(p.sv>0?`${p.sv}`:'0')}</td>
    <td class="r">${gl(p.hld,rpL.hld)||(p.hld>0?`${p.hld}`:'0')}</td>
    <td class="r">${gl(p.bs,rpL.bs)||(p.bs>0?`${p.bs}`:'0')}</td>
    <td class="r">${gl(p.w,rpL.w)||(p.w>0?`${p.w}`:'0')}</td>
    <td class="r">${gl(p.whiffs,rpL.whiffs)||fWh(p.whiffs)}</td>
    <td class="r">${glMin(p.hard_hits,rpMin.hard_hits)||fHrd(p.hard_hits)}</td>
    <td class="r">${glMin(p.barrels,rpMin.barrels)||fBar(p.barrels)}</td>
    <td class="r">${gl(p.stuff_plus,rpL.stuff_plus)||fSP(p.stuff_plus)}</td>
    <td class="r">${gl(p.location_plus,rpL.location_plus)||fLP(p.location_plus)}</td>
    <td>${pitchArsenal(p.pitch_types)}</td>
  </tr>`).join('');
}

function srtTA(th,type,col){
  if(type==='h'){
    if(taHSC===col)taHSD*=-1;else{taHSC=col;taHSD=-1;}
    clrSort('ta-h-tbl');th.classList.add(taHSD===1?'sort-asc':'sort-desc');
    taHD.sort((a,b)=>cmp(a,b,col,taHSD));renderTAH();
  } else if(type==='sp'){
    if(taSPSC===col)taSPSD*=-1;else{taSPSC=col;taSPSD=-1;}
    clrSort('ta-sp-tbl');th.classList.add(taSPSD===1?'sort-asc':'sort-desc');
    taSPD.sort((a,b)=>cmp(a,b,col,taSPSD));renderTASP();
  } else {
    if(taRPSC===col)taRPSD*=-1;else{taRPSC=col;taRPSD=-1;}
    clrSort('ta-rp-tbl');th.classList.add(taRPSD===1?'sort-asc':'sort-desc');
    taRPD.sort((a,b)=>cmp(a,b,col,taRPSD));renderTARP();
  }
}

taHD.sort((a,b)=>cmp(a,b,'barrels',-1));
taSPD.sort((a,b)=>cmp(a,b,'ip_float',-1));
taRPD.sort((a,b)=>cmp(a,b,'sv',-1));
renderTAH();renderTASP();renderTARP();

function showTASPView(view,btn){
  document.querySelectorAll('#ta-sp-yday-btn,#ta-sp-season-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('ta-sp-yday-wrap').style.display=view==='yday'?'':'none';
  document.getElementById('ta-sp-season-wrap').style.display=view==='season'?'':'none';
  if(view==='season') renderTASPLB();
}
function showTARPView(view,btn){
  document.querySelectorAll('#ta-rp-yday-btn,#ta-rp-season-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('ta-rp-yday-wrap').style.display=view==='yday'?'':'none';
  document.getElementById('ta-rp-season-wrap').style.display=view==='season'?'':'none';
  if(view==='season') renderTARPLB();
}

// ── Season Leaderboard data ────────────────────────────────────────────────
const LB_ALL  = __LB_JSON__;
const LB_QUAL = LB_ALL.filter(p=>p.qualified);
const LB_SP_ALL  = __LB_SP_JSON__;
const LB_SP_QUAL = LB_SP_ALL.filter(p=>p.qualified);
const LB_RP_ALL  = __LB_RP_JSON__;
const LB_RP_QUAL = LB_RP_ALL.filter(p=>p.qualified);

// Now that LB_ALL/LB_SP_ALL/LB_RP_ALL are defined, initialize TA season data
const TA_LB    = LB_ALL.filter(p=>TA_ROSTER_NORMS.has(taNorm(p.name)));
taLBD = [...TA_LB];
const TA_SP_LB = LB_SP_ALL.filter(p=>TA_ROSTER_NORMS.has(taNorm(p.name)));
const TA_RP_LB = LB_RP_ALL.filter(p=>TA_ROSTER_NORMS.has(taNorm(p.name)));
let taSPLBD=[...TA_SP_LB], taSPLBSC='ip_f', taSPLBSD=-1;
let taRPLBD=[...TA_RP_LB], taRPLBSC='sv',   taRPLBSD=-1;

document.getElementById('lb-tc').textContent = LB_QUAL.length;

// ── Generic rank-color engine ──────────────────────────────────────────────
function buildCfg(cfg_obj, qual_arr){
  Object.keys(cfg_obj).forEach(col=>{
    const cfg = cfg_obj[col];
    const vals = qual_arr.map(p=>p[col]).filter(v=>v!==null&&v!==undefined&&!isNaN(v));
    cfg.sorted = [...vals].sort((a,b)=>cfg.inv?a-b:b-a);
    cfg.vals   = vals;
    cfg.best   = cfg.sorted.length ? cfg.sorted[0] : null;
  });
}

function mkRankColor(cfg_obj, col, val){
  if(val===null||val===undefined) return null;
  const cfg = cfg_obj[col];
  if(!cfg||!cfg.sorted||!cfg.sorted.length) return null;
  if(val===cfg.best) return '#f0c040';
  const better = cfg.inv
    ? cfg.vals.filter(v=>v<val-0.00001).length
    : cfg.vals.filter(v=>v>val+0.00001).length;
  const total = cfg.vals.length;
  if(total<=1) return null;
  const t = better/(total-1);
  // Vivid red (best) → vivid blue (worst), bright on dark bg
  const r=Math.round(255*(1-t)+50*t);
  const g=Math.round(75*(1-t)+115*t);
  const b=Math.round(55*(1-t)+255*t);
  return `rgb(${r},${g},${b})`;
}

// ── Hitter leaderboard column config ──────────────────────────────────────
// inv=true → lower is better (for hitters)
const LB_COL_CFG = {
  r:             {inv:false}, hr:            {inv:false}, rbi:          {inv:false},
  sb:            {inv:false}, obp:           {inv:false}, woba:         {inv:false},
  xwoba:         {inv:false}, chase_pct:     {inv:true},  whiff_pct:    {inv:true},
  k_pct:         {inv:true},  so:            {inv:true},  bb_pct:       {inv:false},
  hard_hit_pct:  {inv:false}, barrel_pct:    {inv:false}, barrels:      {inv:false},
  sweet_spot_pct:{inv:false}, avg_ev:        {inv:false}, max_ev:       {inv:false},
  bat_speed:     {inv:false}, sprint_speed:  {inv:false},
};
buildCfg(LB_COL_CFG, LB_QUAL);

function lbRankColor(col, val){ return mkRankColor(LB_COL_CFG, col, val); }

// ── Pitcher leaderboard column configs ────────────────────────────────────
// Note: Chase% and Whiff% NOT inverted for pitchers (higher = better for pitcher)
const PL_SP_COL_CFG = {
  ip_f:{inv:false}, w:{inv:false},
  era:{inv:true},  whip:{inv:true},  xera:{inv:true},  siera:{inv:true},
  stuff_plus:{inv:false}, loc_plus:{inv:false},
  k:{inv:false}, k_pct:{inv:false}, bb_pct:{inv:true},
  chase_pct:{inv:false}, whiff_pct:{inv:false},
  barrel_pct:{inv:true}, hard_hit_pct:{inv:true}, gb_pct:{inv:false},
  woba:{inv:true}, xwoba:{inv:true}, avg_ev:{inv:true}, fb_velo:{inv:false},
};
const PL_RP_COL_CFG = {
  ip_f:{inv:false}, w:{inv:false}, sv:{inv:false}, hld:{inv:false},
  era:{inv:true},  whip:{inv:true},  xera:{inv:true},  siera:{inv:true},
  stuff_plus:{inv:false}, loc_plus:{inv:false},
  k:{inv:false}, k_pct:{inv:false}, bb_pct:{inv:true},
  chase_pct:{inv:false}, whiff_pct:{inv:false},
  barrel_pct:{inv:true}, hard_hit_pct:{inv:true}, gb_pct:{inv:false},
  woba:{inv:true}, xwoba:{inv:true}, avg_ev:{inv:true}, fb_velo:{inv:false},
};
buildCfg(PL_SP_COL_CFG, LB_SP_QUAL);
buildCfg(PL_RP_COL_CFG, LB_RP_QUAL);

// ── Inverted sort sets ─────────────────────────────────────────────────────
// For pitchers, bb_pct/barrel_pct/hard_hit_pct/woba/xwoba/avg_ev/era/xera/siera/whip = first click ascending
const PL_INV_SORT = new Set(['era','whip','xera','siera','bb_pct','barrel_pct','hard_hit_pct','woba','xwoba','avg_ev']);

// ── Shared display helpers ─────────────────────────────────────────────────
const D2=()=>'<span class="c-dim">—</span>';

function lbCell(col, val, dispVal){
  if(val===null||val===undefined) return D2();
  const color=lbRankColor(col,val);
  const fw=color?';font-weight:600':'';
  const style=color?` style="color:${color}${fw}"`:'';
  return `<span${style}>${dispVal!==undefined?dispVal:val}</span>`;
}
function plCellSP(col, val, disp){
  if(val===null||val===undefined) return D2();
  const color=mkRankColor(PL_SP_COL_CFG, col, val);
  const fw=color?';font-weight:600':'';
  const style=color?` style="color:${color}${fw}"`:'';
  return `<span${style}>${disp!==undefined?disp:val}</span>`;
}
function plCellRP(col, val, disp){
  if(val===null||val===undefined) return D2();
  const color=mkRankColor(PL_RP_COL_CFG, col, val);
  const fw=color?';font-weight:600':'';
  const style=color?` style="color:${color}${fw}"`:'';
  return `<span${style}>${disp!==undefined?disp:val}</span>`;
}

function fmtPct(col,v){return v==null?D2():lbCell(col,v,v.toFixed(1)+'%');}
function fmtEV(col,v){return v==null?D2():lbCell(col,v,v.toFixed(1));}
function fmtSpd(col,v){return v==null?D2():lbCell(col,v,v.toFixed(1));}
function fmtInt(col,v){return v==null?D2():lbCell(col,v,v);}
function fmtRate(col,v){
  if(v==null) return D2();
  const s=v.toFixed(3).replace('0.','.');
  const color=lbRankColor(col,v);
  const style=color?` style="color:${color};font-weight:600"`:'';
  return `<span${style}>${s}</span>`;
}
function fmtSB(p){
  const color=lbRankColor('sb',p.sb);
  const style=color?` style="color:${color};font-weight:600"`:'';
  if(p.sba>0) return `<span${style}>${p.sb}</span><span class="c-dim" style="font-size:.68rem">/${p.sba}</span>`;
  return `<span${style}>${p.sb}</span>`;
}

// ── Leaderboard type toggle ────────────────────────────────────────────────
let lbType='h';
function showLBType(type, btn){
  lbType=type;
  document.querySelectorAll('#leaderboard-panel .toggle-group .tgl-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('lb-h-wrap').style.display  = type==='h'  ? '' : 'none';
  document.getElementById('lb-sp-wrap').style.display = type==='sp' ? '' : 'none';
  document.getElementById('lb-rp-wrap').style.display = type==='rp' ? '' : 'none';
  const cnt = type==='h' ? LB_QUAL.length : type==='sp' ? LB_SP_QUAL.length : LB_RP_QUAL.length;
  document.getElementById('lb-tc').textContent = cnt;
  if(type==='sp') renderLBSP();
  if(type==='rp') renderLBRP();
}

// ── Hitter leaderboard ─────────────────────────────────────────────────────
let lbD=[...LB_QUAL], lbSC='hr', lbSD=-1;

function renderLB(){
  const tb=document.getElementById('lb-body');
  const ct=document.getElementById('lb-cnt');
  if(!lbD.length){
    tb.innerHTML='<tr><td colspan="22"><div class="empty"><div class="ico">📊</div><p>No leaderboard data yet.</p></div></td></tr>';
    ct.textContent='';return;
  }
  ct.textContent=`${lbD.length} player${lbD.length===1?'':'s'}`;
  tb.innerHTML=lbD.map(p=>`<tr>
    <td class="nm">${p.name} ${p.team?tm(p.team):''}${!p.qualified?'<span class="c-dim" style="font-size:.65rem;margin-left:4px">[NQ]</span>':''}</td>
    <td class="r">${fmtInt('r',   p.r)}</td>
    <td class="r">${fmtInt('hr',  p.hr)}</td>
    <td class="r">${fmtInt('rbi', p.rbi)}</td>
    <td class="r">${fmtSB(p)}</td>
    <td class="r">${fmtRate('obp',   p.obp)}</td>
    <td class="r">${fmtRate('woba',  p.woba)}</td>
    <td class="r">${fmtRate('xwoba', p.xwoba)}</td>
    <td class="r">${fmtPct('chase_pct',    p.chase_pct)}</td>
    <td class="r">${fmtPct('whiff_pct',    p.whiff_pct)}</td>
    <td class="r">${fmtPct('k_pct',        p.k_pct)}</td>
    <td class="r">${fmtInt('so',           p.so)}</td>
    <td class="r">${fmtPct('bb_pct',       p.bb_pct)}</td>
    <td class="r">${fmtPct('hard_hit_pct', p.hard_hit_pct)}</td>
    <td class="r">${fmtPct('barrel_pct',   p.barrel_pct)}</td>
    <td class="r">${fmtInt('barrels',      p.barrels)}</td>
    <td class="r">${fmtPct('sweet_spot_pct',p.sweet_spot_pct)}</td>
    <td class="r">${fmtEV( 'avg_ev',       p.avg_ev)}</td>
    <td class="r">${fmtEV( 'max_ev',       p.max_ev)}</td>
    <td class="r">${fmtSpd('bat_speed',    p.bat_speed)}</td>
    <td class="r">${fmtSpd('sprint_speed', p.sprint_speed)}</td>
  </tr>`).join('');
}

function filterLB(){
  const q   = document.getElementById('lb-search').value.toLowerCase().trim();
  const qual = document.getElementById('lb-qual-chk').checked;
  document.getElementById('lb-qual-lbl').style.opacity = q ? '0.4' : '1';
  let base = q ? LB_ALL : (qual ? LB_QUAL : LB_ALL);
  if(q) base = base.filter(p=>p.name.toLowerCase().includes(q)||(p.team||'').toLowerCase().includes(q));
  lbD=[...base];
  if(lbSC) lbD.sort((a,b)=>cmp(a,b,lbSC,lbSD));
  renderLB();
}

function srtLB(th,col){
  if(lbSC===col)lbSD*=-1;else{lbSC=col;lbSD=LB_INV_SORT.has(col)?1:-1;}
  clrSort('lb-tbl');th.classList.add(lbSD===1?'sort-asc':'sort-desc');
  lbD.sort((a,b)=>cmp(a,b,col,lbSD));renderLB();
}

lbD.sort((a,b)=>cmp(a,b,'hr',-1));
document.querySelector('#lb-tbl th[data-k="hr"]')?.classList.add('sort-desc');
renderLB();

// ── SP Leaderboard ─────────────────────────────────────────────────────────
let lbSpD=[...LB_SP_QUAL], lbSpSC='ip_f', lbSpSD=-1;

function renderLBSP(){
  const tb=document.getElementById('lb-sp-body');
  const ct=document.getElementById('lb-sp-cnt');
  if(!lbSpD.length){
    tb.innerHTML='<tr><td colspan="21"><div class="empty"><div class="ico">📊</div><p>No SP leaderboard data yet.</p></div></td></tr>';
    ct.textContent='';return;
  }
  ct.textContent=`${lbSpD.length} pitcher${lbSpD.length===1?'':'s'}`;
  const D=plCellSP;
  tb.innerHTML=lbSpD.map(p=>`<tr>
    <td class="nm">${p.name} ${p.team?tm(p.team):''}${!p.qualified?'<span class="c-dim" style="font-size:.65rem;margin-left:4px">[NQ]</span>':''}</td>
    <td class="r">${D('ip_f',        p.ip_f,        p.ip_f!=null?p.ip_f.toFixed(1):null)}</td>
    <td class="r">${D('w',           p.w,           p.w)}</td>
    <td class="r">${D('era',         p.era,         p.era!=null?p.era.toFixed(2):null)}</td>
    <td class="r">${D('whip',        p.whip,        p.whip!=null?p.whip.toFixed(2):null)}</td>
    <td class="r">${D('xera',        p.xera,        p.xera!=null?p.xera.toFixed(2):null)}</td>
    <td class="r">${D('siera',       p.siera,       p.siera!=null?p.siera.toFixed(2):null)}</td>
    <td class="r">${D('stuff_plus',  p.stuff_plus,  p.stuff_plus)}</td>
    <td class="r">${D('loc_plus',    p.loc_plus,    p.loc_plus)}</td>
    <td class="r">${D('k',           p.k,           p.k)}</td>
    <td class="r">${p.k_pct!=null?D('k_pct',p.k_pct,p.k_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.bb_pct!=null?D('bb_pct',p.bb_pct,p.bb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.chase_pct!=null?D('chase_pct',p.chase_pct,p.chase_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.whiff_pct!=null?D('whiff_pct',p.whiff_pct,p.whiff_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.barrel_pct!=null?D('barrel_pct',p.barrel_pct,p.barrel_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.hard_hit_pct!=null?D('hard_hit_pct',p.hard_hit_pct,p.hard_hit_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.gb_pct!=null?D('gb_pct',p.gb_pct,p.gb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.woba!=null?D('woba',p.woba,p.woba.toFixed(3).replace('0.','.')):D2()}</td>
    <td class="r">${p.xwoba!=null?D('xwoba',p.xwoba,p.xwoba.toFixed(3).replace('0.','.')):D2()}</td>
    <td class="r">${D('avg_ev',      p.avg_ev,      p.avg_ev!=null?p.avg_ev.toFixed(1):null)}</td>
    <td class="r">${D('fb_velo',     p.fb_velo,     p.fb_velo!=null?p.fb_velo.toFixed(1):null)}</td>
  </tr>`).join('');
}

function filterLBSP(){
  const q    = document.getElementById('lb-sp-search').value.toLowerCase().trim();
  const qual = document.getElementById('lb-sp-qual-chk').checked;
  document.getElementById('lb-sp-qual-lbl').style.opacity = q ? '0.4' : '1';
  let base = q ? LB_SP_ALL : (qual ? LB_SP_QUAL : LB_SP_ALL);
  if(q) base = base.filter(p=>p.name.toLowerCase().includes(q)||(p.team||'').toLowerCase().includes(q));
  lbSpD=[...base];
  if(lbSpSC) lbSpD.sort((a,b)=>cmp(a,b,lbSpSC,lbSpSD));
  renderLBSP();
}

function srtLBSP(th,col){
  if(lbSpSC===col)lbSpSD*=-1;else{lbSpSC=col;lbSpSD=PL_INV_SORT.has(col)?1:-1;}
  clrSort('lb-sp-tbl');th.classList.add(lbSpSD===1?'sort-asc':'sort-desc');
  lbSpD.sort((a,b)=>cmp(a,b,col,lbSpSD));renderLBSP();
}

// ── RP Leaderboard ─────────────────────────────────────────────────────────
let lbRpD=[...LB_RP_QUAL], lbRpSC='sv', lbRpSD=-1;

function renderLBRP(){
  const tb=document.getElementById('lb-rp-body');
  const ct=document.getElementById('lb-rp-cnt');
  if(!lbRpD.length){
    tb.innerHTML='<tr><td colspan="23"><div class="empty"><div class="ico">📊</div><p>No RP leaderboard data yet.</p></div></td></tr>';
    ct.textContent='';return;
  }
  ct.textContent=`${lbRpD.length} pitcher${lbRpD.length===1?'':'s'}`;
  const D=plCellRP;
  tb.innerHTML=lbRpD.map(p=>`<tr>
    <td class="nm">${p.name} ${p.team?tm(p.team):''}${!p.qualified?'<span class="c-dim" style="font-size:.65rem;margin-left:4px">[NQ]</span>':''}</td>
    <td class="r">${D('ip_f',        p.ip_f,        p.ip_f!=null?p.ip_f.toFixed(1):null)}</td>
    <td class="r">${D('w',           p.w,           p.w)}</td>
    <td class="r">${p.sv_opp>0?D('sv',p.sv,p.sv+'/'+p.sv_opp):D('sv',p.sv,p.sv)}</td>
    <td class="r">${D('hld',         p.hld,         p.hld)}</td>
    <td class="r">${D('era',         p.era,         p.era!=null?p.era.toFixed(2):null)}</td>
    <td class="r">${D('whip',        p.whip,        p.whip!=null?p.whip.toFixed(2):null)}</td>
    <td class="r">${D('xera',        p.xera,        p.xera!=null?p.xera.toFixed(2):null)}</td>
    <td class="r">${D('siera',       p.siera,       p.siera!=null?p.siera.toFixed(2):null)}</td>
    <td class="r">${D('stuff_plus',  p.stuff_plus,  p.stuff_plus)}</td>
    <td class="r">${D('loc_plus',    p.loc_plus,    p.loc_plus)}</td>
    <td class="r">${D('k',           p.k,           p.k)}</td>
    <td class="r">${p.k_pct!=null?D('k_pct',p.k_pct,p.k_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.bb_pct!=null?D('bb_pct',p.bb_pct,p.bb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.chase_pct!=null?D('chase_pct',p.chase_pct,p.chase_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.whiff_pct!=null?D('whiff_pct',p.whiff_pct,p.whiff_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.barrel_pct!=null?D('barrel_pct',p.barrel_pct,p.barrel_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.hard_hit_pct!=null?D('hard_hit_pct',p.hard_hit_pct,p.hard_hit_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.gb_pct!=null?D('gb_pct',p.gb_pct,p.gb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r">${p.woba!=null?D('woba',p.woba,p.woba.toFixed(3).replace('0.','.')):D2()}</td>
    <td class="r">${p.xwoba!=null?D('xwoba',p.xwoba,p.xwoba.toFixed(3).replace('0.','.')):D2()}</td>
    <td class="r">${D('avg_ev',      p.avg_ev,      p.avg_ev!=null?p.avg_ev.toFixed(1):null)}</td>
    <td class="r">${D('fb_velo',     p.fb_velo,     p.fb_velo!=null?p.fb_velo.toFixed(1):null)}</td>
  </tr>`).join('');
}

function filterLBRP(){
  const q    = document.getElementById('lb-rp-search').value.toLowerCase().trim();
  const qual = document.getElementById('lb-rp-qual-chk').checked;
  document.getElementById('lb-rp-qual-lbl').style.opacity = q ? '0.4' : '1';
  let base = q ? LB_RP_ALL : (qual ? LB_RP_QUAL : LB_RP_ALL);
  if(q) base = base.filter(p=>p.name.toLowerCase().includes(q)||(p.team||'').toLowerCase().includes(q));
  lbRpD=[...base];
  if(lbRpSC) lbRpD.sort((a,b)=>cmp(a,b,lbRpSC,lbRpSD));
  renderLBRP();
}

function srtLBRP(th,col){
  if(lbRpSC===col)lbRpSD*=-1;else{lbRpSC=col;lbRpSD=PL_INV_SORT.has(col)?1:-1;}
  clrSort('lb-rp-tbl');th.classList.add(lbRpSD===1?'sort-asc':'sort-desc');
  lbRpD.sort((a,b)=>cmp(a,b,col,lbRpSD));renderLBRP();
}
</script>
</body>
</html>
"""

def render_html(date_display, ts, n_games, hitters, all_pitchers,
                ta_hitters, ta_starters, ta_relievers,
                lb_data=None, lb_pitch_data=None):
    # Add is_starter flag to all pitchers for client-side filtering
    starters = []
    relievers = []
    for p in all_pitchers:
        p_copy = p.copy()
        p_copy["is_starter"] = p_copy.get("ip_float", 0) >= 3
        if p_copy["is_starter"]:
            starters.append(p_copy)
        else:
            relievers.append(p_copy)

    lb_sp = (lb_pitch_data or {}).get("starters", [])
    lb_rp = (lb_pitch_data or {}).get("relievers", [])

    return (HTML_TEMPLATE
        .replace("__DATE_DISPLAY__", date_display)
        .replace("__N_GAMES__", str(n_games))
        .replace("__TS__", ts)
        .replace("__HITTERS_JSON__",  json.dumps(hitters,        default=str))
        .replace("__ALL_PITCHERS_JSON__", json.dumps(all_pitchers, default=str))
        .replace("__TA_H_JSON__",     json.dumps(ta_hitters,    default=str))
        .replace("__TA_SP_JSON__",    json.dumps(ta_starters,   default=str))
        .replace("__TA_RP_JSON__",    json.dumps(ta_relievers,  default=str))
        .replace("__LB_JSON__",       json.dumps(lb_data or [],  default=str))
        .replace("__LB_SP_JSON__",    json.dumps(lb_sp,          default=str))
        .replace("__LB_RP_JSON__",    json.dumps(lb_rp,          default=str))
        .replace("__TA_NAMES_JSON__", json.dumps(sorted(TEAM_ALEX_NAMES)))
    )


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    import sys
    from zoneinfo import ZoneInfo
    _et = ZoneInfo("America/New_York")
    # Accept explicit date arg (YYYY-MM-DD) for manual runs; otherwise use Eastern Time
    if len(sys.argv) > 1:
        yesterday = sys.argv[1]
    else:
        yesterday = (datetime.now(_et).date() - timedelta(days=1)).strftime("%Y-%m-%d")
    _yd          = datetime.strptime(yesterday, "%Y-%m-%d")
    date_display = _yd.strftime("%A, %B ") + str(_yd.day) + _yd.strftime(", %Y")
    _now         = datetime.now(_et)
    ts           = (_now.strftime("%b ") + str(_now.day) + _now.strftime(", %Y ")
                    + _now.strftime("%I:%M %p").lstrip("0"))
    year         = _yd.year

    print(f"\n{'='*55}")
    print(f"  MLB Daily Stats · {date_display}")
    print(f"{'='*55}\n")

    print("[ 1/6 ] Statcast")
    df = fetch_statcast(yesterday)
    if df.empty:
        print("No game data — dashboard not updated.")
        return
    starters = identify_starters(df)
    print(f"  Starters identified: {len(starters)}")

    print("\n[ 2/6 ] Stolen Bases (MLB Stats API)")
    sb_map = fetch_mlb_sb(yesterday)

    print("\n[ 2b/6 ] Pitcher box score data (W/SV/HLD/BS)")
    box_data = fetch_pitcher_box_data(yesterday)

    print("\n[ 3/6 ] Aggregating stats")
    hitters  = build_hitter_stats(df, sb_map)
    all_pitchers = build_pitcher_stats(df, set(df["pitcher"].unique()), box_data)
    print(f"  {len(hitters)} batter rows · {len(all_pitchers)} pitcher rows")

    print("\n[ 4/6 ] Player lookup")
    all_ids = [h["id"] for h in hitters] + [p["id"] for p in all_pitchers]
    p_info  = get_player_info(all_ids)
    for h in hitters:
        h["name"] = p_info.get(h["id"], {}).get("name", f"Player #{h['id']}")
    for p in all_pitchers:
        p["name"] = p_info.get(p["id"], {}).get("name", f"Player #{p['id']}")

    print("\n[ 5/6 ] Stuff+")
    # Source 0: stuff_plus_stuff_avg column from Statcast pitch-by-pitch (rarely populated)
    attached_sc = sum(1 for p in all_pitchers if p["stuff_plus"] is not None)
    print(f"  Stuff+ from Statcast pitch columns: {attached_sc}/{len(all_pitchers)} pitcher(s)")

    # Season velocity from Baseball Savant (used by all code paths below)
    pitcher_mlbam_ids = set(p["id"] for p in all_pitchers)
    savant_velo = fetch_savant_season_velo(year, pitcher_mlbam_ids)

    # Source 1: FanGraphs game-log API
    # Always tries — uses cookie file if present, then Playwright stealth as fallback.
    # Playwright launches a visible Chrome window (~9 s) to pass Cloudflare's JS challenge.
    has_cookie = bool(_load_fg_cookie())
    print(f"  FanGraphs: {'cookie file found + ' if has_cookie else ''}trying Playwright…")
    # Fetch FanGraphs season leaderboard to build name→fg_id map.
    # This is the key fallback for newer players (e.g. 2023-2025 debuts) whose
    # FG IDs are missing from pybaseball's Chadwick register.
    fg_velo_dict, fg_name_to_fgid, fg_mlbam_to_fgid = fetch_fg_season_velo(year)
    print(f"  FanGraphs season leaderboard: {len(fg_name_to_fgid)} name→ID, {len(fg_mlbam_to_fgid)} MLBAM→ID mappings")

    # Patch p_info: fill in FG IDs for players whose Chadwick key_fangraphs is blank,
    # using the xMLBAMID→playerid mapping extracted from the FanGraphs leaderboard.
    # This promotes those pitchers from priority-3 (CF-blocked name search) to
    # priority-1 (known FG ID), so they're processed before Cloudflare rate-limits kick in.
    leaderboard_patched = 0
    for mlbam, fg_id in fg_mlbam_to_fgid.items():
        if mlbam in p_info and p_info[mlbam].get("fg_id") is None:
            p_info[mlbam]["fg_id"] = fg_id
            leaderboard_patched += 1
    if leaderboard_patched:
        print(f"  Patched {leaderboard_patched} missing FG ID(s) via leaderboard xMLBAMID column")

    game_stuff = fetch_fg_game_stuff(yesterday, year, all_pitchers, p_info, fg_name_to_fgid)
    attach_fg_data(all_pitchers, p_info, game_stuff, fg_velo_dict, savant_velo)
    attached_fg = sum(1 for p in all_pitchers if p["stuff_plus"] is not None)
    print(f"  Stuff+ attached: {attached_fg}/{len(all_pitchers)} pitcher(s)")

    print("\n[ 5b/6 ] Team Alex roster filter")
    ta_hitters  = [h for h in hitters if ta_norm(h["name"]) in TEAM_ALEX_NAMES]
    ta_all_pitchers = [p for p in all_pitchers if ta_norm(p["name"]) in TEAM_ALEX_NAMES]
    ta_starters = [p for p in ta_all_pitchers if p.get("ip_float", 0) >= 3]
    ta_relievers = [p for p in ta_all_pitchers if p.get("ip_float", 0) < 3]
    print(f"  Team Alex: {len(ta_hitters)} hitter(s), {len(ta_starters)} starter(s), {len(ta_relievers)} reliever(s) played yesterday")

    print("\n[ 5c/6 ] Season batting leaderboard")
    lb_data = fetch_season_batting_leaderboard(year)

    print("\n[ 5d/6 ] Season pitching leaderboard")
    lb_pitch_data = fetch_season_pitching_leaderboard(year)

    print("\n[ 6/6 ] Rendering HTML")
    hitters.sort(key=lambda x: (x["barrels"], x["hard_hits"]), reverse=True)
    all_pitchers.sort(key=lambda x: x["ip_float"], reverse=True)

    n_games = df["game_pk"].nunique()
    html    = render_html(date_display, ts, n_games, hitters, all_pitchers,
                          ta_hitters, ta_starters, ta_relievers,
                          lb_data, lb_pitch_data)
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mlb_daily_stats.html")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)

    print(f"\n✅  Dashboard saved → {out_path}")
    starters = [p for p in all_pitchers if p.get("ip_float", 0) >= 3]
    relievers = [p for p in all_pitchers if p.get("ip_float", 0) < 3]
    print(f"    {n_games} game(s) · {len(hitters)} batters · {len(starters)} starters · {len(relievers)} relievers\n")

    _close_pw()   # shut down Chromium

    # ── Firebase deploy (runs automatically if Firebase CLI is installed) ──
    script_dir = os.path.dirname(os.path.abspath(__file__))
    firebase_rc = os.path.join(script_dir, ".firebaserc")
    if os.path.exists(firebase_rc):
        print("[ Firebase ] Deploying to Firebase Hosting…")
        try:
            result = subprocess.run(
                ["firebase", "deploy", "--only", "hosting", "--non-interactive"],
                cwd=script_dir, capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                # Extract hosting URL from output
                for line in result.stdout.splitlines():
                    if "web.app" in line or "firebaseapp.com" in line:
                        print(f"  ✅ Live at: {line.strip()}")
                        break
                else:
                    print("  ✅ Firebase deploy successful")
            else:
                print(f"  ⚠️  Firebase deploy failed: {result.stderr[:300]}")
        except FileNotFoundError:
            print("  Firebase CLI not found — skipping deploy")
            print("  (Run 'npm install -g firebase-tools' to enable auto-deploy)")
        except Exception as e:
            print(f"  Firebase deploy error: {e}")


if __name__ == "__main__":
    main()