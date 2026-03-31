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

import subprocess, sys, os, json, unicodedata
from datetime import date, timedelta, datetime

# ── Auto-install (skipped on GitHub Actions / CI where requirements.txt is used) ──
if not os.environ.get("SKIP_AUTO_INSTALL"):
    print("Checking dependencies…")
    for _pkg in ("pybaseball", "pandas", "numpy", "requests", "MLB-StatsAPI", "playwright", "playwright-stealth"):
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", _pkg, "--break-system-packages", "-q"],
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
        page.wait_for_timeout(6_000)
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


def _pw_fetch_json(url: str, params: dict | None = None) -> object:
    """
    Fetch JSON from a FanGraphs API endpoint.

    Priority order:
      1. fg_cookie.txt  — user-supplied cf_clearance cookie (most reliable)
      2. Playwright      — headless+stealth Chrome (automatic but may be blocked)

    Returns parsed JSON or None on failure.
    """
    from urllib.parse import urlencode
    full_url = (url + "?" + urlencode(params)) if params else url

    # ── Option C: manual cookie file ─────────────────────────────────────
    cf_cookie = _load_fg_cookie()
    if cf_cookie:
        try:
            sess = _fg_session_from_cookie(cf_cookie)
            r = sess.get(full_url, timeout=20)
            if r.status_code == 200:
                return r.json()
            print(f"    fg_cookie.txt returned {r.status_code} — "
                  f"cookie may have expired, refresh fg_cookie.txt")
        except Exception as e:
            print(f"    Cookie request error: {e}")
        return None   # don't fall through to Playwright if cookie file exists

    # ── Option B: Playwright stealth browser ──────────────────────────────
    page = _get_pw_page()
    if page is None:
        return None
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
        if isinstance(result, dict) and "_err" in result:
            print(f"    FanGraphs fetch error: {result['_err']} — {full_url}")
            return None
        return result
    except Exception as e:
        print(f"    Playwright evaluate error: {e}")
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
    """Extract the list of game rows from a FanGraphs game-log API response."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("mlbgamelog", "data", "gamelog", "games", "playerGameLog"):
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
    stuff_col = next((c for c in sample
                      if c in ("Stuff+", "Stuff+ (pfx)", "xStuff+", "SP_stuff",
                                "stuff_plus", "stuffplus")), None)
    if not stuff_col:
        stuff_col = next((c for c in sample if _is_overall(c, "stuff")), None)

    loc_col = next((c for c in sample
                    if c in ("Location+", "Location+ (pfx)", "xLocation+",
                              "SP_loc", "location_plus")), None)
    if not loc_col:
        loc_col = next((c for c in sample
                        if _is_overall(c, "location")), None)

    return stuff_col, loc_col

def _fg_search_player_id(name: str) -> int | None:
    """
    Query FanGraphs player search API to get a player's numeric playerid.
    Used when pybaseball's ID is missing or stale.
    """
    try:
        raw = _pw_fetch_json(
            _FG_SEARCH,
            {"pos": "all", "stats": "pit", "q": name,
             "type": "data", "season": ""},
        )
        if raw is None:
            return None
        players = raw if isinstance(raw, list) else raw.get("data", raw.get("players", []))
        for p in players:
            for k in ("playerid", "id", "PlayerID", "fgid"):
                pid = p.get(k)
                if pid:
                    try:
                        return int(float(pid))
                    except Exception:
                        pass
    except Exception:
        pass
    return None

def _call_game_log(player_id, year: int) -> list:
    """
    Fetch a pitcher's season game log (Stuff+ type) via Playwright.
    Runs fetch() inside the real Chromium browser so Cloudflare is bypassed.
    Returns list of game rows, or [].
    """
    for type_val in ("stuff", "stuffplus"):
        data = _pw_fetch_json(
            _GL_URL,
            {"playerid": player_id, "position": "P",
             "type": type_val, "season": year, "z": 0},
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

    for p in pitchers:
        mlbam = p["id"]
        info  = p_info.get(mlbam, {})
        fg_id = info.get("fg_id")
        name  = info.get("name", f"#{mlbam}")
        nm    = norm_name(name)

        # Build ordered list of candidate player IDs to try
        candidates: list = []
        if fg_id:
            candidates.append(("pybaseball", fg_id))
        nm_fgid = name_to_fgid.get(nm)
        if nm_fgid and nm_fgid != fg_id:
            candidates.append(("velo_map", nm_fgid))
        candidates.append(("mlbam", mlbam))    # MLBAM sometimes accepted by FG

        rows      = []
        found_id  = None
        for src, cand_id in candidates:
            rows = _call_game_log(cand_id, year)
            if rows:
                found_id = cand_id
                print(f"    {name}: got {len(rows)} game-log rows via {src} id={cand_id}")
                break

        # If all pre-known IDs failed, try FanGraphs name search as last resort
        if not rows:
            searched_id = _fg_search_player_id(name)
            if searched_id and searched_id not in [c[1] for c in candidates]:
                rows = _call_game_log(searched_id, year)
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
    Returns (velo_dict, name_to_fgid) where:
      velo_dict   = {norm_name | fg_id: {pitch_code: avg_velo}}
      name_to_fgid= {norm_name: fg_id}   (useful for pitchers missing from pybaseball)
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
    if not rows:
        print("    Could not retrieve season velocity.")
        return {}, {}

    sample_v = rows[0]
    matched_vcols = [c for c in sample_v if c in FG_VELO_COL_MAP]
    print(f"    Season velo: {len(rows)} rows, matched velo columns: {matched_vcols or 'none'}")

    velo_dict    = {}
    name_to_fgid = {}
    for row in rows:
        name  = row.get("PlayerName", row.get("Name", "")).strip()
        fg_id = row.get("playerid")
        if name and fg_id:
            try:
                name_to_fgid[norm_name(name)] = int(float(fg_id))
            except Exception:
                pass
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
    return velo_dict, name_to_fgid

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

def get_player_info(ids: list) -> dict:
    if not ids:
        return {}
    result = {}
    try:
        lkp = pybaseball.playerid_reverse_lookup(list(set(ids)), key_type="mlbam")
        for _, r in lkp.iterrows():
            mlbam  = int(r["key_mlbam"])
            raw_fg = r.get("key_fangraphs")
            fg_id  = int(float(raw_fg)) if pd.notna(raw_fg) else None
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
  --green:#2ecc71;--orange:#e67e22;--blue:#3498db;--red:#e74c3c;
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
tbody tr:hover{background:rgba(255,255,255,.04)}
tbody tr:nth-child(even){background:rgba(255,255,255,.017)}
tbody td{padding:8px 9px;vertical-align:middle}
tbody td.r{text-align:right;font-variant-numeric:tabular-nums}
td.nm{font-weight:600;white-space:nowrap;color:#fff;font-size:.83rem}
.tm{display:inline-block;background:rgba(255,255,255,.06);border:1px solid var(--border);
  border-radius:4px;padding:1px 6px;font-size:.65rem;font-weight:800;
  letter-spacing:.5px;color:#9bbcd0;white-space:nowrap;}
.c-barrel{color:var(--gold);font-weight:700}
.c-great{color:var(--green);font-weight:600}
.c-good{color:#7dcea0}
.c-warn{color:var(--orange)}
.c-neg{color:var(--red)}
.c-dim{color:#3d5264}
.c-blue{color:#5dade2}
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
.sv{color:#7fb3d3;font-size:.67rem}
.gs{color:#a0cfee}
.empty{text-align:center;padding:48px;color:var(--muted)}
.empty .ico{font-size:2.2rem;margin-bottom:8px}
footer{text-align:center;padding:18px;color:var(--muted);font-size:.69rem;
  border-top:1px solid var(--border);margin-top:40px;}
.ta-section-hdr{font-size:.82rem;font-weight:700;color:var(--muted);
  text-transform:uppercase;letter-spacing:.9px;padding:10px 2px 6px;
  border-bottom:1px solid var(--border);margin-bottom:10px;
  display:flex;align-items:center;gap:7px}
.tab-btn.ta-btn{color:#c9a227}
.tab-btn.ta-btn.active{color:#f0c040;border-bottom-color:#f0c040}
.tab-btn.ta-btn.active .tab-count{background:#b8860b}
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
  <button class="tab-btn" onclick="showTab('starters',this)">
    ⚾ Starting Pitchers <span class="tab-count" id="sp-tc">—</span>
  </button>
  <button class="tab-btn" onclick="showTab('relievers',this)">
    🔥 Relief Pitchers <span class="tab-count" id="rp-tc">—</span>
  </button>
  <button class="tab-btn ta-btn" onclick="showTab('teamalex',this)">
    👑 Team Alex <span class="tab-count" id="ta-tc">—</span>
  </button>
</div>

<main>

<!-- ══ HITTERS ══ -->
<div id="hitters-panel" class="tab-panel active">
  <div class="legend">
    <div class="leg-item"><span class="leg-dot" style="background:var(--gold)"></span>Leader in category</div>
    <div class="leg-item"><span class="leg-dot" style="background:#e74c3c"></span>Max EV: high (red) → low (blue)</div>
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

<!-- ══ STARTING PITCHERS ══ -->
<div id="starters-panel" class="tab-panel">
  <div class="note">
    ⓘ &nbsp;<strong>Stuff+</strong> and <strong>Loc+</strong> are season averages from Baseball Savant (game-level unavailable — FanGraphs is Cloudflare-blocked).
    Arsenal: game velocity <span class="vd">(season avg)</span> —
    fastball shown in <span style="color:var(--red);font-weight:700">red</span> if &gt;1 mph below season avg.
    <span class="gs">S+</span> = game Stuff+ for that pitch type.
  </div>
  <div class="legend">
    <div class="leg-item"><span class="leg-dot" style="background:var(--gold)"></span>Leader in category</div>
  </div>
  <div class="controls">
    <input id="sp-search" type="text" placeholder="Search pitcher or team…" oninput="filterSP()">
    <span class="row-count" id="sp-cnt"></span>
    <span class="sort-hint">Click headers to sort</span>
  </div>
  <div class="table-wrap">
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
        <th class="sortable r sc" data-k="stuff_plus"    onclick="srtSP(this,'stuff_plus')">Stuff+<span class="vd" style="font-size:.58rem"> szn</span></th>
        <th class="sortable r sc" data-k="location_plus" onclick="srtSP(this,'location_plus')">Loc+<span class="vd" style="font-size:.58rem"> szn</span></th>
        <th>Arsenal</th>
      </tr></thead>
      <tbody id="sp-body"></tbody>
    </table>
  </div>
</div>

<!-- ══ RELIEF PITCHERS ══ -->
<div id="relievers-panel" class="tab-panel">
  <div class="note">
    ⓘ &nbsp;<strong>Stuff+</strong> and <strong>Loc+</strong> are season averages from Baseball Savant.
    <strong>SV</strong> = Saves, <strong>HLD</strong> = Holds, <strong>BS</strong> = Blown Saves.
  </div>
  <div class="legend">
    <div class="leg-item"><span class="leg-dot" style="background:var(--gold)"></span>Leader in category</div>
  </div>
  <div class="controls">
    <input id="rp-search" type="text" placeholder="Search pitcher or team…" oninput="filterRP()">
    <span class="row-count" id="rp-cnt"></span>
    <span class="sort-hint">Click headers to sort</span>
  </div>
  <div class="table-wrap">
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
        <th class="sortable r sc" data-k="stuff_plus"    onclick="srtRP(this,'stuff_plus')">Stuff+<span class="vd" style="font-size:.58rem"> szn</span></th>
        <th class="sortable r sc" data-k="location_plus" onclick="srtRP(this,'location_plus')">Loc+<span class="vd" style="font-size:.58rem"> szn</span></th>
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
      <div style="font-size:1.05rem;font-weight:800;color:#f0c040">Team Alex</div>
      <div style="font-size:.72rem;color:var(--muted)">24-player roster · yesterday's game results</div>
    </div>
  </div>

  <div class="ta-section-hdr">🏏 Hitters <span class="tab-count" id="ta-h-tc">—</span></div>
  <div class="table-wrap" style="margin-bottom:24px">
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

  <div class="ta-section-hdr">⚾ Starting Pitchers <span class="tab-count" id="ta-sp-tc">—</span></div>
  <div class="table-wrap" style="margin-bottom:24px">
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
        <th class="sortable r sc" data-k="stuff_plus"    onclick="srtTA(this,'sp','stuff_plus')">Stuff+<span class="vd" style="font-size:.58rem"> szn</span></th>
        <th class="sortable r sc" data-k="location_plus" onclick="srtTA(this,'sp','location_plus')">Loc+<span class="vd" style="font-size:.58rem"> szn</span></th>
        <th>Arsenal</th>
      </tr></thead>
      <tbody id="ta-sp-body"></tbody>
    </table>
  </div>

  <div class="ta-section-hdr">🔥 Relief Pitchers <span class="tab-count" id="ta-rp-tc">—</span></div>
  <div class="table-wrap">
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
        <th class="sortable r sc" data-k="stuff_plus"    onclick="srtTA(this,'rp','stuff_plus')">Stuff+<span class="vd" style="font-size:.58rem"> szn</span></th>
        <th class="sortable r sc" data-k="location_plus" onclick="srtTA(this,'rp','location_plus')">Loc+<span class="vd" style="font-size:.58rem"> szn</span></th>
        <th>Arsenal</th>
      </tr></thead>
      <tbody id="ta-rp-body"></tbody>
    </table>
  </div>
  <div class="note" style="margin-top:14px">
    Only roster members who played yesterday are shown.
  </div>
</div>

</main>

<footer>
  Statcast (incl. Stuff+/Loc+) · MLB Stats API (SB) &nbsp;·&nbsp;
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

let hD=[...HITTERS], spD=[...STARTERS], rpD=[...RELIEVERS];
let hSC='barrels', hSD=-1, spSC='ip_float', spSD=-1, rpSC='sv', rpSD=-1;

function showTab(nm,btn){
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(nm+'-panel').classList.add('active');
}

const D  = ()=>'<span class="c-dim">—</span>';
const tm = t=>`<span class="tm">${t}</span>`;

// Counting stats: show 0 when absent; rate/measurement stats: keep dash
const fHR  = v=>v>0?`${v}`:'0';
const fK_h = v=>v>0?`${v}`:'0';
const fBB_h= v=>v>0?`${v}`:'0';
const fSB  = v=>v>0?`${v}`:'0';
const fHrd = v=>v>0?`${v}`:'0';
const fBar = v=>v>0?`${v}`:'0';
const fEV  = (v,b)=>{if(v==null||b===0)return D();const t=Math.max(0,Math.min(1,(v-_evMin)/(_evMax-_evMin||1)));const r=Math.round(52+179*t),g=Math.round(152-76*t),bl=Math.round(219-159*t);return `<span style="color:rgb(${r},${g},${bl});font-weight:600">${v}</span>`;};
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
  const ct=document.getElementById('sp-cnt');
  document.getElementById('sp-tc').textContent=spD.length;
  if(!spD.length){tb.innerHTML='<tr><td colspan="16"><div class="empty"><div class="ico">😴</div><p>No data.</p></div></td></tr>';ct.textContent='';return;}
  ct.textContent=`${spD.length} starter${spD.length===1?'':'s'}`;
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
  const ct=document.getElementById('rp-cnt');
  document.getElementById('rp-tc').textContent=rpD.length;
  if(!rpD.length){tb.innerHTML='<tr><td colspan="18"><div class="empty"><div class="ico">😴</div><p>No relief data.</p></div></td></tr>';ct.textContent='';return;}
  ct.textContent=`${rpD.length} reliever${rpD.length===1?'':'s'}`;
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
function srtSP(th,col){if(spSC===col)spSD*=-1;else{spSC=col;spSD=-1;}clrSort('sp-tbl');th.classList.add(spSD===1?'sort-asc':'sort-desc');spD.sort((a,b)=>cmp(a,b,col,spSD));renderSP();}
function srtRP(th,col){if(rpSC===col)rpSD*=-1;else{rpSC=col;rpSD=-1;}clrSort('rp-tbl');th.classList.add(rpSD===1?'sort-asc':'sort-desc');rpD.sort((a,b)=>cmp(a,b,col,rpSD));renderRP();}

function filterH(){
  const q=document.getElementById('h-search').value.toLowerCase().trim();
  hD=q?HITTERS.filter(h=>h.name.toLowerCase().includes(q)||h.team.toLowerCase().includes(q)||h.opp.toLowerCase().includes(q)):[...HITTERS];
  if(hSC)hD.sort((a,b)=>cmp(a,b,hSC,hSD));renderH();
}
function filterSP(){
  const q=document.getElementById('sp-search').value.toLowerCase().trim();
  spD=q?STARTERS.filter(p=>p.name.toLowerCase().includes(q)||p.team.toLowerCase().includes(q)||p.opp.toLowerCase().includes(q)):[...STARTERS];
  if(spSC)spD.sort((a,b)=>cmp(a,b,spSC,spSD));renderSP();
}
function filterRP(){
  const q=document.getElementById('rp-search').value.toLowerCase().trim();
  rpD=q?RELIEVERS.filter(p=>p.name.toLowerCase().includes(q)||p.team.toLowerCase().includes(q)||p.opp.toLowerCase().includes(q)):[...RELIEVERS];
  if(rpSC)rpD.sort((a,b)=>cmp(a,b,rpSC,rpSD));renderRP();
}

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

document.getElementById('ta-tc').textContent=TA_HITTERS.length+TA_STARTERS.length+TA_RELIEVERS.length;

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
</script>
</body>
</html>
"""

def render_html(date_display, ts, n_games, hitters, all_pitchers,
                ta_hitters, ta_starters, ta_relievers):
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

    return (HTML_TEMPLATE
        .replace("__DATE_DISPLAY__", date_display)
        .replace("__N_GAMES__", str(n_games))
        .replace("__TS__", ts)
        .replace("__HITTERS_JSON__",  json.dumps(hitters,        default=str))
        .replace("__ALL_PITCHERS_JSON__", json.dumps(all_pitchers, default=str))
        .replace("__TA_H_JSON__",     json.dumps(ta_hitters,    default=str))
        .replace("__TA_SP_JSON__",    json.dumps(ta_starters,   default=str))
        .replace("__TA_RP_JSON__",    json.dumps(ta_relievers,  default=str))
    )

# ── Baseball Savant: season Stuff+/Loc+ ───────────────────────────────────
def fetch_savant_arsenal_stuff(year: int, mlbam_ids: set) -> dict:
    """
    Returns {mlbam_id: {'stuff_plus': float|None, 'location_plus': float|None}}.

    Route 1 – pybaseball.statcast_pitcher_arsenal_stats() without arsenal_type.
    Route 2 – Baseball Savant pitch-arsenal leaderboard CSVs (direct HTTP).
               Computes a pitch-count-weighted average across all pitch types
               since the CSV has per-pitch-type columns (ff_stuff_plus, sl_stuff_plus…).
    """
    from io import StringIO
    result: dict = {}

    # ── Route 1: pybaseball arsenal stats ────────────────────────────────
    print(f"  Trying pybaseball arsenal stats ({year})…")
    try:
        adf = pybaseball.statcast_pitcher_arsenal_stats(year=year, minPA=0)
        print(f"  pybaseball arsenal → {len(adf)} rows, cols: {list(adf.columns)}")
        cols_lower = {c.lower(): c for c in adf.columns}
        id_col = next(
            (cols_lower[k] for k in ("pitcher","player_id","mlbam","key_mlbam")
             if k in cols_lower), None)
        sp_col = next(
            (cols_lower[k] for k in
             ("stuff_plus","stuff_plus_avg","stuff_plus_stuff_avg","n_stuff_plus",
              "avg_stuff_plus")
             if k in cols_lower), None)
        lp_col = next(
            (cols_lower[k] for k in
             ("location_plus","loc_plus","stuff_plus_loc_avg","n_location_plus",
              "avg_location_plus","pitching_plus","avg_pitching_plus")
             if k in cols_lower), None)
        print(f"  id_col={id_col!r}  sp_col={sp_col!r}  lp_col={lp_col!r}")
        if id_col and sp_col:
            for _, row in adf.iterrows():
                try:
                    pid = int(row[id_col])
                except (ValueError, TypeError):
                    continue
                sp = (round(float(row[sp_col]), 0)
                      if pd.notna(row.get(sp_col, float("nan"))) else None)
                lp = (round(float(row[lp_col]), 0)
                      if lp_col and pd.notna(row.get(lp_col, float("nan"))) else None)
                result[pid] = {"stuff_plus": sp, "location_plus": lp}
            if any(v["stuff_plus"] is not None for v in result.values()):
                hits = sum(1 for pid in mlbam_ids
                           if pid in result and result[pid]["stuff_plus"] is not None)
                print(f"  ✓ Matched {hits}/{len(mlbam_ids)} target pitchers")
                return result
    except Exception as e:
        print(f"  pybaseball arsenal failed: {e}")

    # ── Route 2: direct Savant CSV (weighted average across pitch types) ──
    # The CSV has per-pitch-type columns (ff_stuff_plus, sl_stuff_plus, …) not
    # a single aggregate.  We fetch the pitch-count CSV too and compute a
    # weighted average: Σ(count_i × stuff_plus_i) / Σ(count_i).
    print(f"  Trying Baseball Savant arsenal CSV ({year}) — weighted avg…")
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    base_url = "https://baseballsavant.mlb.com/leaderboard/pitch-arsenals"
    ID_COL = "pitcher"   # confirmed from prior console output

    def _fetch_savant_csv(type_val: str) -> pd.DataFrame | None:
        try:
            r = requests.get(base_url,
                params={"year": year, "min": 0, "type": type_val,
                        "hand": "", "pos": "P", "teamId": "",
                        "active_sw": "", "csv": "true"},
                headers=hdrs, timeout=25)
            r.raise_for_status()
            df = pd.read_csv(StringIO(r.text))
            df[ID_COL] = pd.to_numeric(df[ID_COL], errors="coerce")
            return df
        except Exception as e:
            print(f"  Savant CSV type={type_val!r} failed: {e}")
            return None

    def _weighted_avg(n_df: pd.DataFrame, val_df: pd.DataFrame,
                      val_suffix: str) -> dict:
        """Return {pitcher_id: weighted_avg} across pitch-type columns."""
        out = {}
        # pitch-type prefixes from the count CSV: ff_n → ff
        pt_prefixes = [c[:-2] for c in n_df.columns
                       if c.endswith("_n") and c != ID_COL]
        # merge (drop name column from val_df to avoid clash)
        val_keep = [ID_COL] + [c for c in val_df.columns
                                if c != ID_COL and not c.startswith("last_name")]
        merged = n_df.merge(val_df[val_keep], on=ID_COL, how="inner")
        for _, row in merged.iterrows():
            try:
                pid = int(row[ID_COL])
            except (ValueError, TypeError):
                continue
            total_n = 0.0
            weighted = 0.0
            for pfx in pt_prefixes:
                n_key  = f"{pfx}_n"
                v_key  = f"{pfx}_{val_suffix}"
                if n_key in row and v_key in row:
                    nv = row[n_key]
                    vv = row[v_key]
                    if pd.notna(nv) and pd.notna(vv) and float(nv) > 0:
                        total_n  += float(nv)
                        weighted += float(nv) * float(vv)
            out[pid] = round(weighted / total_n, 0) if total_n > 0 else None
        return out

    # Try current year first, fall back to prior year if too few matches
    for fetch_year in (year, year - 1):
        result = {}
        n_df = _fetch_savant_csv("n") if fetch_year == year else None
        if fetch_year != year:
            # Re-fetch with prior year
            try:
                r = requests.get(base_url,
                    params={"year": fetch_year, "min": 0, "type": "n",
                            "hand": "", "pos": "P", "teamId": "",
                            "active_sw": "", "csv": "true"},
                    headers=hdrs, timeout=25)
                r.raise_for_status()
                n_df = pd.read_csv(StringIO(r.text))
                n_df[ID_COL] = pd.to_numeric(n_df[ID_COL], errors="coerce")
                print(f"  Savant n CSV ({fetch_year}): {len(n_df)} rows")
            except Exception as e:
                print(f"  Savant n CSV ({fetch_year}) failed: {e}")
                continue

        if n_df is None:
            continue

        savant_ids = set(int(x) for x in n_df[ID_COL].dropna())
        our_ids    = set(int(x) for x in mlbam_ids if pd.notna(x))
        overlap    = our_ids & savant_ids
        print(f"  Savant {fetch_year}: {len(savant_ids)} pitchers in CSV, "
              f"{len(overlap)}/{len(our_ids)} of our starters present")
        if not overlap:
            print(f"  ✗ No ID overlap with {fetch_year} Savant data — "
                  f"sample Savant IDs: {sorted(savant_ids)[:5]}, "
                  f"our IDs: {sorted(our_ids)[:5]}")
            continue

        def _fetch_year_csv(type_val: str, yr: int) -> pd.DataFrame | None:
            try:
                r = requests.get(base_url,
                    params={"year": yr, "min": 0, "type": type_val,
                            "hand": "", "pos": "P", "teamId": "",
                            "active_sw": "", "csv": "true"},
                    headers=hdrs, timeout=25)
                r.raise_for_status()
                df = pd.read_csv(StringIO(r.text))
                df[ID_COL] = pd.to_numeric(df[ID_COL], errors="coerce")
                return df
            except Exception as e:
                print(f"  Savant CSV {type_val!r}/{yr} failed: {e}")
                return None

        # ── Stuff+ weighted avg ──────────────────────────────────────────
        sp_df = _fetch_year_csv("stuff_plus", fetch_year)
        if sp_df is not None:
            sp_map = _weighted_avg(n_df, sp_df, "stuff_plus")
            for pid, val in sp_map.items():
                result[pid] = {"stuff_plus": val, "location_plus": None}
            hits = sum(1 for pid in our_ids
                       if result.get(pid, {}).get("stuff_plus") is not None)
            print(f"  Stuff+ weighted avg ({fetch_year}): "
                  f"{hits}/{len(our_ids)} target pitchers matched")
            # Sample output for diagnostics
            sample = [(pid, result[pid]["stuff_plus"])
                      for pid in our_ids if pid in result][:3]
            if sample:
                print(f"  Sample values: {sample}")

        # ── Location+ (pitching_plus) weighted avg ───────────────────────
        lp_df = _fetch_year_csv("pitching_plus", fetch_year)
        if lp_df is not None:
            lp_map = _weighted_avg(n_df, lp_df, "pitching_plus")
            for pid, val in lp_map.items():
                if pid in result:
                    result[pid]["location_plus"] = val
                else:
                    result[pid] = {"stuff_plus": None, "location_plus": val}
            lp_hits = sum(1 for pid in our_ids
                          if result.get(pid, {}).get("location_plus") is not None)
            print(f"  Loc+ weighted avg ({fetch_year}):   "
                  f"{lp_hits}/{len(our_ids)} target pitchers matched")
        else:
            print(f"  pitching_plus CSV not available for {fetch_year} (Loc+ will be blank)")

        if any(v["stuff_plus"] is not None for v in result.values()):
            yr_label = f" (prior-year {fetch_year})" if fetch_year != year else ""
            print(f"  ✓ Savant Stuff+ attached{yr_label}")
            return result

    print("  ✗ Could not retrieve Stuff+/Loc+ from any source")
    return result


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

    # Source 1: FanGraphs game-log API — uses fg_cookie.txt (cf_clearance) if present
    if _load_fg_cookie():
        print("  fg_cookie.txt found — trying FanGraphs per-game Stuff+/Loc+…")
        game_stuff = fetch_fg_game_stuff(yesterday, year, all_pitchers, p_info, {})
        attach_fg_data(all_pitchers, p_info, game_stuff, {}, savant_velo)
        attached_fg = sum(1 for p in all_pitchers if p["stuff_plus"] is not None)
        print(f"  Stuff+ after FanGraphs: {attached_fg}/{len(all_pitchers)} pitcher(s)")
    else:
        print("  No fg_cookie.txt found — skipping FanGraphs (see instructions to add it)")
        attach_fg_data(all_pitchers, p_info, {}, {}, savant_velo)

    # Source 2: Baseball Savant arsenal leaderboard (season avg, weighted across pitch types)
    missing_ids = {p["id"] for p in all_pitchers if p["stuff_plus"] is None}
    if missing_ids:
        savant_stuff = fetch_savant_arsenal_stuff(year, pitcher_mlbam_ids)
        for p in all_pitchers:
            if p["id"] in savant_stuff:
                if p["stuff_plus"] is None:
                    p["stuff_plus"]    = savant_stuff[p["id"]]["stuff_plus"]
                if p["location_plus"] is None:
                    p["location_plus"] = savant_stuff[p["id"]]["location_plus"]

    attached_total = sum(1 for p in all_pitchers if p["stuff_plus"] is not None)
    print(f"  Stuff+ after all sources: {attached_total}/{len(all_pitchers)} pitcher(s)")

    print("\n[ 5b/6 ] Team Alex roster filter")
    ta_hitters  = [h for h in hitters if ta_norm(h["name"]) in TEAM_ALEX_NAMES]
    ta_all_pitchers = [p for p in all_pitchers if ta_norm(p["name"]) in TEAM_ALEX_NAMES]
    ta_starters = [p for p in ta_all_pitchers if p.get("ip_float", 0) >= 3]
    ta_relievers = [p for p in ta_all_pitchers if p.get("ip_float", 0) < 3]
    print(f"  Team Alex: {len(ta_hitters)} hitter(s), {len(ta_starters)} starter(s), {len(ta_relievers)} reliever(s) played yesterday")

    print("\n[ 6/6 ] Rendering HTML")
    hitters.sort(key=lambda x: (x["barrels"], x["hard_hits"]), reverse=True)
    all_pitchers.sort(key=lambda x: x["ip_float"], reverse=True)

    n_games = df["game_pk"].nunique()
    html    = render_html(date_display, ts, n_games, hitters, all_pitchers,
                          ta_hitters, ta_starters, ta_relievers)
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
