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
from fangraphs import *

from fetch_mlb_stats import _FANT, main

__all__ = ['_avg_fg_auction', '_avg_proj_sets', '_dollar_color', '_fant_stat', '_fetch_fg_auction_full', '_fmt_dollar', '_merge_players', '_team_badge_py', '_z_color', '_z_to_dollars', 'compute_fantasy_dollar_values', 'fetch_fg_auction_dollar_values', 'fetch_fg_projections', 'render_fantasy_tab']


def fetch_fg_projections(year: int, proj_type: str, stats_type: str) -> list:
    """Fetch FanGraphs projections.  proj_type: 'oopsy' | 'batx'  stats_type: 'bat' | 'pit'"""
    url = (f"https://www.fangraphs.com/api/projections"
           f"?type={proj_type}&stats={stats_type}&pos=all&team=0&players=0&lg=all")
    try:
        cookie_str = _load_fg_cookie()
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer":    "https://www.fangraphs.com/",
            "Accept":     "application/json",
        }
        if cookie_str:
            if isinstance(cookie_str, dict):
                hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookie_str.items())
            else:
                hdrs["Cookie"] = str(cookie_str)
        resp = requests.get(url, headers=hdrs, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            print(f"    [{proj_type}/{stats_type}] {len(data)} rows")
            return data
        print(f"    [{proj_type}/{stats_type}] HTTP {resp.status_code}")
    except Exception as exc:
        print(f"    [{proj_type}/{stats_type}] error: {exc}")
    return []


def _avg_proj_sets(a: list, b: list, id_key: str = "playerid") -> list:
    """Average two projection lists element-wise, matched by player ID."""
    if not b:
        return list(a or [])
    if not a:
        return list(b)
    b_map = {str(p.get(id_key, "")): p for p in b if p.get(id_key)}
    result = []
    for pa in a:
        pid = str(pa.get(id_key, ""))
        pb  = b_map.get(pid)
        if not pb:
            result.append(pa)
            continue
        merged = dict(pa)
        for k, va in pa.items():
            vb = pb.get(k)
            try:
                merged[k] = (float(va) + float(vb)) / 2.0
            except (TypeError, ValueError):
                pass
        result.append(merged)
    return result


def fetch_fg_auction_dollar_values(proj: str, player_type: str = "bat") -> dict:
    """
    Fetch dollar values from the FanGraphs Auction Calculator API for our league.

    proj:        FanGraphs projection-system code, e.g. 'roopsydc' or 'rthebatx'.
    player_type: 'bat' (hitters) or 'pit' (pitchers).

    Returns dict mapping str(playerid) -> dollars  AND  str(mlbam_id) -> dollars.
    Returns {} on failure.

    League settings match _FANT:
      10 teams, $260, 6×6 Roto (R/HR/RBI/SB/SO/OBP + W/SV/ERA/WHIP/SO/HLD),
      C/1B/2B/3B/SS/CI/MI/OF(3)/UTIL/P/SP(7)/RP(2)/Bench(6), 35 IP min.
    Projection codes confirmed from FanGraphs dropdown:
      'roopsydc'  = OOPYS DC (RoS)
      'rthebatx'  = THE BAT X (RoS)
    """
    from urllib.parse import urlencode
    # points=c|1,2,3,4,9|0,1,12,2,3,4  encodes batting|pitching category IDs.
    # pos=1,1,1,1,3,1,1,1,0,1,7,2,1,6,35 encodes slot counts + MinIP (SP=7 to match actual rostered SP count).
    params = {
        "teams": 10, "lg": "MLB", "dollars": 260, "mb": 1,
        "mp": 20, "msp": 5, "mrp": 5,
        "type": player_type,
        "players": "", "proj": proj, "split": "",
        "points": "c|1,2,3,4,9,5|0,1,12,2,3,4",  # 5=OBP added to hitting cats
        "rep": 0, "drp": 0,
        "pp": "C,SS,2B,3B,OF,1B",
        "pos": "1,1,1,1,3,1,1,1,0,1,7,2,1,6,35",
        "sort": "", "view": 0,
    }
    url = "https://www.fangraphs.com/api/fantasy/auction-calculator/data"
    full_url = url + "?" + urlencode(params)
    print(f"  [FANTASY] FG auction-calc {proj}/{player_type}…")

    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer":    "https://www.fangraphs.com/fantasy-tools/auction-calculator",
        "Accept":     "application/json",
    }
    cookie_str = _load_fg_cookie()
    if cookie_str:
        hdrs["Cookie"] = str(cookie_str)

    try:
        resp = requests.get(full_url, headers=hdrs, timeout=30)
        if resp.status_code == 200:
            payload = resp.json()
            rows = payload.get("data", payload) if isinstance(payload, dict) else payload
            result: dict = {}
            for p in (rows or []):
                d = p.get("Dollars")
                if d is None:
                    continue
                fgid    = p.get("playerid")
                mlbamid = p.get("xMLBAMID")
                if fgid:
                    try:
                        result[str(int(float(fgid)))] = float(d)
                    except (ValueError, TypeError):
                        result[str(fgid)] = float(d)
                if mlbamid:
                    try:
                        result[str(int(float(mlbamid)))] = float(d)
                    except (ValueError, TypeError):
                        pass
            print(f"    → {len(rows or [])} players, {len(result)} keys")
            return result
        print(f"  [FANTASY] FG auction-calc HTTP {resp.status_code}")
    except Exception as exc:
        print(f"  [FANTASY] FG auction-calc error: {exc}")
    return {}


def _fetch_fg_auction_full(proj: str, player_type: str = "bat") -> list:
    """
    Like fetch_fg_auction_dollar_values but returns the **full** row list
    so callers can access projected stats (R, HR, RBI, OBP, etc.) in addition
    to the Dollars value.  Same league settings / URL params as the original.

    Returns [] on failure.
    """
    from urllib.parse import urlencode
    params = {
        "teams": 10, "lg": "MLB", "dollars": 260, "mb": 1,
        "mp": 20, "msp": 5, "mrp": 5,
        "type": player_type,
        "players": "", "proj": proj, "split": "",
        "points": "c|1,2,3,4,9,5|0,1,12,2,3,4",  # 5=OBP added to hitting cats
        "rep": 0, "drp": 0,
        "pp": "C,SS,2B,3B,OF,1B",
        "pos": "1,1,1,1,3,1,1,1,0,1,7,2,1,6,35",
        "sort": "", "view": 0,
    }
    url = ("https://www.fangraphs.com/api/fantasy/auction-calculator/data"
           + "?" + urlencode(params))
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer":    "https://www.fangraphs.com/fantasy-tools/auction-calculator",
        "Accept":     "application/json",
    }
    cookie_str = _load_fg_cookie()
    if cookie_str:
        hdrs["Cookie"] = str(cookie_str)
    try:
        resp = requests.get(url, headers=hdrs, timeout=30)
        if resp.status_code == 200:
            payload = resp.json()
            rows = payload.get("data", payload) if isinstance(payload, dict) else payload
            print(f"    [fg-full {proj}/{player_type}] {len(rows or [])} rows")
            return list(rows or [])
        print(f"  [FANTASY] FG full {proj}/{player_type} HTTP {resp.status_code}")
    except Exception as exc:
        print(f"  [FANTASY] FG full {proj}/{player_type} error: {exc}")
    return []


def _avg_fg_auction(a: dict, b: dict) -> dict:
    """Average two FG auction-calculator dollar-value dicts by key."""
    if not a:
        return b
    if not b:
        return a
    out = {}
    all_keys = set(a) | set(b)
    for k in all_keys:
        va = a.get(k)
        vb = b.get(k)
        if va is not None and vb is not None:
            out[k] = (va + vb) / 2.0
        else:
            out[k] = va if va is not None else vb
    return out


def _fant_stat(row: dict, cat: str) -> float:
    """Pull a fantasy category value from a player row, handling key variants."""
    try:
        if cat == "K":
            return float(row.get("SO") or row.get("K") or row.get("k") or 0)
        if cat == "HLD":
            return float(row.get("HLD") or row.get("holds") or row.get("hld") or 0)
        return float(row.get(cat) or row.get(cat.lower()) or 0)
    except (TypeError, ValueError):
        return 0.0


def _z_to_dollars(players: list, cats: list, neg_cats: set,
                  n_roster: int, usable: float, is_pitcher: bool,
                  min_ip: float = 0) -> list:
    """Core z-score → dollar-value calculation.
    min_ip: if >0, only pitchers meeting this IP threshold are used to define
    the z-score baseline pool (e.g. league ERA/WHIP minimums). All pitchers
    still receive a dollar value against that baseline.
    """
    if not players:
        return []

    def _get_ip(p):
        return float(p.get("IP") or p.get("ip") or p.get("ip_f") or p.get("ip_float") or 0)

    pt_key = (_get_ip if is_pitcher
              else (lambda p: float(p.get("PA") or p.get("pa") or
                                    float(p.get("G") or 0) * 3.8)))

    # Pool = players who set the z-score baseline.
    # For pitchers, enforce the IP minimum so small-sample arms don't distort
    # ERA/WHIP means (mirrors the league's innings-pitched qualifying rule).
    if is_pitcher and min_ip > 0:
        eligible = [p for p in players
                    if float(p.get("IP") or p.get("ip") or
                             p.get("ip_float") or p.get("ip_f") or 0) >= min_ip]
    else:
        eligible = players
    pool = sorted(eligible, key=pt_key, reverse=True)[:n_roster]

    # Per-category mean / std within the pool
    cat_params: dict = {}
    for cat in cats:
        vals = [_fant_stat(p, cat) for p in pool]
        vals = [v for v in vals if v != 0]
        mu  = float(np.mean(vals)) if vals else 0.0
        sig = float(np.std(vals))  if vals else 1e-9
        cat_params[cat] = (mu, max(sig, 1e-9))

    # Compute z-scores for every player
    out = []
    for p in players:
        zc: dict = {}
        z_sum = 0.0
        for cat in cats:
            mu, sig = cat_params[cat]
            v = _fant_stat(p, cat)
            z = (v - mu) / sig
            if cat in neg_cats:
                z = -z
            zc[cat]  = round(z, 2)
            z_sum   += z
        out.append({"player": p, "z": round(z_sum, 2), "zc": zc})

    pos_z_total = sum(max(0.0, r["z"]) for r in out) or 1.0
    for r in out:
        r["dollar"] = round(max(1.0, (r["z"] / pos_z_total) * usable + 1.0), 1)

    out.sort(key=lambda x: x["dollar"], reverse=True)
    return out


def compute_fantasy_dollar_values(lb_data: list, lb_pitch_data: dict, year: int) -> dict:
    """
    Compute projected fantasy dollar values for hitters and pitchers.

    Dollar values AND stat columns come EXCLUSIVELY from the FanGraphs
    Auction Calculator API (averaged across OOPSY DC RoS and Bat X RoS).
    Dollar values are strictly from FG auction calc.
    Projected stat values (R, HR, RBI …) come from the FG projections API
    using the same RoS projection systems, purely for display purposes.

    Returns dict with keys: fut_h, fut_p.
    """

    def _pid(r):
        """Normalize playerid to str-int key."""
        v = r.get("playerid") if isinstance(r, dict) else r
        if not v:
            return None
        try:
            return str(int(float(v)))
        except (ValueError, TypeError):
            return str(v)

    # ── Projected raw stats (OOPSY DC RoS + Bat X RoS average) ────────────
    # Used ONLY for display (e.g. "48 HR"). Dollar values come from auction calc.
    print("  [FANTASY] Fetching projected stats (roopsydc + rthebatx + steamerr)…")
    ob = fetch_fg_projections(year, "roopsydc", "bat")
    bb = fetch_fg_projections(year, "rthebatx", "bat")
    sb = fetch_fg_projections(year, "steamerr",  "bat")
    op = fetch_fg_projections(year, "roopsydc", "pit")
    bp = fetch_fg_projections(year, "rthebatx", "pit")
    sp = fetch_fg_projections(year, "steamerr",  "pit")
    avg_b = _avg_proj_sets(_avg_proj_sets(ob, bb), sb)
    avg_p = _avg_proj_sets(_avg_proj_sets(op, bp), sp)
    proj_h_map = {k: r for r in (avg_b or []) if (k := _pid(r))}
    proj_p_map = {k: r for r in (avg_p or []) if (k := _pid(r))}

    # ── FanGraphs Auction Calculator rows (both projection systems) ────────
    print("  [FANTASY] Fetching FG auction-calculator rows (OOPSY DC RoS + Bat X RoS + Steamer RoS)…")
    rows_oo_h = _fetch_fg_auction_full("roopsydc", "bat")
    rows_bx_h = _fetch_fg_auction_full("rthebatx", "bat")
    rows_st_h = _fetch_fg_auction_full("steamerr",  "bat")
    rows_oo_p = _fetch_fg_auction_full("roopsydc", "pit")
    rows_bx_p = _fetch_fg_auction_full("rthebatx", "pit")
    rows_st_p = _fetch_fg_auction_full("steamerr",  "pit")

    def _merge_auction(rows_a: list, rows_b: list,
                       is_pitcher: bool, proj_stat_map: dict,
                       rows_c: list = None) -> list:
        """
        Merge two or three FG auction row-sets by playerid, average their Dollars.
        m* fields → per-category dollar contributions (for color coding / sort).
        proj_stat_map → raw projected stats (for primary cell display).
        """
        by_id: dict = {}
        for r in (rows_a or []):
            k = _pid(r)
            if k:
                by_id.setdefault(k, {})["a"] = r
        for r in (rows_b or []):
            k = _pid(r)
            if k:
                by_id.setdefault(k, {})["b"] = r
        for r in (rows_c or []):
            k = _pid(r)
            if k:
                by_id.setdefault(k, {})["c"] = r

        result = []
        for fgid, ab in by_id.items():
            a   = ab.get("a")
            b   = ab.get("b")
            c   = ab.get("c")
            ref = a or b or c
            ds  = [float(x.get("Dollars", 0)) for x in (a, b, c) if x is not None]
            dollar = sum(ds) / len(ds) if ds else 0.0

            name  = ref.get("PlayerName") or ref.get("Name") or ""
            team  = ref.get("Team") or ""
            mlbam = ref.get("xMLBAMID")
            proj  = proj_stat_map.get(fgid) or {}

            def _avg_field(*keys):
                """Average field across a/b/c rows; None if all absent."""
                vals = []
                for row in (x for x in (a, b, c) if x):
                    for k in keys:
                        v = row.get(k)
                        if v is not None:
                            try:
                                vals.append(float(v))
                            except (TypeError, ValueError):
                                pass
                            break
                if not vals:
                    return None
                return sum(vals) / len(vals)

            def _ps(key):
                """Return projected stat float from proj dict, or None."""
                v = proj.get(key)
                if v is None:
                    return None
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            if is_pitcher:
                player = {
                    "name": name, "team": team,
                    "fg_id": fgid, "mlbam": mlbam,
                    # Marginal dollar contributions (FG auction calc) — sort/color
                    "W":    _avg_field("mW"),
                    "ERA":  _avg_field("mERA"),
                    "WHIP": _avg_field("mWHIP"),
                    "SO":   _avg_field("mSO"),
                    "SV":   _avg_field("mSV"),
                    "HLD":  _avg_field("mHLD"),
                    # Projected raw stats (projections API) — primary display
                    "W_p":    _ps("W"),
                    "ERA_p":  _ps("ERA"),
                    "WHIP_p": _ps("WHIP"),
                    "SO_p":   _ps("SO"),
                    "SV_p":   _ps("SV"),
                    "HLD_p":  _ps("HLD"),
                    "IP_p":   _ps("IP"),   # for ERA/WHIP weighting in z-score calc
                    # Structural (not displayed)
                    "_ip":  _avg_field("IP"),   # SP/RP classification
                }
            else:
                # Extract FG position for ESPN slot filtering.
                # FG uses "POS" or "minpos" — e.g. "C", "1B", "2B/SS", "OF".
                _fg_pos = (ref.get("POS") or ref.get("minpos") or
                           ref.get("position") or ref.get("Pos") or "")
                player = {
                    "name": name, "team": team,
                    "fg_id": fgid, "mlbam": mlbam,
                    "fg_pos": _fg_pos,
                    # Marginal dollar contributions (FG auction calc) — sort/color
                    "R":   _avg_field("mR"),
                    "HR":  _avg_field("mHR"),
                    "RBI": _avg_field("mRBI"),
                    "SB":  _avg_field("mSB"),
                    "SO":  _avg_field("mSO"),
                    "OBP": _avg_field("mOBP"),
                    # Projected raw stats (projections API) — primary display
                    "R_p":   _ps("R"),
                    "HR_p":  _ps("HR"),
                    "RBI_p": _ps("RBI"),
                    "SB_p":  _ps("SB"),
                    "SO_p":  _ps("SO"),
                    "OBP_p": _ps("OBP"),
                    "PA_p":  _ps("PA"),    # for OBP weighting in z-score calc
                }

            result.append({
                "player": player,
                "dollar": round(float(dollar), 1),
                "z": 0.0, "zc": {},
            })

        result.sort(key=lambda x: x["dollar"], reverse=True)
        return result

    fut_h = _merge_auction(rows_oo_h, rows_bx_h, False, proj_h_map, rows_st_h)
    fut_p = _merge_auction(rows_oo_p, rows_bx_p, True,  proj_p_map, rows_st_p)

    print(f"  [FANTASY] Proj — {len(fut_h)} hitters, {len(fut_p)} pitchers "
          f"(from FG auction calc, OOPSY DC RoS + Bat X RoS + Steamer RoS avg)")

    return {"fut_h": fut_h, "fut_p": fut_p}


# ─── HTML helpers ─────────────────────────────────────────────────────────────

# MLB team colour map — matches the JS TEAM_COLORS in the dashboard JS
_TEAM_COLORS_PY: dict = {
    "ARI": ("#A71930", "#fff"), "ATL": ("#CE1141", "#fff"), "BAL": ("#DF4601", "#fff"),
    "BOS": ("#BD3039", "#fff"), "CHC": ("#0E3386", "#fff"), "CWS": ("#27251F", "#fff"),
    "CIN": ("#C6011F", "#fff"), "CLE": ("#00385D", "#fff"), "COL": ("#33006F", "#fff"),
    "DET": ("#0C2340", "#fff"), "HOU": ("#002D62", "#fff"), "KC":  ("#004687", "#fff"),
    "LAA": ("#BA0021", "#fff"), "LAD": ("#005A9C", "#fff"), "MIA": ("#00A3E0", "#fff"),
    "MIL": ("#12284B", "#fff"), "MIN": ("#002B5C", "#fff"), "NYM": ("#002D72", "#fff"),
    "NYY": ("#003087", "#fff"), "OAK": ("#003831", "#fff"), "PHI": ("#E81828", "#fff"),
    "PIT": ("#FDB827", "#1a1a1a"), "SD":  ("#2F241D", "#fff"), "SEA": ("#0C2C56", "#fff"),
    "SF":  ("#FD5A1E", "#fff"),  "STL": ("#C41E3A", "#fff"), "TB":  ("#092C5C", "#fff"),
    "TEX": ("#003278", "#fff"), "TOR": ("#134A8E", "#fff"), "WSH": ("#AB0003", "#fff"),
}


def _team_badge_py(abbr: str) -> str:
    """Render an MLB team badge <span> matching the JS tm() helper."""
    if not abbr:
        return ""
    key = abbr.strip().upper()
    tc  = _TEAM_COLORS_PY.get(key)
    if tc:
        return (f'<span class="tm" style="background:{tc[0]};color:{tc[1]};'
                f'border-color:{tc[0]}44">{key}</span>')
    return f'<span class="tm" style="background:rgba(255,255,255,.12);color:var(--text)">{key}</span>'


def _fmt_dollar(v):
    if v is None:
        return "–"
    return f"${v:.1f}" if v >= 0 else f"−${abs(v):.1f}"


def _dollar_color(v):
    if v is None:
        return "#888"
    return "#4CAF50"


def _z_color(z):
    if z >=  1.5: return "#4CAF50"
    if z >=  0.5: return "#8BC34A"
    if z >= -0.5: return "#aaa"
    if z >= -1.5: return "#FF9800"
    return "#ef5350"


def _merge_players(ytd_list: list, fut_list: list, is_pitcher: bool) -> list:
    """
    Combine YTD and future dollar lists into one merged list.
    Matched by fg_id first, then by lower-cased name.
    Sorted by projected $, then YTD $.
    """
    # Build lookup maps for future data
    fut_by_fgid: dict = {}
    fut_by_name: dict = {}
    for fr in fut_list:
        p    = fr["player"]
        fgid = str(p.get("fg_id") or p.get("playerid") or "")
        nm   = (p.get("name") or p.get("PlayerName") or "").strip().lower()
        if fgid:
            fut_by_fgid[fgid] = fr
        if nm:
            fut_by_name[nm] = fr

    seen: set = set()
    rows: list = []

    for yr in ytd_list:
        p    = yr["player"]
        fgid = str(p.get("fg_id") or p.get("playerid") or "")
        nm   = (p.get("name") or p.get("PlayerName") or "").strip().lower()
        fr   = fut_by_fgid.get(fgid) or fut_by_name.get(nm)
        key  = fgid or nm
        seen.add(key)
        # Also mark the matched future player as seen by both its fgid AND name
        # so it isn't re-added in the fut_list pass below (fixes duplicate rows
        # when YTD dicts lack fg_id and are matched only by name).
        if fr:
            fr_p    = fr["player"]
            fr_fgid = str(fr_p.get("fg_id") or fr_p.get("playerid") or "")
            fr_nm   = (fr_p.get("name") or fr_p.get("PlayerName") or "").strip().lower()
            if fr_fgid: seen.add(fr_fgid)
            if fr_nm:   seen.add(fr_nm)
        rows.append({
            "ytd": yr, "fut": fr,
            "name": p.get("name") or p.get("PlayerName") or "–",
            "team": p.get("team") or p.get("Team") or "",
            "sort": (fr["dollar"] if fr else 0, yr["dollar"]),
        })

    for fr in fut_list:
        p    = fr["player"]
        fgid = str(p.get("fg_id") or p.get("playerid") or "")
        nm   = (p.get("name") or p.get("PlayerName") or "").strip().lower()
        key  = fgid or nm
        if key in seen:
            continue
        rows.append({
            "ytd": None, "fut": fr,
            "name": p.get("name") or p.get("PlayerName") or "–",
            "team": p.get("team") or p.get("Team") or "",
            "sort": (fr["dollar"], 0),
        })

    rows.sort(key=lambda x: x["sort"], reverse=True)
    return rows


# ── ESPN Season Projections rendering ─────────────────────────────────────
# Looks for an ESPN bookmarklet snapshot in the working directory, runs the
# lineup optimizer + z-score pipeline, and returns standings HTML for the new
# Season Projections sub-tab. Returns a placeholder block if the snapshot is
# missing or the parser/optimizer can't be imported.
ESPN_ROSTER_FILES = ["espn_rosters.json"]   # search order
PROJ_TEAM_CAT_ORDER = ["R", "HR", "RBI", "SO_h", "SB", "OBP",
                       "W", "SO_p", "SV", "HLD", "ERA", "WHIP"]
PROJ_CAT_LABELS = {
    "R": "R", "HR": "HR", "RBI": "RBI", "SO_h": "K", "SB": "SB", "OBP": "OBP",
    "W": "W", "SO_p": "K", "SV": "SV", "HLD": "HLD", "ERA": "ERA", "WHIP": "WHIP",
}
PROJ_LOWER_BETTER = {"SO_h", "ERA", "WHIP"}


def _proj_placeholder(msg: str) -> str:
    return (
        '<div style="padding:30px 22px;color:var(--muted);font-size:.86rem;'
        'background:#181818;border:1px dashed #444;border-radius:8px;'
        'max-width:760px;margin:18px auto;text-align:center;line-height:1.55">'
        f'{msg}</div>'
    )


def _fmt_proj_stat(cat: str, v) -> str:
    """Format a stat value for the standings table."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if cat == "OBP":
        return f"{f:.3f}"
    if cat in ("ERA", "WHIP"):
        return f"{f:.2f}"
    return str(int(round(f)))


def _proj_rank_color(rank: int, n: int) -> str:
    """Gold #1, then gradient red (best non-#1) → white (mid) → blue (worst).
    Matches the main fantasy table convention (red=high, blue=low)."""
    if n <= 1:
        return "#fff"
    if rank == 1:
        return "#f0c040"
    # t in [0, 1]: 0 = best non-#1 (rank 2), 1 = worst (rank n)
    t = (rank - 2) / max(1, n - 2)
    if t < 0.5:
        # Red → white
        s = t * 2
        r = round(255 + (235 - 255) * s)
        g = round(60  + (235 - 60)  * s)
        b = round(50  + (235 - 50)  * s)
    else:
        # White → blue
        s = (t - 0.5) * 2
        r = round(235 + (60  - 235) * s)
        g = round(235 + (140 - 235) * s)
        b = round(235 + (255 - 235) * s)
    return f"rgb({r},{g},{b})"


def _render_season_projections(fdata: dict) -> str:
    """
    Load ESPN snapshot, run optimizer + z-scores, return standings HTML.

    Returns an instructional placeholder if the snapshot file is missing or
    the optimizer chain isn't importable.
    """
    # Locate ESPN snapshot file
    snap_path = None
    base = os.path.dirname(os.path.abspath(__file__))
    for fn in ESPN_ROSTER_FILES:
        candidate = os.path.join(base, fn)
        if os.path.exists(candidate):
            snap_path = candidate
            break
    if snap_path is None:
        return _proj_placeholder(
            "<strong style='color:#ddd'>Season Projections not yet available.</strong><br><br>"
            "Run the ESPN Roster Sync bookmarklet to download an "
            "<code>espn_rosters.json</code> snapshot, then drop it into the project "
            "folder and rebuild.<br>"
            "See <code>espn_bookmarklet.html</code> for setup instructions."
        )

    try:
        from parse_espn_rosters import parse_league
        from lineup_optimizer import build_season_projections
    except Exception as e:
        return _proj_placeholder(
            f"Season Projections module failed to import: <code>{e}</code>"
        )

    try:
        parsed = parse_league(snap_path, fdata, verbose=False)
        team_rows = build_season_projections(parsed, verbose=False)
    except Exception as e:
        return _proj_placeholder(
            f"Season Projections build failed: <code>{e}</code>"
        )

    if not team_rows:
        return _proj_placeholder("ESPN snapshot loaded but no teams found.")

    # ── Build the standings table ──
    n_teams = len(team_rows)
    league_id = parsed.get("league_id") or "?"
    fetched_at = parsed.get("fetched_at") or ""

    h_sub = ["R", "HR", "RBI", "SO_h", "SB", "OBP"]
    p_sub = ["W", "SO_p", "SV", "HLD", "ERA", "WHIP"]

    # ── Compute hitter / pitcher z-subtotals per team and their ranks ──
    for t in team_rows:
        t["z_hit"] = round(sum(t["z"][c] for c in h_sub), 3)
        t["z_pit"] = round(sum(t["z"][c] for c in p_sub), 3)
    for sub_key in ("z_hit", "z_pit"):
        order = sorted(team_rows, key=lambda x: x[sub_key], reverse=True)
        rank_key = "rank_hit" if sub_key == "z_hit" else "rank_pit"
        for i, t in enumerate(order, start=1):
            t[rank_key] = i

    # Sortable header builder. Each <th> gets:
    #   onclick="projSort(this)"  data-col=N  data-default="asc"|"desc"
    # Plus an indicator span (▼) that lights up when active.
    _arrow = ('<span class="psi" style="color:var(--muted);font-size:.6rem;'
              'opacity:.35;margin-left:3px">&#9660;</span>')
    _th_base = 'cursor:pointer;user-select:none;'

    def _proj_th(label, col, default, extra_style=""):
        return (
            f'<th onclick="projSort(this)" data-col="{col}" '
            f'data-default="{default}" '
            f'style="{_th_base}{extra_style}">{label}{_arrow}</th>'
        )

    head_cells = [
        _proj_th('#',    0, 'asc',
                 'text-align:left;padding:8px 10px;font-size:.7rem'),
        _proj_th('Team', 1, 'asc',
                 'text-align:left;padding:8px 10px;font-size:.72rem'),
    ]

    # Hitter cats (R..OBP) → H Z subtotal → Pitcher cats (W..WHIP) → P Z subtotal → Z Total
    col_idx = 2
    for c in h_sub:
        default = 'asc' if c in PROJ_LOWER_BETTER else 'desc'
        head_cells.append(_proj_th(
            PROJ_CAT_LABELS[c], col_idx, default,
            'text-align:center;padding:8px 6px;font-size:.7rem;white-space:nowrap'
        ))
        col_idx += 1
    head_cells.append(_proj_th(
        'H&nbsp;Z', col_idx, 'desc',
        'text-align:center;padding:8px 8px;font-size:.7rem;'
        'border-left:1px solid #2a2a2a'
    ))
    col_idx += 1
    for c in p_sub:
        default = 'asc' if c in PROJ_LOWER_BETTER else 'desc'
        head_cells.append(_proj_th(
            PROJ_CAT_LABELS[c], col_idx, default,
            'text-align:center;padding:8px 6px;font-size:.7rem;white-space:nowrap'
        ))
        col_idx += 1
    head_cells.append(_proj_th(
        'P&nbsp;Z', col_idx, 'desc',
        'text-align:center;padding:8px 8px;font-size:.7rem;'
        'border-left:1px solid #2a2a2a'
    ))
    col_idx += 1
    head_cells.append(_proj_th(
        'Z&nbsp;Total', col_idx, 'desc',
        'text-align:center;padding:8px 10px;font-size:.7rem;'
        'border-left:1px solid #2a2a2a'
    ))

    section_hdr = (
        '<tr style="background:#161616;color:var(--muted);font-size:.62rem;'
        'text-transform:uppercase;letter-spacing:.05em">'
        '<td colspan="2" style="padding:4px 10px;text-align:right">cats:</td>'
        '<td colspan="7" style="padding:4px 6px;text-align:center;'
        'border-left:1px solid #2a2a2a">Hitters</td>'
        '<td colspan="7" style="padding:4px 6px;text-align:center;'
        'border-left:1px solid #2a2a2a">Pitchers</td>'
        '<td style="border-left:1px solid #2a2a2a"></td>'
        '</tr>'
    )

    def _subtotal_cell(z_val, rank, sort_val):
        color = _proj_rank_color(rank, n_teams)
        return (
            f'<td data-sort="{sort_val}" '
            f'style="text-align:center;padding:6px 8px;'
            f'border-left:1px solid #2a2a2a">'
            f'<div style="font-size:.82rem;font-weight:700;color:{color};'
            f'line-height:1.1">{z_val:+.2f}</div>'
            f'<div style="font-size:.6rem;color:#666;line-height:1.1;'
            f'margin-top:1px">#{rank}</div>'
            f'</td>'
        )

    body_rows = []
    for row in team_rows:
        rt = row["rank_total"]
        team_sort_key = (row["name"] or "").replace('"', '&quot;').lower()
        cells = [
            f'<td data-sort="{rt}" '
            f'style="padding:8px 10px;font-weight:700;color:#aaa">{rt}</td>',
            f'<td data-sort="{team_sort_key}" '
            f'style="padding:8px 10px;font-weight:600;color:#ddd;'
            f'white-space:nowrap">{row["name"]}</td>',
        ]
        # Hitter category cells
        for c in h_sub:
            raw_val = row["stats"][c]
            stat_str = _fmt_proj_stat(c, raw_val)
            z_val = row["z"][c]
            rank = row["rank"][c]
            color = _proj_rank_color(rank, n_teams)
            z_color = "#4caf50" if z_val > 0.05 else "#f44336" if z_val < -0.05 else "#888"
            cells.append(
                f'<td data-sort="{raw_val}" '
                f'style="text-align:center;padding:6px 6px">'
                f'<div style="font-size:.82rem;font-weight:700;color:{color};'
                f'line-height:1.1">{stat_str}</div>'
                f'<div style="font-size:.6rem;color:{z_color};line-height:1.1;'
                f'margin-top:1px">{z_val:+.2f}z</div>'
                f'<div style="font-size:.55rem;color:#555;line-height:1.1;'
                f'margin-top:1px">#{rank}</div>'
                f'</td>'
            )
        # Hitter z subtotal — sort by raw z_hit value (desc default = best at top)
        cells.append(_subtotal_cell(row["z_hit"], row["rank_hit"], row["z_hit"]))
        # Pitcher category cells
        for c in p_sub:
            raw_val = row["stats"][c]
            stat_str = _fmt_proj_stat(c, raw_val)
            z_val = row["z"][c]
            rank = row["rank"][c]
            color = _proj_rank_color(rank, n_teams)
            z_color = "#4caf50" if z_val > 0.05 else "#f44336" if z_val < -0.05 else "#888"
            cells.append(
                f'<td data-sort="{raw_val}" '
                f'style="text-align:center;padding:6px 6px">'
                f'<div style="font-size:.82rem;font-weight:700;color:{color};'
                f'line-height:1.1">{stat_str}</div>'
                f'<div style="font-size:.6rem;color:{z_color};line-height:1.1;'
                f'margin-top:1px">{z_val:+.2f}z</div>'
                f'<div style="font-size:.55rem;color:#555;line-height:1.1;'
                f'margin-top:1px">#{rank}</div>'
                f'</td>'
            )
        # Pitcher z subtotal
        cells.append(_subtotal_cell(row["z_pit"], row["rank_pit"], row["z_pit"]))
        # Z Total — same gold/red→blue gradient as the other category cells
        # (gold at #1, red→white→blue for #2..#10), with #N rank suffix.
        zt = row["z_total"]
        zt_color = _proj_rank_color(rt, n_teams)
        zt_str = f"{zt:+.2f}"
        cells.append(
            f'<td data-sort="{zt}" '
            f'style="text-align:center;padding:6px 10px;'
            f'border-left:1px solid #2a2a2a">'
            f'<div style="font-size:.85rem;font-weight:700;color:{zt_color};'
            f'line-height:1.1">{zt_str}</div>'
            f'<div style="font-size:.6rem;color:#666;line-height:1.1;'
            f'margin-top:1px">#{rt}</div>'
            f'</td>'
        )
        body_rows.append('<tr style="border-bottom:1px solid #1f1f1f">' + "".join(cells) + '</tr>')

    table_html = (
        '<div style="overflow-x:auto;padding:0 12px 18px">'
        '<table id="proj-table" class="stats-table" '
        'style="width:100%;border-collapse:collapse;'
        'background:#0e0e0e;border-radius:8px">'
        '<thead style="background:#1a1a1a">'
        + section_hdr
        + '<tr>' + "".join(head_cells) + '</tr>'
        '</thead>'
        '<tbody>' + "".join(body_rows) + '</tbody>'
        '</table>'
        '</div>'
    )

    fetched_str = fetched_at[:10] if fetched_at else "unknown"
    legend = (
        f'<div style="padding:6px 22px 12px;color:var(--muted);font-size:.74rem">'
        f'League {league_id} &nbsp;&bull;&nbsp; Snapshot {fetched_str} '
        f'&nbsp;&bull;&nbsp; Hitter starting 11 chosen by '
        f'<strong>FG&nbsp;$ × position eligibility</strong> '
        f'(C, 1B, 2B, 3B, SS, 3OF, MI, CI, UTIL); all rostered pitchers count. '
        f'Z-scores computed across {n_teams} teams. K/ERA/WHIP are lower-is-better.'
        f'</div>'
    )

    # Monte Carlo finish-probability button + output container.
    # Handler (mcRunSeasonProjSim) lives in the main fantasy-tab JS block and
    # uses PHASE3_LEAGUE.teams, which is the same payload that drives the
    # standings table above.
    mc_block = (
        '<div style="padding:6px 22px 18px">'
        '<button onclick="mcRunSeasonProjSim()" '
        'style="background:#1a1a1a;border:1px solid #333;color:#ddd;'
        'padding:7px 16px;border-radius:6px;cursor:pointer;'
        'font-size:.78rem;font-weight:600">'
        '&#x1F3B2; Run finish-probability sim (50,000 trials)'
        '</button>'
        '<div id="mc-proj-out" style="margin-top:12px"></div>'
        '</div>'
    )

    return legend + table_html + mc_block


# ── Standings sub-tab renderer ─────────────────────────────────────────────
# Reads the ESPN roster snapshot (espn_rosters.json) and builds an HTML
# standings table: W-L-T record, win %, games back, streak, and season
# totals for each of the 12 H2H categories. This is the "live" standings
# as of when the bookmarklet last ran — so running the bookmarklet +
# push_update.bat + GHA refreshes the standings on the dashboard.
#
# ESPN encodes category stats as statId → number in team["valuesByStat"].
# The statIds used by this league come from settings.scoringSettings
# .scoringItems (12 entries for a 6x6 H2H league). We map them to human
# labels below. `reversed` items (lower-is-better: ERA, WHIP, K_h) are
# flagged so CSS can color them appropriately if we decide to later.
_STAT_ID_LABELS = {
    # (label, reversed_lower_is_better, decimal_precision, hit_or_pit)
    # IDs derived from ESPN's API + value-range sanity check against
    # the real snapshot values for this 2026 league.
    # Hitting
    5:  ("HR",    False, 0, "hit"),
    17: ("OBP",   False, 3, "hit"),
    20: ("R",     False, 0, "hit"),
    21: ("RBI",   False, 0, "hit"),
    23: ("SB",    False, 0, "hit"),
    27: ("K",     True,  0, "hit"),   # hitter strikeouts (reversed)
    # Pitching
    41: ("WHIP",  True,  3, "pit"),
    47: ("ERA",   True,  2, "pit"),
    48: ("K",     False, 0, "pit"),   # pitcher strikeouts
    53: ("SV",    False, 0, "pit"),
    57: ("W",     False, 0, "pit"),   # wins
    60: ("HLD",   False, 0, "pit"),   # holds
}


def _render_standings_html() -> str:
    """Build the Standings sub-tab HTML from espn_rosters.json.

    Returns an empty wrapper div if the snapshot is missing or malformed,
    so the sub-tab button still works even when the bookmarklet hasn't
    been run yet.
    """
    import json as _json_s
    import os as _os_s
    base = _os_s.path.dirname(_os_s.path.abspath(__file__))
    snap_path = None
    for fn in ESPN_ROSTER_FILES:
        cand = _os_s.path.join(base, fn)
        if _os_s.path.exists(cand):
            snap_path = cand
            break
    if not snap_path:
        return ('<div id="fant-standings-wrap" style="display:none;padding:18px 20px 0">'
                '<p style="color:var(--muted);font-size:.88rem">'
                'No ESPN snapshot found — run the bookmarklet to populate standings.'
                '</p></div>')

    try:
        with open(snap_path, "r", encoding="utf-8") as _f:
            _snap = _json_s.load(_f)
    except Exception as _e:
        return ('<div id="fant-standings-wrap" style="display:none;padding:18px 20px 0">'
                f'<p style="color:var(--muted);font-size:.88rem">Could not parse '
                f'espn_rosters.json: {_e}</p></div>')

    _raw = _snap.get("raw", _snap) or {}
    _teams = _raw.get("teams", []) or []
    _settings = _raw.get("settings", {}) or {}
    _scoring = (_settings.get("scoringSettings") or {}).get("scoringItems") or []
    # Preserve the league's own ordering of categories where possible.
    _cat_ids = [item.get("statId") for item in _scoring if item.get("statId") is not None]
    # Division list: [{"id": int, "name": str, "size": int}, ...]
    _divisions = ((_settings.get("scheduleSettings") or {}).get("divisions")) or []

    # Build the header row of category columns using our label map.
    def _label_for(sid):
        lbl = _STAT_ID_LABELS.get(sid)
        if lbl:
            return lbl[0], lbl[1], lbl[2], lbl[3]
        return f"#{sid}", False, 0, "hit"

    # Both K columns just display "K" — the hit/pit block separator below
    # makes the context clear without the parenthetical labels.
    _cat_meta = []
    for sid in _cat_ids:
        name, rev, prec, group = _label_for(sid)
        _cat_meta.append({"sid": sid, "name": name, "reversed": rev,
                          "prec": prec, "group": group})

    # Fallback if scoringItems missing: use every statId we have a label for.
    if not _cat_meta:
        for sid, (name, rev, prec, group) in sorted(_STAT_ID_LABELS.items()):
            _cat_meta.append({"sid": sid, "name": name, "reversed": rev,
                              "prec": prec, "group": group})

    # Explicit display order the user requested:
    #   Hitters:  R → HR → RBI → K → SB → OBP
    #   Pitchers: K → W  → SV  → HLD → ERA → WHIP
    # Keyed by statId so it's independent of ESPN's scoringItems ordering.
    _CAT_DISPLAY_ORDER = {
        # Hitters
        20: 0,   # R
        5:  1,   # HR
        21: 2,   # RBI
        27: 3,   # K (hitter, reversed)
        23: 4,   # SB
        17: 5,   # OBP
        # Pitchers
        48: 10,  # K (pitcher)
        57: 11,  # W
        53: 12,  # SV
        60: 13,  # HLD
        47: 14,  # ERA
        41: 15,  # WHIP
    }
    _cat_meta.sort(key=lambda m: (
        0 if m["group"] == "hit" else 1,
        _CAT_DISPLAY_ORDER.get(m["sid"], 99),
    ))

    # Compose team rows.
    def _fmt(val, prec):
        if val is None:
            return "—"
        try:
            f = float(val)
        except (TypeError, ValueError):
            return "—"
        if prec == 0:
            return f"{int(round(f))}"
        if prec == 2:
            return f"{f:.2f}"
        if prec == 3:
            # Trim leading zero on rate stats (ESPN style: .315 not 0.315).
            s = f"{f:.3f}"
            return s[1:] if s.startswith("0.") else s
        return f"{f:g}"

    rows = []
    for t in _teams:
        rec_ov = ((t.get("record") or {}).get("overall")) or {}
        rec_dv = ((t.get("record") or {}).get("division")) or {}
        w = rec_ov.get("wins") or 0
        l = rec_ov.get("losses") or 0
        ti = rec_ov.get("ties") or 0
        pct = rec_ov.get("percentage")
        gb = rec_ov.get("gamesBack")
        # Division-relative stats for the top (per-division) records table.
        dw = rec_dv.get("wins") or 0
        dl = rec_dv.get("losses") or 0
        dti = rec_dv.get("ties") or 0
        dpct = rec_dv.get("percentage")
        dgb = rec_dv.get("gamesBack")
        vbs = t.get("valuesByStat") or {}
        raw_cats = {}
        for m in _cat_meta:
            v = vbs.get(str(m["sid"]))
            try:
                raw_cats[m["sid"]] = float(v) if v is not None else None
            except (TypeError, ValueError):
                raw_cats[m["sid"]] = None
        rows.append({
            "name": t.get("name") or "",
            "abbrev": t.get("abbrev") or "",
            "seed": t.get("playoffSeed") or 0,
            "division_id": t.get("divisionId") if t.get("divisionId") is not None else -1,
            "record": f"{w}-{l}-{ti}" if ti else f"{w}-{l}",
            "wpct": pct if pct is not None else 0.0,
            "gb": gb if gb is not None else 0.0,
            "div_record": f"{dw}-{dl}-{dti}" if dti else f"{dw}-{dl}",
            "div_wpct": dpct if dpct is not None else 0.0,
            "div_gb": dgb if dgb is not None else 0.0,
            "cats": {
                m["sid"]: _fmt(vbs.get(str(m["sid"])), m["prec"])
                for m in _cat_meta
            },
            "raw_cats": raw_cats,
        })
    # Sort by playoff seed (ESPN's current ranking); fall back to wpct desc.
    rows.sort(key=lambda r: (r["seed"] if r["seed"] else 999, -r["wpct"]))

    # ── Rank each team in each category (1 = best) so we can color-code
    #    cells with a gold→red→blue gradient AND display the rank below
    #    the value (matching the Season Projections table style). Ties
    #    share the same rank.
    n_teams = len(rows)
    cell_colors = {}  # {(row_idx, sid): hex_color}
    cell_ranks = {}   # {(row_idx, sid): int_rank}
    def _rank_to_color(rank, n):
        """rank 1 = gold; 2..n interpolated from red → grey → blue.
        Matches pctColor() gradient used elsewhere."""
        if rank <= 1:
            return "#f0c040"  # gold for best
        if n <= 1:
            return "#888"
        pct = (n - rank) / (n - 1) * 100.0
        if pct >= 50:
            t = (pct - 50.0) / 50.0
            r = int(round(136 + 88 * t)); g = int(round(136 - 104 * t)); b = int(round(136 - 104 * t))
        else:
            t = (pct - 1.0) / 49.0 if pct > 1 else 0
            r = int(round(30 + 106 * t)); g = int(round(63 + 73 * t)); b = int(round(186 - 50 * t))
        return f"rgb({r},{g},{b})"

    for m in _cat_meta:
        vals = [(idx, r["raw_cats"].get(m["sid"])) for idx, r in enumerate(rows)]
        valid = [(i, v) for i, v in vals if v is not None]
        # Sort so rank 1 = best; reversed cats = ascending, else descending
        valid.sort(key=lambda iv: iv[1], reverse=(not m["reversed"]))
        prev_val = object()
        prev_rank = 0
        for j, (i, v) in enumerate(valid, start=1):
            rank = prev_rank if v == prev_val else j
            cell_colors[(i, m["sid"])] = _rank_to_color(rank, len(valid))
            cell_ranks[(i, m["sid"])] = rank
            prev_val = v
            prev_rank = rank

    # ── Compute z-scores per category (positive = good; reversed cats
    #    sign-flipped so low ERA/WHIP/K-hit yields a positive z). Skipped
    #    when fewer than 2 teams have a value or stdev is 0. ─────────────
    cell_z = {}                 # {(row_i, sid): float}
    total_z = [0.0] * n_teams   # sum of z across all cats, per team
    for m in _cat_meta:
        nums = [r["raw_cats"].get(m["sid"]) for r in rows]
        pairs = [(i, v) for i, v in enumerate(nums) if v is not None]
        if len(pairs) < 2:
            continue
        vals_only = [v for _, v in pairs]
        mu = sum(vals_only) / len(vals_only)
        var = sum((v - mu) ** 2 for v in vals_only) / len(vals_only)
        sd = var ** 0.5
        if sd == 0:
            for i, _ in pairs:
                cell_z[(i, m["sid"])] = 0.0
            continue
        for i, v in pairs:
            z = (v - mu) / sd
            if m["reversed"]:
                z = -z
            cell_z[(i, m["sid"])] = z
            total_z[i] += z

    # Average category rank per team (simple mean of ranks across cats).
    avg_rank = [0.0] * n_teams
    for i in range(n_teams):
        rks = [cell_ranks[(i, m["sid"])] for m in _cat_meta
               if (i, m["sid"]) in cell_ranks]
        if rks:
            avg_rank[i] = sum(rks) / len(rks)

    # ── HTML build ─────────────────────────────────────────────────────────
    if not rows:
        return ('<div id="fant-standings-wrap" style="display:none;padding:18px 20px 0">'
                '<p style="color:var(--muted);font-size:.88rem">ESPN snapshot has no '
                'team records yet — season may not have started.</p></div>')

    # ── TOP SECTION: per-division records tables (ESPN-style) ──────────────
    def _fmt_wpct(p):
        s = f"{p:.3f}"
        return s[1:] if s.startswith("0.") else s

    def _fmt_gb(g):
        return "—" if g in (0, 0.0) else f"{g:.1f}"

    # Group rows by divisionId
    div_groups = {}  # {div_id: [row, ...]}
    for r in rows:
        div_groups.setdefault(r["division_id"], []).append(r)

    # Build an ordered list of (div_id, div_name) pairs. Use the settings
    # division list if present; otherwise fall back to whatever IDs appear
    # in team data. Unknown ID (-1) is labeled "Unassigned" and placed last.
    div_order = []
    if _divisions:
        for d in _divisions:
            did = d.get("id")
            dname = d.get("name") or f"Division {did}"
            div_order.append((did, dname))
        for did in div_groups:
            if did not in [d[0] for d in div_order]:
                div_order.append((did, f"Division {did}"))
    else:
        for did in sorted(div_groups.keys()):
            div_order.append((did, "League" if did == -1 else f"Division {did}"))

    def _build_division_table(div_name, teams_in_div):
        # Sort within division by overall win% desc (best first), then by GB.
        teams_in_div = sorted(teams_in_div,
                              key=lambda x: (-x["wpct"], x["gb"]))
        # Compute GB within this division relative to the leader's record.
        # ESPN's own `record.division.gamesBack` is confusing when the user
        # asked to drop division-specific columns, so we derive GB here from
        # the overall records of the teams in each division: GB = ((leaderW −
        # teamW) + (teamL − leaderL)) / 2.
        lw = teams_in_div[0]["wpct"] if teams_in_div else 0
        rows_html = []
        for i, r in enumerate(teams_in_div, start=1):
            # Parse W and L out of the "W-L" or "W-L-T" record string
            try:
                parts = r["record"].split("-")
                tw, tl = int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                tw = tl = 0
            if i == 1:
                gb_disp = "—"
            else:
                # Use leader's parsed W/L
                try:
                    lp = teams_in_div[0]["record"].split("-")
                    lead_w, lead_l = int(lp[0]), int(lp[1])
                    gb_val = ((lead_w - tw) + (tl - lead_l)) / 2.0
                    gb_disp = f"{gb_val:.1f}" if gb_val > 0 else "—"
                except Exception:
                    gb_disp = "—"
            rows_html.append(
                f'<tr>'
                f'<td style="text-align:center;color:var(--muted)">{i}</td>'
                f'<td style="padding-left:8px">{r["name"]}</td>'
                f'<td style="text-align:center;font-variant-numeric:tabular-nums;font-weight:600">{r["record"]}</td>'
                f'<td style="text-align:center">{_fmt_wpct(r["wpct"])}</td>'
                f'<td style="text-align:center">{gb_disp}</td>'
                f'</tr>'
            )
        # table-layout:fixed + explicit colgroup widths makes the two
        # division tables line up pixel-for-pixel next to each other.
        return (
            f'<div>'
            f'<div style="font-size:.72rem;font-weight:700;color:var(--accent);'
            f'text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">'
            f'{div_name}</div>'
            f'<table class="standings-div-tbl" style="width:100%;font-size:.82rem;'
            f'border-collapse:collapse;table-layout:fixed">'
            f'<colgroup>'
            f'<col style="width:42px">'
            f'<col>'
            f'<col style="width:78px">'
            f'<col style="width:58px">'
            f'<col style="width:50px">'
            f'</colgroup>'
            f'<thead><tr style="border-bottom:1px solid #333">'
            f'<th style="text-align:center;padding:6px 4px;color:var(--muted);'
            f'font-size:.68rem;text-transform:uppercase;letter-spacing:.05em">#</th>'
            f'<th style="text-align:left;padding:6px 8px;color:var(--muted);'
            f'font-size:.68rem;text-transform:uppercase;letter-spacing:.05em">Team</th>'
            f'<th style="text-align:center;padding:6px 4px;color:var(--muted);'
            f'font-size:.68rem;text-transform:uppercase;letter-spacing:.05em">W-L-T</th>'
            f'<th style="text-align:center;padding:6px 4px;color:var(--muted);'
            f'font-size:.68rem;text-transform:uppercase;letter-spacing:.05em">PCT</th>'
            f'<th style="text-align:center;padding:6px 4px;color:var(--muted);'
            f'font-size:.68rem;text-transform:uppercase;letter-spacing:.05em">GB</th>'
            f'</tr></thead>'
            f'<tbody>{"".join(rows_html)}</tbody>'
            f'</table>'
            f'</div>'
        )

    # Render division tables side-by-side when there are ≥2 divisions. Each
    # goes in a flex column that shrinks/wraps on narrow mobile screens.
    div_tables_list = []
    for did, dname in div_order:
        teams_here = div_groups.get(did, [])
        if teams_here:
            div_tables_list.append(_build_division_table(dname, teams_here))
    if len(div_tables_list) >= 2:
        div_tables_html = (
            '<div style="display:flex;gap:18px;flex-wrap:wrap;margin-bottom:8px">'
            + "".join(
                f'<div style="flex:1 1 320px;min-width:280px">{t}</div>'
                for t in div_tables_list
            )
            + '</div>'
        )
    else:
        div_tables_html = "".join(div_tables_list)

    # ── BOTTOM SECTION: category stats table (no division split) ───────────
    # data-rev → first-click direction (hi=desc/best first, lo=asc/best first)
    # cat-group-start → left border divider
    th_cells = [
        '<th class="sortable" data-sort="rank" data-type="num" data-rev="lo" '
        'style="text-align:center">Rank</th>',
        '<th class="sortable" data-sort="team" data-type="str" '
        'style="text-align:left">Team</th>',
    ]
    first_cat = True
    prev_group = None
    for m in _cat_meta:
        rev = "lo" if m["reversed"] else "hi"
        is_boundary = first_cat or (prev_group is not None and prev_group != m["group"])
        border_cls = " cat-group-start" if is_boundary else ""
        first_cat = False
        prev_group = m["group"]
        th_cells.append(
            f'<th class="sortable{border_cls}" data-sort="cat_{m["sid"]}" data-type="num" '
            f'data-rev="{rev}" data-grp="{m["group"]}" '
            f'style="text-align:center" '
            f'title="statId {m["sid"]}{" (lower is better)" if m["reversed"] else ""}">'
            f'{m["name"]}</th>'
        )
    thead = "<tr>" + "".join(th_cells) + "</tr>"

    tr_html = []
    for row_i, r in enumerate(rows):
        rank = row_i + 1
        cells = []
        first_cat = True
        prev_group = None
        for m in _cat_meta:
            is_boundary = first_cat or (prev_group is not None and prev_group != m["group"])
            border_cls = " cat-group-start" if is_boundary else ""
            first_cat = False
            prev_group = m["group"]
            color = cell_colors.get((row_i, m["sid"]), "#ccc")
            cat_rank = cell_ranks.get((row_i, m["sid"]))
            val_html = r["cats"].get(m["sid"], "—")
            rank_html = (f'<div style="font-size:.58rem;color:#666;line-height:1.1;'
                         f'margin-top:2px">#{cat_rank}</div>') if cat_rank else ''
            cat_z = cell_z.get((row_i, m["sid"]))
            if cat_z is not None:
                z_sign = "+" if cat_z >= 0 else ""
                z_color = "#6fa86f" if cat_z >= 0 else "#b66"
                z_html = (f'<div style="font-size:.55rem;color:{z_color};'
                          f'line-height:1.1">z {z_sign}{cat_z:.2f}</div>')
            else:
                z_html = ''
            cells.append(
                f'<td class="{border_cls.strip()}" style="text-align:center">'
                f'<div style="color:{color};font-weight:700;line-height:1.1">{val_html}</div>'
                f'{rank_html}'
                f'{z_html}'
                f'</td>'
            )
        cat_cells = "".join(cells)
        tr_html.append(
            f'<tr data-rank="{rank}" data-team="{r["name"]}" '
            f'data-wpct="{r["wpct"]}" data-gb="{r["gb"]}">'
            f'<td style="text-align:center;color:var(--muted)">{rank}</td>'
            f'<td style="text-align:left;padding-left:8px">{r["name"]}</td>'
            f'{cat_cells}'
            f'</tr>'
        )

    # ── wRank summary table: Team | Avg Cat Rank | wRank (total z) | W-L | W%
    #    Sorted by wRank desc so the "truest" team sits on top.
    summary = []
    for i, r in enumerate(rows):
        summary.append({
            "name": r["name"],
            "abbrev": r["abbrev"],
            "avg_rank": avg_rank[i],
            "wrank": total_z[i],
            "record": r["record"],
            "wpct": r["wpct"] or 0.0,
        })
    summary.sort(key=lambda s: -s["wrank"])

    def _fmt_wpct_s(p):
        s = f"{p:.3f}"
        return s[1:] if s.startswith("0.") else s

    summary_rows_html = []
    for i, s in enumerate(summary, start=1):
        w_sign = "+" if s["wrank"] >= 0 else ""
        w_color = "#6fa86f" if s["wrank"] >= 0 else "#b66"
        summary_rows_html.append(
            f'<tr>'
            f'<td style="text-align:center;color:var(--muted)">{i}</td>'
            f'<td style="padding-left:8px">{s["name"]}</td>'
            f'<td style="text-align:center;font-variant-numeric:tabular-nums">'
            f'{s["avg_rank"]:.2f}</td>'
            f'<td style="text-align:center;font-variant-numeric:tabular-nums;'
            f'font-weight:700;color:{w_color}">{w_sign}{s["wrank"]:.2f}</td>'
            f'<td style="text-align:center;font-variant-numeric:tabular-nums">'
            f'{s["record"]}</td>'
            f'<td style="text-align:center;font-variant-numeric:tabular-nums">'
            f'{_fmt_wpct_s(s["wpct"])}</td>'
            f'</tr>'
        )
    summary_table_html = (
        '<h4 style="color:var(--text);margin:22px 0 6px;font-size:.9rem;'
        'font-weight:700">Expected Strength (wRank)</h4>'
        '<p style="color:var(--muted);font-size:.72rem;margin:0 0 8px">'
        'wRank = sum of z-scores across all 12 categories (lower-is-better '
        'cats sign-flipped so positive = good). Sorted best first.</p>'
        '<div class="table-wrap" style="overflow-x:auto;-webkit-overflow-scrolling:touch">'
        '<table class="wrank-tbl" '
        'style="min-width:520px;font-size:.82rem;border-collapse:collapse;width:100%">'
        '<thead><tr style="border-bottom:1px solid #333">'
        '<th style="text-align:center;padding:6px 4px;color:var(--muted);'
        'font-size:.68rem;text-transform:uppercase;letter-spacing:.05em">#</th>'
        '<th style="text-align:left;padding:6px 8px;color:var(--muted);'
        'font-size:.68rem;text-transform:uppercase;letter-spacing:.05em">Team</th>'
        '<th style="text-align:center;padding:6px 4px;color:var(--muted);'
        'font-size:.68rem;text-transform:uppercase;letter-spacing:.05em" '
        'title="Average of the team\'s rank across all 12 categories">Avg Rank</th>'
        '<th style="text-align:center;padding:6px 4px;color:var(--muted);'
        'font-size:.68rem;text-transform:uppercase;letter-spacing:.05em" '
        'title="Total z-score across all 12 categories">wRank</th>'
        '<th style="text-align:center;padding:6px 4px;color:var(--muted);'
        'font-size:.68rem;text-transform:uppercase;letter-spacing:.05em">W-L</th>'
        '<th style="text-align:center;padding:6px 4px;color:var(--muted);'
        'font-size:.68rem;text-transform:uppercase;letter-spacing:.05em">W%</th>'
        '</tr></thead>'
        f'<tbody>{"".join(summary_rows_html)}</tbody>'
        '</table></div>'
    )

    # ── Scatter plot: wRank (x) vs W% (y). Pure inline SVG — no JS libs.
    #    Points are labeled with the team owner's name (not team name);
    #    mapping kept here as a list of (substring, owner) tuples in
    #    priority order so "Floyd Bros" resolves to Dave before "Floyd"
    #    falls through to Floyd's own team. Easy to extend mid-season.
    _OWNER_MAP = [
        ("floyd bros",   "Dave"),
        ("lawn serv",    "Dave"),
        ("lockwood",     "Nate"),
        ("honey nut",    "Needham"),
        ("chourio",      "Needham"),
        ("roman empire", "Alex B"),
        ("yankees",      "Don"),
        ("team alex",    "Lex"),
        ("team jason",   "Jason"),
        ("team floyd",   "Floyd"),
        ("cleveland",    "Ryan"),
        ("indians",      "Ryan"),
        ("duh bing",     "Dom"),
    ]

    def _owner_for(team_name: str, abbrev: str) -> str:
        nm = (team_name or "").lower()
        for needle, owner in _OWNER_MAP:
            if needle in nm:
                return owner
        return abbrev or (team_name[:6] if team_name else "")

    _VB_W, _VB_H = 620, 360
    _ML, _MR, _MT, _MB = 58, 20, 28, 44
    plot_w = _VB_W - _ML - _MR
    plot_h = _VB_H - _MT - _MB

    if summary:
        xs = [s["wrank"] for s in summary]
        ys = [s["wpct"] for s in summary]
        x_min, x_max = min(xs), max(xs)
        if x_min == x_max:
            x_min -= 1.0; x_max += 1.0
        x_pad = (x_max - x_min) * 0.12
        x_min -= x_pad; x_max += x_pad
        # Y axis: anchor at 0-1 for W%; pad to the actual data with a margin.
        y_min = max(0.0, min(ys) - 0.05) if ys else 0.0
        y_max = min(1.0, max(ys) + 0.05) if ys else 1.0
        if y_min == y_max:
            y_min = max(0.0, y_min - 0.1); y_max = min(1.0, y_max + 0.1)

        def _sx(v):
            return _ML + (v - x_min) / (x_max - x_min) * plot_w
        def _sy(v):
            return _MT + (1 - (v - y_min) / (y_max - y_min)) * plot_h

        # Build 5 x-axis ticks and 5 y-axis ticks evenly spaced.
        def _nice_ticks(lo, hi, n=5):
            step = (hi - lo) / (n - 1)
            return [lo + i * step for i in range(n)]

        x_ticks = _nice_ticks(x_min, x_max, 5)
        y_ticks = _nice_ticks(y_min, y_max, 5)

        # Linear regression for the trendline (least squares on the data).
        # Compute slope/intercept on RAW (data-space) values, then project
        # both endpoints into pixel space and clip them to the visible y
        # range so the line never escapes the plot rect.
        n_pts = len(xs)
        mx_raw = sum(xs) / n_pts
        my_raw = sum(ys) / n_pts
        denom = sum((x - mx_raw) ** 2 for x in xs)
        if denom > 0:
            slope = sum((x - mx_raw) * (y - my_raw) for x, y in zip(xs, ys)) / denom
        else:
            slope = 0.0
        intercept = my_raw - slope * mx_raw

        def _line_y(x):
            return slope * x + intercept

        # Solve for x where the line crosses the y-axis bounds, and clip.
        endpoints = []
        for x_end in (x_min, x_max):
            y_end = _line_y(x_end)
            if y_min <= y_end <= y_max:
                endpoints.append((x_end, y_end))
        if slope != 0:
            for y_bound in (y_min, y_max):
                x_at = (y_bound - intercept) / slope
                if x_min <= x_at <= x_max:
                    endpoints.append((x_at, y_bound))
        # Dedup near-identical endpoints, keep the two extremes by x.
        endpoints = sorted(set((round(x, 6), round(y, 6)) for x, y in endpoints))
        if len(endpoints) >= 2:
            (tx1, ty1), (tx2, ty2) = endpoints[0], endpoints[-1]
            trend_segment = (
                f'<line x1="{_sx(tx1):.1f}" y1="{_sy(ty1):.1f}" '
                f'x2="{_sx(tx2):.1f}" y2="{_sy(ty2):.1f}" '
                f'stroke="#f0c040" stroke-width="1.5" stroke-opacity=".55" '
                f'stroke-dasharray="6,4"/>'
            )
        else:
            trend_segment = ""

        svg_parts = [
            f'<svg viewBox="0 0 {_VB_W} {_VB_H}" xmlns="http://www.w3.org/2000/svg" '
            f'style="width:100%;height:auto;max-width:720px;display:block;'
            f'margin:6px auto 0;font-family:inherit">',
            # plot-area background
            f'<rect x="{_ML}" y="{_MT}" width="{plot_w}" height="{plot_h}" '
            f'fill="#1a1a1a" stroke="#333" stroke-width="1"/>',
        ]
        # Grid + x-axis tick labels
        for t in x_ticks:
            x = _sx(t)
            svg_parts.append(
                f'<line x1="{x:.1f}" y1="{_MT}" x2="{x:.1f}" y2="{_MT + plot_h}" '
                f'stroke="#2a2a2a" stroke-width="1"/>'
            )
            svg_parts.append(
                f'<text x="{x:.1f}" y="{_MT + plot_h + 14}" fill="#888" '
                f'font-size="10" text-anchor="middle">{t:+.1f}</text>'
            )
        # Grid + y-axis tick labels
        for t in y_ticks:
            y = _sy(t)
            svg_parts.append(
                f'<line x1="{_ML}" y1="{y:.1f}" x2="{_ML + plot_w}" y2="{y:.1f}" '
                f'stroke="#2a2a2a" stroke-width="1"/>'
            )
            yl = f"{t:.3f}".lstrip("0") if 0 < t < 1 else f"{t:.2f}"
            svg_parts.append(
                f'<text x="{_ML - 6}" y="{y + 3:.1f}" fill="#888" '
                f'font-size="10" text-anchor="end">{yl}</text>'
            )
        # Zero line for x-axis if 0 is in range
        if x_min < 0 < x_max:
            zx = _sx(0)
            svg_parts.append(
                f'<line x1="{zx:.1f}" y1="{_MT}" x2="{zx:.1f}" y2="{_MT + plot_h}" '
                f'stroke="#555" stroke-width="1" stroke-dasharray="3,3"/>'
            )
        # .500 reference line if in range
        if y_min < 0.5 < y_max:
            zy = _sy(0.5)
            svg_parts.append(
                f'<line x1="{_ML}" y1="{zy:.1f}" x2="{_ML + plot_w}" y2="{zy:.1f}" '
                f'stroke="#555" stroke-width="1" stroke-dasharray="3,3"/>'
            )
        # Axis labels
        svg_parts.append(
            f'<text x="{_ML + plot_w / 2:.1f}" y="{_VB_H - 8}" fill="#bbb" '
            f'font-size="11" text-anchor="middle" font-weight="600">wRank (total z)</text>'
        )
        svg_parts.append(
            f'<text x="14" y="{_MT + plot_h / 2:.1f}" fill="#bbb" '
            f'font-size="11" text-anchor="middle" font-weight="600" '
            f'transform="rotate(-90 14 {_MT + plot_h / 2:.1f})">Win %</text>'
        )
        # Trendline drawn BEFORE points so dots sit on top of the line.
        if trend_segment:
            svg_parts.append(trend_segment)

        # Points + labels (labels use owner name, not team name)
        for s in summary:
            cx = _sx(s["wrank"])
            cy = _sy(s["wpct"])
            label = _owner_for(s["name"], s["abbrev"])
            svg_parts.append(
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4.5" '
                f'fill="#f0c040" stroke="#1a1a1a" stroke-width="1">'
                f'<title>{s["name"]} ({label}): wRank {s["wrank"]:+.2f}, W% '
                f'{_fmt_wpct_s(s["wpct"])}</title></circle>'
            )
            svg_parts.append(
                f'<text x="{cx + 7:.1f}" y="{cy - 6:.1f}" fill="#ddd" '
                f'font-size="10" font-weight="600">{label}</text>'
            )
        svg_parts.append('</svg>')
        scatter_html = (
            '<h4 style="color:var(--text);margin:22px 0 6px;font-size:.9rem;'
            'font-weight:700">wRank vs Win %</h4>'
            '<p style="color:var(--muted);font-size:.72rem;margin:0 0 4px">'
            'Teams above the diagonal are outperforming their underlying '
            'category strength; teams below are underperforming.</p>'
            + "".join(svg_parts)
        )
    else:
        scatter_html = ""

    # ── Final wrapper. Division tables don't need horizontal scroll; the
    #    category table does on mobile (14 columns).
    return (
        '<div id="fant-standings-wrap" style="display:none;padding:18px 20px 0">'
        '<h3 style="color:var(--accent);margin:0 0 10px;font-size:1.05rem">'
        '&#x1F3C6; League Standings</h3>'
        '<p style="color:var(--muted);font-size:.78rem;margin:0 0 14px">'
        'Live from ESPN snapshot (last roster sync).</p>'
        # --- Divisional records ---
        + div_tables_html +
        # --- Category stats ---
        '<h4 style="color:var(--text);margin:18px 0 8px;font-size:.9rem;'
        'font-weight:700">Category Totals</h4>'
        '<p style="color:var(--muted);font-size:.72rem;margin:0 0 8px">'
        'Click any column to sort (best first). Each cell shows the category '
        'total, rank (#n), and z-score.</p>'
        '<div class="table-wrap" style="overflow-x:auto;-webkit-overflow-scrolling:touch">'
        '<table id="fant-standings-tbl" class="standings-tbl" '
        'style="min-width:720px;font-size:.82rem;border-collapse:collapse;width:100%">'
        f'<thead>{thead}</thead>'
        f'<tbody>{"".join(tr_html)}</tbody>'
        '</table></div>'
        # --- wRank summary table ---
        + summary_table_html
        # --- Scatter plot ---
        + scatter_html +
        '</div>'
    )


# ── Phase 3: trade-machine league-state payload ────────────────────────────
# Builds a JSON-serialisable snapshot of every team's roster, projected stats,
# baseline z-scores and ranks. The trade-machine JS uses this to recompute
# the league standings on-the-fly when a hypothetical trade is composed.
def _build_phase3_payload(fdata: dict) -> dict:
    """
    Returns a dict with the league state needed for in-browser trade simulation:
        {
          "ok": True/False,
          "user_team_id": int | None,         # "Team Alex" lookup
          "hit_cats":   ["R","HR","RBI","SO_h","SB","OBP"],
          "pit_cats":   ["W","SO_p","SV","HLD","ERA","WHIP"],
          "lower_better": ["SO_h","ERA","WHIP"],
          "slots": [[slot_id,label], ...],   # 11 hitter slot instances
          "teams": [
            {
              "team_id": int,
              "name": str,
              "abbrev": str,
              "hitters":  [ {espn_id, name, team, dollars, elig:[...],
                             R, HR, RBI, SO_h, SB, OBP, PA}, ... ],
              "pitchers": [ {espn_id, name, team, dollars,
                             W, SO_p, SV, HLD, ERA, WHIP, IP}, ... ],
              "stats":    {cat: float},     # baseline 12 totals
              "z":        {cat: float},     # baseline z-scores (lower-better negated)
              "rank":     {cat: int},
              "z_total":  float, "rank_total": int,
              "z_hit":    float, "rank_hit":   int,
              "z_pit":    float, "rank_pit":   int,
            }, ...
          ]
        }
    Returns {"ok": False, "reason": "..."} if the snapshot can't be loaded.
    """
    snap_path = None
    base = os.path.dirname(os.path.abspath(__file__))
    for fn in ESPN_ROSTER_FILES:
        candidate = os.path.join(base, fn)
        if os.path.exists(candidate):
            snap_path = candidate
            break
    if snap_path is None:
        return {"ok": False, "reason": "no snapshot"}

    try:
        from parse_espn_rosters import parse_league
        from lineup_optimizer import build_season_projections
    except Exception as e:
        return {"ok": False, "reason": f"import: {e}"}

    try:
        parsed = parse_league(snap_path, fdata, verbose=False)
        team_rows = build_season_projections(parsed, verbose=False)
    except Exception as e:
        return {"ok": False, "reason": f"build: {e}"}

    # Extract matchup schedule info from ESPN snapshot for weeks-remaining calc
    matchup_total = None
    matchup_current = None
    try:
        import json as _json_mod
        with open(snap_path, "r", encoding="utf-8") as _sf:
            _snap_raw = _json_mod.load(_sf)
        _raw = _snap_raw.get("raw", _snap_raw)
        _sched = (_raw.get("settings") or {}).get("scheduleSettings") or {}
        matchup_total = _sched.get("matchupPeriodCount")
        _status = _raw.get("status") or {}
        matchup_current = _status.get("currentMatchupPeriod")
    except Exception:
        pass

    if not team_rows:
        return {"ok": False, "reason": "empty league"}

    # Same hitter / pitcher z-subtotal logic that _render_season_projections uses
    h_sub = ["R", "HR", "RBI", "SO_h", "SB", "OBP"]
    p_sub = ["W", "SO_p", "SV", "HLD", "ERA", "WHIP"]
    for t in team_rows:
        t["z_hit"] = round(sum(t["z"][c] for c in h_sub), 3)
        t["z_pit"] = round(sum(t["z"][c] for c in p_sub), 3)
    for sub_key in ("z_hit", "z_pit"):
        order = sorted(team_rows, key=lambda x: x[sub_key], reverse=True)
        rank_key = "rank_hit" if sub_key == "z_hit" else "rank_pit"
        for i, t in enumerate(order, start=1):
            t[rank_key] = i

    # Build per-player records by joining each team's rostered players to fdata.
    # parsed["teams"] holds the raw hitter/pitcher recs (with elig + fdata).
    parsed_by_id = {pt["team_id"]: pt for pt in parsed.get("teams", [])}

    def _f(v) -> float:
        if v is None:
            return 0.0
        try:
            f = float(v)
            return 0.0 if f != f else f
        except (TypeError, ValueError):
            return 0.0

    def _hit_record(rec: dict) -> dict:
        fd = rec.get("fdata") or {}
        p  = fd.get("player") or {}
        return {
            "espn_id": rec.get("espn_id"),
            "name":    rec.get("name") or p.get("name") or "",
            "team":    rec.get("team") or "",
            "dollars": round(_f(fd.get("dollar")), 1),
            "elig":    list(rec.get("elig") or []),
            "R":    round(_f(p.get("R_p")),    1),
            "HR":   round(_f(p.get("HR_p")),   1),
            "RBI":  round(_f(p.get("RBI_p")),  1),
            "SO_h": round(_f(p.get("SO_p")),   1),  # hitter K
            "SB":   round(_f(p.get("SB_p")),   1),
            "OBP":  round(_f(p.get("OBP_p")),  4),
            "PA":   round(_f(p.get("PA_p")),   1),
        }

    def _pit_record(rec: dict) -> dict:
        fd = rec.get("fdata") or {}
        p  = fd.get("player") or {}
        return {
            "espn_id": rec.get("espn_id"),
            "name":    rec.get("name") or p.get("name") or "",
            "team":    rec.get("team") or "",
            "dollars": round(_f(fd.get("dollar")), 1),
            "W":    round(_f(p.get("W_p")),    1),
            "SO_p": round(_f(p.get("SO_p")),   1),
            "SV":   round(_f(p.get("SV_p")),   1),
            "HLD":  round(_f(p.get("HLD_p")),  1),
            "ERA":  round(_f(p.get("ERA_p")),  3),
            "WHIP": round(_f(p.get("WHIP_p")), 3),
            "IP":   round(_f(p.get("IP_p")),   1),
        }

    # Build a name→(team_id, espn_id) lookup covering ALL rostered players
    # (active + inactive). The trade pool uses this to tag IL/NA stars with a
    # team_id so they're not mistaken for free agents. Without this they'd
    # slip into the FA picker because _phase3["teams"] excludes inactive.
    #
    # IMPORTANT: the ESPN fullName and the FG projection name don't always
    # match exactly (e.g. ESPN "Cam Schlittler" vs FG "Cameron Schlittler").
    # We key by BOTH the ESPN name and the FG name (plus norm_name variants)
    # so the trade pool — which is built from FG data — still joins cleanly.
    try:
        from utils import norm_name as _norm_nm
    except Exception:
        def _norm_nm(s: str) -> str:
            return (s or "").strip().lower().replace(".", "").replace("-", " ")

    all_rostered: dict = {}
    all_elig: dict = {}  # name_key → [slot_id, ...] for ESPN position eligibility
    def _add_key(key: str, ptid, eid, elig_list=None) -> None:
        if key:
            all_rostered.setdefault(key, (ptid, eid))
            if elig_list is not None:
                all_elig.setdefault(key, elig_list)

    for pt in parsed.get("teams", []):
        ptid = pt.get("team_id")
        for rec in pt.get("hitters", []) + pt.get("pitchers", []):
            eid = rec.get("espn_id")
            _elig_raw = list(rec.get("elig") or [])
            espn_nm = (rec.get("name") or "").strip()
            _add_key(espn_nm.lower(), ptid, eid, _elig_raw)
            _add_key(_norm_nm(espn_nm), ptid, eid, _elig_raw)
            # Also key on the FG projection name if available — this catches
            # cases where FG and ESPN disagree on first-name spelling
            # (Cam/Cameron, Nicky/Nick, Matt/Matthew, etc.).
            fd = rec.get("fdata") or {}
            fg_p = fd.get("player") or {}
            fg_nm = (fg_p.get("name") or "").strip()
            if fg_nm:
                _add_key(fg_nm.lower(), ptid, eid, _elig_raw)
                _add_key(_norm_nm(fg_nm), ptid, eid, _elig_raw)

    teams_payload = []
    user_team_id = None
    for tr in team_rows:
        tid = tr.get("team_id")
        nm  = tr.get("name") or ""
        if user_team_id is None and nm.strip().lower() == "team alex":
            user_team_id = tid
        pt = parsed_by_id.get(tid, {})
        teams_payload.append({
            "team_id":    tid,
            "name":       nm,
            "abbrev":     tr.get("abbrev") or "",
            # IMPORTANT: filter out IL/NA (inactive) players before serializing.
            # build_season_projections() in lineup_optimizer.py filters to active
            # players before aggregating, so tr["stats"] (the Python baseline)
            # reflects only active players. If we hand the JS every hitter/pitcher
            # here, then _phase3SimulateTrade() will re-aggregate the two affected
            # teams over a DIFFERENT player pool than the baseline — inflating
            # their totals relative to the 8 untouched teams and corrupting the
            # league mean/sigma inside _phase3RecomputeZ. Keep the JS player pool
            # in lockstep with the Python baseline.
            "hitters":    [_hit_record(h) for h in pt.get("hitters",  []) if not h.get("inactive")],
            "pitchers":   [_pit_record(p) for p in pt.get("pitchers", []) if not p.get("inactive")],
            "stats":      tr.get("stats", {}),
            "z":          tr.get("z",     {}),
            "rank":       tr.get("rank",  {}),
            "z_total":    tr.get("z_total"),
            "rank_total": tr.get("rank_total"),
            "z_hit":      tr.get("z_hit"),
            "rank_hit":   tr.get("rank_hit"),
            "z_pit":      tr.get("z_pit"),
            "rank_pit":   tr.get("rank_pit"),
        })

    # ── Sim cfg (per-player mu/role/injury + role_models) ─────────────────
    # This is what the in-browser Monte Carlo uses to run the variability
    # math (sim_module.py's sigma + Cholesky + injury + closer logic)
    # rather than the old _MC_CV dict. If the caches are missing we leave
    # sim_cfg as None and the JS falls back to the legacy sim path.
    try:
        from sim_projections import build_sim_payload
        sim_cfg = build_sim_payload(parsed, fantasy_data=fdata, verbose=False)
        if not sim_cfg.get("ok"):
            sim_cfg = None
    except Exception as _e:
        sim_cfg = None

    return {
        "ok": True,
        "user_team_id": user_team_id,
        "hit_cats": h_sub,
        "pit_cats": p_sub,
        "lower_better": ["SO_h", "ERA", "WHIP"],
        "slots": [
            [0,  "C"],   [1, "1B"], [2, "2B"], [3, "3B"], [4, "SS"],
            [5, "OF"],   [5, "OF"], [5, "OF"],
            [6, "MI"],   [7, "CI"], [12, "UTIL"],
        ],
        "teams": teams_payload,
        # Sim cfg for the in-browser Monte Carlo — None if caches missing.
        "sim_cfg": sim_cfg,
        # ESPN H2H matchup schedule — used by Waiver Wire for weeks remaining
        "matchup_total": matchup_total,      # e.g. 21 regular-season weeks
        "matchup_current": matchup_current,  # e.g. 2 = in week 2
        # Internal-only: full roster name lookup including IL/NA. Caller strips
        # this before JSON-encoding for the JS payload.
        "_all_rostered": all_rostered,
        "_all_elig": all_elig,
    }


def render_fantasy_tab(fdata: dict, pos_lookup: dict | None = None,
                       il_pitcher_names: set | None = None) -> str:
    """Generate the full HTML for the Fantasy tab panel.

    il_pitcher_names: optional set of normalized pitcher names on the MLB 60-day
    IL. Used to tag TRADE_PITCHERS records with an `il` flag so the Waiver Wire
    streamer simulation can exclude them from the "top 8 FA SPs" baseline
    (otherwise high-$ injured arms like Bieber inflate what "streaming" looks
    like relative to reality).
    """
    if not fdata:
        return '<div id="fantasy-panel" class="tab-panel"></div>'

    cfg    = _FANT
    h_cats = cfg["h_cats"]
    p_cats = cfg["p_cats"]

    # ── stat display helpers ───────────────────────────────────────────────
    def _fmt_proj(v, cat):
        """Format a raw projected stat value for primary cell display."""
        if v is None:
            return ""
        fv = float(v)
        if cat == "OBP":
            return f"{fv:.3f}"
        if cat in ("ERA", "WHIP"):
            return f"{fv:.2f}"
        return str(int(round(fv)))

    def _fmt_mval(v):
        """Format a marginal dollar contribution as $X.X or −$X.X."""
        fv = float(v)
        return f"${fv:.1f}" if fv >= 0 else f"−${abs(fv):.1f}"

    def _get_stat(p, cat):
        """Return (dollar_contribution, projected_stat) for a category.
        'K' maps to 'SO' key in player dict."""
        key = "SO" if cat == "K" else cat
        dollar = p.get(key)
        proj   = p.get(key + "_p")
        try:
            dollar = float(dollar) if dollar is not None else None
        except (TypeError, ValueError):
            dollar = None
        try:
            proj = float(proj) if proj is not None else None
        except (TypeError, ValueError):
            proj = None
        return dollar, proj

    # ── build one HTML table ───────────────────────────────────────────────
    # Columns: # | Name | Team | Proj $ | [stat cols…]
    # Primary cell: projected stat (e.g. 48 HR); sub-text: dollar contribution
    _pos_lk = pos_lookup or {}

    def _build_table(players: list, cats: list, table_id: str,
                     info_cats: list | None = None,
                     show_pos: bool = False) -> str:
        """Build an HTML table.  *info_cats* are display-only columns
        (e.g. PA, IP) shown between Proj $ and the scoring categories.
        They render the projected stat value with no dollar contribution."""
        info_cats = info_cats or []
        sort_js = f"fantSort('{table_id}',this)"
        def _th(label, col_idx):
            return (f'<th onclick="{sort_js}" data-col="{col_idx}" '
                    f'style="cursor:pointer;user-select:none;white-space:nowrap">'
                    f'{label}&nbsp;<span class="fsi" '
                    f'style="color:var(--muted);font-size:.65rem;opacity:.35">'
                    f'&#9660;</span></th>')

        col = 0
        hdr_parts = ['<thead><tr>',
                     _th('#', col),
                     _th('Name', (col := col + 1)),
                     _th('Team', (col := col + 1)),
                     _th('Proj&nbsp;$', (col := col + 1))]
        for ic in info_cats:
            col += 1
            hdr_parts.append(_th(ic, col))
        for c in cats:
            col += 1
            hdr_parts.append(_th(c, col))
        hdr_parts.append('</tr></thead>')
        hdr = "".join(hdr_parts)

        rows_html = []
        for rank, entry in enumerate(players, 1):
            p    = entry["player"]
            nm   = p.get("name") or p.get("PlayerName") or "–"
            tm   = (p.get("team") or p.get("Team") or "").strip().upper()
            role = entry.get("role", "")
            fdol = entry["dollar"]
            fdol_str = _fmt_dollar(fdol)
            fdol_col = _dollar_color(fdol)
            fdol_val = fdol

            team_cell = _team_badge_py(tm)

            # Info-only columns (PA / IP) — just the projected value, no $
            info_cells = ""
            for ic in info_cats:
                raw = p.get(ic + "_p")
                try:
                    val = float(raw) if raw is not None else None
                except (TypeError, ValueError):
                    val = None
                if val is not None:
                    info_cells += (
                        f'<td style="text-align:center;padding:3px 6px;'
                        f'color:#999;font-size:.88rem" '
                        f'data-val="{val:.1f}">{val:.0f}</td>'
                    )
                else:
                    info_cells += (
                        f'<td style="text-align:center;opacity:.5" data-val="0">—</td>'
                    )

            stat_cells = ""
            for cat in cats:
                dollar, proj = _get_stat(p, cat)
                if dollar is None:
                    stat_cells += (
                        f'<td style="text-align:center;opacity:.5" data-val="0">—</td>'
                    )
                else:
                    dol_html  = (f'<div style="font-size:.9rem;line-height:1.15">'
                                 f'{_fmt_mval(dollar)}</div>')
                    proj_html = (f'<div style="font-size:.68rem;color:#777;font-weight:400;'
                                 f'line-height:1.1;margin-top:1px">'
                                 f'({_fmt_proj(proj, cat)})</div>'
                                 if proj is not None else '')
                    stat_cells += (
                        f'<td style="text-align:center;padding:3px 6px" '
                        f'data-val="{dollar}">'
                        f'{dol_html}{proj_html}</td>'
                    )

            # Position badge (ESPN elig → fg_pos fallback)
            _pos_raw = ""
            _pos_str = ""
            if show_pos:
                _pos_raw = _pos_lk.get(nm, "") or p.get("fg_pos", "")
                if _pos_raw:
                    _pos_str = (f'<span style="color:#777;font-size:.58rem;'
                                f'font-weight:700;margin-left:4px">{_pos_raw}</span>')

            rows_html.append(
                f'<tr data-role="{role}" data-pos="{_pos_raw}">'
                f'<td class="rank-col" data-val="{rank}">{rank}</td>'
                f'<td class="name-col">{nm}{_pos_str}</td>'
                f'<td style="white-space:nowrap">{team_cell}</td>'
                f'<td style="color:{fdol_col};font-weight:700;font-size:.95rem"'
                f' data-val="{fdol_val}">{fdol_str}</td>'
                f'{info_cells}{stat_cells}'
                f'</tr>'
            )

        return (f'<div class="table-wrap" id="{table_id}">'
                f'<table class="stats-table"><colgroup></colgroup>'
                f'{hdr}'
                f'<tbody>{"".join(rows_html)}</tbody>'
                f'</table></div>')

    # ── classify pitcher role (SP vs RP) from projected IP ────────────────
    for entry in fdata["fut_p"]:
        ip_v = entry["player"].get("_ip")
        ip = float(ip_v) if ip_v is not None else 0.0
        entry["role"] = "sp" if ip >= 100 else "rp"

    tbl_h = _build_table(fdata["fut_h"], h_cats, "fant-h-tbl", info_cats=["PA"], show_pos=True)
    tbl_p = _build_table(fdata["fut_p"], p_cats, "fant-p-tbl", info_cats=["IP"], show_pos=True)

    # ── trade tab: embed player pool as JSON for client-side search ─────────
    import json as _json
    def _sf(v):
        try: return round(float(v or 0), 1)
        except: return 0.0
    def _sfp(v, d=3):
        try: return round(float(v or 0), d)
        except: return 0.0
    # NOTE: hitter strikeouts use key 'K_h' (lower = better) and pitcher
    # strikeouts use key 'K_p' (higher = better). They used to share the key
    # 'K', which silently collided in mixed trade aggregation.
    _trade_h = []
    for _e in fdata["fut_h"]:
        _p = _e["player"]
        _trade_h.append({
            "name": _p.get("name",""), "team": (_p.get("team") or "").upper(),
            "fg_pos": _p.get("fg_pos", ""),
            "dollars": _sf(_e["dollar"]), "is_pitcher": False,
            "cats": {"R":_sf(_p.get("R")),"HR":_sf(_p.get("HR")),"RBI":_sf(_p.get("RBI")),
                     "SB":_sf(_p.get("SB")),"K_h":_sf(_p.get("SO")),"OBP":_sf(_p.get("OBP"))},
            # PA_p is carried so a waiver-pickup can still be PA-weighted
            # into a team's aggregate OBP during Phase 3 simulation.
            "proj": {"R":_sfp(_p.get("R_p"),0),"HR":_sfp(_p.get("HR_p"),0),
                     "RBI":_sfp(_p.get("RBI_p"),0),"SB":_sfp(_p.get("SB_p"),0),
                     "K_h":_sfp(_p.get("SO_p"),0),"OBP":_sfp(_p.get("OBP_p"),3),
                     "PA":_sfp(_p.get("PA_p"),0)}
        })
    # Build a normalized lookup for the 60-day IL names so the matching
    # tolerates ESPN/FG first-name differences (e.g. "Cam" vs "Cameron").
    try:
        from utils import norm_name as _norm_nm_il
    except Exception:
        def _norm_nm_il(s: str) -> str:
            return (s or "").strip().lower().replace(".", "").replace("-", " ")
    _il_norm_set = set()
    if il_pitcher_names:
        for _n in il_pitcher_names:
            if not _n:
                continue
            _il_norm_set.add(_norm_nm_il(_n))
    _trade_p = []
    for _e in fdata["fut_p"]:
        _p = _e["player"]
        _nm_raw = _p.get("name", "")
        _is_il = bool(_il_norm_set) and (_norm_nm_il(_nm_raw) in _il_norm_set)
        _trade_p.append({
            "name": _nm_raw, "team": (_p.get("team") or "").upper(),
            "role": _e.get("role","sp"), "dollars": _sf(_e["dollar"]), "is_pitcher": True,
            "il": _is_il,
            "cats": {"W":_sf(_p.get("W")),"ERA":_sf(_p.get("ERA")),"WHIP":_sf(_p.get("WHIP")),
                     "K_p":_sf(_p.get("SO")),"SV":_sf(_p.get("SV")),"HLD":_sf(_p.get("HLD"))},
            "proj": {"W":_sfp(_p.get("W_p"),0),"ERA":_sfp(_p.get("ERA_p"),2),
                     "WHIP":_sfp(_p.get("WHIP_p"),2),"K_p":_sfp(_p.get("SO_p"),0),
                     "SV":_sfp(_p.get("SV_p"),0),"HLD":_sfp(_p.get("HLD_p"),0),
                     "IP":_sfp(_p.get("IP_p"),1)}
        })
    _trade_h_json = _json.dumps(_trade_h)
    _trade_p_json = _json.dumps(_trade_p)

    # ── ESPN Season Projections sub-tab ────────────────────────────────────
    # Loads the bookmarklet-exported roster snapshot (if present), runs the
    # lineup optimizer + z-score pipeline, and renders the standings HTML.
    # Falls back to an instructional placeholder if the JSON isn't there yet.
    proj_html = _render_season_projections(fdata)
    # Standings sub-tab — reads espn_rosters.json for live team records +
    # category totals. Wrap in try/except so an edge case in the snapshot
    # data (missing fields, new ESPN schema, etc.) can't take down the
    # whole dashboard build. Empty wrapper still gets rendered so the
    # sub-tab button doesn't dangle.
    try:
        standings_html = _render_standings_html()
    except Exception as _e:
        import traceback as _tb
        print(f"  [standings] render failed: {_e}")
        _tb.print_exc()
        standings_html = (
            '<div id="fant-standings-wrap" style="display:none;padding:18px 20px 0">'
            f'<p style="color:var(--muted);font-size:.88rem">Standings render '
            f'failed: <code>{str(_e)[:200]}</code></p></div>'
        )

    # ── Phase 3: trade-machine league-state payload ────────────────────────
    # Builds the per-team rostered-player snapshot the JS uses to recompute
    # standings on the fly when a hypothetical trade is composed.
    # If the ESPN snapshot isn't present, the trade machine still functions
    # in its old "FG values only" mode (PHASE3 == null guards everything).
    _phase3 = _build_phase3_payload(fdata)
    if _phase3.get("ok"):
        # Tag every player in the (existing flat) trade pool with the team_id
        # they belong to, so the search can be filtered by side. Players who
        # aren't on any roster are dropped — you can't trade unrostered guys.
        # Use _all_rostered (which covers IL/NA players) rather than
        # _phase3["teams"] (which excludes them) so IL stars don't slip back
        # into the free-agent picker with a null team_id.
        _all_rostered = _phase3.get("_all_rostered") or {}
        _all_elig_map = _phase3.get("_all_elig") or {}
        try:
            from utils import norm_name as _norm_nm_caller
        except Exception:
            def _norm_nm_caller(s: str) -> str:
                return (s or "").strip().lower().replace(".", "").replace("-", " ")
        for _rec in _trade_h + _trade_p:
            _nm_raw = (_rec["name"] or "").strip()
            _nm_low = _nm_raw.lower()
            _nm_norm = _norm_nm_caller(_nm_raw)
            _tid_eid = (_all_rostered.get(_nm_low)
                        or _all_rostered.get(_nm_norm))
            if _tid_eid is not None:
                _rec["team_id"], _rec["espn_id"] = _tid_eid
            else:
                _rec["team_id"] = None
                _rec["espn_id"] = None
            # Attach ESPN position eligibility for hitters (rostered players)
            _elig_val = _all_elig_map.get(_nm_low) or _all_elig_map.get(_nm_norm)
            if _elig_val is not None:
                _rec["elig"] = _elig_val
        # Re-serialise with the new team_id / espn_id / elig fields baked in.
        _trade_h_json = _json.dumps(_trade_h)
        _trade_p_json = _json.dumps(_trade_p)
        # Strip the internal-only maps before shipping to JS.
        _phase3.pop("_all_rostered", None)
        _phase3.pop("_all_elig", None)
        _phase3_json = _json.dumps(_phase3)
    else:
        _phase3_json = "null"

    inner = f"""
<div id="fantasy-panel" class="tab-panel">
  <div style="padding:18px 20px 6px">
    <h2 style="color:var(--accent);margin:0 0 6px">&#x1F4B0; Fantasy Dollar Values</h2>
    <p style="color:var(--muted);font-size:.82rem;margin:0 0 14px">
      10-team H2H &nbsp;&bull;&nbsp; $260/team &nbsp;&bull;&nbsp; 6&times;6
      &nbsp;&bull;&nbsp; 35&nbsp;IP&nbsp;min
      &nbsp;&bull;&nbsp; Click any column header to sort
    </p>
    <div style="display:flex;gap:10px;margin-bottom:14px">
      <button id="fant-h-btn" class="tab-btn active"
              onclick="fantSwitch('h')"
              style="border-bottom:3px solid var(--accent);color:#fff;padding:8px 18px">
        Hitters
      </button>
      <button id="fant-p-btn" class="tab-btn"
              onclick="fantSwitch('p')"
              style="padding:8px 18px">
        Pitchers
      </button>
      <button id="fant-standings-btn" class="tab-btn"
              onclick="fantSwitch('standings')"
              style="padding:8px 18px">
        &#x1F3C6; Standings
      </button>
      <button id="fant-trade-btn" class="tab-btn"
              onclick="fantSwitch('trade')"
              style="padding:8px 18px">
        &#x1F4B1; Trade Machine
      </button>
      <button id="fant-proj-btn" class="tab-btn"
              onclick="fantSwitch('proj')"
              style="padding:8px 18px">
        &#x1F4CA; Season Projections
      </button>
      <button id="fant-cmp-btn" class="tab-btn"
              onclick="fantSwitch('cmp')"
              style="padding:8px 18px">
        &#x1F50D; Compare Players
      </button>
      <button id="fant-waiver-btn" class="tab-btn"
              onclick="fantSwitch('waiver')"
              style="padding:8px 18px">
        &#x267B; Waiver Wire
      </button>
    </div>
  </div>

  <!-- Hitters table -->
  <div id="fant-h-wrap">
    <div style="padding:6px 20px 10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <span style="color:var(--muted);font-size:.8rem;margin-right:4px">Position:</span>
      <button id="fh-all-btn" class="tab-btn active"
              onclick="fantHitFilter('all')"
              style="border-bottom:3px solid var(--accent);color:#fff;padding:5px 14px;font-size:.82rem">
        All
      </button>
      <button id="fh-C-btn" class="tab-btn" onclick="fantHitFilter('C')" style="padding:5px 14px;font-size:.82rem">C</button>
      <button id="fh-1B-btn" class="tab-btn" onclick="fantHitFilter('1B')" style="padding:5px 14px;font-size:.82rem">1B</button>
      <button id="fh-2B-btn" class="tab-btn" onclick="fantHitFilter('2B')" style="padding:5px 14px;font-size:.82rem">2B</button>
      <button id="fh-3B-btn" class="tab-btn" onclick="fantHitFilter('3B')" style="padding:5px 14px;font-size:.82rem">3B</button>
      <button id="fh-SS-btn" class="tab-btn" onclick="fantHitFilter('SS')" style="padding:5px 14px;font-size:.82rem">SS</button>
      <button id="fh-OF-btn" class="tab-btn" onclick="fantHitFilter('OF')" style="padding:5px 14px;font-size:.82rem">OF</button>
      <button id="fh-DH-btn" class="tab-btn" onclick="fantHitFilter('DH')" style="padding:5px 14px;font-size:.82rem">DH</button>
      <input id="fant-h-search" type="text" placeholder="&#128269; Search hitters…"
             oninput="fantSearchHit(this.value)"
             style="background:#1e1e1e;border:1px solid #444;color:#fff;
                    padding:6px 12px;border-radius:6px;font-size:.85rem;
                    width:240px;outline:none;margin-left:12px">
    </div>
    {tbl_h}
  </div>

  <!-- Pitchers section with SP/RP sub-toggle + search -->
  <div id="fant-p-wrap" style="display:none">
    <div style="padding:6px 20px 10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <span style="color:var(--muted);font-size:.8rem;margin-right:4px">Filter:</span>
      <button id="fp-all-btn" class="tab-btn active"
              onclick="fantPitchFilter('all')"
              style="border-bottom:3px solid var(--accent);color:#fff;padding:5px 14px;font-size:.82rem">
        All
      </button>
      <button id="fp-sp-btn" class="tab-btn"
              onclick="fantPitchFilter('sp')"
              style="padding:5px 14px;font-size:.82rem">
        SP
      </button>
      <button id="fp-rp-btn" class="tab-btn"
              onclick="fantPitchFilter('rp')"
              style="padding:5px 14px;font-size:.82rem">
        RP
      </button>
      <input id="fant-p-search" type="text" placeholder="&#128269; Search pitchers…"
             oninput="fantSearchPit(this.value)"
             style="background:#1e1e1e;border:1px solid #444;color:#fff;
                    padding:6px 12px;border-radius:6px;font-size:.85rem;
                    width:240px;outline:none;margin-left:12px">
    </div>
    {tbl_p}
  </div>

  <!-- Compare Players panel -->
  <div id="fant-cmp-wrap" style="display:none;padding:6px 20px 20px">
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px">
      <div style="position:relative;flex:1;min-width:260px;max-width:400px">
        <input id="fcmp-search" type="text" placeholder="&#128269; Search any player…"
               oninput="fcmpSearch()" autocomplete="off"
               style="background:#1e1e1e;border:1px solid #444;color:#fff;
                      padding:8px 14px;border-radius:6px;font-size:.88rem;
                      width:100%;box-sizing:border-box;outline:none">
        <div id="fcmp-dropdown"
             style="display:none;position:absolute;z-index:60;left:0;right:0;top:100%;
                    background:#1e1e1e;border:1px solid #444;border-radius:0 0 8px 8px;
                    max-height:280px;overflow-y:auto;box-shadow:0 6px 20px rgba(0,0,0,.6)">
        </div>
      </div>
      <button onclick="fcmpClear()"
              style="background:#333;color:#ccc;border:1px solid #555;border-radius:6px;
                     padding:7px 14px;cursor:pointer;font-size:.82rem;font-weight:600">
        Clear All
      </button>
      <span id="fcmp-cnt" style="color:var(--muted);font-size:.8rem"></span>
    </div>
    <div id="fcmp-chips" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px"></div>

    <!-- Hitters comparison -->
    <div id="fcmp-h-section" style="display:none;margin-bottom:18px">
      <h3 style="color:var(--accent);margin:0 0 6px;font-size:.92rem">Hitters</h3>
      <div class="table-wrap">
        <table id="fcmp-h-tbl" class="stats-table" style="table-layout:fixed">
          <colgroup>
            <col style="width:140px">
            <col style="width:55px">
            <col style="width:70px">
            <col style="width:60px">
            <col style="width:70px">
            <col style="width:70px">
            <col style="width:70px">
            <col style="width:70px">
            <col style="width:70px">
            <col style="width:70px">
            <col style="width:36px">
          </colgroup>
          <thead><tr>
            <th>Player</th>
            <th>Team</th>
            <th style="text-align:center">$</th>
            <th style="text-align:center">PA</th>
            <th style="text-align:center">R</th>
            <th style="text-align:center">HR</th>
            <th style="text-align:center">RBI</th>
            <th style="text-align:center">SB</th>
            <th style="text-align:center">K</th>
            <th style="text-align:center">OBP</th>
            <th></th>
          </tr></thead>
          <tbody id="fcmp-h-body"></tbody>
        </table>
      </div>
    </div>

    <!-- Pitchers comparison -->
    <div id="fcmp-p-section" style="display:none">
      <h3 style="color:var(--accent);margin:0 0 6px;font-size:.92rem">Pitchers</h3>
      <div class="table-wrap">
        <table id="fcmp-p-tbl" class="stats-table" style="table-layout:fixed">
          <colgroup>
            <col style="width:140px">
            <col style="width:55px">
            <col style="width:70px">
            <col style="width:70px">
            <col style="width:70px">
            <col style="width:70px">
            <col style="width:70px">
            <col style="width:70px">
            <col style="width:70px">
            <col style="width:70px">
            <col style="width:36px">
          </colgroup>
          <thead><tr>
            <th>Pitcher</th>
            <th>Team</th>
            <th style="text-align:center">$</th>
            <th style="text-align:center">IP</th>
            <th style="text-align:center">W</th>
            <th style="text-align:center">ERA</th>
            <th style="text-align:center">WHIP</th>
            <th style="text-align:center">K</th>
            <th style="text-align:center">SV</th>
            <th style="text-align:center">HLD</th>
            <th></th>
          </tr></thead>
          <tbody id="fcmp-p-body"></tbody>
        </table>
      </div>
    </div>

    <div id="fcmp-empty" style="text-align:center;padding:40px 0;color:var(--muted)">
      <div style="font-size:1.6rem;margin-bottom:8px">&#x1F50D;</div>
      <p style="margin:0">Search for any player above to start comparing.</p>
    </div>
  </div>
</div>

<!-- ══ TRADE CALCULATOR ══ -->
<div id="fant-trade-wrap" style="display:none;padding:18px 20px 0">

  <!-- Two-column layout: Sending | Compare | Receiving -->
  <div class="trade-three-col" style="display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap">

    <!-- ── SENDING ── -->
    <div style="flex:1;min-width:260px">
      <div style="color:var(--accent);font-weight:700;font-size:.88rem;margin-bottom:10px;
                  padding:6px 10px;background:rgba(255,80,70,.1);border-radius:6px;
                  border-left:3px solid var(--accent)">
        &#x1F4E4; Sending
      </div>
      <!-- Phase 3: send-side team selector -->
      <div id="trade-send-wrap" style="display:none;margin-bottom:8px">
        <label style="display:block;font-size:.7rem;color:var(--muted);font-weight:700;
                       text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">
          Trading from
        </label>
        <select id="trade-send-sel" onchange="tradeSetSender(this.value)"
                style="width:100%;background:#1e1e1e;border:1px solid #444;color:#fff;
                       padding:7px 10px;border-radius:6px;font-size:.84rem;outline:none">
        </select>
      </div>
      <div style="position:relative;margin-bottom:10px">
        <input id="trade-send-search" type="text" placeholder="&#128269; Add player&#8230;"
               oninput="tradeSearch('send',this.value)"
               onblur="setTimeout(function(){{var d=document.getElementById('trade-send-dd');if(d)d.style.display='none';}},160)"
               style="width:100%;box-sizing:border-box;background:#1e1e1e;border:1px solid #444;
                      color:#fff;padding:7px 12px;border-radius:6px;font-size:.84rem;outline:none">
        <div id="trade-send-dd"
             style="display:none;position:absolute;top:calc(100% + 2px);left:0;right:0;
                    background:#1a1a1a;border:1px solid #555;border-radius:6px;z-index:200;
                    max-height:220px;overflow-y:auto;box-shadow:0 4px 14px rgba(0,0,0,.6)"></div>
      </div>
      <div id="trade-send-list" style="min-height:50px"></div>
      <div style="margin-top:10px;padding:7px 10px;background:#1a1a1a;border-radius:6px;
                  display:flex;justify-content:space-between;align-items:center">
        <span style="color:var(--muted);font-size:.8rem">Total value</span>
        <span id="trade-send-total" style="font-weight:700;font-size:.93rem;color:#aaa">$0.0</span>
      </div>
    </div>

    <!-- ── CENTER: verdict + category breakdown ── -->
    <div style="width:240px;flex-shrink:0;display:flex;flex-direction:column;align-items:stretch">
      <div id="trade-verdict" style="text-align:center;margin-bottom:10px">
        <span style="color:var(--muted);font-size:.8rem">Add players<br>to both sides</span>
      </div>
      <div id="trade-cat-breakdown" style="width:100%"></div>
    </div>

    <!-- ── RECEIVING ── -->
    <div style="flex:1;min-width:260px">
      <div style="color:#4caf50;font-weight:700;font-size:.88rem;margin-bottom:10px;
                  padding:6px 10px;background:rgba(76,175,80,.1);border-radius:6px;
                  border-left:3px solid #4caf50">
        &#x1F4E5; Receiving
      </div>
      <!-- Phase 3: counterparty selector. Hidden if no PHASE3_LEAGUE. -->
      <div id="trade-counter-wrap" style="display:none;margin-bottom:8px">
        <label style="display:block;font-size:.7rem;color:var(--muted);font-weight:700;
                       text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">
          Trading with
        </label>
        <select id="trade-counter-sel" onchange="tradeSetCounter(this.value)"
                style="width:100%;background:#1e1e1e;border:1px solid #444;color:#fff;
                       padding:7px 10px;border-radius:6px;font-size:.84rem;outline:none">
        </select>
      </div>
      <div style="position:relative;margin-bottom:10px">
        <input id="trade-recv-search" type="text" placeholder="&#128269; Add player&#8230;"
               oninput="tradeSearch('recv',this.value)"
               onblur="setTimeout(function(){{var d=document.getElementById('trade-recv-dd');if(d)d.style.display='none';}},160)"
               style="width:100%;box-sizing:border-box;background:#1e1e1e;border:1px solid #444;
                      color:#fff;padding:7px 12px;border-radius:6px;font-size:.84rem;outline:none">
        <div id="trade-recv-dd"
             style="display:none;position:absolute;top:calc(100% + 2px);left:0;right:0;
                    background:#1a1a1a;border:1px solid #555;border-radius:6px;z-index:200;
                    max-height:220px;overflow-y:auto;box-shadow:0 4px 14px rgba(0,0,0,.6)"></div>
      </div>
      <div id="trade-recv-list" style="min-height:50px"></div>
      <div style="margin-top:10px;padding:7px 10px;background:#1a1a1a;border-radius:6px;
                  display:flex;justify-content:space-between;align-items:center">
        <span style="color:var(--muted);font-size:.8rem">Total value</span>
        <span id="trade-recv-total" style="font-weight:700;font-size:.93rem;color:#aaa">$0.0</span>
      </div>
    </div>

  </div>

  <!-- Phase 3: post-trade league impact. Populated by JS once a trade has
       at least one player on each side and PHASE3_LEAGUE is loaded. -->
  <div id="phase3-wrap" style="display:none;margin-top:18px;
                                border-top:1px solid #2a2a2a;padding-top:14px">
    <div style="font-size:.75rem;color:var(--muted);font-weight:700;
                text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">
      &#x1F4CA; League impact (rebalanced lineups, full z-score recompute)
    </div>
    <div id="phase3-delta"></div>
    <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
      <button id="phase3-lineup-btn" onclick="phase3ToggleLineups()"
              style="background:#1a1a1a;border:1px solid #333;color:#bbb;
                     padding:6px 14px;border-radius:6px;cursor:pointer;
                     font-size:.78rem;font-weight:600">
        &#x25BC; Show optimized lineups + staff (before / after)
      </button>
      <button id="phase3-toggle-btn" onclick="phase3ToggleTable()"
              style="background:#1a1a1a;border:1px solid #333;color:#bbb;
                     padding:6px 14px;border-radius:6px;cursor:pointer;
                     font-size:.78rem;font-weight:600">
        &#x25BC; Show full updated standings
      </button>
      <button id="phase3-mc-btn" onclick="phase3ToggleMc()"
              style="background:#1a1a1a;border:1px solid #333;color:#bbb;
                     padding:6px 14px;border-radius:6px;cursor:pointer;
                     font-size:.78rem;font-weight:600">
        &#x25BC; Show finish-probability sim (before / after)
      </button>
    </div>
    <div id="phase3-lineup-wrap" style="display:none;margin-top:10px"></div>
    <div id="phase3-table-wrap" style="display:none;margin-top:10px"></div>
    <div id="phase3-mc-wrap"     style="display:none;margin-top:10px"></div>
  </div>
</div>

<!-- ══ STANDINGS ══ -->
{standings_html}

<!-- ══ SEASON PROJECTIONS ══ -->
<div id="fant-proj-wrap" style="display:none">
  {proj_html}
</div>

<!-- ══ WAIVER WIRE ══ -->
<div id="fant-waiver-wrap" style="display:none;padding:18px 20px 0">
  <!-- Inner sub-tabs: Add/Drop | Stream Pitchers -->
  <div style="display:flex;gap:10px;margin-bottom:14px">
    <button id="ww-adddrop-btn" class="tab-btn active"
            onclick="wwSwitch('adddrop')"
            style="border-bottom:3px solid var(--accent);color:#fff;padding:6px 14px;font-size:.82rem">
      &#x1F504; Add / Drop
    </button>
    <button id="ww-stream-btn" class="tab-btn"
            onclick="wwSwitch('stream')"
            style="padding:6px 14px;font-size:.82rem">
      &#x1F525; Stream Pitchers
    </button>
  </div>

  <!-- ── ADD/DROP MODE ── -->
  <div id="ww-adddrop-wrap">
    <!-- Team selector -->
    <div style="margin-bottom:12px;max-width:320px">
      <label style="display:block;font-size:.7rem;color:var(--muted);font-weight:700;
                     text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">
        Select your team
      </label>
      <select id="ww-team-sel" onchange="wwSetTeam(this.value)"
              style="width:100%;background:#1e1e1e;border:1px solid #444;color:#fff;
                     padding:7px 10px;border-radius:6px;font-size:.84rem;outline:none">
      </select>
    </div>

    <!-- Current roster display + drop buttons -->
    <div id="ww-roster-wrap" style="display:none">
      <div style="font-size:.75rem;color:var(--muted);font-weight:700;
                  text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">
        Current Roster <span id="ww-roster-count" style="color:#888;font-weight:400"></span>
      </div>
      <div id="ww-roster-list" style="margin-bottom:12px"></div>

      <!-- Add player search (shown when team has open spot or has dropped someone) -->
      <div id="ww-add-section" style="display:none">
        <div style="font-size:.75rem;color:#4caf50;font-weight:700;
                    text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">
          &#x2795; Add from Free Agency
        </div>
        <div style="position:relative;margin-bottom:10px;max-width:400px">
          <input id="ww-add-search" type="text" placeholder="&#128269; Search free agents…"
                 oninput="wwSearchFA(this.value)" autocomplete="off"
                 onblur="setTimeout(function(){{var d=document.getElementById('ww-add-dd');if(d)d.style.display='none';}},160)"
                 style="width:100%;box-sizing:border-box;background:#1e1e1e;border:1px solid #444;
                        color:#fff;padding:7px 12px;border-radius:6px;font-size:.84rem;outline:none">
          <div id="ww-add-dd"
               style="display:none;position:absolute;top:calc(100% + 2px);left:0;right:0;
                      background:#1a1a1a;border:1px solid #555;border-radius:6px;z-index:200;
                      max-height:280px;overflow-y:auto;box-shadow:0 4px 14px rgba(0,0,0,.6)"></div>
        </div>
        <div id="ww-add-list" style="margin-bottom:12px"></div>
      </div>

      <!-- Results section -->
      <div id="ww-impact-wrap" style="display:none;margin-top:18px;
                                      border-top:1px solid #2a2a2a;padding-top:14px">
        <div style="font-size:.75rem;color:var(--muted);font-weight:700;
                    text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">
          &#x1F4CA; League impact (rebalanced lineups, full z-score recompute)
        </div>
        <div id="ww-delta"></div>
        <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
          <button id="ww-standings-btn" onclick="wwToggleTable()"
                  style="background:#1a1a1a;border:1px solid #333;color:#bbb;
                         padding:6px 14px;border-radius:6px;cursor:pointer;
                         font-size:.78rem;font-weight:600">
            &#x25BC; Show full updated standings
          </button>
          <button id="ww-mc-btn" onclick="wwToggleMc()"
                  style="background:#1a1a1a;border:1px solid #333;color:#bbb;
                         padding:6px 14px;border-radius:6px;cursor:pointer;
                         font-size:.78rem;font-weight:600">
            &#x25BC; Show finish-probability sim (before / after)
          </button>
        </div>
        <div id="ww-table-wrap" style="display:none;margin-top:10px"></div>
        <div id="ww-mc-wrap" style="display:none;margin-top:10px"></div>
      </div>
    </div>
  </div>

  <!-- ── STREAM PITCHERS MODE ── -->
  <div id="ww-stream-wrap" style="display:none">
    <!-- Team selector -->
    <div style="margin-bottom:12px;max-width:320px">
      <label style="display:block;font-size:.7rem;color:var(--muted);font-weight:700;
                     text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">
        Select your team
      </label>
      <select id="ww-stream-team-sel" onchange="wwStreamSetTeam(this.value)"
              style="width:100%;background:#1e1e1e;border:1px solid #444;color:#fff;
                     padding:7px 10px;border-radius:6px;font-size:.84rem;outline:none">
      </select>
    </div>

    <div id="ww-stream-body" style="display:none">
      <div style="background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:12px 16px;
                  margin-bottom:14px;font-size:.8rem;color:#bbb;line-height:1.5">
        <strong style="color:var(--accent)">How it works:</strong>
        This simulates streaming <strong>4 starting pitchers per week</strong> for the
        rest of the season. The streamer stats are the average RoS projections of the
        <strong>top 8 SP in free agency</strong> (100+ proj IP) by dollar value. If your roster is full,
        drop a player first to make room.
      </div>

      <!-- Drop to make room (optional) -->
      <div id="ww-stream-drop-section">
        <div style="font-size:.75rem;color:var(--muted);font-weight:700;
                    text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">
          Current Roster
        </div>
        <div id="ww-stream-roster" style="margin-bottom:12px"></div>
      </div>

      <!-- Streaming stats preview -->
      <div id="ww-stream-preview" style="display:none;margin-bottom:14px">
        <div style="font-size:.75rem;color:#4caf50;font-weight:700;
                    text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">
          &#x1F525; Streamer profile (avg of top 8 waiver SP, 100+ IP)
        </div>
        <div id="ww-stream-stats" style="background:#1a1a1a;border:1px solid #333;
                                          border-radius:8px;padding:10px 14px"></div>
      </div>

      <!-- Results section -->
      <div id="ww-stream-impact" style="display:none;margin-top:18px;
                                        border-top:1px solid #2a2a2a;padding-top:14px">
        <div style="font-size:.75rem;color:var(--muted);font-weight:700;
                    text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">
          &#x1F4CA; League impact with streaming (full z-score recompute)
        </div>
        <div id="ww-stream-delta"></div>
        <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
          <button id="ww-stream-standings-btn" onclick="wwStreamToggleTable()"
                  style="background:#1a1a1a;border:1px solid #333;color:#bbb;
                         padding:6px 14px;border-radius:6px;cursor:pointer;
                         font-size:.78rem;font-weight:600">
            &#x25BC; Show full updated standings
          </button>
          <button id="ww-stream-mc-btn" onclick="wwStreamToggleMc()"
                  style="background:#1a1a1a;border:1px solid #333;color:#bbb;
                         padding:6px 14px;border-radius:6px;cursor:pointer;
                         font-size:.78rem;font-weight:600">
            &#x25BC; Show finish-probability sim (before / after)
          </button>
        </div>
        <div id="ww-stream-table-wrap" style="display:none;margin-top:10px"></div>
        <div id="ww-stream-mc-wrap" style="display:none;margin-top:10px"></div>
      </div>
    </div>
  </div>
</div>

<script>
/* ── Hitter / Pitcher main toggle ────────────────────────────────── */
function fantSwitch(which) {{
  document.getElementById('fant-h-wrap').style.display     = which==='h'     ? '' : 'none';
  document.getElementById('fant-p-wrap').style.display     = which==='p'     ? '' : 'none';
  document.getElementById('fant-trade-wrap').style.display = which==='trade' ? '' : 'none';
  var sw = document.getElementById('fant-standings-wrap');
  if (sw) sw.style.display = which==='standings' ? '' : 'none';
  var pw = document.getElementById('fant-proj-wrap');
  if (pw) pw.style.display = which==='proj' ? '' : 'none';
  var cw = document.getElementById('fant-cmp-wrap');
  if (cw) cw.style.display = which==='cmp' ? '' : 'none';
  var ww = document.getElementById('fant-waiver-wrap');
  if (ww) ww.style.display = which==='waiver' ? '' : 'none';
  ['h','p','standings','trade','proj','cmp','waiver'].forEach(function(w) {{
    var btn = document.getElementById('fant-'+w+'-btn');
    if (!btn) return;
    var on = (w === which);
    btn.style.borderBottom = on ? '3px solid var(--accent)' : 'none';
    btn.style.color = on ? '#fff' : '';
  }});
}}

/* ── Standings sort: click any <th> to sort the standings table ────
   First click on a column sorts teams BEST first: higher-is-better
   stats (R, HR, RBI, etc.) sort descending; lower-is-better stats
   (ERA, WHIP, K-Bat, GB, Rank) sort ascending. Subsequent clicks on
   the same column reverse the direction. The data-rev attribute on
   each <th> ("hi" or "lo") tells us which direction is "best". */
window._standingsSort = {{col: 'rank', dir: 1}};
function fantStandingsSort(col, type, rev) {{
  var tbl = document.getElementById('fant-standings-tbl');
  if (!tbl) return;
  var st = window._standingsSort;
  if (st.col === col) {{
    st.dir = -st.dir;
  }} else {{
    st.col = col;
    // First click on a new column: 'hi' means higher-is-better → desc (-1).
    // 'lo' means lower-is-better (or smaller rank/GB) → asc (1).
    st.dir = (rev === 'hi') ? -1 : 1;
  }}
  var tbody = tbl.querySelector('tbody');
  var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
  rows.sort(function(a, b){{
    var av, bv;
    if (col === 'rank') {{ av = +a.dataset.rank; bv = +b.dataset.rank; }}
    else if (col === 'team') {{ av = a.dataset.team || ''; bv = b.dataset.team || ''; }}
    else if (col === 'wpct') {{ av = +a.dataset.wpct; bv = +b.dataset.wpct; }}
    else if (col === 'gb')   {{ av = +a.dataset.gb;   bv = +b.dataset.gb; }}
    else if (col.indexOf('cat_') === 0) {{
      var idx = Array.prototype.indexOf.call(tbl.querySelectorAll('thead th'),
        tbl.querySelector('th[data-sort="' + col + '"]'));
      var parseNum = function(td){{
        var t = (td.textContent || '').trim();
        if (t === '—' || t === '') return null;
        // Re-prepend leading 0 on rate stats like ".315"
        if (t.charAt(0) === '.') t = '0' + t;
        var f = parseFloat(t);
        return isNaN(f) ? null : f;
      }};
      av = parseNum(a.cells[idx]);
      bv = parseNum(b.cells[idx]);
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
    }}
    if (av < bv) return -st.dir;
    if (av > bv) return  st.dir;
    return 0;
  }});
  rows.forEach(function(r){{ tbody.appendChild(r); }});
  // Visual cue on active header
  tbl.querySelectorAll('thead th').forEach(function(th){{
    th.classList.remove('sort-asc', 'sort-desc');
    if (th.dataset.sort === col) {{
      th.classList.add(st.dir === 1 ? 'sort-asc' : 'sort-desc');
    }}
  }});
}}
// Wire up header clicks after the tab hydrates.
document.addEventListener('click', function(e){{
  var th = e.target.closest && e.target.closest('#fant-standings-tbl thead th[data-sort]');
  if (th) fantStandingsSort(th.dataset.sort, th.dataset.type, th.dataset.rev);
}});

/* ── SP / RP filter ──────────────────────────────────────────── */
var _fpRole = 'all';
var _fhPos = 'all';
function fantHitFilter(pos) {{
  _fhPos = pos;
  var tbl = document.getElementById('fant-h-tbl');
  if (!tbl) return;
  var srch = (document.getElementById('fant-h-search') || {{}}).value || '';
  srch = srch.toLowerCase();
  Array.from(tbl.querySelectorAll('tbody tr')).forEach(function(tr) {{
    var pv = (tr.dataset.pos || '').split('/');
    var matchPos = (pos === 'all') || pv.indexOf(pos) >= 0;
    var matchSearch = !srch || tr.textContent.toLowerCase().includes(srch);
    tr.style.display = (matchPos && matchSearch) ? '' : 'none';
  }});
  ['all','C','1B','2B','3B','SS','OF','DH'].forEach(function(p) {{
    var btn = document.getElementById('fh-'+p+'-btn');
    if (btn) {{
      btn.style.borderBottom = (p === pos) ? '3px solid var(--accent)' : 'none';
      btn.style.color = (p === pos) ? '#fff' : '';
    }}
  }});
  applyFantColors('fant-h-tbl');
}}
function fantSearchHit(q) {{
  var tbl = document.getElementById('fant-h-tbl');
  if (!tbl) return;
  q = q.toLowerCase();
  Array.from(tbl.querySelectorAll('tbody tr')).forEach(function(tr) {{
    var pv = (tr.dataset.pos || '').split('/');
    var matchPos = (_fhPos === 'all') || pv.indexOf(_fhPos) >= 0;
    var matchSearch = !q || tr.textContent.toLowerCase().includes(q);
    tr.style.display = (matchPos && matchSearch) ? '' : 'none';
  }});
  applyFantColors('fant-h-tbl');
}}

function fantPitchFilter(role) {{
  _fpRole = role;
  var tbl = document.getElementById('fant-p-tbl');
  if (!tbl) return;
  var srch = (document.getElementById('fant-p-search') || {{}}).value || '';
  srch = srch.toLowerCase();
  Array.from(tbl.querySelectorAll('tbody tr')).forEach(function(tr) {{
    var matchRole = (role === 'all') || (tr.dataset.role === role);
    var matchSearch = !srch || tr.textContent.toLowerCase().includes(srch);
    tr.style.display = (matchRole && matchSearch) ? '' : 'none';
  }});
  ['all','sp','rp'].forEach(function(r) {{
    var btn = document.getElementById('fp-'+r+'-btn');
    if (btn) {{
      btn.style.borderBottom = (r === role) ? '3px solid var(--accent)' : 'none';
      btn.style.color = (r === role) ? '#fff' : '';
    }}
  }});
  applyFantColors('fant-p-tbl');
}}

/* ── Text search ─────────────────────────────────────────────── */
function fantSearch(tblId, q) {{
  var tbl = document.getElementById(tblId);
  if (!tbl) return;
  q = q.toLowerCase();
  Array.from(tbl.querySelectorAll('tbody tr')).forEach(function(tr) {{
    tr.style.display = (!q || tr.textContent.toLowerCase().includes(q)) ? '' : 'none';
  }});
  applyFantColors(tblId);
}}
function fantSearchPit(q) {{
  var tbl = document.getElementById('fant-p-tbl');
  if (!tbl) return;
  q = q.toLowerCase();
  Array.from(tbl.querySelectorAll('tbody tr')).forEach(function(tr) {{
    var matchRole = (_fpRole === 'all') || (tr.dataset.role === _fpRole);
    var matchSearch = !q || tr.textContent.toLowerCase().includes(q);
    tr.style.display = (matchRole && matchSearch) ? '' : 'none';
  }});
  applyFantColors('fant-p-tbl');
}}

/* ── Column sort ─────────────────────────────────────────────── */
function fantSort(tblId, th) {{
  var tbl = document.getElementById(tblId);
  if (!tbl) return;
  var col = parseInt(th.dataset.col, 10);
  var tbody = tbl.querySelector('tbody');
  var rows  = Array.from(tbody.querySelectorAll('tr'));
  // Default first click = descending (highest first)
  var sortDesc = th.dataset.dir !== 'desc';
  th.dataset.dir = sortDesc ? 'desc' : 'asc';
  rows.sort(function(a, b) {{
    var av = parseFloat(a.cells[col].dataset.val) || 0;
    var bv = parseFloat(b.cells[col].dataset.val) || 0;
    return sortDesc ? (bv - av) : (av - bv);
  }});
  rows.forEach(function(r) {{ tbody.appendChild(r); }});
  Array.from(tbody.querySelectorAll('tr')).forEach(function(tr, i) {{
    var rc = tr.querySelector('.rank-col');
    if (rc) {{ rc.textContent = i + 1; rc.dataset.val = i + 1; }}
  }});
  // Update sort indicator arrows
  Array.from(tbl.querySelectorAll('thead th .fsi')).forEach(function(s) {{
    s.innerHTML = '&#9660;'; s.style.color = 'var(--muted)'; s.style.opacity = '.35';
  }});
  var activeInd = th.querySelector('.fsi');
  if (activeInd) {{
    activeInd.innerHTML = sortDesc ? '&#9660;' : '&#9650;';
    activeInd.style.color = 'var(--accent)';
    activeInd.style.opacity = '1';
  }}
  applyFantColors(tblId);
}}

/* ── Season Projections column sort ──────────────────────────── */
/* Sorts the #proj-table by the clicked header's data-col, using each
   <td>'s data-sort attribute (numeric or alphabetic). Each header has a
   data-default direction ("asc" or "desc"); first click uses that, then
   clicks toggle. Cell colors are baked in (per-stat rank), so we don't
   need to recompute them — we only re-order rows. */
function projSort(th) {{
  var tbl = document.getElementById('proj-table');
  if (!tbl) return;
  var col = parseInt(th.dataset.col, 10);
  if (isNaN(col)) return;
  var tbody = tbl.querySelector('tbody');
  if (!tbody) return;
  var rows = Array.from(tbody.querySelectorAll('tr'));

  var def = th.dataset.default || 'desc';
  var prev = th.dataset.dir || '';
  var dir;
  if (prev === 'asc')       dir = 'desc';
  else if (prev === 'desc') dir = 'asc';
  else                       dir = def;

  // Clear dir on every header, then set on the active one
  Array.from(tbl.querySelectorAll('thead th')).forEach(function(h) {{
    delete h.dataset.dir;
  }});
  th.dataset.dir = dir;

  function _val(tr) {{
    var cell = tr.cells[col];
    if (!cell) return null;
    return cell.dataset.sort != null ? cell.dataset.sort : cell.textContent;
  }}

  rows.sort(function(a, b) {{
    var av = _val(a), bv = _val(b);
    var an = parseFloat(av), bn = parseFloat(bv);
    var cmp;
    if (!isNaN(an) && !isNaN(bn)) {{
      cmp = an - bn;
    }} else {{
      cmp = String(av).localeCompare(String(bv));
    }}
    return dir === 'desc' ? -cmp : cmp;
  }});
  rows.forEach(function(r) {{ tbody.appendChild(r); }});

  // Update header arrow indicators
  Array.from(tbl.querySelectorAll('thead th .psi')).forEach(function(s) {{
    s.innerHTML = '&#9660;';
    s.style.color = 'var(--muted)';
    s.style.opacity = '.35';
  }});
  var ind = th.querySelector('.psi');
  if (ind) {{
    ind.innerHTML = (dir === 'desc') ? '&#9660;' : '&#9650;';
    ind.style.color = 'var(--accent)';
    ind.style.opacity = '1';
  }}
}}

/* ── Column color coding (gold leader + red→white→blue gradient) ─── */
/* Rankings always computed from ALL rows (full league), not just visible ones */
function applyFantColors(tblId) {{
  var tbl = document.getElementById(tblId);
  if (!tbl) return;
  var allRows = Array.from(tbl.querySelectorAll('tbody tr'));
  if (!allRows.length) return;
  var nCols = allRows[0].cells.length;
  // Gold for leader in each category column (skip rank=0, name=1, team=2, dollar=3)
  for (var c = 4; c < nCols; c++) {{
    var vals = [];
    allRows.forEach(function(tr) {{
      var v = parseFloat(tr.cells[c] && tr.cells[c].dataset.val);
      if (!isNaN(v)) vals.push(v);
    }});
    if (!vals.length) continue;
    var best = Math.max.apply(null, vals);
    allRows.forEach(function(tr) {{
      var cell = tr.cells[c];
      if (!cell) return;
      var v = parseFloat(cell.dataset.val);
      if (isNaN(v)) return;
      if (Math.abs(v - best) < 0.00001) {{
        cell.style.color = '#f0c040';
        cell.style.fontWeight = '700';
      }}
    }});
  }}
  // Gold for top dollar value (column 3)
  var dolVals = [];
  allRows.forEach(function(tr) {{
    var v = parseFloat(tr.cells[3] && tr.cells[3].dataset.val);
    if (!isNaN(v)) dolVals.push(v);
  }});
  if (dolVals.length) {{
    var bestDol = Math.max.apply(null, dolVals);
    allRows.forEach(function(tr) {{
      var cell = tr.cells[3];
      if (!cell) return;
      var v = parseFloat(cell.dataset.val);
      if (!isNaN(v) && Math.abs(v - bestDol) < 0.00001) {{
        cell.style.color = '#f0c040';
      }}
    }});
  }}
}}
// Apply colors on initial load
applyFantColors('fant-h-tbl');
applyFantColors('fant-p-tbl');

/* ── Compare Players (Fantasy tab) ─────────────────────────────── */
/* Uses TRADE_HITTERS / TRADE_PITCHERS — same data as the Fantasy $ tables.
   Color coding uses applyFantColors (gold leader + red→white→blue gradient)
   computed against the FULL league pool, not just the selected players. */
var _fcmpHNames = new Set();   // selected hitter names
var _fcmpPNames = new Set();   // selected pitcher names
var _fcmpDdIdx = -1;

function fcmpSearch() {{
  var q = document.getElementById('fcmp-search').value.toLowerCase().trim();
  var dd = document.getElementById('fcmp-dropdown');
  if (!q) {{ dd.style.display='none'; _fcmpDdIdx=-1; return; }}
  var hMatches = TRADE_HITTERS.filter(function(p) {{
    return p.name.toLowerCase().includes(q) || (p.team||'').toLowerCase().includes(q);
  }}).slice(0, 12);
  var pMatches = TRADE_PITCHERS.filter(function(p) {{
    return p.name.toLowerCase().includes(q) || (p.team||'').toLowerCase().includes(q);
  }}).slice(0, 12);
  var all = [];
  hMatches.forEach(function(p) {{ all.push({{player:p, type:'h'}}); }});
  pMatches.forEach(function(p) {{ all.push({{player:p, type:'p'}}); }});
  if (!all.length) {{ dd.style.display='none'; _fcmpDdIdx=-1; return; }}
  dd.innerHTML = all.map(function(item) {{
    var p = item.player;
    var isH = item.type === 'h';
    var added = isH ? _fcmpHNames.has(p.name) : _fcmpPNames.has(p.name);
    var tag = isH ? '<span style="color:#3d9be9;font-size:.7rem;font-weight:600;margin-right:4px">BAT</span>'
                  : '<span style="color:#e8832a;font-size:.7rem;font-weight:600;margin-right:4px">' + (p.role==='sp'?'SP':'RP') + '</span>';
    var _pl = isH ? _posLabel(p) : '';
    var posStr = _pl ? '<span style="color:#777;font-size:.6rem;font-weight:700;margin-left:4px">' + _pl + '</span>' : '';
    return '<div class="cmp-di" data-name="'+p.name.replace(/"/g,'&amp;quot;')+'" data-type="'+item.type+'" onmousedown="fcmpAdd(this.dataset.name,this.dataset.type)">'
      + tag + ' <span style="color:var(--muted);font-size:.78rem;margin-right:4px">' + (p.team||'') + '</span>'
      + '<span>' + p.name + '</span>' + posStr
      + (added ? '<span style="color:var(--muted);font-size:.72rem;margin-left:auto">Added</span>' : '')
      + '</div>';
  }}).join('');
  dd.style.display = '';
  _fcmpDdIdx = -1;
}}

function fcmpAdd(name, type) {{
  if (type === 'h') _fcmpHNames.add(name); else _fcmpPNames.add(name);
  document.getElementById('fcmp-search').value = '';
  document.getElementById('fcmp-dropdown').style.display = 'none';
  _fcmpDdIdx = -1;
  fcmpRender();
}}

function fcmpRemove(name, type) {{
  if (type === 'h') _fcmpHNames.delete(name); else _fcmpPNames.delete(name);
  fcmpRender();
}}

function fcmpClear() {{
  _fcmpHNames.clear();
  _fcmpPNames.clear();
  fcmpRender();
}}

/* Color compare-table cells using full-league data from TRADE arrays.
   keys = ordered list of stat keys matching columns starting at col 2 (after Name, Team).
   isHitter: true for hitters (K is negative = lower better), false for pitchers (ERA/WHIP negative). */
function _fcmpColorize(tblId, pool, keys, isHitter) {{
  var tbl = document.getElementById(tblId);
  if (!tbl) return;
  var rows = Array.from(tbl.querySelectorAll('tbody tr'));
  if (!rows.length) return;
  keys.forEach(function(key, ki) {{
    var colIdx = ki + 2;
    var allVals = [];
    pool.forEach(function(p) {{
      var v;
      if (key === 'dollars') v = p.dollars;
      else if (key === 'PA' || key === 'IP') v = (p.proj || {{}})[key] || (p.cats || {{}})[key];
      else v = (p.cats || {{}})[key];
      if (v != null && !isNaN(v)) allVals.push(v);
    }});
    if (!allVals.length) return;
    var best = Math.max.apply(null, allVals);
    rows.forEach(function(tr) {{
      var cell = tr.cells[colIdx];
      if (!cell) return;
      var v = parseFloat(cell.dataset.val);
      if (isNaN(v)) return;
      if (Math.abs(v - best) < 0.00001) {{
        cell.style.color = '#f0c040';
        cell.style.fontWeight = '700';
        return;
      }}
      var better = allVals.filter(function(x) {{ return x > v + 0.00001; }}).length;
      var total = allVals.length;
      if (total <= 1) return;
      var t = better / (total - 1);
      var r, g, b;
      if (t < 0.5) {{
        var s = t * 2;
        r = Math.round(255 + (235 - 255) * s);
        g = Math.round(60  + (235 - 60)  * s);
        b = Math.round(50  + (235 - 50)  * s);
      }} else {{
        var s2 = (t - 0.5) * 2;
        r = Math.round(235 + ( 60 - 235) * s2);
        g = Math.round(235 + (140 - 235) * s2);
        b = Math.round(235 + (255 - 235) * s2);
      }}
      cell.style.color = 'rgb(' + r + ',' + g + ',' + b + ')';
      cell.style.fontWeight = '600';
    }});
  }});
}}

function _fcmpFmtDol(v) {{
  if (v == null) return '—';
  var col = v >= 0 ? '#4CAF50' : '#e74c3c';
  return '<span style="color:'+col+';font-weight:700">$' + v.toFixed(1) + '</span>';
}}

function _fcmpStatCell(dolVal, projVal, cat) {{
  /* Two-line cell: dollar contribution on top, projected stat below */
  if (dolVal == null) return '<td style="text-align:center;padding:3px 6px;opacity:.5" data-val="0">—</td>';
  var dv = parseFloat(dolVal);
  var dStr = (dv >= 0 ? '$' : '\u2212$') + Math.abs(dv).toFixed(1);
  var proj = '';
  if (projVal != null) {{
    var pv = parseFloat(projVal);
    var pStr;
    if (cat === 'OBP') pStr = pv.toFixed(3);
    else if (cat === 'ERA' || cat === 'WHIP') pStr = pv.toFixed(2);
    else pStr = Math.round(pv).toString();
    proj = '<div style="font-size:.68rem;color:#777;font-weight:400;line-height:1.1;margin-top:1px">(' + pStr + ')</div>';
  }}
  return '<td style="text-align:center;padding:3px 6px" data-val="' + dv + '">'
    + '<div style="font-size:.9rem;line-height:1.15">' + dStr + '</div>'
    + proj + '</td>';
}}

function _fcmpInfoCell(val, decimals) {{
  /* Info-only cell (PA, IP) — just the projected value, no dollar */
  if (val == null) return '<td style="text-align:center;padding:3px 6px;opacity:.5" data-val="0">—</td>';
  var v = parseFloat(val);
  var s = decimals > 0 ? v.toFixed(decimals) : Math.round(v).toString();
  return '<td style="text-align:center;padding:3px 6px;color:#999;font-size:.88rem" data-val="' + v.toFixed(1) + '">' + s + '</td>';
}}

function fcmpRender() {{
  var hNames = Array.from(_fcmpHNames);
  var pNames = Array.from(_fcmpPNames);
  var hasH = hNames.length > 0;
  var hasP = pNames.length > 0;

  document.getElementById('fcmp-empty').style.display = (hasH || hasP) ? 'none' : '';
  document.getElementById('fcmp-h-section').style.display = hasH ? '' : 'none';
  document.getElementById('fcmp-p-section').style.display = hasP ? '' : 'none';

  var total = hNames.length + pNames.length;
  document.getElementById('fcmp-cnt').textContent = total ? total + ' player' + (total === 1 ? '' : 's') : '';

  // Chips
  var chips = [];
  hNames.forEach(function(name) {{
    var p = TRADE_HITTERS.find(function(x) {{ return x.name === name; }});
    if (!p) return;
    chips.push('<span style="display:inline-flex;align-items:center;gap:4px;background:#1a2a3a;'
      + 'border:1px solid #3d9be9;border-radius:14px;padding:3px 10px;font-size:.78rem;color:#7bb8e8">'
      + (p.team||'') + ' ' + p.name
      + ' <span data-name="'+p.name.replace(/"/g,'&amp;quot;')+'" data-type="h" onclick="fcmpRemove(this.dataset.name,this.dataset.type)" style="cursor:pointer;color:#888;font-weight:700;margin-left:2px">✕</span></span>');
  }});
  pNames.forEach(function(name) {{
    var p = TRADE_PITCHERS.find(function(x) {{ return x.name === name; }});
    if (!p) return;
    chips.push('<span style="display:inline-flex;align-items:center;gap:4px;background:#2a1f12;'
      + 'border:1px solid #e8832a;border-radius:14px;padding:3px 10px;font-size:.78rem;color:#e8b87a">'
      + (p.team||'') + ' ' + p.name
      + ' <span data-name="'+p.name.replace(/"/g,'&amp;quot;')+'" data-type="p" onclick="fcmpRemove(this.dataset.name,this.dataset.type)" style="cursor:pointer;color:#888;font-weight:700;margin-left:2px">✕</span></span>');
  }});
  document.getElementById('fcmp-chips').innerHTML = chips.join('');

  // Hitter table — uses same columns as Fantasy $ hitter table
  if (hasH) {{
    var tb = document.getElementById('fcmp-h-body');
    tb.innerHTML = hNames.map(function(name) {{
      var p = TRADE_HITTERS.find(function(x) {{ return x.name === name; }});
      if (!p) return '';
      var c = p.cats || {{}};
      var pr = p.proj || {{}};
      return '<tr>'
        + '<td class="nm">' + p.name + '</td>'
        + '<td style="white-space:nowrap">' + (p.team||'') + '</td>'
        + '<td style="text-align:center;padding:3px 6px" data-val="' + (p.dollars||0) + '">' + _fcmpFmtDol(p.dollars) + '</td>'
        + _fcmpInfoCell(pr.PA, 0)
        + _fcmpStatCell(c.R, pr.R, 'R')
        + _fcmpStatCell(c.HR, pr.HR, 'HR')
        + _fcmpStatCell(c.RBI, pr.RBI, 'RBI')
        + _fcmpStatCell(c.SB, pr.SB, 'SB')
        + _fcmpStatCell(c.K_h, pr.K_h, 'K_h')
        + _fcmpStatCell(c.OBP, pr.OBP, 'OBP')
        + '<td style="text-align:center"><button class="cmp-remove" data-name="'+p.name.replace(/"/g,'&amp;quot;')+'" data-type="h" onclick="fcmpRemove(this.dataset.name,this.dataset.type)">✕</button></td>'
        + '</tr>';
    }}).join('');
    // Color against full league
    _fcmpColorize('fcmp-h-tbl', TRADE_HITTERS, ['dollars','PA','R','HR','RBI','SB','K_h','OBP'], true);
  }}

  // Pitcher table — uses same columns as Fantasy $ pitcher table
  if (hasP) {{
    var ptb = document.getElementById('fcmp-p-body');
    ptb.innerHTML = pNames.map(function(name) {{
      var p = TRADE_PITCHERS.find(function(x) {{ return x.name === name; }});
      if (!p) return '';
      var c = p.cats || {{}};
      var pr = p.proj || {{}};
      return '<tr>'
        + '<td class="nm">' + p.name + '</td>'
        + '<td style="white-space:nowrap">' + (p.team||'') + '</td>'
        + '<td style="text-align:center;padding:3px 6px" data-val="' + (p.dollars||0) + '">' + _fcmpFmtDol(p.dollars) + '</td>'
        + _fcmpInfoCell(pr.IP, 1)
        + _fcmpStatCell(c.W, pr.W, 'W')
        + _fcmpStatCell(c.ERA, pr.ERA, 'ERA')
        + _fcmpStatCell(c.WHIP, pr.WHIP, 'WHIP')
        + _fcmpStatCell(c.K_p, pr.K_p, 'K_p')
        + _fcmpStatCell(c.SV, pr.SV, 'SV')
        + _fcmpStatCell(c.HLD, pr.HLD, 'HLD')
        + '<td style="text-align:center"><button class="cmp-remove" data-name="'+p.name.replace(/"/g,'&amp;quot;')+'" data-type="p" onclick="fcmpRemove(this.dataset.name,this.dataset.type)">✕</button></td>'
        + '</tr>';
    }}).join('');
    _fcmpColorize('fcmp-p-tbl', TRADE_PITCHERS, ['dollars','IP','W','ERA','WHIP','K_p','SV','HLD'], false);
  }}
}}

// Keyboard nav for compare search
(function() {{
  var el = document.getElementById('fcmp-search');
  if (!el) return;
  el.addEventListener('keydown', function(e) {{
    var dd = document.getElementById('fcmp-dropdown');
    var items = Array.from(dd.querySelectorAll('.cmp-di'));
    if (!items.length) return;
    if (e.key === 'ArrowDown') {{
      e.preventDefault();
      _fcmpDdIdx = Math.min(_fcmpDdIdx + 1, items.length - 1);
      items.forEach(function(el, i) {{ el.classList.toggle('active', i === _fcmpDdIdx); }});
    }} else if (e.key === 'ArrowUp') {{
      e.preventDefault();
      _fcmpDdIdx = Math.max(_fcmpDdIdx - 1, 0);
      items.forEach(function(el, i) {{ el.classList.toggle('active', i === _fcmpDdIdx); }});
    }} else if (e.key === 'Enter') {{
      e.preventDefault();
      if (_fcmpDdIdx >= 0 && items[_fcmpDdIdx]) {{
        var it = items[_fcmpDdIdx];
        fcmpAdd(it.dataset.name, it.dataset.type);
      }}
    }} else if (e.key === 'Escape') {{
      dd.style.display = 'none';
      _fcmpDdIdx = -1;
    }}
  }});
  el.addEventListener('blur', function() {{
    setTimeout(function() {{ document.getElementById('fcmp-dropdown').style.display = 'none'; }}, 200);
  }});
}})();

/* ── Trade Calculator ────────────────────────────────────────── */
var TRADE_HITTERS  = {_trade_h_json};
var TRADE_PITCHERS = {_trade_p_json};
/* Phase 3: full league state (per-team rosters + baseline z-scores).
   Null if no ESPN snapshot is present — the calculator falls back to the
   classic projected-stat-diff verdict only in that case. */
var PHASE3_LEAGUE  = {_phase3_json};
var _tradeRoster   = {{
  send: [], recv: [], sendTeamId: null, recvTeamId: null,
  pickups: [], pitcherPickups: [],          /* user-side waiver adds */
  oppPickups: [], oppPitcherPickups: [],    /* opponent-side waiver adds */
  drops: [], oppDrops: []                   /* roster drops when a side receives > sends */
}};
/* Active free-agent pickup-picker state. When set, _phase3RenderLineups renders
   an inline free-agent list in place of the "+" button for the target slot.
   Cleared on any add/remove. */
window._phase3PickerSlot     = null;
window._phase3OppPickerSlot  = null;
window._phase3PitcherPicker     = false;
window._phase3OppPitcherPicker  = false;
window._phase3DropPicker        = false;   /* user drop picker visible */
window._phase3OppDropPicker     = false;   /* opponent drop picker visible */

function tradeSearch(side, q) {{
  var dd = document.getElementById('trade-' + side + '-dd');
  q = (q || '').toLowerCase().trim();
  if (!q) {{ dd.style.display = 'none'; return; }}
  var added = _tradeRoster.send.concat(_tradeRoster.recv).map(function(p) {{ return p.name; }});
  var pool  = TRADE_HITTERS.concat(TRADE_PITCHERS);
  // Phase 3 filter: send side = selected send team, recv side = selected counterparty.
  // (If PHASE3 isn't loaded the pool stays unfiltered — old behavior.)
  if (PHASE3_LEAGUE) {{
    var sendTid = _tradeRoster.sendTeamId;
    var recvTid = _tradeRoster.recvTeamId;
    pool = pool.filter(function(p) {{
      if (p.team_id == null) return false;        // unrostered → drop
      if (side === 'send') return sendTid != null && p.team_id === sendTid;
      if (side === 'recv') return recvTid != null && p.team_id === recvTid;
      return true;
    }});
  }}
  var hits  = pool.filter(function(p) {{
    return added.indexOf(p.name) === -1 && p.name.toLowerCase().indexOf(q) !== -1;
  }}).slice(0, 8);
  if (!hits.length) {{
    dd.innerHTML = '<div style="padding:8px 12px;color:var(--muted);font-size:.82rem">No results</div>';
  }} else {{
    dd.innerHTML = hits.map(function(p) {{
      var meta = p.team + (p.role ? ' ' + p.role.toUpperCase() : '') + '  $' + p.dollars.toFixed(1);
      var _plt = !p.is_pitcher ? _posLabel(p) : '';
      var posTag = _plt ? '<span style="color:#777;font-size:.6rem;font-weight:700;margin-left:4px">' + _plt + '</span>' : '';
      return '<div data-side="' + side + '" data-name="' + p.name.replace(/"/g,"&quot;") + '"'
        + ' onmousedown="tradeAdd(this.dataset.side,this.dataset.name)"'
        + ' style="padding:7px 12px;cursor:pointer;border-bottom:1px solid #252525;font-size:.83rem"'
        + '>'
        + '<span style="font-weight:600">' + p.name + '</span>' + posTag
        + '<span style="opacity:.55;font-size:.77rem;margin-left:7px">' + meta + '</span>'
        + '</div>';
    }}).join('');
  }}
  dd.style.display = '';
}}

function tradeAdd(side, name) {{
  var pool = TRADE_HITTERS.concat(TRADE_PITCHERS);
  var p = null;
  for (var i=0;i<pool.length;i++) {{ if (pool[i].name === name) {{ p=pool[i]; break; }} }}
  if (!p) return;
  var already = false;
  for (var i=0;i<_tradeRoster[side].length;i++) {{ if (_tradeRoster[side][i].name===name) {{ already=true; break; }} }}
  if (!already) _tradeRoster[side].push(p);
  var inp = document.getElementById('trade-' + side + '-search');
  if (inp) inp.value = '';
  var dd = document.getElementById('trade-' + side + '-dd');
  if (dd) dd.style.display = 'none';
  _tradeRender();
}}

function tradeRemove(side, name) {{
  _tradeRoster[side] = _tradeRoster[side].filter(function(p) {{ return p.name !== name; }});
  // Clear pickups if the trade is now empty — orphaned waiver adds are
  // confusing and phase3 simulation won't run without both sides anyway.
  if (!_tradeRoster.send.length && !_tradeRoster.recv.length) {{
    _tradeRoster.pickups = [];
    _tradeRoster.pitcherPickups = [];
    _tradeRoster.oppPickups = [];
    _tradeRoster.oppPitcherPickups = [];
    _tradeRoster.drops = [];
    _tradeRoster.oppDrops = [];
    window._phase3PickerSlot = null;
    window._phase3OppPickerSlot = null;
    window._phase3PitcherPicker = false;
    window._phase3OppPitcherPicker = false;
    window._phase3DropPicker = false;
    window._phase3OppDropPicker = false;
  }}
  _tradeRender();
}}

function _tradePlayerRow(side, p) {{
  var d = p.dollars;
  var dStr = (d >= 0 ? '$' : '−$') + Math.abs(d).toFixed(1);
  var roleTag = p.role ? '<span style="opacity:.45;font-size:.68rem;margin-left:3px">' + p.role.toUpperCase() + '</span>' : '';
  var posTag = '';
  if (!p.is_pitcher) {{
    var lbl = _posLabel(p);
    if (lbl) posTag = '<span style="color:#888;font-size:.62rem;font-weight:700;margin-left:4px">' + lbl + '</span>';
  }}
  return '<div style="display:flex;align-items:center;justify-content:space-between;'
    + 'padding:5px 8px;background:#1a1a1a;border-radius:5px;margin-bottom:3px">'
    + '<div><span style="font-size:.83rem;font-weight:600">' + p.name + '</span>' + posTag
    + '<span style="font-size:.73rem;color:var(--muted);margin-left:5px">' + p.team + roleTag + '</span></div>'
    + '<div style="display:flex;align-items:center;gap:7px">'
    + '<span style="color:#ddd;font-weight:700;font-size:.82rem;white-space:nowrap">' + dStr + '</span>'
    + '<button data-side="' + side + '" data-name="' + p.name.replace(/"/g,"&quot;") + '"'
    + ' onmousedown="tradeRemove(this.dataset.side,this.dataset.name)"'
    + ' style="background:none;border:none;color:#888;cursor:pointer;font-size:.75rem;padding:2px 4px;line-height:1;border-radius:3px">&#x2715;</button>'
    + '</div></div>';
}}

function _tradeSectionRows(players, isPitcher) {{
  return players.filter(function(p) {{ return !!p.is_pitcher === isPitcher; }});
}}

function _tradeRender() {{
  ['send','recv'].forEach(function(side) {{
    var players = _tradeRoster[side];
    var hitters  = _tradeSectionRows(players, false);
    var pitchers = _tradeSectionRows(players, true);
    var html = '';
    if (!players.length) {{
      html = '<div style="color:var(--muted);font-size:.79rem;font-style:italic;padding:6px 2px">No players added</div>';
    }} else {{
      if (hitters.length) {{
        html += '<div style="font-size:.7rem;color:var(--muted);font-weight:700;text-transform:uppercase;'
              + 'letter-spacing:.05em;margin:6px 0 4px">&#x1F3CF; Hitters</div>';
        hitters.forEach(function(p) {{ html += _tradePlayerRow(side, p); }});
      }}
      if (pitchers.length) {{
        html += '<div style="font-size:.7rem;color:var(--muted);font-weight:700;text-transform:uppercase;'
              + 'letter-spacing:.05em;margin:8px 0 4px">&#x26BE; Pitchers</div>';
        pitchers.forEach(function(p) {{ html += _tradePlayerRow(side, p); }});
      }}
    }}
    document.getElementById('trade-' + side + '-list').innerHTML = html;
    var tot = _tradeRoster[side].reduce(function(s,p){{ return s + (p.dollars||0); }}, 0);
    var tel = document.getElementById('trade-' + side + '-total');
    if (tel) tel.textContent = (tot >= 0 ? '$' : '\u2212$') + Math.abs(tot).toFixed(1);
  }});
  _tradeCalc();
}}

/* \u2500\u2500 Trade Calc: verdict + per-stat breakdown \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500 */
// Counting stats are summed; rate stats (OBP/ERA/WHIP) are averaged across the side.
var TRADE_RATE_STATS = {{'OBP':true,'ERA':true,'WHIP':true}};
// Lower-is-better categories. Hitter strikeouts (K_h) are lower-better;
// pitcher strikeouts (K_p) are higher-better — they used to share the key
// 'K' which silently collided in mixed trades.
var TRADE_LOWER_BETTER = {{'ERA':true,'WHIP':true,'K_h':true}};
// Stat order so hitter cats line up with sidebar order, pitcher cats too
var TRADE_STAT_ORDER = ['R','HR','RBI','SB','K_h','OBP','W','SV','HLD','K_p','ERA','WHIP'];
// Display label map — lets us use disambiguated internal keys while showing
// friendlier labels in the UI. Unmapped keys render as-is.
var TRADE_DISPLAY_LABEL = {{'K_h':'K (H)','K_p':'K (P)'}};

function _aggProj(players) {{
  // Returns {{stat: aggregated_value}} — sums for counting stats, averages for rate stats
  var sums = {{}}, counts = {{}};
  players.forEach(function(p) {{
    if (!p.proj) return;
    Object.keys(p.proj).forEach(function(k) {{
      var v = parseFloat(p.proj[k]);
      if (isNaN(v)) return;
      sums[k] = (sums[k] || 0) + v;
      counts[k] = (counts[k] || 0) + 1;
    }});
  }});
  var out = {{}};
  Object.keys(sums).forEach(function(k) {{
    out[k] = TRADE_RATE_STATS[k] ? (sums[k] / counts[k]) : sums[k];
  }});
  return out;
}}

function _fmtStatDiff(k, diff) {{
  var sign = diff >= 0 ? '+' : '\u2212';
  var mag  = Math.abs(diff);
  if (k === 'OBP')  return sign + mag.toFixed(3);
  if (k === 'ERA' || k === 'WHIP') return sign + mag.toFixed(2);
  // Counting stats — round to nearest whole number
  return sign + Math.round(mag).toString();
}}

function _tradeCalc() {{
  var send = _tradeRoster['send'];
  var recv = _tradeRoster['recv'];
  var verdictEl = document.getElementById('trade-verdict');
  var breakEl   = document.getElementById('trade-cat-breakdown');
  if (!send.length && !recv.length) {{
    verdictEl.innerHTML = '<span style="color:var(--muted);font-size:.8rem">Add players<br>to both sides</span>';
    breakEl.innerHTML = '';
    return;
  }}
  var sendTot = send.reduce(function(s,p){{ return s + (p.dollars||0); }}, 0);
  var recvTot = recv.reduce(function(s,p){{ return s + (p.dollars||0); }}, 0);
  var net = recvTot - sendTot;

  // Resolve team names for the verdict
  var sendName = 'Sender', recvName = 'Receiver';
  if (PHASE3_LEAGUE) {{
    var _sT = PHASE3_LEAGUE.teams.find(function(t) {{ return t.team_id === _tradeRoster.sendTeamId; }});
    var _rT = PHASE3_LEAGUE.teams.find(function(t) {{ return t.team_id === _tradeRoster.recvTeamId; }});
    if (_sT) sendName = _sT.name;
    if (_rT) recvName = _rT.name;
  }}
  var arrowHtml, vc;
  if (net > 0.5) {{
    // Sender (left side) receives more value than they give → sender wins
    arrowHtml = '<div style="font-size:3rem;color:#4caf50;line-height:1;margin:4px 0">&#x25B6;</div>'
              + '<div style="font-size:.75rem;color:#4caf50;font-weight:800;letter-spacing:.06em">'
              + sendName.toUpperCase() + ' WINS</div>';
    vc = '#4caf50';
  }} else if (net < -0.5) {{
    // Sender gives more value than they get → receiver wins
    arrowHtml = '<div style="font-size:3rem;color:#e05555;line-height:1;margin:4px 0;transform:rotate(180deg)">&#x25B6;</div>'
              + '<div style="font-size:.75rem;color:#e05555;font-weight:800;letter-spacing:.06em">'
              + recvName.toUpperCase() + ' WINS</div>';
    vc = '#e05555';
  }} else {{
    arrowHtml = '<div style="font-size:2.4rem;color:#aaa;line-height:1;margin:4px 0">&#x21C6;</div>'
              + '<div style="font-size:.75rem;color:#aaa;font-weight:800;letter-spacing:.06em">EVEN</div>';
    vc = '#aaa';
  }}
  var netStr = (net >= 0 ? '+$' : '\u2212$') + Math.abs(net).toFixed(1);
  verdictEl.innerHTML = arrowHtml
    + '<div style="font-size:1.5rem;font-weight:800;color:' + vc + ';margin:6px 0 2px">' + netStr + '</div>'
    + '<div style="font-size:.69rem;color:var(--muted);margin-top:2px">'
    + '<span style="color:#e05555">&#x25BE; $' + sendTot.toFixed(1) + ' sent</span>'
    + '&nbsp;&nbsp;<span style="color:#4caf50">&#x25B4; $' + recvTot.toFixed(1) + ' recv</span>'
    + '</div>';

  // Use projected season totals (proj) for actual stat differentials
  var sendAgg = _aggProj(send);
  var recvAgg = _aggProj(recv);
  var seen = {{}}, allKeys = [];
  // Order keys via canonical TRADE_STAT_ORDER first, then anything unexpected
  TRADE_STAT_ORDER.forEach(function(k) {{
    if ((k in sendAgg) || (k in recvAgg)) {{ seen[k]=1; allKeys.push(k); }}
  }});
  Object.keys(sendAgg).concat(Object.keys(recvAgg)).forEach(function(k) {{
    if (!seen[k]) {{ seen[k]=1; allKeys.push(k); }}
  }});
  if (!allKeys.length) {{ breakEl.innerHTML=''; return; }}
  var bHtml = '<div style="font-size:.66rem;color:var(--muted);text-transform:uppercase;'
            + 'letter-spacing:.06em;font-weight:700;margin:8px 0 4px;text-align:center">'
            + 'Projected Season \u0394</div>'
            + '<div style="display:flex;flex-direction:column;gap:4px">';
  allKeys.forEach(function(k) {{
    var sv = sendAgg[k]||0, rv = recvAgg[k]||0;
    var rawDiff = rv - sv;
    var adjDiff = TRADE_LOWER_BETTER[k] ? -rawDiff : rawDiff;
    // Rate stats (ERA/WHIP/OBP) need a finer significance threshold.
    var isRate = (k === 'OBP' || k === 'ERA' || k === 'WHIP');
    var thresh = (k === 'OBP') ? 0.0005 : (isRate ? 0.005 : 0.5);
    var isPos = adjDiff >  thresh;
    var isNeg = adjDiff < -thresh;
    var col   = isPos ? '#4caf50' : isNeg ? '#e05555' : '#888';
    var arrow = isPos ? '&#x25B2;' : isNeg ? '&#x25BC;' : '&#x25A0;';
    var dispDiff = _fmtStatDiff(k, rawDiff);
    var label = TRADE_DISPLAY_LABEL[k] || k;
    bHtml += '<div style="display:flex;justify-content:space-between;align-items:center;'
           + 'padding:5px 9px;background:#1a1a1a;border-radius:5px;border-left:3px solid ' + col + '">'
           + '<span style="font-size:.76rem;font-weight:700;color:#ccc">' + label + '</span>'
           + '<span style="font-size:.8rem;font-weight:700;color:' + col + '">' + arrow + ' ' + dispDiff + '</span>'
           + '</div>';
  }});
  bHtml += '</div>';
  breakEl.innerHTML = bHtml;

  // Phase 3: full league recompute (only when ESPN snapshot is loaded
  // and the trade has at least one player on each side)
  if (PHASE3_LEAGUE && send.length && recv.length) {{
    _phase3RenderImpact();
  }} else {{
    var p3 = document.getElementById('phase3-wrap');
    if (p3) p3.style.display = 'none';
  }}
}}

/* ════════════════════════════════════════════════════════════════════
   ── Phase 3: Trade impact on league standings ─────────────────────
   ════════════════════════════════════════════════════════════════════
   When a trade has both sides populated, swap players between the two
   affected teams, re-optimize each team's hitter lineup with a JS LAP
   solver, re-aggregate stats, and recompute z-scores across all 10
   teams. Then render a compact before/after delta block plus an
   optional collapsible full standings table.
   ──────────────────────────────────────────────────────────────────── */

/* One-time init: populate the counterparty dropdown and default to the
   first non-user team. Hides the whole UI if PHASE3_LEAGUE is unavailable. */
function phase3Init() {{
  var sendWrap = document.getElementById('trade-send-wrap');
  var recvWrap = document.getElementById('trade-counter-wrap');
  if (!PHASE3_LEAGUE) {{
    if (sendWrap) sendWrap.style.display = 'none';
    if (recvWrap) recvWrap.style.display = 'none';
    return;
  }}
  var allTeams = PHASE3_LEAGUE.teams.slice().sort(function(a,b) {{
    return (a.name || '').localeCompare(b.name || '');
  }});
  var optHtml = '<option value="">\u2014 pick a team \u2014</option>';
  allTeams.forEach(function(t) {{
    optHtml += '<option value="' + t.team_id + '">' + t.name + '</option>';
  }});
  // Send-side dropdown
  var sendSel = document.getElementById('trade-send-sel');
  if (sendSel) {{ sendSel.innerHTML = optHtml; }}
  if (sendWrap) sendWrap.style.display = '';
  // Recv-side dropdown
  var recvSel = document.getElementById('trade-counter-sel');
  if (recvSel) {{ recvSel.innerHTML = optHtml; }}
  if (recvWrap) recvWrap.style.display = '';
}}
phase3Init();

/* Send-side team change handler. Wipes the send side because old players
   would belong to a different team. */
function tradeSetSender(val) {{
  _tradeRoster.sendTeamId = val ? parseInt(val, 10) : null;
  _tradeRoster.send = [];
  _tradeRoster.pickups = [];
  _tradeRoster.pitcherPickups = [];
  _tradeRoster.drops = [];
  window._phase3PickerSlot = null;
  window._phase3PitcherPicker = false;
  window._phase3DropPicker = false;
  var inp = document.getElementById('trade-send-search');
  if (inp) inp.value = '';
  _tradeRender();
}}

/* Counterparty change handler. Wipes the recv side because old players
   would belong to a different team. */
function tradeSetCounter(val) {{
  _tradeRoster.recvTeamId = val ? parseInt(val, 10) : null;
  _tradeRoster.recv = [];
  _tradeRoster.oppPickups = [];
  _tradeRoster.oppPitcherPickups = [];
  _tradeRoster.oppDrops = [];
  window._phase3OppPickerSlot = null;
  window._phase3OppPitcherPicker = false;
  window._phase3OppDropPicker = false;
  var inp = document.getElementById('trade-recv-search');
  if (inp) inp.value = '';
  _tradeRender();
}}

/* ── Hungarian LAP solver (square matrix, minimizes total cost) ─────
   Pure-JS port of the classic O(n^3) Munkres-Kuhn algorithm. We use it
   the same way scipy does on the Python side: build a cost matrix where
   ineligible (player, slot) pairs get a very large penalty and eligible
   pairs get -dollar (so minimizing cost = maximizing $).               */
function _phase3Hungarian(cost) {{
  var n = cost.length;
  if (!n) return [];
  var INF = 1e18;
  var u = new Array(n + 1).fill(0);
  var v = new Array(n + 1).fill(0);
  var p = new Array(n + 1).fill(0);
  var way = new Array(n + 1).fill(0);
  for (var i = 1; i <= n; i++) {{
    p[0] = i;
    var j0 = 0;
    var minv = new Array(n + 1).fill(INF);
    var used = new Array(n + 1).fill(false);
    do {{
      used[j0] = true;
      var i0 = p[j0], delta = INF, j1 = 0;
      for (var j = 1; j <= n; j++) {{
        if (used[j]) continue;
        var cur = cost[i0 - 1][j - 1] - u[i0] - v[j];
        if (cur < minv[j]) {{ minv[j] = cur; way[j] = j0; }}
        if (minv[j] < delta) {{ delta = minv[j]; j1 = j; }}
      }}
      for (var j = 0; j <= n; j++) {{
        if (used[j]) {{ u[p[j]] += delta; v[j] -= delta; }}
        else {{ minv[j] -= delta; }}
      }}
      j0 = j1;
    }} while (p[j0] !== 0);
    do {{ var j2 = way[j0]; p[j0] = p[j2]; j0 = j2; }} while (j0 !== 0);
  }}
  // p[j] = row assigned to column j → flip to row→col
  var ans = new Array(n).fill(-1);
  for (var jj = 1; jj <= n; jj++) {{
    if (p[jj] > 0) ans[p[jj] - 1] = jj - 1;
  }}
  return ans;
}}

/* Pick the best 11-hitter lineup from the given roster.
   Returns the subset of `hitters` (player records) that fill starting
   slots — same shape as the Python optimize_hitter_lineup output. */
/* LAP-optimize hitters and ALSO return per-slot assignment so callers
   can render the actual lineup the optimizer picked. */
function _phase3OptimizeHittersFull(hitters) {{
  var SLOTS = PHASE3_LEAGUE.slots;
  var nSlots = SLOTS.length;
  var emptyAssigns = [];
  for (var k = 0; k < nSlots; k++) {{
    emptyAssigns.push({{slot_id: SLOTS[k][0], slot_label: SLOTS[k][1], player: null}});
  }}
  if (!hitters || !hitters.length) return {{starters: [], assigns: emptyAssigns}};
  var nPlayers = hitters.length;
  var n = Math.max(nPlayers, nSlots);
  var INELIG = 1e9;
  var cost = [];
  for (var i = 0; i < n; i++) {{
    var row = new Array(n).fill(INELIG);
    if (i < nPlayers) {{
      var rec = hitters[i];
      var elig = rec.elig || [];
      var dollar = rec.dollars || 0;
      for (var j = 0; j < nSlots; j++) {{
        var slotId = SLOTS[j][0];
        if (elig.indexOf(slotId) !== -1) {{
          // Negate so the LAP minimizer maximizes dollar value.
          // +1 ensures even $0 players strictly beat ineligible cells.
          row[j] = -(dollar + 1.0);
        }}
      }}
    }}
    cost.push(row);
  }}
  var assign = _phase3Hungarian(cost);
  var starters = [];
  var slotPlayer = {{}};   // slot_index -> player record (only for valid fills)
  for (var r = 0; r < n; r++) {{
    var c = assign[r];
    if (c < 0 || c >= nSlots) continue;
    if (r >= nPlayers) continue;
    if (cost[r][c] >= INELIG / 2) continue;   // ineligible filler — skip
    starters.push(hitters[r]);
    slotPlayer[c] = hitters[r];
  }}
  var assigns = [];
  for (var jj = 0; jj < nSlots; jj++) {{
    assigns.push({{
      slot_id:    SLOTS[jj][0],
      slot_label: SLOTS[jj][1],
      player:     slotPlayer[jj] || null
    }});
  }}
  return {{starters: starters, assigns: assigns}};
}}

/* Backwards-compatible wrapper used by the simulation pipeline. */
function _phase3OptimizeHitters(hitters) {{
  return _phase3OptimizeHittersFull(hitters).starters;
}}

/* Sum counting stats + PA-weighted OBP across the optimized lineup */
function _phase3AggHit(starters) {{
  var out = {{R:0, HR:0, RBI:0, SO_h:0, SB:0, OBP:0}};
  var obpNum = 0, paTotal = 0;
  starters.forEach(function(p) {{
    out.R    += p.R    || 0;
    out.HR   += p.HR   || 0;
    out.RBI  += p.RBI  || 0;
    out.SO_h += p.SO_h || 0;
    out.SB   += p.SB   || 0;
    var pa = p.PA || 0, obp = p.OBP || 0;
    if (pa > 0 && obp > 0) {{ obpNum += obp * pa; paTotal += pa; }}
  }});
  out.OBP = paTotal > 0 ? (obpNum / paTotal) : 0;
  return out;
}}

/* Sum counting stats + IP-weighted ERA/WHIP across all rostered pitchers */
function _phase3AggPit(pitchers) {{
  var out = {{W:0, SO_p:0, SV:0, HLD:0, ERA:0, WHIP:0}};
  var eraNum = 0, whipNum = 0, ipTotal = 0;
  pitchers.forEach(function(p) {{
    out.W    += p.W    || 0;
    out.SO_p += p.SO_p || 0;
    out.SV   += p.SV   || 0;
    out.HLD  += p.HLD  || 0;
    var ip = p.IP || 0, era = p.ERA || 0, whip = p.WHIP || 0;
    if (ip > 0) {{
      if (era  > 0) eraNum  += era  * ip;
      if (whip > 0) whipNum += whip * ip;
      ipTotal += ip;
    }}
  }});
  out.ERA  = ipTotal > 0 ? (eraNum  / ipTotal) : 0;
  out.WHIP = ipTotal > 0 ? (whipNum / ipTotal) : 0;
  return out;
}}

/* Z-score every team across all 12 cats; return ranks + subtotals.
   Mutates the passed-in `teams` array in place by setting .stats, .z,
   .rank, .z_total, .z_hit, .z_pit, .rank_total, .rank_hit, .rank_pit. */
function _phase3RecomputeZ(teams) {{
  var allCats = PHASE3_LEAGUE.hit_cats.concat(PHASE3_LEAGUE.pit_cats);
  var lower = {{}};
  PHASE3_LEAGUE.lower_better.forEach(function(c) {{ lower[c] = true; }});
  // mean / std per category
  allCats.forEach(function(cat) {{
    var vals = teams.map(function(t) {{ return t.stats[cat] || 0; }});
    var mu = vals.reduce(function(a,b) {{ return a+b; }}, 0) / vals.length;
    var ssd = vals.reduce(function(a,b) {{ return a + (b-mu)*(b-mu); }}, 0);
    var sig = Math.sqrt(ssd / vals.length);
    if (sig < 1e-9) sig = 1e-9;
    teams.forEach(function(t) {{
      var v = t.stats[cat] || 0;
      var z = (v - mu) / sig;
      if (lower[cat]) z = -z;
      if (!t.z) t.z = {{}};
      t.z[cat] = Math.round(z * 1000) / 1000;
    }});
  }});
  // per-cat ranks
  allCats.forEach(function(cat) {{
    var sorted = teams.slice().sort(function(a,b) {{ return b.z[cat] - a.z[cat]; }});
    sorted.forEach(function(t, i) {{
      if (!t.rank) t.rank = {{}};
      t.rank[cat] = i + 1;
    }});
  }});
  // subtotals + total ranks
  teams.forEach(function(t) {{
    var zh = 0, zp = 0;
    PHASE3_LEAGUE.hit_cats.forEach(function(c) {{ zh += t.z[c]; }});
    PHASE3_LEAGUE.pit_cats.forEach(function(c) {{ zp += t.z[c]; }});
    t.z_hit = Math.round(zh * 1000) / 1000;
    t.z_pit = Math.round(zp * 1000) / 1000;
    t.z_total = Math.round((zh + zp) * 1000) / 1000;
  }});
  ['z_hit','z_pit','z_total'].forEach(function(key) {{
    var rk = key === 'z_hit' ? 'rank_hit' : key === 'z_pit' ? 'rank_pit' : 'rank_total';
    var sorted = teams.slice().sort(function(a,b) {{ return b[key] - a[key]; }});
    sorted.forEach(function(t, i) {{ t[rk] = i + 1; }});
  }});
  return teams;
}}

/* ══ Monte Carlo finish-probability simulation ═════════════════════════
   PLAYER-LEVEL rotisserie sim that mirrors sim_module.py. For every
   rostered player we:
     1. Sample (n_trials × n_stats) correlated shocks using the Cholesky
        factor of the MEASURED within-player residual correlation matrix
        (sim_backtest_cache.json), scaled by the single-year sigma_cv
        (yoy_sigma / sqrt(2)) per stat per role group.
     2. Apply a Beta-distributed injury fraction using the Zimmerman
        expected_loss from sim_data_cache.json.
     3. Apply discrete closer role-change events for projected closers
        on the same MLB team, transferring saves to the next-best RP.
   We then roll up to team totals (optimized 11 hitters + all pitchers)
   and score roto-style (rank each cat, sum points). _mcRunSim's external
   contract is the same as before — called with a `teams` array, returns
   {{team_id: {{probs, expFinish}}}} — so the Season Projections and Trade
   Machine buttons keep working unchanged.
   The old team-level sim (_MC_CV, _mcRunSimLegacy) is kept as a fallback
   for when sim_cfg isn't in the payload (caches missing, etc.).
   ───────────────────────────────────────────────────────────────────── */

var _MC_TRIALS = 50000;
var _mcBaselineCache = null;

/* ── Legacy knobs (fallback path only) ── */
var _MC_CV = {{
  R: 0.05, HR: 0.08, RBI: 0.06, SB: 0.12, SO_h: 0.06, OBP: 0.015,
  W: 0.10, SO_p: 0.06, SV: 0.15, HLD: 0.15, ERA: 0.05, WHIP: 0.03
}};

/* ── New-sim constants (mirror sim_module.py) ── */
var _MC_RATIO_STATS = {{OBP: 1, AVG: 1, ERA: 1, WHIP: 1, "K/9": 1, "BB/9": 1}};
var _MC_CONF_ROOKIE = 1.15;
var _MC_ROOKIE_PA   = 200;
var _MC_ROOKIE_IP   = 50;
var _MC_CATS        = ['R','HR','RBI','SO_h','SB','OBP',
                       'W','SO_p','SV','HLD','ERA','WHIP'];
var _MC_LOWER       = {{SO_h: 1, ERA: 1, WHIP: 1}};

/* Box-Muller standard normal. */
function _mcGaussian() {{
  var u = 1 - Math.random(), v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}}

/* Marsaglia-Tsang gamma sampler (shape a, scale 1). Uses the boost trick
   for a < 1. Fast and numerically stable for the 0.05..20 range this sim
   actually hits. */
function _mcSampleGamma(a) {{
  if (a < 1) {{
    var x = _mcSampleGamma(a + 1);
    var u = Math.random();
    return x * Math.pow(u, 1 / a);
  }}
  var d = a - 1 / 3;
  var c = 1 / Math.sqrt(9 * d);
  for (;;) {{
    var x, v;
    do {{
      x = _mcGaussian();
      v = 1 + c * x;
    }} while (v <= 0);
    v = v * v * v;
    var u2 = Math.random();
    if (u2 < 1 - 0.0331 * x * x * x * x) return d * v;
    if (Math.log(u2) < 0.5 * x * x + d * (1 - v + Math.log(v))) return d * v;
  }}
}}

/* Beta(a, b) via gamma ratio. */
function _mcSampleBeta(a, b) {{
  var ga = _mcSampleGamma(a), gb = _mcSampleGamma(b);
  return ga / (ga + gb);
}}

/* Build a fallback sim_cfg player entry from a raw team.hitters/pitchers
   record. Used when a player (eg. a free-agent pickup added in the trade
   machine) doesn't have a server-side sim_cfg entry. The sampler still
   runs — just without a Zimmerman injury profile. */
function _mcFallbackPlayerCfg(rec, isPit) {{
  if (isPit) {{
    var mu = {{
      IP:   rec.IP   || 0, W:   rec.W    || 0, SO: rec.SO_p || 0,
      ERA:  rec.ERA  || 0, WHIP: rec.WHIP || 0,
      SV:   rec.SV   || 0, HLD: rec.HLD  || 0
    }};
    var role = (mu.IP < 80 || mu.SV >= 5) ? 'RP' : 'SP';
    var out = {{
      is_pitcher:    true,
      role:          role,
      mlb_team:      (rec.team || '').toUpperCase(),
      mu:            mu,
      volume_proj:   mu.IP,
      expected_loss: 0
    }};
    // Pass through variance_scale for composite/streaming pitchers
    if (rec.variance_scale != null) out.variance_scale = rec.variance_scale;
    return out;
  }}
  return {{
    is_pitcher:    false,
    role:          'hitter',
    mlb_team:      (rec.team || '').toUpperCase(),
    mu: {{
      PA:  rec.PA  || 0, R:  rec.R  || 0, HR:  rec.HR  || 0,
      RBI: rec.RBI || 0, SB: rec.SB || 0, SO:  rec.SO_h || 0,
      OBP: rec.OBP || 0
    }},
    volume_proj:   rec.PA || 0,
    expected_loss: 0
  }};
}}

/* Sample a single player nTrials times. Returns {{statName: Float64Array}}.
   Uses correlated shocks from the role's Cholesky factor, scales by the
   measured single-year sigma, applies rookie confidence bump, applies a
   Beta-distributed injury trim on counting stats. */
function _mcSamplePlayer(player, roleModel, nTrials) {{
  var stats = roleModel.stats;
  var cvs   = roleModel.sigma_cv;
  var chol  = roleModel.chol;
  var k     = stats.length;

  var muVec = new Float64Array(k);
  var have  = new Uint8Array(k);
  for (var i = 0; i < k; i++) {{
    var v = (player.mu && player.mu[stats[i]] != null) ? player.mu[stats[i]] : null;
    if (v !== null && v !== undefined) {{
      muVec[i] = +v;
      have[i]  = 1;
    }}
  }}

  // Rookie confidence bump (wider sigma for low-volume samples)
  var conf = 1.0;
  var vol  = player.volume_proj || 0;
  if (player.is_pitcher) {{
    if (vol < _MC_ROOKIE_IP) conf = _MC_CONF_ROOKIE;
  }} else {{
    if (vol < _MC_ROOKIE_PA) conf = _MC_CONF_ROOKIE;
  }}

  // sigma[i] = |mu| × cv × conf × variance_scale
  // variance_scale < 1 for composite/streaming pitchers (diversification)
  var vScale = player.variance_scale || 1.0;

  var sig = new Float64Array(k);
  for (var i2 = 0; i2 < k; i2++) {{
    if (!have[i2]) continue;
    var m = Math.abs(muVec[i2]);
    if (_MC_RATIO_STATS[stats[i2]]) {{
      if (m < 0.01) m = 0.01;
    }} else {{
      if (m < 1.0) m = 1.0;
    }}
    sig[i2] = m * cvs[i2] * conf * vScale;
  }}

  // Output per-stat sample arrays
  var samples = {{}};
  for (var i3 = 0; i3 < k; i3++) {{
    if (have[i3]) samples[stats[i3]] = new Float64Array(nTrials);
  }}

  // Scratch: iid normals and correlated shocks
  var z = new Float64Array(k);
  for (var t = 0; t < nTrials; t++) {{
    for (var i4 = 0; i4 < k; i4++) z[i4] = _mcGaussian();
    // shock[i] = Σ_{{j≤i}} chol[i][j] × z[j]  (chol is lower triangular)
    for (var i5 = 0; i5 < k; i5++) {{
      if (!have[i5]) continue;
      var row = chol[i5];
      var s = 0;
      for (var j = 0; j <= i5; j++) s += row[j] * z[j];
      var val = muVec[i5] + sig[i5] * s;
      var sn = stats[i5];
      var isRatio = _MC_RATIO_STATS[sn];
      if (!isRatio) {{
        if (val < 0) val = 0;                 // counting stats floored at 0
      }} else if (sn === 'OBP' || sn === 'AVG') {{
        if (val < 0) val = 0;
        else if (val > 1) val = 1;
      }} else {{                              // ERA/WHIP/K9/BB9
        if (val < 0) val = 0;
      }}
      samples[sn][t] = val;
    }}
  }}

  // Injury trimming — Beta(a, b) centred on expected_loss / volume_proj.
  // We record the (1-keep) * vol "missing volume" per trial so the team
  // rollup can fill that gap with replacement-level production (a waiver
  // pickup) instead of leaving the playing time empty.
  var expLoss = player.expected_loss || 0;
  if (vol > 0 && expLoss > 0) {{
    var frac = expLoss / vol;
    if (frac < 0) frac = 0;
    if (frac > 0.9) frac = 0.9;
    var conc = 8.0;
    var aa = frac * conc;             if (aa < 0.05) aa = 0.05;
    var bb = (1 - frac) * conc;       if (bb < 0.05) bb = 0.05;
    var missingVol = new Float64Array(nTrials);
    for (var t2 = 0; t2 < nTrials; t2++) {{
      var keep = 1.0 - _mcSampleBeta(aa, bb);
      missingVol[t2] = (1.0 - keep) * vol;
      for (var i6 = 0; i6 < k; i6++) {{
        if (!have[i6]) continue;
        if (_MC_RATIO_STATS[stats[i6]]) continue;
        samples[stats[i6]][t2] *= keep;
      }}
    }}
    samples.__missingVol = missingVol;
  }}

  return samples;
}}

/* Apply closer role-change in-place on pitcher samples. pitcherRecs is a
   flat array of {{player, samples}} tuples for every rostered pitcher
   across every team. Pitchers are grouped by MLB team — a projected
   closer (mu.SV > threshold) rolls a fire event per trial; when fired,
   a fraction of their saves is transferred to the best-ERA same-team RP. */
function _mcApplyCloserRoleChange(pitcherRecs, cfg, nTrials) {{
  // Group by MLB team
  var byTeam = {{}};
  for (var i = 0; i < pitcherRecs.length; i++) {{
    var pr = pitcherRecs[i];
    var tm = (pr.player.mlb_team || '').toUpperCase();
    if (!tm) continue;
    if (!byTeam[tm]) byTeam[tm] = [];
    byTeam[tm].push(pr);
  }}

  for (var tm2 in byTeam) {{
    var roster = byTeam[tm2];
    for (var ci = 0; ci < roster.length; ci++) {{
      var closer = roster[ci];
      var svMu = (closer.player.mu && closer.player.mu.SV) || 0;
      if (svMu <= cfg.sv_threshold) continue;

      // 4-tier ERA-bucketed fire probability. Elite closers (projected
      // sub-3.00 ERA) almost always keep the job; average ones lose it
      // ~20% of seasons; shaky/bad ones are more volatile.
      var era = (closer.player.mu && closer.player.mu.ERA) || 4.0;
      var prob;
      if      (era <= cfg.elite_era)   prob = cfg.p_elite;
      else if (era <= cfg.average_era) prob = cfg.p_average;
      else if (era <= cfg.shaky_era)   prob = cfg.p_shaky;
      else                              prob = cfg.p_bad;
      if (prob > cfg.p_cap) prob = cfg.p_cap;

      // Find successor: lowest-ERA RP on same team (excluding the closer)
      var successor = null;
      var bestEra = Infinity;
      for (var si = 0; si < roster.length; si++) {{
        if (si === ci) continue;
        if (roster[si].player.role !== 'RP') continue;
        var e2 = (roster[si].player.mu && roster[si].player.mu.ERA) || 99;
        if (e2 < bestEra) {{
          bestEra   = e2;
          successor = roster[si];
        }}
      }}

      var svArr  = closer.samples.SV;
      var hldArr = closer.samples.HLD;
      var sucSv  = (successor && successor.samples.SV) ? successor.samples.SV : null;
      if (!svArr) continue;
      var keep = 1.0 - cfg.saves_transfer;
      for (var t3 = 0; t3 < nTrials; t3++) {{
        if (Math.random() < prob) {{
          var orig = svArr[t3];
          svArr[t3] = orig * keep;
          if (sucSv) sucSv[t3] += orig * cfg.saves_transfer;
          if (hldArr) hldArr[t3] += cfg.hld_compensation;
        }}
      }}
    }}
  }}
}}

/* Player-level _mcRunSim. Returns {{team_id: {{probs, expFinish}}}}. */
function _mcRunSim(teams) {{
  var N = teams.length;
  if (!N || !PHASE3_LEAGUE) return {{}};

  var cfg = PHASE3_LEAGUE.sim_cfg;
  if (!cfg || !cfg.ok || !cfg.role_models) {{
    // Fallback: caches not available — use the legacy team-level CV sim
    return _mcRunSimLegacy(teams);
  }}

  var nTrials    = _MC_TRIALS;
  var roleModels = cfg.role_models;
  var playerCfgs = cfg.players || {{}};
  var closerCfg  = cfg.closer_cfg;
  var replRates  = cfg.replacement_rates || {{}};  // per-role injury gap-fill

  // Per-team: optimize hitter lineup (same LAP the dashboard already uses),
  // then sample starters + all pitchers.
  var teamSamples  = new Array(N);
  var pitcherRecs  = [];

  for (var i = 0; i < N; i++) {{
    var team = teams[i];
    var starters = _phase3OptimizeHitters(team.hitters || []);
    var pitchers = team.pitchers || [];

    var recs = [];

    for (var h = 0; h < starters.length; h++) {{
      var hit = starters[h];
      var key = hit.espn_id != null ? String(hit.espn_id) : null;
      var pc  = (key && playerCfgs[key]) || _mcFallbackPlayerCfg(hit, false);
      var samp = _mcSamplePlayer(pc, roleModels.hitter, nTrials);
      recs.push({{player: pc, samples: samp, is_pitcher: false,
                  replacement: replRates.hitter || null}});
    }}

    for (var pi = 0; pi < pitchers.length; pi++) {{
      var pit = pitchers[pi];
      var pkey = pit.espn_id != null ? String(pit.espn_id) : null;
      var ppc  = (pkey && playerCfgs[pkey]) || _mcFallbackPlayerCfg(pit, true);
      var role = ppc.role === 'RP' ? 'RP' : 'SP';
      var psamp = _mcSamplePlayer(ppc, roleModels[role], nTrials);
      var prec = {{player: ppc, samples: psamp, is_pitcher: true,
                   replacement: replRates[role] || null}};
      recs.push(prec);
      pitcherRecs.push(prec);
    }}

    teamSamples[i] = recs;
  }}

  // Closer role-change across the full pitcher pool
  _mcApplyCloserRoleChange(pitcherRecs, closerCfg, nTrials);

  // Roll up per-team totals into Float64Arrays (one per cat)
  var teamTotals = new Array(N);
  for (var ti = 0; ti < N; ti++) {{
    var tot = {{}};
    for (var ci2 = 0; ci2 < _MC_CATS.length; ci2++) {{
      tot[_MC_CATS[ci2]] = new Float64Array(nTrials);
    }}
    var sumPA     = new Float64Array(nTrials);
    var sumOBPxPA = new Float64Array(nTrials);
    var sumIP     = new Float64Array(nTrials);
    var sumERAxIP = new Float64Array(nTrials);
    var sumWHPxIP = new Float64Array(nTrials);
    var recs2 = teamSamples[ti];
    for (var ri = 0; ri < recs2.length; ri++) {{
      var rec  = recs2[ri];
      var s    = rec.samples;
      var miss = s.__missingVol || null;      // null if no injury trimming
      var repl = rec.replacement || null;     // {{R, HR, ..., OBP}} or {{W, ERA, ..., WHIP}}
      if (rec.is_pitcher) {{
        if (s.W)   {{ var sW = s.W;   var oW = tot.W;    for (var t = 0; t < nTrials; t++) oW[t]   += sW[t]; }}
        if (s.SO)  {{ var sK = s.SO;  var oK = tot.SO_p; for (var t = 0; t < nTrials; t++) oK[t]   += sK[t]; }}
        if (s.SV)  {{ var sV = s.SV;  var oV = tot.SV;   for (var t = 0; t < nTrials; t++) oV[t]   += sV[t]; }}
        if (s.HLD) {{ var sH = s.HLD; var oH = tot.HLD;  for (var t = 0; t < nTrials; t++) oH[t]   += sH[t]; }}
        if (s.IP) {{
          var sI = s.IP, sE = s.ERA, sWH = s.WHIP;
          for (var t = 0; t < nTrials; t++) {{
            var ip = sI[t];
            sumIP[t] += ip;
            if (sE)  sumERAxIP[t] += sE[t]  * ip;
            if (sWH) sumWHPxIP[t] += sWH[t] * ip;
          }}
        }}
        // Replacement-level injury gap-fill (pitcher)
        if (miss && repl) {{
          var rW = repl.W || 0, rSO = repl.SO || 0, rSV = repl.SV || 0,
              rHLD = repl.HLD || 0, rERA = repl.ERA || 0, rWHIP = repl.WHIP || 0;
          var oW2 = tot.W, oK2 = tot.SO_p, oV2 = tot.SV, oH2 = tot.HLD;
          for (var t = 0; t < nTrials; t++) {{
            var mi = miss[t];
            if (mi <= 0) continue;
            oW2[t]  += rW   * mi;
            oK2[t]  += rSO  * mi;
            oV2[t]  += rSV  * mi;
            oH2[t]  += rHLD * mi;
            sumIP[t]     += mi;
            sumERAxIP[t] += rERA  * mi;
            sumWHPxIP[t] += rWHIP * mi;
          }}
        }}
      }} else {{
        if (s.R)   {{ var sR = s.R;   var oR = tot.R;    for (var t = 0; t < nTrials; t++) oR[t]   += sR[t]; }}
        if (s.HR)  {{ var sHr = s.HR; var oHr = tot.HR;  for (var t = 0; t < nTrials; t++) oHr[t]  += sHr[t]; }}
        if (s.RBI) {{ var sRb = s.RBI; var oRb = tot.RBI; for (var t = 0; t < nTrials; t++) oRb[t] += sRb[t]; }}
        if (s.SO)  {{ var sSO = s.SO; var oSO = tot.SO_h; for (var t = 0; t < nTrials; t++) oSO[t] += sSO[t]; }}
        if (s.SB)  {{ var sSB = s.SB; var oSB = tot.SB;  for (var t = 0; t < nTrials; t++) oSB[t] += sSB[t]; }}
        if (s.PA) {{
          var sPA = s.PA, sOBP = s.OBP;
          for (var t = 0; t < nTrials; t++) {{
            var pa = sPA[t];
            sumPA[t] += pa;
            if (sOBP) sumOBPxPA[t] += sOBP[t] * pa;
          }}
        }}
        // Replacement-level injury gap-fill (hitter)
        if (miss && repl) {{
          var rR = repl.R || 0, rHR = repl.HR || 0, rRBI = repl.RBI || 0,
              rSBh = repl.SB || 0, rSOh = repl.SO || 0, rOBP = repl.OBP || 0;
          var oR3 = tot.R, oHr3 = tot.HR, oRb3 = tot.RBI, oSO3 = tot.SO_h, oSB3 = tot.SB;
          for (var t = 0; t < nTrials; t++) {{
            var mi = miss[t];
            if (mi <= 0) continue;
            oR3[t]  += rR   * mi;
            oHr3[t] += rHR  * mi;
            oRb3[t] += rRBI * mi;
            oSO3[t] += rSOh * mi;
            oSB3[t] += rSBh * mi;
            sumPA[t]     += mi;
            sumOBPxPA[t] += rOBP * mi;
          }}
        }}
      }}
    }}
    // Finalize PA- and IP-weighted ratios
    var oOBP = tot.OBP, oERA = tot.ERA, oWHIP = tot.WHIP;
    for (var tx = 0; tx < nTrials; tx++) {{
      var pa = sumPA[tx]; if (pa < 1) pa = 1;
      var ip = sumIP[tx]; if (ip < 1) ip = 1;
      oOBP[tx]  = sumOBPxPA[tx] / pa;
      oERA[tx]  = sumERAxIP[tx] / ip;
      oWHIP[tx] = sumWHPxIP[tx] / ip;
    }}
    teamTotals[ti] = tot;
  }}

  // Rank each cat per trial, sum roto points, accumulate finish counts
  var counts = new Array(N);
  for (var ci3 = 0; ci3 < N; ci3++) {{
    counts[ci3] = new Int32Array(N);
  }}
  var roto = new Float64Array(N);
  var idx  = new Array(N);
  var nCats = _MC_CATS.length;

  for (var t4 = 0; t4 < nTrials; t4++) {{
    for (var ri2 = 0; ri2 < N; ri2++) roto[ri2] = 0;
    for (var k2 = 0; k2 < nCats; k2++) {{
      var c = _MC_CATS[k2];
      for (var ii = 0; ii < N; ii++) idx[ii] = ii;
      var isLower = _MC_LOWER[c] ? 1 : 0;
      (function(catName, low, trial) {{
        idx.sort(function(a, b) {{
          var va = teamTotals[a][catName][trial];
          var vb = teamTotals[b][catName][trial];
          return low ? (va - vb) : (vb - va);
        }});
      }})(c, isLower, t4);
      for (var jj = 0; jj < N; jj++) roto[idx[jj]] += (N - jj);
    }}
    for (var ii2 = 0; ii2 < N; ii2++) idx[ii2] = ii2;
    idx.sort(function(a, b) {{ return roto[b] - roto[a]; }});
    for (var jj2 = 0; jj2 < N; jj2++) counts[idx[jj2]][jj2]++;
  }}

  var result = {{}};
  for (var fi = 0; fi < N; fi++) {{
    var probs = new Array(N);
    var exp   = 0;
    for (var fj = 0; fj < N; fj++) {{
      probs[fj] = counts[fi][fj] / nTrials;
      exp += probs[fj] * (fj + 1);
    }}
    result[teams[fi].team_id] = {{probs: probs, expFinish: exp}};
  }}
  return result;
}}

/* Legacy team-level CV sim — kept as a fallback for when PHASE3_LEAGUE.sim_cfg
   is missing (caches not present, etc.). This was the pre-refactor behaviour. */
function _mcRunSimLegacy(teams) {{
  var N = teams.length;
  if (!N || !PHASE3_LEAGUE) return {{}};
  var cats  = PHASE3_LEAGUE.hit_cats.concat(PHASE3_LEAGUE.pit_cats);
  var nCats = cats.length;
  var lower = {{}};
  PHASE3_LEAGUE.lower_better.forEach(function(c) {{ lower[c] = true; }});

  var mu  = new Array(N);
  var sig = new Array(N);
  for (var i = 0; i < N; i++) {{
    var mrow = new Array(nCats);
    var srow = new Array(nCats);
    var st = teams[i].stats || {{}};
    for (var k = 0; k < nCats; k++) {{
      var c = cats[k];
      var v = st[c] || 0;
      mrow[k] = v;
      var s = Math.abs(v) * (_MC_CV[c] || 0.08);
      if (s < 1e-6) s = Math.max(1e-6, Math.abs(v) * 0.02);
      srow[k] = s;
    }}
    mu[i] = mrow;
    sig[i] = srow;
  }}
  var counts = new Array(N);
  for (var i = 0; i < N; i++) {{
    counts[i] = new Array(N);
    for (var j = 0; j < N; j++) counts[i][j] = 0;
  }}
  var sampled = new Array(N);
  for (var i = 0; i < N; i++) sampled[i] = new Array(nCats);
  var roto = new Array(N);
  var idx  = new Array(N);
  var nTrials = _MC_TRIALS;
  for (var trial = 0; trial < nTrials; trial++) {{
    for (var i = 0; i < N; i++) {{
      roto[i] = 0;
      for (var k = 0; k < nCats; k++) {{
        sampled[i][k] = mu[i][k] + sig[i][k] * _mcGaussian();
      }}
    }}
    for (var k = 0; k < nCats; k++) {{
      for (var i = 0; i < N; i++) idx[i] = i;
      var isLower = lower[cats[k]];
      var kk = k;
      idx.sort(function(a, b) {{
        var va = sampled[a][kk], vb = sampled[b][kk];
        return isLower ? (va - vb) : (vb - va);
      }});
      for (var i = 0; i < N; i++) roto[idx[i]] += (N - i);
    }}
    for (var i = 0; i < N; i++) idx[i] = i;
    idx.sort(function(a, b) {{ return roto[b] - roto[a]; }});
    for (var i = 0; i < N; i++) counts[idx[i]][i]++;
  }}
  var result = {{}};
  for (var i = 0; i < N; i++) {{
    var probs = new Array(N);
    var exp = 0;
    for (var j = 0; j < N; j++) {{
      probs[j] = counts[i][j] / nTrials;
      exp += probs[j] * (j + 1);
    }}
    result[teams[i].team_id] = {{probs: probs, expFinish: exp}};
  }}
  return result;
}}

/* Baseline league sim is expensive and never changes within a session;
   cache it so repeated trade-machine renders don't rerun it. */
function _mcGetBaseline() {{
  if (_mcBaselineCache) return _mcBaselineCache;
  _mcBaselineCache = _mcRunSim(PHASE3_LEAGUE.teams);
  return _mcBaselineCache;
}}

/* Render a rows=teams, cols=finish-positions heatmap table.
   Teams sorted ASC by expected finish (best on top).
   highlightTids: optional array of team_ids to bold / yellow-edge. */
function _mcRenderHeatmap(teams, sim, highlightTids) {{
  var rows = teams.map(function(t) {{
    var s = sim[t.team_id] || {{probs: [], expFinish: 0}};
    return {{
      team_id:   t.team_id,
      name:      t.name,
      probs:     s.probs,
      expFinish: s.expFinish
    }};
  }});
  rows.sort(function(a, b) {{ return a.expFinish - b.expFinish; }});
  var N = rows.length;
  var hl = {{}};
  (highlightTids || []).forEach(function(tid) {{ hl[tid] = true; }});

  function cellBg(p) {{
    if (p < 0.005) return 'transparent';
    var a = Math.min(0.85, 0.08 + p * 2.2);
    return 'rgba(78,180,100,' + a.toFixed(3) + ')';
  }}

  var html = '<table style="width:100%;border-collapse:collapse;background:#0e0e0e;'
           + 'border-radius:8px;overflow:hidden;font-size:.68rem">'
           + '<thead><tr style="background:#161616;color:var(--muted);'
           + 'text-transform:uppercase;letter-spacing:.05em">'
           + '<th style="padding:5px 10px;text-align:left;font-size:.62rem">Team</th>'
           + '<th style="padding:5px 6px;text-align:right;font-size:.62rem">Exp.</th>';
  for (var i = 1; i <= N; i++) {{
    html += '<th style="padding:5px 4px;text-align:center;width:32px;font-size:.62rem">' + i + '</th>';
  }}
  html += '</tr></thead><tbody>';
  rows.forEach(function(r) {{
    var hlStyle = hl[r.team_id] ? 'background:#1c1610;border-left:2px solid #f0c040;' : '';
    html += '<tr style="border-bottom:1px solid #1f1f1f;' + hlStyle + '">'
         +  '<td style="padding:4px 10px;font-weight:600;color:#ddd;white-space:nowrap">' + r.name + '</td>'
         +  '<td style="padding:4px 6px;text-align:right;font-weight:700;color:#ddd">' + r.expFinish.toFixed(1) + '</td>';
    for (var i = 0; i < N; i++) {{
      var p   = r.probs[i] || 0;
      var pct = p * 100;
      var label = pct < 0.5 ? '' : (pct < 10 ? pct.toFixed(1) : Math.round(pct).toString());
      html += '<td style="padding:3px;text-align:center;background:' + cellBg(p)
           +  ';color:#eee;font-weight:600">' + label + '</td>';
    }}
    html += '</tr>';
  }});
  html += '</tbody></table>';
  return html;
}}

/* Trade machine: render side-by-side before / after heatmaps with a
   compact expected-finish delta summary for the two involved teams. */
function _mcRenderBeforeAfter(baseTeams, newTeams, baseSim, newSim, userTid, oppTid) {{
  function fmtD(d) {{
    if (Math.abs(d) < 0.05) return '<span style="color:#888;font-weight:700">\u25A0 0.00</span>';
    var col  = d < 0 ? '#4caf50' : '#e05555';
    var sign = d < 0 ? '\u25B2' : '\u25BC';   // up arrow = better (lower exp finish)
    return '<span style="color:' + col + ';font-weight:700">' + sign + ' ' + Math.abs(d).toFixed(2) + '</span>';
  }}
  function row(label, tid, color) {{
    var b = baseSim[tid], a = newSim[tid];
    if (!b || !a) return '';
    return '<div style="display:grid;grid-template-columns:110px 1fr 1fr 1fr;gap:10px;'
         + 'padding:4px 0;font-size:.78rem;align-items:center">'
         + '<div style="color:' + color + ';font-weight:700;text-transform:uppercase;letter-spacing:.05em;font-size:.68rem">' + label + '</div>'
         + '<div style="color:var(--muted)">Before <span style="color:#fff;font-weight:700;margin-left:6px">' + b.expFinish.toFixed(2) + '</span></div>'
         + '<div style="color:var(--muted)">After  <span style="color:#fff;font-weight:700;margin-left:6px">' + a.expFinish.toFixed(2) + '</span></div>'
         + '<div>\u0394 ' + fmtD(a.expFinish - b.expFinish) + '</div>'
         + '</div>';
  }}
  var summary = '<div style="background:#131313;border-radius:8px;padding:10px 14px;margin-bottom:10px">'
              + '<div style="font-size:.68rem;color:var(--muted);font-weight:700;'
              + 'text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px">'
              + 'Expected finish \u2014 10,000-trial roto sim</div>'
              + row('You',          userTid, '#4caf50')
              + row('Counterparty', oppTid,  '#e05555')
              + '</div>';

  var hl = [userTid, oppTid];
  var grid = '<div style="display:flex;gap:12px;flex-wrap:wrap">'
           + '<div style="flex:1;min-width:320px">'
           + '<div style="font-size:.66rem;color:var(--muted);font-weight:700;'
           + 'text-transform:uppercase;letter-spacing:.05em;padding:0 0 6px 4px">Before trade</div>'
           + _mcRenderHeatmap(baseTeams, baseSim, hl)
           + '</div>'
           + '<div style="flex:1;min-width:320px">'
           + '<div style="font-size:.66rem;color:var(--muted);font-weight:700;'
           + 'text-transform:uppercase;letter-spacing:.05em;padding:0 0 6px 4px">After trade</div>'
           + _mcRenderHeatmap(newTeams, newSim, hl)
           + '</div>'
           + '</div>';
  return summary + grid;
}}

function phase3ToggleMc() {{
  var mw  = document.getElementById('phase3-mc-wrap');
  var btn = document.getElementById('phase3-mc-btn');
  if (!mw) return;
  var open = mw.style.display !== 'none';
  if (open) {{
    mw.style.display = 'none';
    btn.innerHTML = '\u25BC Show finish-probability sim (before / after)';
  }} else {{
    if (!window._phase3Last || !window._phase3Last.newLeague) return;
    _phase3RenderMcSim();
    mw.style.display = '';
    btn.innerHTML = '\u25B2 Hide finish-probability sim';
  }}
}}

function _phase3RenderMcSim() {{
  var mw = document.getElementById('phase3-mc-wrap');
  if (!mw || !window._phase3Last || !window._phase3Last.newLeague) return;
  mw.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:.75rem">Running 50,000 trials\u2026</div>';
  setTimeout(function() {{
    var L = window._phase3Last;
    var userTid = _tradeRoster.sendTeamId;
    var oppTid  = _tradeRoster.recvTeamId;
    var baseSim = _mcGetBaseline();
    var newSim  = _mcRunSim(L.newLeague);
    mw.innerHTML = _mcRenderBeforeAfter(PHASE3_LEAGUE.teams, L.newLeague, baseSim, newSim, userTid, oppTid);
  }}, 20);
}}

/* Season Projections tab handler: run baseline sim over the current league
   and render a single heatmap with Team Alex highlighted. */
function mcRunSeasonProjSim() {{
  if (!PHASE3_LEAGUE) return;
  var out = document.getElementById('mc-proj-out');
  if (!out) return;
  out.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:.75rem">Running 50,000 trials\u2026</div>';
  setTimeout(function() {{
    var sim = _mcGetBaseline();
    var userTid = PHASE3_LEAGUE.user_team_id;
    out.innerHTML = _mcRenderHeatmap(PHASE3_LEAGUE.teams, sim, userTid != null ? [userTid] : []);
  }}, 20);
}}


/* Build a deep-copied league with the trade applied. The two affected
   teams have their hitter / pitcher lists rewritten (subtract sent
   players, add received players), then their lineups are re-optimized
   and stats re-aggregated. Untouched teams keep their existing stats. */
function _phase3SimulateTrade() {{
  var league = JSON.parse(JSON.stringify(PHASE3_LEAGUE.teams));
  var userTid = _tradeRoster.sendTeamId;
  var oppTid  = _tradeRoster.recvTeamId;
  if (userTid == null || oppTid == null) return null;

  var sendIds = {{}};   // espn_ids leaving the user
  var recvIds = {{}};   // espn_ids leaving the opponent
  _tradeRoster.send.forEach(function(p) {{ if (p.espn_id != null) sendIds[p.espn_id] = true; }});
  _tradeRoster.recv.forEach(function(p) {{ if (p.espn_id != null) recvIds[p.espn_id] = true; }});

  function findById(teamId, espn_id) {{
    var tm = league.find(function(t) {{ return t.team_id === teamId; }});
    if (!tm) return null;
    var hit = tm.hitters .find(function(p) {{ return p.espn_id === espn_id; }});
    if (hit) return hit;
    return tm.pitchers.find(function(p) {{ return p.espn_id === espn_id; }});
  }}

  // Pull the actual player records (with full stat fields) from the
  // original league snapshot — the trade-roster entries don't carry the
  // hitter PA / pitcher IP fields needed for re-aggregation.
  var sendPlayers = _tradeRoster.send.map(function(p) {{
    return findById(userTid, p.espn_id) || p;
  }});
  var recvPlayers = _tradeRoster.recv.map(function(p) {{
    return findById(oppTid, p.espn_id) || p;
  }});

  league.forEach(function(team) {{
    if (team.team_id !== userTid && team.team_id !== oppTid) return;
    var isUserTeam = (team.team_id === userTid);
    var rmIds = isUserTeam ? sendIds : recvIds;
    var addList = isUserTeam ? recvPlayers : sendPlayers;
    var origPitIds = team.pitchers.map(function(p){{ return p.espn_id; }}).sort().join();
    team.hitters  = team.hitters .filter(function(p) {{ return !rmIds[p.espn_id]; }});
    team.pitchers = team.pitchers.filter(function(p) {{ return !rmIds[p.espn_id]; }});
    addList.forEach(function(p) {{
      // Decide bucket: if it has elig list it's a hitter, else pitcher
      // (every hitter record carries elig; pitchers don't)
      if (p.elig && p.elig.length) team.hitters.push(p);
      else team.pitchers.push(p);
    }});

    // Apply roster drops for this side. Drops carry espn_id from the
    // original roster so this filters out any player the user chose to drop
    // when the trade's inbound count exceeds outbound. Drops never remove
    // players that are being sent away (already gone above) because the
    // drop picker excludes those from its list.
    var dropList = isUserTeam
      ? (_tradeRoster.drops || [])
      : (_tradeRoster.oppDrops || []);
    if (dropList.length) {{
      var dropIds = {{}};
      dropList.forEach(function(d) {{ if (d.espn_id != null) dropIds[d.espn_id] = true; }});
      team.hitters  = team.hitters .filter(function(p) {{ return !dropIds[p.espn_id]; }});
      team.pitchers = team.pitchers.filter(function(p) {{ return !dropIds[p.espn_id]; }});
    }}

    // Apply any free-agent hitter pickups. Each pickup carries elig=[slotId]
    // so the LAP pins it to the target slot.
    var hitPickups = isUserTeam
      ? (_tradeRoster.pickups || [])
      : (_tradeRoster.oppPickups || []);
    hitPickups.forEach(function(pk) {{ team.hitters.push(pk); }});

    // Apply any free-agent pitcher pickups. Pitchers have no slot/LAP —
    // they just add to the aggregate pool.
    var pitPickups = isUserTeam
      ? (_tradeRoster.pitcherPickups || [])
      : (_tradeRoster.oppPitcherPickups || []);
    pitPickups.forEach(function(pk) {{ team.pitchers.push(pk); }});
    // Re-optimize hitter lineup
    var starters = _phase3OptimizeHitters(team.hitters);
    var hAgg = _phase3AggHit(starters);
    team.stats.R    = Math.round(hAgg.R    * 10) / 10;
    team.stats.HR   = Math.round(hAgg.HR   * 10) / 10;
    team.stats.RBI  = Math.round(hAgg.RBI  * 10) / 10;
    team.stats.SO_h = Math.round(hAgg.SO_h * 10) / 10;
    team.stats.SB   = Math.round(hAgg.SB   * 10) / 10;
    team.stats.OBP  = Math.round(hAgg.OBP  * 10000) / 10000;
    // Only recompute pitcher stats if pitcher roster actually changed —
    // avoids floating-point drift from JS re-aggregating IP-weighted ERA/WHIP
    var newPitIds = team.pitchers.map(function(p){{ return p.espn_id; }}).sort().join();
    if (origPitIds !== newPitIds) {{
      var pAgg = _phase3AggPit(team.pitchers);
      team.stats.W    = Math.round(pAgg.W    * 10) / 10;
      team.stats.SO_p = Math.round(pAgg.SO_p * 10) / 10;
      team.stats.SV   = Math.round(pAgg.SV   * 10) / 10;
      team.stats.HLD  = Math.round(pAgg.HLD  * 10) / 10;
      team.stats.ERA  = Math.round(pAgg.ERA  * 1000) / 1000;
      team.stats.WHIP = Math.round(pAgg.WHIP * 1000) / 1000;
    }}
  }});

  _phase3RecomputeZ(league);
  return league;
}}

/* Color helper — exact JS port of Python _proj_rank_color in fantasy.py.
   Gold (#f0c040) for #1; then red(255,60,50) → white(235,235,235) → blue(60,140,255)
   for ranks 2..n. Matches the rest of the dashboard's red=best/blue=worst gradient. */
function _phase3RankColor(rank, n) {{
  if (n <= 1) return '#fff';
  if (rank === 1) return '#f0c040';
  var t = (rank - 2) / Math.max(1, n - 2);
  var r, g, b;
  if (t < 0.5) {{
    var s = t * 2;
    r = Math.round(255 + (235 - 255) * s);
    g = Math.round( 60 + (235 -  60) * s);
    b = Math.round( 50 + (235 -  50) * s);
  }} else {{
    var s2 = (t - 0.5) * 2;
    r = Math.round(235 + ( 60 - 235) * s2);
    g = Math.round(235 + (140 - 235) * s2);
    b = Math.round(235 + (255 - 235) * s2);
  }}
  return 'rgb(' + r + ',' + g + ',' + b + ')';
}}

/* Convert an ESPN elig slot-ID array to a compact position label string.
   Filters to real positions (C/1B/2B/3B/SS/OF/DH), skips MI/CI/UTIL. */
var _ELIG_MAP = {{0:'C',1:'1B',2:'2B',3:'3B',4:'SS',5:'OF'}};
function _eligLabel(elig) {{
  if (!elig || !elig.length) return '';
  var parts = elig.map(function(e){{ return _ELIG_MAP[e] || ''; }})
                  .filter(function(x){{ return x; }});
  // Show DH only when the player has no real field position (e.g. Schwarber)
  if (!parts.length && elig.indexOf(11) >= 0) parts.push('DH');
  return parts.join('/');
}}

/* Map FanGraphs position string → array of ESPN slot IDs.
   FG uses strings like "C", "1B", "2B/SS", "OF", "DH". We parse all
   slash-separated tokens and map to slot IDs. Also add composite slots
   (MI=6 for 2B/SS, CI=7 for 1B/3B, UTIL=12 for any). */
var _FG_POS_TO_SLOT = {{'C':0,'1B':1,'2B':2,'3B':3,'SS':4,'OF':5,'DH':19}};
function _fgPosToSlots(fgPos) {{
  if (!fgPos) return [];
  var parts = fgPos.split('/');
  var slots = [];
  parts.forEach(function(p) {{
    var s = _FG_POS_TO_SLOT[p.trim()];
    if (s !== undefined) slots.push(s);
  }});
  // Add composite positions
  var has2B = slots.indexOf(2) >= 0, hasSS = slots.indexOf(4) >= 0;
  var has1B = slots.indexOf(1) >= 0, has3B = slots.indexOf(3) >= 0;
  if (has2B || hasSS) slots.push(6);  // MI
  if (has1B || has3B) slots.push(7);  // CI
  slots.push(12); // UTIL — every hitter qualifies
  return slots;
}}

/* Check if a player (with elig array or fg_pos string) can fill a given slot ID.
   Returns true if the player is eligible for that slot. */
function _canFillSlot(player, slotId) {{
  // ESPN elig takes priority (more accurate)
  if (player.elig && player.elig.length) {{
    return player.elig.indexOf(slotId) >= 0;
  }}
  // Fall back to FG position
  if (player.fg_pos) {{
    var slots = _fgPosToSlots(player.fg_pos);
    return slots.indexOf(slotId) >= 0;
  }}
  return true; // no position data → don't filter out
}}

/* For FA hitters without ESPN elig, derive a label from fg_pos. */
function _posLabel(player) {{
  if (player.elig && player.elig.length) return _eligLabel(player.elig);
  if (player.fg_pos) return player.fg_pos;
  return '';
}}

/* Compute the LAP-optimized lineup dollar value + slot assignments for one team.
   Also returns the pitcher staff sorted by dollar value (no slot constraints).
   Used for the diagnostic display so the user can SEE why z changed. */
function _phase3LineupSummary(team) {{
  var full = _phase3OptimizeHittersFull(team.hitters || []);
  var hDollar = 0;
  full.starters.forEach(function(s) {{ hDollar += (s.dollars || 0); }});
  var pitchers = (team.pitchers || []).slice().sort(function(a, b) {{
    return (b.dollars || 0) - (a.dollars || 0);
  }});
  var pDollar = 0;
  pitchers.forEach(function(p) {{ pDollar += (p.dollars || 0); }});
  return {{
    dollar:   hDollar,               // back-compat: 'dollar' = hitter lineup $
    hDollar:  hDollar,
    pDollar:  pDollar,
    count:    full.starters.length,
    assigns:  full.assigns,          // [{{slot_id, slot_label, player|null}}, ...]
    pitchers: pitchers               // dollar-sorted full staff (no slot)
  }};
}}

/* Render the compact 2-team delta + (collapsed) full standings table */
function _phase3RenderImpact() {{
  var newLeague = _phase3SimulateTrade();
  var wrap = document.getElementById('phase3-wrap');
  if (!newLeague || !wrap) {{ if (wrap) wrap.style.display = 'none'; return; }}
  wrap.style.display = '';
  var n = PHASE3_LEAGUE.teams.length;
  var userTid = _tradeRoster.sendTeamId;
  var oppTid  = _tradeRoster.recvTeamId;
  function findOld(tid) {{ return PHASE3_LEAGUE.teams.find(function(t) {{ return t.team_id === tid; }}); }}
  function findNew(tid) {{ return newLeague.find(function(t) {{ return t.team_id === tid; }}); }}
  var oldUser = findOld(userTid), newUser = findNew(userTid);
  var oldOpp  = findOld(oppTid),  newOpp  = findNew(oppTid);
  if (!oldUser || !newUser || !oldOpp || !newOpp) {{
    wrap.style.display = 'none'; return;
  }}

  // Compute lineup-$ before/after for both teams (diagnostic)
  var sumOldUser = _phase3LineupSummary(oldUser);
  var sumNewUser = _phase3LineupSummary(newUser);
  var sumOldOpp  = _phase3LineupSummary(oldOpp);
  var sumNewOpp  = _phase3LineupSummary(newOpp);

  function fmtDelta(before, after, decimals) {{
    var d = after - before;
    var sign = d > 0 ? '+' : (d < 0 ? '\u2212' : '');
    var col  = d > 0.005 ? '#4caf50' : (d < -0.005 ? '#e05555' : '#888');
    return '<span style="color:' + col + ';font-weight:700">'
         + sign + Math.abs(d).toFixed(decimals) + '</span>';
  }}
  function fmtRankDelta(before, after) {{
    var d = before - after;  // positive = improved (rank dropped numerically)
    var col, sign;
    if (d > 0) {{ col = '#4caf50'; sign = '\u25B2'; }}
    else if (d < 0) {{ col = '#e05555'; sign = '\u25BC'; }}
    else {{ col = '#888'; sign = '\u25A0'; }}
    return '<span style="color:' + col + ';font-weight:700">'
         + sign + ' ' + Math.abs(d) + '</span>';
  }}

  function teamCard(label, oldT, newT, oldSum, newSum, accent) {{
    var oC = _phase3RankColor(oldT.rank_total, n);
    var nC = _phase3RankColor(newT.rank_total, n);
    var dollarDelta = newSum.dollar - oldSum.dollar;
    var dCol = dollarDelta > 0.05 ? '#4caf50' : (dollarDelta < -0.05 ? '#e05555' : '#888');
    var dSign = dollarDelta > 0 ? '+' : (dollarDelta < 0 ? '\u2212' : '');
    var dollarStr = '<span style="color:' + dCol + ';font-weight:700">'
                  + dSign + '$' + Math.abs(dollarDelta).toFixed(1) + '</span>';
    return '<div style="flex:1;min-width:230px;background:#1a1a1a;border-radius:8px;'
         + 'padding:10px 13px;border-left:3px solid ' + accent + '">'
         + '<div style="font-size:.7rem;color:var(--muted);font-weight:700;'
         +   'text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">'
         +   label + '</div>'
         + '<div style="font-size:.92rem;font-weight:700;color:#fff;margin-bottom:8px">'
         +   newT.name + '</div>'
         + '<div style="display:grid;grid-template-columns:auto auto auto auto;gap:5px 10px;'
         +   'font-size:.78rem;align-items:center">'
         +   '<div style="color:var(--muted)">Z Total</div>'
         +   '<div>' + oldT.z_total.toFixed(2) + '</div>'
         +   '<div style="color:#666">\u2192</div>'
         +   '<div style="color:' + nC + ';font-weight:700">' + newT.z_total.toFixed(2) + '</div>'
         +   '<div style="color:var(--muted)">Rank</div>'
         +   '<div style="color:' + oC + '">#' + oldT.rank_total + '</div>'
         +   '<div style="color:#666">\u2192</div>'
         +   '<div style="color:' + nC + ';font-weight:700">#' + newT.rank_total + '&nbsp;&nbsp;'
         +     fmtRankDelta(oldT.rank_total, newT.rank_total) + '</div>'
         +   '<div style="color:var(--muted)">H&nbsp;Z</div>'
         +   '<div>' + oldT.z_hit.toFixed(2) + '</div>'
         +   '<div style="color:#666">\u2192</div>'
         +   '<div>' + newT.z_hit.toFixed(2) + '&nbsp;&nbsp;' + fmtDelta(oldT.z_hit, newT.z_hit, 2) + '</div>'
         +   '<div style="color:var(--muted)">P&nbsp;Z</div>'
         +   '<div>' + oldT.z_pit.toFixed(2) + '</div>'
         +   '<div style="color:#666">\u2192</div>'
         +   '<div>' + newT.z_pit.toFixed(2) + '&nbsp;&nbsp;' + fmtDelta(oldT.z_pit, newT.z_pit, 2) + '</div>'
         +   '<div style="color:var(--muted)" title="Sum of $ values of the 11 LAP-optimized starting hitters">Lineup&nbsp;$</div>'
         +   '<div>$' + oldSum.dollar.toFixed(1) + '</div>'
         +   '<div style="color:#666">\u2192</div>'
         +   '<div>$' + newSum.dollar.toFixed(1) + '&nbsp;&nbsp;' + dollarStr + '</div>'
         + '</div>'
         + '</div>';
  }}

  document.getElementById('phase3-delta').innerHTML =
    '<div style="display:flex;gap:12px;flex-wrap:wrap">'
    + teamCard('You',          oldUser, newUser, sumOldUser, sumNewUser, '#4caf50')
    + teamCard('Counterparty', oldOpp,  newOpp,  sumOldOpp,  sumNewOpp,  '#e05555')
    + '</div>';

  // Stash summaries + new league so the toggle handlers can re-render
  // their respective views without recomputing the simulation.
  window._phase3Last = {{
    sumOldUser: sumOldUser, sumNewUser: sumNewUser,
    sumOldOpp:  sumOldOpp,  sumNewOpp:  sumNewOpp,
    oldUser:    oldUser,    newUser:    newUser,
    oldOpp:     oldOpp,     newOpp:     newOpp,
    newLeague:  newLeague
  }};

  // Re-build any open sub-views
  var lw = document.getElementById('phase3-lineup-wrap');
  if (lw && lw.style.display !== 'none') _phase3RenderLineups();
  var tw = document.getElementById('phase3-table-wrap');
  if (tw && tw.style.display !== 'none') _phase3RenderTable(newLeague);
  var mw = document.getElementById('phase3-mc-wrap');
  if (mw && mw.style.display !== 'none') _phase3RenderMcSim();
}}

/* Toggle the optimized-lineup table (slot-by-slot before/after for both teams) */
function phase3ToggleLineups() {{
  var lw  = document.getElementById('phase3-lineup-wrap');
  var btn = document.getElementById('phase3-lineup-btn');
  if (!lw) return;
  var open = lw.style.display !== 'none';
  if (open) {{
    lw.style.display = 'none';
    btn.innerHTML = '\u25BC Show optimized lineups + staff (before / after)';
  }} else {{
    if (!window._phase3Last) {{
      // No render has happened yet — bail quietly.
      return;
    }}
    _phase3RenderLineups();
    lw.style.display = '';
    btn.innerHTML = '\u25B2 Hide optimized lineups';
  }}
}}

/* Build a phase3-compatible hitter record from a TRADE_HITTERS free-agent row.
   The pickup is pinned to exactly one slot via elig=[slotId] so the LAP puts
   them there and nowhere else — honest "fill the hole" semantics. */
function _phase3MakePickup(p, slotId) {{
  return {{
    espn_id:  'fa_' + (p.name || '').replace(/[^A-Za-z0-9]+/g, '_'),
    name:     p.name,
    team:     p.team,
    dollars:  p.dollars || 0,
    elig:     [slotId],
    R:        (p.proj && p.proj.R)    || 0,
    HR:       (p.proj && p.proj.HR)   || 0,
    RBI:      (p.proj && p.proj.RBI)  || 0,
    SO_h:     (p.proj && p.proj.K_h)  || 0,
    SB:       (p.proj && p.proj.SB)   || 0,
    OBP:      (p.proj && p.proj.OBP)  || 0,
    PA:       (p.proj && p.proj.PA)   || 600,
    is_pickup:   true,
    pickup_slot: slotId
  }};
}}

/* Return the top-N unrostered hitters from the global trade pool, sorted by $.
   Excludes any players already pinned as pickups (on either side). */
function _phase3FreeAgentHitters(limit) {{
  var inUse = {{}};
  (_tradeRoster.pickups    || []).forEach(function(pk) {{ inUse[pk.name] = true; }});
  (_tradeRoster.oppPickups || []).forEach(function(pk) {{ inUse[pk.name] = true; }});
  var fas = TRADE_HITTERS.filter(function(p) {{
    return p.team_id == null && !inUse[p.name];
  }});
  fas.sort(function(a, b) {{ return (b.dollars || 0) - (a.dollars || 0); }});
  return fas.slice(0, limit || 15);
}}

/* Open the picker menu for a given empty slot. Stores state on the window
   so the next _phase3RenderLineups() call renders the inline list.
   `side` is 'user' or 'opp'. */
function phase3OpenPickupMenu(slotId, slotLabel) {{
  window._phase3PickerSlot = {{slot_id: slotId, slot_label: slotLabel}};
  window._phase3OppPickerSlot = null;
  _phase3RenderLineups();
}}
function phase3ClosePickupMenu() {{
  window._phase3PickerSlot = null;
  _phase3RenderLineups();
}}
function phase3OpenOppPickupMenu(slotId, slotLabel) {{
  window._phase3OppPickerSlot = {{slot_id: slotId, slot_label: slotLabel}};
  window._phase3PickerSlot = null;
  _phase3RenderLineups();
}}
function phase3CloseOppPickupMenu() {{
  window._phase3OppPickerSlot = null;
  _phase3RenderLineups();
}}

/* Add a free-agent pickup to the user's team pinned to the current picker slot,
   then re-run the full phase3 simulation so z-scores, ranks, and the lineup
   display all reflect the pickup. */
function phase3AddPickup(playerName) {{
  if (!window._phase3PickerSlot) return;
  var slotId = window._phase3PickerSlot.slot_id;
  var p = TRADE_HITTERS.find(function(x) {{ return x.name === playerName; }});
  if (!p) return;
  _tradeRoster.pickups.push(_phase3MakePickup(p, slotId));
  window._phase3PickerSlot = null;
  _tradeCalc();          // triggers _phase3RenderImpact → re-renders lineups too
}}

/* Add a free-agent pickup to the OPPONENT's team. Used when a trade opens a
   hole on the counterparty side and we want a realistic sim instead of one
   where the opposing team is playing short a starter. */
function phase3AddOppPickup(playerName) {{
  if (!window._phase3OppPickerSlot) return;
  var slotId = window._phase3OppPickerSlot.slot_id;
  var p = TRADE_HITTERS.find(function(x) {{ return x.name === playerName; }});
  if (!p) return;
  if (!_tradeRoster.oppPickups) _tradeRoster.oppPickups = [];
  _tradeRoster.oppPickups.push(_phase3MakePickup(p, slotId));
  window._phase3OppPickerSlot = null;
  _tradeCalc();
}}

/* Remove an active pickup by name. */
function phase3RemovePickup(name) {{
  _tradeRoster.pickups = (_tradeRoster.pickups || []).filter(function(pk) {{
    return pk.name !== name;
  }});
  _tradeCalc();
}}
function phase3RemoveOppPickup(name) {{
  _tradeRoster.oppPickups = (_tradeRoster.oppPickups || []).filter(function(pk) {{
    return pk.name !== name;
  }});
  _tradeCalc();
}}

/* ── Pitcher free-agent pickups ────────────────────────────────────────
   Mirrors the hitter pickup pipeline but simpler: pitchers have no slot
   positions, so a pickup is just another entry in the team.pitchers list.
   Triggered when a trade leaves the user with more pitchers going out than
   coming in — each open pitcher spot gets a "+" button that opens an
   inline picker of top-10 SP + top-3 RP available from free agency. */

function _phase3MakePickupPitcher(p) {{
  return {{
    espn_id:  'fa_' + (p.name || '').replace(/[^A-Za-z0-9]+/g, '_'),
    name:     p.name,
    team:     p.team,
    dollars:  p.dollars || 0,
    role:     p.role || 'sp',
    W:        (p.proj && p.proj.W)    || 0,
    SO_p:     (p.proj && p.proj.K_p)  || 0,
    SV:       (p.proj && p.proj.SV)   || 0,
    HLD:      (p.proj && p.proj.HLD)  || 0,
    ERA:      (p.proj && p.proj.ERA)  || 0,
    WHIP:     (p.proj && p.proj.WHIP) || 0,
    // IP fallback: SP ~ 160, RP ~ 65. Rough but good enough for IP-weighted
    // ERA/WHIP aggregation — FA pickups don't hugely move a team's rates.
    IP:       (p.role === 'sp' ? 160 : 65),
    is_pickup:   true,
    is_pitcher:  true
  }};
}}

/* Top N unrostered pitchers sorted by $, split out by role.
   Returns top 10 SPs followed by top 3 RPs, excluding anyone already
   picked up. team_id == null catches both true FAs and IL/NA players since
   IL players now carry a team_id (see _all_rostered in _build_phase3_payload). */
function _phase3FreeAgentPitchers() {{
  var inUse = {{}};
  (_tradeRoster.pitcherPickups    || []).forEach(function(pk) {{ inUse[pk.name] = true; }});
  (_tradeRoster.oppPitcherPickups || []).forEach(function(pk) {{ inUse[pk.name] = true; }});
  var pool = TRADE_PITCHERS.filter(function(p) {{
    return p.team_id == null && !inUse[p.name];
  }});
  var sps = pool.filter(function(p) {{ return (p.role || 'sp') === 'sp'; }});
  var rps = pool.filter(function(p) {{ return (p.role || 'sp') === 'rp'; }});
  sps.sort(function(a, b) {{ return (b.dollars || 0) - (a.dollars || 0); }});
  rps.sort(function(a, b) {{ return (b.dollars || 0) - (a.dollars || 0); }});
  return sps.slice(0, 10).concat(rps.slice(0, 3));
}}

function phase3OpenPitcherPicker() {{
  window._phase3PitcherPicker = true;
  window._phase3OppPitcherPicker = false;
  _phase3RenderLineups();
}}
function phase3ClosePitcherPicker() {{
  window._phase3PitcherPicker = false;
  _phase3RenderLineups();
}}
function phase3OpenOppPitcherPicker() {{
  window._phase3OppPitcherPicker = true;
  window._phase3PitcherPicker = false;
  _phase3RenderLineups();
}}
function phase3CloseOppPitcherPicker() {{
  window._phase3OppPitcherPicker = false;
  _phase3RenderLineups();
}}

function phase3AddPitcherPickup(playerName) {{
  var p = TRADE_PITCHERS.find(function(x) {{ return x.name === playerName; }});
  if (!p) return;
  if (!_tradeRoster.pitcherPickups) _tradeRoster.pitcherPickups = [];
  _tradeRoster.pitcherPickups.push(_phase3MakePickupPitcher(p));
  window._phase3PitcherPicker = false;
  _tradeCalc();          // triggers _phase3RenderImpact → re-renders lineups too
}}
function phase3AddOppPitcherPickup(playerName) {{
  var p = TRADE_PITCHERS.find(function(x) {{ return x.name === playerName; }});
  if (!p) return;
  if (!_tradeRoster.oppPitcherPickups) _tradeRoster.oppPitcherPickups = [];
  _tradeRoster.oppPitcherPickups.push(_phase3MakePickupPitcher(p));
  window._phase3OppPitcherPicker = false;
  _tradeCalc();
}}

function phase3RemovePitcherPickup(name) {{
  _tradeRoster.pitcherPickups = (_tradeRoster.pitcherPickups || []).filter(function(pk) {{
    return pk.name !== name;
  }});
  _tradeCalc();
}}
function phase3RemoveOppPitcherPickup(name) {{
  _tradeRoster.oppPitcherPickups = (_tradeRoster.oppPitcherPickups || []).filter(function(pk) {{
    return pk.name !== name;
  }});
  _tradeCalc();
}}

/* ── Roster drops (for trades where inbound > outbound on one side) ────
   When a side receives more players than they send, they need to drop
   someone from their roster to keep the sim honest. Drops can be ANY
   rostered player (hitter or pitcher). Players being sent away are
   excluded from the drop-picker list so you can't double-count them. */
function phase3OpenDropPicker() {{
  window._phase3DropPicker = true;
  window._phase3OppDropPicker = false;
  _phase3RenderLineups();
}}
function phase3CloseDropPicker() {{
  window._phase3DropPicker = false;
  _phase3RenderLineups();
}}
function phase3OpenOppDropPicker() {{
  window._phase3OppDropPicker = true;
  window._phase3DropPicker = false;
  _phase3RenderLineups();
}}
function phase3CloseOppDropPicker() {{
  window._phase3OppDropPicker = false;
  _phase3RenderLineups();
}}

/* Find a rostered player's record (hitter or pitcher) on a given team.
   Returns {{ rec, is_pitcher }} or null. */
function _phase3FindRostered(teamId, espnId) {{
  if (!PHASE3_LEAGUE) return null;
  var tm = PHASE3_LEAGUE.teams.find(function(t) {{ return t.team_id === teamId; }});
  if (!tm) return null;
  for (var i = 0; i < (tm.hitters || []).length; i++) {{
    if (tm.hitters[i].espn_id === espnId) {{
      return {{ rec: tm.hitters[i], is_pitcher: false }};
    }}
  }}
  for (var j = 0; j < (tm.pitchers || []).length; j++) {{
    if (tm.pitchers[j].espn_id === espnId) {{
      return {{ rec: tm.pitchers[j], is_pitcher: true }};
    }}
  }}
  return null;
}}

function phase3AddDrop(espnIdStr) {{
  if (!PHASE3_LEAGUE) return;
  var espnId = isNaN(parseInt(espnIdStr, 10)) ? espnIdStr : parseInt(espnIdStr, 10);
  var userTid = _tradeRoster.sendTeamId;
  var found = _phase3FindRostered(userTid, espnId);
  if (!found) return;
  if (!_tradeRoster.drops) _tradeRoster.drops = [];
  // Prevent duplicates
  for (var i = 0; i < _tradeRoster.drops.length; i++) {{
    if (_tradeRoster.drops[i].espn_id === espnId) return;
  }}
  _tradeRoster.drops.push({{
    espn_id:    espnId,
    name:       found.rec.name || '',
    team:       found.rec.team || '',
    dollars:    found.rec.dollars || 0,
    is_pitcher: found.is_pitcher
  }});
  window._phase3DropPicker = false;
  _tradeCalc();
}}

function phase3AddOppDrop(espnIdStr) {{
  if (!PHASE3_LEAGUE) return;
  var espnId = isNaN(parseInt(espnIdStr, 10)) ? espnIdStr : parseInt(espnIdStr, 10);
  var oppTid = _tradeRoster.recvTeamId;
  if (oppTid == null) return;
  var found = _phase3FindRostered(oppTid, espnId);
  if (!found) return;
  if (!_tradeRoster.oppDrops) _tradeRoster.oppDrops = [];
  for (var i = 0; i < _tradeRoster.oppDrops.length; i++) {{
    if (_tradeRoster.oppDrops[i].espn_id === espnId) return;
  }}
  _tradeRoster.oppDrops.push({{
    espn_id:    espnId,
    name:       found.rec.name || '',
    team:       found.rec.team || '',
    dollars:    found.rec.dollars || 0,
    is_pitcher: found.is_pitcher
  }});
  window._phase3OppDropPicker = false;
  _tradeCalc();
}}

function phase3RemoveDrop(espnIdStr) {{
  var espnId = isNaN(parseInt(espnIdStr, 10)) ? espnIdStr : parseInt(espnIdStr, 10);
  _tradeRoster.drops = (_tradeRoster.drops || []).filter(function(d) {{
    return d.espn_id !== espnId;
  }});
  _tradeCalc();
}}
function phase3RemoveOppDrop(espnIdStr) {{
  var espnId = isNaN(parseInt(espnIdStr, 10)) ? espnIdStr : parseInt(espnIdStr, 10);
  _tradeRoster.oppDrops = (_tradeRoster.oppDrops || []).filter(function(d) {{
    return d.espn_id !== espnId;
  }});
  _tradeCalc();
}}

/* How many drops does a given side need? = max(0, recv - send - existing pickups).
   We treat hitter/pitcher pickups as already filling incoming spots, so only
   the remaining surplus needs a drop. */
function _phase3DropsNeeded(side) {{
  // side: 'user' | 'opp'
  if (side === 'user') {{
    var inc = _tradeRoster.recv.length;
    var out = _tradeRoster.send.length;
    var pk  = (_tradeRoster.pickups || []).length + (_tradeRoster.pitcherPickups || []).length;
    return Math.max(0, (inc + pk) - out);
  }} else {{
    var incO = _tradeRoster.send.length;
    var outO = _tradeRoster.recv.length;
    var pkO  = (_tradeRoster.oppPickups || []).length + (_tradeRoster.oppPitcherPickups || []).length;
    return Math.max(0, (incO + pkO) - outO);
  }}
}}

/* Render a 2-column (You / Counterparty) slot-by-slot before/after lineup table.
   Highlights any slot whose occupant changed. Includes pitcher staff and
   "+" buttons on empty hitter slots so the user can simulate a waiver pickup.
   Empty slots on the opponent side now also get "+ FA" buttons so the sim
   doesn't treat the counterparty as playing short a starter. */
function _phase3RenderLineups() {{
  var lw = document.getElementById('phase3-lineup-wrap');
  if (!lw || !window._phase3Last) return;
  var L = window._phase3Last;
  var userTid = _tradeRoster.sendTeamId;
  var userPicker = window._phase3PickerSlot;
  var oppPicker  = window._phase3OppPickerSlot;

  function fmtCell(p) {{
    if (!p) return '<span style="color:#e05555;font-weight:700">(empty)</span>';
    var tag = p.is_pickup
      ? '<span style="color:#f0c040;font-size:.62rem;font-weight:700;margin-left:4px">PICKUP</span>'
      : '';
    var posTag = (p.elig && !p.is_pitcher) ? '<span style="color:#777;font-size:.58rem;font-weight:700;margin-left:3px">' + _eligLabel(p.elig) + '</span>' : '';
    return '<span style="color:#ddd">' + (p.name || '?') + '</span>' + posTag
         + '<span style="color:#888;margin-left:4px">$' + (p.dollars || 0).toFixed(1) + '</span>'
         + tag;
  }}

  // Picker sub-row: list of top FA hitters. `side` is 'user' or 'opp' and
  // determines which add/close handlers get wired.
  function renderPicker(side, slotLabel, slotId) {{
    var fas = _phase3FreeAgentHitters(50);
    // Filter to position-eligible players for the target slot
    if (slotId != null) {{
      fas = fas.filter(function(p) {{ return _canFillSlot(p, slotId); }});
    }}
    fas = fas.slice(0, 15);
    var closeFn = (side === 'user') ? 'phase3ClosePickupMenu' : 'phase3CloseOppPickupMenu';
    var addFn   = (side === 'user') ? 'phase3AddPickup'       : 'phase3AddOppPickup';
    var headerTxt = (side === 'user' ? 'Pick up a FA hitter for ' : 'Opponent picks up a FA hitter for ') + slotLabel;
    var html = '<div style="background:#0f0f0f;border:1px solid #333;border-radius:6px;'
             + 'padding:7px 9px;margin:4px 0">'
             + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">'
             + '<span style="font-size:.68rem;color:var(--muted);font-weight:700;letter-spacing:.05em;text-transform:uppercase">'
             + headerTxt + '</span>'
             + '<button onmousedown="' + closeFn + '()" '
             + 'style="background:none;border:none;color:#888;cursor:pointer;font-size:.9rem;padding:0 4px">&#x2715;</button>'
             + '</div>';
    if (!fas.length) {{
      html += '<div style="color:var(--muted);font-size:.75rem;padding:4px">No unrostered hitters available.</div>';
    }} else {{
      html += '<div style="display:flex;flex-wrap:wrap;gap:3px">';
      fas.forEach(function(p) {{
        var dCol = p.dollars >= 10 ? '#f0c040' : p.dollars >= 0 ? '#7ec87e' : '#888';
        var pl = _posLabel(p);
        var posLbl = pl ? '<span style="color:#777;font-size:.56rem;font-weight:700">' + pl + '</span>' : '';
        html += '<button onmousedown="' + addFn + '(this.dataset.name)" '
             +  'data-name="' + (p.name || '').replace(/"/g,'&quot;') + '" '
             +  'style="background:#1a1a1a;border:1px solid #2a2a2a;color:#ddd;'
             +  'padding:4px 7px;border-radius:4px;cursor:pointer;font-size:.73rem;'
             +  'display:inline-flex;align-items:center;gap:4px">'
             +  '<span style="font-weight:600">' + p.name + '</span>' + posLbl
             +  '<span style="color:#777;font-size:.68rem">' + (p.team||'') + '</span>'
             +  '<span style="color:' + dCol + ';font-weight:700">$' + (p.dollars||0).toFixed(1) + '</span>'
             +  '</button>';
      }});
      html += '</div>';
    }}
    html += '</div>';
    return html;
  }}

  function hitterTable(label, oldSum, newSum, accent, side) {{
    // side: 'user' | 'opp' | null — non-null sides get "+ FA" buttons on empty slots
    var rows = '';
    var oldA = oldSum.assigns || [];
    var newA = newSum.assigns || [];
    var n = Math.max(oldA.length, newA.length);
    var picker = (side === 'user') ? userPicker : (side === 'opp') ? oppPicker : null;
    var openFn = (side === 'user') ? 'phase3OpenPickupMenu' : 'phase3OpenOppPickupMenu';
    for (var i = 0; i < n; i++) {{
      var oa = oldA[i] || {{slot_label: '?', player: null}};
      var na = newA[i] || {{slot_label: '?', player: null}};
      var oid = oa.player ? oa.player.espn_id : null;
      var nid = na.player ? na.player.espn_id : null;
      var changed = (oid !== nid);
      var bg = changed ? '#241a1a' : 'transparent';
      var slotCol = changed ? '#f0c040' : 'var(--muted)';
      var afterCell = fmtCell(na.player);
      // "+" button on empty AFTER slots — for both user and opponent sides.
      if (side && !na.player) {{
        var slotLbl = (na.slot_label || '').replace(/"/g, '&quot;');
        afterCell = '<span style="color:#e05555;font-weight:700">(empty)</span>'
                  + '&nbsp;&nbsp;<button '
                  + 'data-slot-id="' + na.slot_id + '" '
                  + 'data-slot-label="' + slotLbl + '" '
                  + 'onmousedown="' + openFn + '(Number(this.dataset.slotId),this.dataset.slotLabel)" '
                  + 'style="background:#1e3a1e;border:1px solid #2e6b2e;color:#9cd39c;'
                  + 'cursor:pointer;font-size:.7rem;padding:1px 7px;border-radius:4px;font-weight:700">'
                  + '+ FA</button>';
      }}
      rows += '<tr style="background:' + bg + ';border-bottom:1px solid #1d1d1d">'
            +   '<td style="padding:4px 8px;color:' + slotCol + ';font-weight:700;width:46px">'
            +     (na.slot_label || oa.slot_label || '?') + '</td>'
            +   '<td style="padding:4px 8px">' + fmtCell(oa.player) + '</td>'
            +   '<td style="padding:4px 8px;color:#666;text-align:center;width:18px">\u2192</td>'
            +   '<td style="padding:4px 8px">' + afterCell + '</td>'
            + '</tr>';
      // If the picker is open on this side and this is the target slot,
      // render the inline FA list as a full-width sub-row below.
      if (side && picker && picker.slot_id === na.slot_id && !na.player) {{
        rows += '<tr><td colspan="4" style="padding:0 8px 6px">'
              + renderPicker(side, picker.slot_label, picker.slot_id)
              + '</td></tr>';
      }}
    }}
    var oldD = (oldSum.hDollar || 0).toFixed(1);
    var newD = (newSum.hDollar || 0).toFixed(1);
    var dDelta = (newSum.hDollar || 0) - (oldSum.hDollar || 0);
    var dCol = dDelta > 0.05 ? '#4caf50' : (dDelta < -0.05 ? '#e05555' : '#888');
    var dSign = dDelta > 0 ? '+' : (dDelta < 0 ? '\u2212' : '');
    return '<div style="font-size:.7rem;color:var(--muted);font-weight:700;'
         +   'text-transform:uppercase;letter-spacing:.05em;margin:0 0 6px">'
         +   label + ' &nbsp;\u2014&nbsp; optimized hitter lineup</div>'
         + '<table style="width:100%;border-collapse:collapse;font-size:.78rem">'
         +   '<thead><tr style="border-bottom:1px solid #333">'
         +     '<th style="text-align:left;padding:4px 8px;color:var(--muted)">Slot</th>'
         +     '<th style="text-align:left;padding:4px 8px;color:var(--muted)">Before</th>'
         +     '<th></th>'
         +     '<th style="text-align:left;padding:4px 8px;color:var(--muted)">After</th>'
         +   '</tr></thead><tbody>'
         + rows
         + '<tr style="border-top:1px solid #333">'
         +   '<td style="padding:6px 8px;color:var(--muted);font-weight:700">Hit&nbsp;$</td>'
         +   '<td style="padding:6px 8px;color:#bbb;font-weight:700">$' + oldD + '</td>'
         +   '<td style="padding:6px 8px;color:#666;text-align:center">\u2192</td>'
         +   '<td style="padding:6px 8px;color:#bbb;font-weight:700">$' + newD
         +     '&nbsp;&nbsp;<span style="color:' + dCol + '">' + dSign
         +     '$' + Math.abs(dDelta).toFixed(1) + '</span></td>'
         + '</tr>'
         + '</tbody></table>';
  }}

  // Picker sub-row for pitcher FA pickups — top 10 SPs + top 3 RPs.
  // `side` is 'user' or 'opp'.
  function renderPitcherPicker(side) {{
    var fas = _phase3FreeAgentPitchers();
    var closeFn = (side === 'user') ? 'phase3ClosePitcherPicker' : 'phase3CloseOppPitcherPicker';
    var addFn   = (side === 'user') ? 'phase3AddPitcherPickup'   : 'phase3AddOppPitcherPickup';
    var headerTxt = (side === 'user' ? 'Pick up a FA pitcher' : 'Opponent picks up a FA pitcher')
                  + ' (top 10 SP &bull; top 3 RP)';
    var html = '<div style="background:#0f0f0f;border:1px solid #333;border-radius:6px;'
             + 'padding:7px 9px;margin:4px 0">'
             + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">'
             + '<span style="font-size:.68rem;color:var(--muted);font-weight:700;letter-spacing:.05em;text-transform:uppercase">'
             + headerTxt + '</span>'
             + '<button onmousedown="' + closeFn + '()" '
             + 'style="background:none;border:none;color:#888;cursor:pointer;font-size:.9rem;padding:0 4px">&#x2715;</button>'
             + '</div>';
    if (!fas.length) {{
      html += '<div style="color:var(--muted);font-size:.75rem;padding:4px">No unrostered pitchers available.</div>';
    }} else {{
      html += '<div style="display:flex;flex-wrap:wrap;gap:3px">';
      fas.forEach(function(p) {{
        var dCol = p.dollars >= 10 ? '#f0c040' : p.dollars >= 0 ? '#7ec87e' : '#888';
        var roleTag = '<span style="color:#777;font-size:.66rem;margin-left:2px">'
                    + (p.role || 'sp').toUpperCase() + '</span>';
        html += '<button onmousedown="' + addFn + '(this.dataset.name)" '
             +  'data-name="' + (p.name || '').replace(/"/g,'&quot;') + '" '
             +  'style="background:#1a1a1a;border:1px solid #2a2a2a;color:#ddd;'
             +  'padding:4px 7px;border-radius:4px;cursor:pointer;font-size:.73rem;'
             +  'display:inline-flex;align-items:center;gap:4px">'
             +  '<span style="font-weight:600">' + p.name + '</span>'
             +  '<span style="color:#777;font-size:.68rem">' + (p.team||'') + '</span>'
             +  roleTag
             +  '<span style="color:' + dCol + ';font-weight:700">$' + (p.dollars||0).toFixed(1) + '</span>'
             +  '</button>';
      }});
      html += '</div>';
    }}
    html += '</div>';
    return html;
  }}

  // Pitcher staff: no slots, so show a union of before/after sorted by $.
  // Added pitchers tinted green, removed tinted red, kept neutral.
  // `side` ('user'|'opp'|null): non-null sides render "+ FA" buttons on any
  // open pitcher spots created by the trade.
  function pitcherTable(oldSum, newSum, side) {{
    var oldP = oldSum.pitchers || [];
    var newP = newSum.pitchers || [];
    var oldIds = {{}}; oldP.forEach(function(p) {{ oldIds[p.espn_id] = p; }});
    var newIds = {{}}; newP.forEach(function(p) {{ newIds[p.espn_id] = p; }});
    // Union, preserving order: new first (so kept pitchers stay in the
    // dollar-sorted order you'll have after the trade), then any removed.
    var union = newP.slice();
    oldP.forEach(function(p) {{ if (!newIds[p.espn_id]) union.push(p); }});
    // Open pitcher spots = how many more pitchers the side had before the
    // trade than after (post-pickup). newSum already includes active pitcher
    // pickups from _phase3SimulateTrade, so this auto-calibrates.
    var openSlots = side ? Math.max(0, oldP.length - newP.length) : 0;
    if (!union.length && !openSlots) {{
      return '<div style="color:var(--muted);font-size:.75rem;padding:4px">No pitchers.</div>';
    }}
    var pickerActive = (side === 'user') ? !!window._phase3PitcherPicker
                      : (side === 'opp')  ? !!window._phase3OppPitcherPicker
                      : false;
    var openFn = (side === 'user') ? 'phase3OpenPitcherPicker' : 'phase3OpenOppPitcherPicker';
    var rows = '';
    union.forEach(function(p) {{
      var inNew = !!newIds[p.espn_id];
      var inOld = !!oldIds[p.espn_id];
      var status, bg, beforeStr, afterStr;
      if (inNew && inOld) {{
        status = ''; bg = 'transparent';
        beforeStr = fmtCell(p); afterStr = fmtCell(p);
      }} else if (inNew && !inOld) {{
        status = 'ADDED'; bg = '#1a2a1a';
        beforeStr = '<span style="color:#666">\u2014</span>'; afterStr = fmtCell(p);
      }} else {{
        status = 'REMOVED'; bg = '#2a1a1a';
        beforeStr = fmtCell(p); afterStr = '<span style="color:#666">\u2014</span>';
      }}
      var role = (p.IP || 0) >= 100 ? 'SP' : 'RP';
      rows += '<tr style="background:' + bg + ';border-bottom:1px solid #1d1d1d">'
            +   '<td style="padding:4px 8px;color:var(--muted);font-weight:700;width:46px">'
            +     role + '</td>'
            +   '<td style="padding:4px 8px">' + beforeStr + '</td>'
            +   '<td style="padding:4px 8px;color:#666;text-align:center;width:18px">\u2192</td>'
            +   '<td style="padding:4px 8px">' + afterStr + '</td>'
            + '</tr>';
    }});
    // Render one "(empty)" row per open pitcher spot with a "+ FA" button.
    for (var os = 0; os < openSlots; os++) {{
      var emptyCell = '<span style="color:#e05555;font-weight:700">(empty)</span>'
                    + '&nbsp;&nbsp;<button '
                    + 'onmousedown="' + openFn + '()" '
                    + 'style="background:#1e3a1e;border:1px solid #2e6b2e;color:#9cd39c;'
                    + 'cursor:pointer;font-size:.7rem;padding:1px 7px;border-radius:4px;font-weight:700">'
                    + '+ FA</button>';
      rows += '<tr style="background:#241a1a;border-bottom:1px solid #1d1d1d">'
            +   '<td style="padding:4px 8px;color:#f0c040;font-weight:700;width:46px">P</td>'
            +   '<td style="padding:4px 8px"><span style="color:#666">\u2014</span></td>'
            +   '<td style="padding:4px 8px;color:#666;text-align:center;width:18px">\u2192</td>'
            +   '<td style="padding:4px 8px">' + emptyCell + '</td>'
            + '</tr>';
      if (os === 0 && pickerActive) {{
        rows += '<tr><td colspan="4" style="padding:0 8px 6px">'
              + renderPitcherPicker(side)
              + '</td></tr>';
      }}
    }}
    var oldD = (oldSum.pDollar || 0).toFixed(1);
    var newD = (newSum.pDollar || 0).toFixed(1);
    var dDelta = (newSum.pDollar || 0) - (oldSum.pDollar || 0);
    var dCol = dDelta > 0.05 ? '#4caf50' : (dDelta < -0.05 ? '#e05555' : '#888');
    var dSign = dDelta > 0 ? '+' : (dDelta < 0 ? '\u2212' : '');
    return '<div style="font-size:.7rem;color:var(--muted);font-weight:700;'
         +   'text-transform:uppercase;letter-spacing:.05em;margin:12px 0 6px">'
         +   'Pitching staff</div>'
         + '<table style="width:100%;border-collapse:collapse;font-size:.78rem">'
         +   '<thead><tr style="border-bottom:1px solid #333">'
         +     '<th style="text-align:left;padding:4px 8px;color:var(--muted)">Role</th>'
         +     '<th style="text-align:left;padding:4px 8px;color:var(--muted)">Before</th>'
         +     '<th></th>'
         +     '<th style="text-align:left;padding:4px 8px;color:var(--muted)">After</th>'
         +   '</tr></thead><tbody>'
         + rows
         + '<tr style="border-top:1px solid #333">'
         +   '<td style="padding:6px 8px;color:var(--muted);font-weight:700">Pit&nbsp;$</td>'
         +   '<td style="padding:6px 8px;color:#bbb;font-weight:700">$' + oldD + '</td>'
         +   '<td style="padding:6px 8px;color:#666;text-align:center">\u2192</td>'
         +   '<td style="padding:6px 8px;color:#bbb;font-weight:700">$' + newD
         +     '&nbsp;&nbsp;<span style="color:' + dCol + '">' + dSign
         +     '$' + Math.abs(dDelta).toFixed(1) + '</span></td>'
         + '</tr>'
         + '</tbody></table>';
  }}

  function teamLineupCard(label, oldSum, newSum, accent, side) {{
    return '<div style="flex:1;min-width:340px;background:#141414;border-radius:8px;'
         + 'padding:10px 12px;border-left:3px solid ' + accent + '">'
         +   hitterTable(label, oldSum, newSum, accent, side)
         +   pitcherTable(oldSum, newSum, side)
         + '</div>';
  }}

  // ── Waiver-adds banner ─────────────────────────────────────────────
  // One chip row per side. Each chip shows position/role, name, $,
  // and a × button that removes that pickup and re-sims.
  function _slotLabelFor(slotId) {{
    var slots = PHASE3_LEAGUE ? PHASE3_LEAGUE.slots : [];
    for (var i = 0; i < slots.length; i++) {{
      if (slots[i][0] === slotId) return slots[i][1];
    }}
    return '?';
  }}
  function _hitterChip(pk, rmFn) {{
    var slotLabel = _slotLabelFor(pk.pickup_slot);
    return '<span style="display:inline-flex;align-items:center;gap:5px;'
         + 'background:#1e2a1e;border:1px solid #2e6b2e;border-radius:12px;'
         + 'padding:3px 9px;margin-right:5px;font-size:.73rem">'
         + '<span style="color:#f0c040;font-weight:700">+' + slotLabel + '</span>'
         + '<span style="color:#ddd">' + pk.name + '</span>'
         + '<span style="color:#888">$' + (pk.dollars||0).toFixed(1) + '</span>'
         + '<button onmousedown="' + rmFn + '(this.dataset.name)" '
         + 'data-name="' + (pk.name||'').replace(/"/g,'&quot;') + '" '
         + 'style="background:none;border:none;color:#888;cursor:pointer;font-size:.85rem;padding:0 2px">&#x2715;</button>'
         + '</span>';
  }}
  function _pitcherChip(pk, rmFn) {{
    var roleLbl = (pk.role || 'sp').toUpperCase();
    return '<span style="display:inline-flex;align-items:center;gap:5px;'
         + 'background:#1e2a1e;border:1px solid #2e6b2e;border-radius:12px;'
         + 'padding:3px 9px;margin-right:5px;font-size:.73rem">'
         + '<span style="color:#f0c040;font-weight:700">+' + roleLbl + '</span>'
         + '<span style="color:#ddd">' + pk.name + '</span>'
         + '<span style="color:#888">$' + (pk.dollars||0).toFixed(1) + '</span>'
         + '<button onmousedown="' + rmFn + '(this.dataset.name)" '
         + 'data-name="' + (pk.name||'').replace(/"/g,'&quot;') + '" '
         + 'style="background:none;border:none;color:#888;cursor:pointer;font-size:.85rem;padding:0 2px">&#x2715;</button>'
         + '</span>';
  }}
  function _dropChip(d, rmFn) {{
    var roleLbl = d.is_pitcher ? (((d.IP || 0) >= 100) ? 'SP' : 'P') : 'H';
    return '<span style="display:inline-flex;align-items:center;gap:5px;'
         + 'background:#2a1a1a;border:1px solid #6b2e2e;border-radius:12px;'
         + 'padding:3px 9px;margin-right:5px;font-size:.73rem">'
         + '<span style="color:#e05555;font-weight:700">\u2212' + roleLbl + '</span>'
         + '<span style="color:#ddd">' + d.name + '</span>'
         + '<span style="color:#888">$' + (d.dollars||0).toFixed(1) + '</span>'
         + '<button onmousedown="' + rmFn + '(this.dataset.eid)" '
         + 'data-eid="' + d.espn_id + '" '
         + 'style="background:none;border:none;color:#888;cursor:pointer;font-size:.85rem;padding:0 2px">&#x2715;</button>'
         + '</span>';
  }}

  function _buildAddsBanner(label, hPks, pPks, rmHit, rmPit) {{
    var chips = '';
    if (hPks && hPks.length) chips += hPks.map(function(pk) {{ return _hitterChip(pk, rmHit); }}).join('');
    if (pPks && pPks.length) chips += pPks.map(function(pk) {{ return _pitcherChip(pk, rmPit); }}).join('');
    if (!chips) return '';
    return '<div style="margin-bottom:6px;padding:6px 9px;'
         + 'background:#0f1a0f;border-radius:6px;border-left:3px solid #4caf50">'
         + '<span style="font-size:.66rem;color:var(--muted);font-weight:700;'
         + 'text-transform:uppercase;letter-spacing:.05em;margin-right:6px">' + label + '</span>'
         + chips + '</div>';
  }}

  var bannerHtml = '';
  bannerHtml += _buildAddsBanner('Your waiver adds',
                                  _tradeRoster.pickups, _tradeRoster.pitcherPickups,
                                  'phase3RemovePickup', 'phase3RemovePitcherPickup');
  bannerHtml += _buildAddsBanner('Counterparty waiver adds',
                                  _tradeRoster.oppPickups, _tradeRoster.oppPitcherPickups,
                                  'phase3RemoveOppPickup', 'phase3RemoveOppPitcherPickup');

  // ── Drop banner ────────────────────────────────────────────────────
  // Shown whenever this side is "receiving" more players than sending
  // (accounting for any pickups). Lets the user pick any rostered player
  // — hitter or pitcher — to drop. The button is always rendered even
  // when drops are zero, as long as there's a roster imbalance, so the
  // user can still choose to preemptively drop someone.
  function _buildDropBanner(side) {{
    // side: 'user' | 'opp'
    var needed = _phase3DropsNeeded(side);
    var drops  = (side === 'user' ? _tradeRoster.drops : _tradeRoster.oppDrops) || [];
    if (!needed && !drops.length) return '';
    var rmFn   = (side === 'user') ? 'phase3RemoveDrop'   : 'phase3RemoveOppDrop';
    var openFn = (side === 'user') ? 'phase3OpenDropPicker' : 'phase3OpenOppDropPicker';
    var label  = (side === 'user') ? 'Your drops'          : 'Counterparty drops';
    var remaining = needed - drops.length;
    var status;
    if (remaining > 0) {{
      status = '<span style="color:#e05555;font-weight:700">'
             + remaining + ' drop' + (remaining === 1 ? '' : 's')
             + ' required</span>';
    }} else if (remaining < 0) {{
      status = '<span style="color:#e05555;font-weight:700">over-dropped by '
             + Math.abs(remaining) + '</span>';
    }} else {{
      status = '<span style="color:#7ec87e;font-weight:700">balanced</span>';
    }}
    var chips = drops.map(function(d) {{ return _dropChip(d, rmFn); }}).join('');
    var addBtn = '<button onmousedown="' + openFn + '()" '
               + 'style="background:#2a1a1a;border:1px solid #6b2e2e;color:#e08484;'
               + 'cursor:pointer;font-size:.72rem;padding:3px 9px;border-radius:12px;'
               + 'font-weight:700;margin-left:4px">+ pick drop</button>';
    return '<div style="margin-bottom:6px;padding:6px 9px;'
         + 'background:#1a0f0f;border-radius:6px;border-left:3px solid #e05555">'
         + '<span style="font-size:.66rem;color:var(--muted);font-weight:700;'
         + 'text-transform:uppercase;letter-spacing:.05em;margin-right:6px">' + label + '</span>'
         + status + '&nbsp;&nbsp;' + chips + addBtn + '</div>';
  }}
  bannerHtml += _buildDropBanner('user');
  bannerHtml += _buildDropBanner('opp');

  // ── Drop picker sub-view ───────────────────────────────────────────
  // Renders the full list of that side's rostered players (hitters +
  // pitchers) sorted by $ descending. Players being sent in the trade
  // and already-dropped players are excluded.
  function _renderDropPicker(side) {{
    if (!PHASE3_LEAGUE) return '';
    var tid = (side === 'user') ? _tradeRoster.sendTeamId : _tradeRoster.recvTeamId;
    if (tid == null) return '';
    var tm = PHASE3_LEAGUE.teams.find(function(t) {{ return t.team_id === tid; }});
    if (!tm) return '';
    var excluded = {{}};
    var sendList = (side === 'user') ? _tradeRoster.send : _tradeRoster.recv;
    sendList.forEach(function(p) {{ if (p.espn_id != null) excluded[p.espn_id] = true; }});
    var dropList = (side === 'user' ? _tradeRoster.drops : _tradeRoster.oppDrops) || [];
    dropList.forEach(function(d) {{ if (d.espn_id != null) excluded[d.espn_id] = true; }});
    var pool = [];
    (tm.hitters  || []).forEach(function(p) {{
      if (!excluded[p.espn_id]) pool.push({{rec: p, is_pitcher: false}});
    }});
    (tm.pitchers || []).forEach(function(p) {{
      if (!excluded[p.espn_id]) pool.push({{rec: p, is_pitcher: true}});
    }});
    pool.sort(function(a, b) {{
      return (a.rec.dollars || 0) - (b.rec.dollars || 0);   // lowest $ first (likely drop)
    }});
    var closeFn = (side === 'user') ? 'phase3CloseDropPicker' : 'phase3CloseOppDropPicker';
    var addFn   = (side === 'user') ? 'phase3AddDrop'         : 'phase3AddOppDrop';
    var heading = (side === 'user') ? 'Drop a player from your roster'
                                     : 'Drop a player from the counterparty roster';
    var html = '<div style="margin-bottom:8px;background:#0f0f0f;border:1px solid #333;'
             + 'border-radius:6px;padding:7px 9px">'
             + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">'
             + '<span style="font-size:.68rem;color:var(--muted);font-weight:700;letter-spacing:.05em;text-transform:uppercase">'
             + heading + ' (sorted by $ asc)</span>'
             + '<button onmousedown="' + closeFn + '()" '
             + 'style="background:none;border:none;color:#888;cursor:pointer;font-size:.9rem;padding:0 4px">&#x2715;</button>'
             + '</div>';
    if (!pool.length) {{
      html += '<div style="color:var(--muted);font-size:.75rem;padding:4px">No droppable players.</div>';
    }} else {{
      html += '<div style="display:flex;flex-wrap:wrap;gap:3px;max-height:220px;overflow-y:auto">';
      pool.forEach(function(item) {{
        var p = item.rec;
        var dCol = p.dollars >= 10 ? '#f0c040' : p.dollars >= 0 ? '#7ec87e' : '#888';
        var roleTag = item.is_pitcher
          ? '<span style="color:#e08484;font-size:.66rem;margin-left:2px">'
            + (((p.IP || 0) >= 100) ? 'SP' : 'P') + '</span>'
          : '';
        var posLbl = (!item.is_pitcher && p.elig) ? '<span style="color:#777;font-size:.56rem;font-weight:700;margin-left:2px">' + _eligLabel(p.elig) + '</span>' : '';
        html += '<button onmousedown="' + addFn + '(this.dataset.eid)" '
             +  'data-eid="' + p.espn_id + '" '
             +  'style="background:#1a1a1a;border:1px solid #2a2a2a;color:#ddd;'
             +  'padding:4px 7px;border-radius:4px;cursor:pointer;font-size:.73rem;'
             +  'display:inline-flex;align-items:center;gap:4px">'
             +  '<span style="font-weight:600">' + (p.name || '?') + '</span>' + posLbl
             +  '<span style="color:#777;font-size:.68rem">' + (p.team || '') + '</span>'
             +  roleTag
             +  '<span style="color:' + dCol + ';font-weight:700">$' + (p.dollars||0).toFixed(1) + '</span>'
             +  '</button>';
      }});
      html += '</div>';
    }}
    html += '</div>';
    return html;
  }}
  if (window._phase3DropPicker)    bannerHtml += _renderDropPicker('user');
  if (window._phase3OppDropPicker) bannerHtml += _renderDropPicker('opp');

  lw.innerHTML = bannerHtml
               + '<div style="display:flex;gap:12px;flex-wrap:wrap">'
               + teamLineupCard('You',          L.sumOldUser, L.sumNewUser, '#4caf50', 'user')
               + teamLineupCard('Counterparty', L.sumOldOpp,  L.sumNewOpp,  '#e05555', 'opp')
               + '</div>';
}}

function phase3ToggleTable() {{
  var tw  = document.getElementById('phase3-table-wrap');
  var btn = document.getElementById('phase3-toggle-btn');
  if (!tw) return;
  var open = tw.style.display !== 'none';
  if (open) {{
    tw.style.display = 'none';
    btn.innerHTML = '\u25BC Show full updated standings';
  }} else {{
    var newLeague = _phase3SimulateTrade();
    if (!newLeague) return;
    _phase3RenderTable(newLeague);
    tw.style.display = '';
    btn.innerHTML = '\u25B2 Hide full updated standings';
  }}
}}

function _phase3RenderTable(newLeague) {{
  var tw = document.getElementById('phase3-table-wrap');
  if (!tw) return;
  var n = newLeague.length;
  function findOld(tid) {{ return PHASE3_LEAGUE.teams.find(function(t) {{ return t.team_id === tid; }}); }}
  var sorted = newLeague.slice().sort(function(a,b) {{ return a.rank_total - b.rank_total; }});
  var lower = {{}};
  PHASE3_LEAGUE.lower_better.forEach(function(c) {{ lower[c] = true; }});

  /* Category display labels */
  var hCats = PHASE3_LEAGUE.hit_cats || [];
  var pCats = PHASE3_LEAGUE.pit_cats || [];
  var allCats = hCats.concat(pCats);
  var catLabel = {{'R':'R','HR':'HR','RBI':'RBI','SO_h':'K','SB':'SB','OBP':'OBP',
                  'W':'W','SO_p':'K','SV':'SV','HLD':'HLD','ERA':'ERA','WHIP':'WHIP'}};
  var thSt = 'padding:5px 8px;text-align:center;color:var(--muted);font-size:.78rem;white-space:nowrap';

  /* Header row */
  var html = '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:.85rem">'
           + '<thead><tr style="border-bottom:1px solid #333">'
           + '<th style="text-align:left;padding:6px 10px;color:var(--muted)">#</th>'
           + '<th style="text-align:left;padding:6px 10px;color:var(--muted)">Team</th>'
           + '<th style="text-align:center;padding:6px 10px;color:var(--muted)">Z Tot</th>'
           + '<th style="text-align:center;padding:6px 10px;color:var(--muted)">\u0394Z</th>'
           + '<th style="text-align:center;padding:6px 10px;color:var(--muted)">\u0394Rk</th>'
           + '<th style="border-left:2px solid #444;' + thSt + '">H Z</th>';
  hCats.forEach(function(c) {{ html += '<th style="' + thSt + '">' + (catLabel[c]||c) + '</th>'; }});
  html += '<th style="border-left:2px solid #444;' + thSt + '">P Z</th>';
  pCats.forEach(function(c) {{ html += '<th style="' + thSt + '">' + (catLabel[c]||c) + '</th>'; }});
  html += '</tr></thead><tbody>';

  /* Compute per-cat ranks for color-coding */
  var allCats = hCats.concat(pCats);
  var catRanks = {{}};
  allCats.forEach(function(cat) {{
    var isLow = lower[cat];
    var pairs = newLeague.map(function(t) {{ return {{tid: t.team_id, v: t.stats[cat]||0}}; }});
    pairs.sort(function(a,b) {{ return isLow ? a.v - b.v : b.v - a.v; }});
    var rk = {{}};
    pairs.forEach(function(p, i) {{ rk[p.tid] = i + 1; }});
    catRanks[cat] = rk;
  }});

  sorted.forEach(function(t) {{
    var old = findOld(t.team_id);
    var rkC = _phase3RankColor(t.rank_total, n);
    var zd  = t.z_total - old.z_total;
    var rd  = old.rank_total - t.rank_total;
    var zCol = zd > 0.005 ? '#4caf50' : zd < -0.005 ? '#e05555' : '#888';
    var rCol = rd > 0     ? '#4caf50' : rd < 0      ? '#e05555' : '#888';
    var zStr = (zd >= 0 ? '+' : '\u2212') + Math.abs(zd).toFixed(2);
    var rStr = rd === 0 ? '\u25A0 0'
             : (rd > 0 ? '\u25B2 ' : '\u25BC ') + Math.abs(rd);
    var isUser = t.team_id === _tradeRoster.sendTeamId;
    var isOpp  = t.team_id === _tradeRoster.recvTeamId;
    var nameAccent = isUser ? '#4caf50' : isOpp ? '#e05555' : '#ddd';
    var bg = (isUser || isOpp) ? '#1a1a1a' : 'transparent';

    /* Format a raw stat value for display */
    function fmtRaw(cat, v) {{
      if (cat === 'OBP') return v.toFixed(3);
      if (cat === 'ERA' || cat === 'WHIP') return v.toFixed(2);
      return Math.round(v).toString();
    }}

    /* Build per-category cell: raw stat on top, z-score + raw delta below */
    function zCell(cat, borderLeft) {{
      var nz = t.z[cat] || 0;
      var oz = old.z[cat] || 0;
      var nv = t.stats[cat] || 0;
      var ov = old.stats[cat] || 0;
      var rawD = nv - ov;
      var zd = nz - oz;

      /* Raw stat change — color by whether the change is GOOD for this cat */
      var isLower = lower[cat];
      var rawGood = isLower ? (rawD < -0.0005) : (rawD > 0.0005);
      var rawBad  = isLower ? (rawD > 0.0005)  : (rawD < -0.0005);
      var rawChanged = Math.abs(rawD) > 0.0005;

      var rawDStr = '';
      if (rawChanged) {{
        var rc = rawGood ? '#4caf50' : rawBad ? '#e05555' : '#888';
        var rawSign = rawD >= 0 ? '+' : '\u2212';
        rawDStr = '<div style="font-size:.66rem;color:' + rc + ';line-height:1;margin-top:2px">'
                + rawSign + fmtRaw(cat, Math.abs(rawD)) + '</div>';
      }}

      /* Z-score line */
      var zStr = '<div style="font-size:.68rem;color:#666;line-height:1;margin-top:2px">z ' + nz.toFixed(2) + '</div>';
      /* Rank number line */
      var rkNum = catRanks[cat][t.team_id];
      var rkStr = '<div style="font-size:.55rem;color:#555;line-height:1;margin-top:1px">#' + rkNum + '</div>';

      var bl = borderLeft ? 'border-left:2px solid #444;' : '';
      return '<td style="' + bl + 'text-align:center;padding:4px 6px">'
           + '<div style="font-size:.92rem;line-height:1.15;font-weight:600;color:' + _phase3RankColor(catRanks[cat][t.team_id], n) + '">' + fmtRaw(cat, nv) + '</div>'
           + zStr + rkStr
           + rawDStr + '</td>';
    }}

    html += '<tr style="background:' + bg + ';border-bottom:1px solid #222">'
          + '<td style="padding:5px 10px;color:' + rkC + ';font-weight:700">#' + t.rank_total + '</td>'
          + '<td style="padding:5px 10px;color:' + nameAccent + ';font-weight:600;white-space:nowrap">' + t.name + '</td>'
          + '<td style="padding:5px 10px;text-align:center;color:' + rkC + ';font-weight:700">'
          +   t.z_total.toFixed(2) + '</td>'
          + '<td style="padding:5px 10px;text-align:center;color:' + zCol + '">' + zStr + '</td>'
          + '<td style="padding:5px 10px;text-align:center;color:' + rCol + '">' + rStr + '</td>';

    /* Hitter subtotal + per-cat */
    var hzd = (t.z_hit||0) - (old.z_hit||0);
    var hzCol = hzd > 0.005 ? '#4caf50' : hzd < -0.005 ? '#e05555' : '#888';
    var hzDStr = Math.abs(hzd) > 0.005
               ? '<div style="font-size:.68rem;color:' + hzCol + ';line-height:1">'
                 + (hzd > 0 ? '+' : '\u2212') + Math.abs(hzd).toFixed(2) + '</div>'
               : '';
    html += '<td style="border-left:2px solid #444;text-align:center;padding:4px 6px;font-weight:600">'
          + '<div style="font-size:.92rem;line-height:1.15;color:' + _phase3RankColor(t.rank_hit, n) + '">' + (t.z_hit||0).toFixed(2) + '</div>' + hzDStr + '</td>';
    hCats.forEach(function(c) {{ html += zCell(c, false); }});

    /* Pitcher subtotal + per-cat */
    var pzd = (t.z_pit||0) - (old.z_pit||0);
    var pzCol = pzd > 0.005 ? '#4caf50' : pzd < -0.005 ? '#e05555' : '#888';
    var pzDStr = Math.abs(pzd) > 0.005
               ? '<div style="font-size:.68rem;color:' + pzCol + ';line-height:1">'
                 + (pzd > 0 ? '+' : '\u2212') + Math.abs(pzd).toFixed(2) + '</div>'
               : '';
    html += '<td style="border-left:2px solid #444;text-align:center;padding:4px 6px;font-weight:600">'
          + '<div style="font-size:.92rem;line-height:1.15;color:' + _phase3RankColor(t.rank_pit, n) + '">' + (t.z_pit||0).toFixed(2) + '</div>' + pzDStr + '</td>';
    pCats.forEach(function(c) {{ html += zCell(c, false); }});

    html += '</tr>';
  }});
  html += '</tbody></table></div>';
  tw.innerHTML = html;
}}

/* ════════════════════════════════════════════════════════════════════
   ── Waiver Wire ──────────────────────────────────────────────────
   ════════════════════════════════════════════════════════════════════ */

/* ── State ── */
var _wwState = {{
  teamId: null,
  drops: [],      // array of player objects dropped
  adds: [],       // array of player objects added from FA
  mode: 'adddrop' // 'adddrop' or 'stream'
}};
var _wwStreamState = {{
  teamId: null,
  drop: null,      // single player dropped (or null if open spot)
  streamerProfile: null  // computed avg stats of top 8 SP (100+ IP)
}};

/* ── Sub-tab toggle ── */
function wwSwitch(which) {{
  document.getElementById('ww-adddrop-wrap').style.display = which==='adddrop' ? '' : 'none';
  document.getElementById('ww-stream-wrap').style.display  = which==='stream'  ? '' : 'none';
  ['adddrop','stream'].forEach(function(w) {{
    var btn = document.getElementById('ww-' + w + '-btn');
    if (!btn) return;
    var on = (w === which);
    btn.style.borderBottom = on ? '3px solid var(--accent)' : 'none';
    btn.style.color = on ? '#fff' : '';
  }});
  _wwState.mode = which;
}}

/* ── Init: populate team dropdowns when waiver tab is first shown ── */
function wwInit() {{
  if (!PHASE3_LEAGUE) return;
  var allTeams = PHASE3_LEAGUE.teams.slice().sort(function(a,b) {{
    return (a.name || '').localeCompare(b.name || '');
  }});
  var optHtml = '<option value="">\u2014 pick a team \u2014</option>';
  allTeams.forEach(function(t) {{
    var sel = (t.team_id === PHASE3_LEAGUE.user_team_id) ? ' selected' : '';
    optHtml += '<option value="' + t.team_id + '"' + sel + '>' + t.name + '</option>';
  }});
  var s1 = document.getElementById('ww-team-sel');
  if (s1) {{ s1.innerHTML = optHtml; }}
  var s2 = document.getElementById('ww-stream-team-sel');
  if (s2) {{ s2.innerHTML = optHtml; }}
  // Auto-select user team if available
  if (PHASE3_LEAGUE.user_team_id != null) {{
    wwSetTeam(PHASE3_LEAGUE.user_team_id);
    wwStreamSetTeam(PHASE3_LEAGUE.user_team_id);
  }}
}}
// Run init after page load
if (document.readyState === 'complete') {{ wwInit(); }}
else {{ window.addEventListener('load', wwInit); }}

/* ════════════════════════════════════════════════════════════════════
   ── ADD / DROP MODE ──────────────────────────────────────────────
   ════════════════════════════════════════════════════════════════════ */

function wwSetTeam(val) {{
  var tid = val === '' ? null : parseInt(val, 10);
  _wwState.teamId = tid;
  _wwState.drops = [];
  _wwState.adds = [];
  var rw = document.getElementById('ww-roster-wrap');
  if (!tid) {{ if (rw) rw.style.display = 'none'; return; }}
  if (rw) rw.style.display = '';
  _wwRenderRoster();
}}

function _wwGetTeam() {{
  if (!PHASE3_LEAGUE || _wwState.teamId == null) return null;
  return PHASE3_LEAGUE.teams.find(function(t) {{ return t.team_id === _wwState.teamId; }});
}}

function _wwRosterSize(team) {{
  return (team.hitters || []).length + (team.pitchers || []).length;
}}

/* Max active roster = hitter slots (11) + bench hitters (variable) + pitchers
   We compute it from the current team size since it varies. */
function _wwMaxRoster(team) {{
  return _wwRosterSize(team);
}}

function _wwRenderRoster() {{
  var team = _wwGetTeam();
  if (!team) return;
  var rList = document.getElementById('ww-roster-list');
  var addSec = document.getElementById('ww-add-section');
  var impWrap = document.getElementById('ww-impact-wrap');
  if (!rList) return;

  var hitters = (team.hitters || []).slice().sort(function(a,b) {{ return (b.dollars||0)-(a.dollars||0); }});
  var pitchers = (team.pitchers || []).slice().sort(function(a,b) {{ return (b.dollars||0)-(a.dollars||0); }});

  // Build drop set
  var dropIds = {{}};
  _wwState.drops.forEach(function(d) {{ dropIds[d.espn_id] = true; }});

  function playerRow(p, isPitcher) {{
    var dropped = !!dropIds[p.espn_id];
    var dc = _dollarRankColor(p.dollars || 0);
    var opacity = dropped ? 'opacity:0.35;' : '';
    var role = isPitcher ? ((p.IP||0) >= 100 ? 'SP' : 'RP') : '';
    var posLabel = isPitcher ? role : _eligLabel(p.elig);
    var posBadge = posLabel ? '<span style="color:#777;font-size:.58rem;font-weight:700;margin-left:4px">' + posLabel + '</span>' : '';
    var html = '<div style="display:flex;align-items:center;gap:8px;padding:5px 8px;'
             + 'background:#1a1a1a;border-radius:5px;margin-bottom:3px;' + opacity + '">'
             + '<span style="flex:1;font-size:.82rem;font-weight:600;color:#ddd">'
             + (p.name || 'Unknown') + posBadge + '</span>'
             + '<span style="font-size:.68rem;color:#888">' + (p.team || '') + '</span>'
             + '<span style="font-size:.82rem;font-weight:700;color:' + dc + ';width:52px;text-align:right">'
             + '$' + (p.dollars||0).toFixed(1) + '</span>';
    if (!dropped) {{
      html += '<button data-eid="' + (p.espn_id+'').replace(/"/g,'&quot;') + '"'
            + ' onclick="wwDrop(this.dataset.eid)"'
            + ' style="background:#3a1a1a;border:1px solid #6b2e2e;color:#e05555;cursor:pointer;'
            + 'font-size:.68rem;padding:2px 8px;border-radius:4px;font-weight:700">Drop</button>';
    }} else {{
      html += '<button data-eid="' + (p.espn_id+'').replace(/"/g,'&quot;') + '"'
            + ' onclick="wwUndrop(this.dataset.eid)"'
            + ' style="background:#1a3a1a;border:1px solid #2e6b2e;color:#9cd39c;cursor:pointer;'
            + 'font-size:.68rem;padding:2px 8px;border-radius:4px;font-weight:700">Undo</button>';
    }}
    html += '</div>';
    return html;
  }}

  var html = '<div style="font-size:.7rem;color:#4caf50;font-weight:700;margin-bottom:4px">Hitters</div>';
  hitters.forEach(function(p) {{ html += playerRow(p, false); }});
  html += '<div style="font-size:.7rem;color:#4caf50;font-weight:700;margin:8px 0 4px">Pitchers</div>';
  pitchers.forEach(function(p) {{ html += playerRow(p, true); }});

  // Show added players
  if (_wwState.adds.length) {{
    html += '<div style="font-size:.7rem;color:#4caf50;font-weight:700;margin:8px 0 4px">&#x2795; Added</div>';
    _wwState.adds.forEach(function(p) {{
      var dc = _dollarColor(p.dollars || 0);
      var role = p.is_pitcher ? ((p.proj && p.proj.IP || 0) >= 100 ? 'SP' : 'RP') : 'H';
      html += '<div style="display:flex;align-items:center;gap:8px;padding:5px 8px;'
            + 'background:#1a2a1a;border-radius:5px;margin-bottom:3px;border-left:3px solid #4caf50">'
            + '<span style="color:#4caf50;font-size:.68rem;font-weight:700;width:36px">' + role + '</span>'
            + '<span style="flex:1;font-size:.82rem;font-weight:600;color:#ddd">'
            + (p.name || 'Unknown') + '</span>'
            + '<span style="font-size:.68rem;color:#888">' + (p.team || '') + '</span>'
            + '<span style="font-size:.82rem;font-weight:700;color:' + dc + ';width:52px;text-align:right">'
            + '$' + (p.dollars||0).toFixed(1) + '</span>'
            + '<button data-pname="' + (p.name||'').replace(/"/g,'&quot;') + '"'
            + ' onclick="wwRemoveAdd(this.dataset.pname)"'
            + ' style="background:#3a1a1a;border:1px solid #6b2e2e;color:#e05555;cursor:pointer;'
            + 'font-size:.68rem;padding:2px 8px;border-radius:4px;font-weight:700">\u2715</button>'
            + '</div>';
    }});
  }}

  var countEl = document.getElementById('ww-roster-count');
  var netChange = _wwState.adds.length - _wwState.drops.length;
  if (countEl) countEl.textContent = '(' + _wwRosterSize(team) + ' active' + (netChange !== 0 ? ', net ' + (netChange > 0 ? '+' : '') + netChange : '') + ')';

  rList.innerHTML = html;

  // Show add section when drops > adds (open spots)
  var openSpots = _wwState.drops.length - _wwState.adds.length;
  if (addSec) addSec.style.display = openSpots > 0 ? '' : 'none';

  // Show impact when at least one add OR one drop
  if (_wwState.adds.length > 0 || _wwState.drops.length > 0) {{
    _wwRenderImpact();
    if (impWrap) impWrap.style.display = '';
  }} else {{
    if (impWrap) impWrap.style.display = 'none';
  }}
}}

function wwDrop(espnId) {{
  var team = _wwGetTeam();
  if (!team) return;
  var all = (team.hitters || []).concat(team.pitchers || []);
  var p = all.find(function(x) {{ return String(x.espn_id) === String(espnId); }});
  if (!p) return;
  // Check if already dropped
  if (_wwState.drops.some(function(d) {{ return String(d.espn_id) === String(espnId); }})) return;
  _wwState.drops.push(p);
  _wwRenderRoster();
}}

function wwUndrop(espnId) {{
  _wwState.drops = _wwState.drops.filter(function(d) {{ return String(d.espn_id) !== String(espnId); }});
  // If we now have more adds than drops, remove the last add
  while (_wwState.adds.length > _wwState.drops.length) {{
    _wwState.adds.pop();
  }}
  _wwRenderRoster();
}}

/* ── Free Agent Search ── */
function wwSearchFA(q) {{
  var dd = document.getElementById('ww-add-dd');
  if (!dd) return;
  q = (q || '').trim().toLowerCase();
  if (q.length < 2) {{ dd.style.display = 'none'; return; }}

  // Build exclusion set: rostered + already added
  var addedNames = {{}};
  _wwState.adds.forEach(function(p) {{ addedNames[p.name] = true; }});

  var pool = TRADE_HITTERS.concat(TRADE_PITCHERS);
  var matches = pool.filter(function(p) {{
    if (p.team_id != null) return false;  // rostered
    if (addedNames[p.name]) return false;
    return (p.name || '').toLowerCase().includes(q);
  }});
  matches.sort(function(a,b) {{ return (b.dollars||0) - (a.dollars||0); }});
  matches = matches.slice(0, 10);

  if (!matches.length) {{
    dd.innerHTML = '<div style="padding:10px;color:var(--muted);font-size:.8rem">No free agents found</div>';
    dd.style.display = '';
    return;
  }}

  var html = '';
  matches.forEach(function(p) {{
    var dc = _dollarRankColor(p.dollars || 0);
    var role = p.is_pitcher ? ((p.proj && p.proj.IP || 0) >= 100 ? 'SP' : 'RP') : 'H';
    html += '<div data-pname="' + (p.name||'').replace(/"/g,'&quot;') + '"'
          + ' onmousedown="wwAddFA(this.dataset.pname)"'
          + ' style="display:flex;align-items:center;gap:8px;padding:6px 10px;cursor:pointer;'
          + 'border-bottom:1px solid #2a2a2a;font-size:.82rem" '
          + 'onmouseover="this.style.background=\\\\x27#2a2a2a\\\\x27" onmouseout="this.style.background=\\\\x27\\\\x27">'
          + '<span style="color:#888;font-size:.68rem;font-weight:700;width:28px">' + role + '</span>'
          + '<span style="flex:1;color:#ddd;font-weight:600">' + p.name
          + (function(){{ var pl = !p.is_pitcher ? _posLabel(p) : ''; return pl ? '<span style="color:#777;font-size:.58rem;font-weight:700;margin-left:4px">' + pl + '</span>' : ''; }})()
          + '</span>'
          + '<span style="color:#888;font-size:.75rem">' + (p.team || '') + '</span>'
          + '<span style="color:' + dc + ';font-weight:700;width:50px;text-align:right">$' + (p.dollars||0).toFixed(1) + '</span>'
          + '</div>';
  }});
  dd.innerHTML = html;
  dd.style.display = '';
}}

function wwAddFA(name) {{
  var pool = TRADE_HITTERS.concat(TRADE_PITCHERS);
  var p = pool.find(function(x) {{ return x.name === name && x.team_id == null; }});
  if (!p) return;
  // Don't add more than we have open spots
  if (_wwState.adds.length >= _wwState.drops.length) return;
  _wwState.adds.push(p);
  var dd = document.getElementById('ww-add-dd');
  if (dd) dd.style.display = 'none';
  var search = document.getElementById('ww-add-search');
  if (search) search.value = '';
  _wwRenderRoster();
}}

function wwRemoveAdd(name) {{
  _wwState.adds = _wwState.adds.filter(function(p) {{ return p.name !== name; }});
  _wwRenderRoster();
}}

/* ── Dollar color helper (reuse from existing) ── */
function _dollarColor(d) {{
  if (d >= 30) return '#4CAF50';
  if (d >= 20) return '#8BC34A';
  if (d >= 10) return '#CDDC39';
  if (d >= 5)  return '#FFC107';
  if (d >= 1)  return '#FF9800';
  return '#ef5350';
}}

/* Rank-based dollar color: sort all players by $ desc, then apply the
   gold/red→blue gradient. Cached per-call for performance. */
var _dollarRankCache = null;
function _dollarRankColor(d) {{
  if (!_dollarRankCache) {{
    var all = (typeof TRADE_HITTERS !== 'undefined' ? TRADE_HITTERS : [])
              .concat(typeof TRADE_PITCHERS !== 'undefined' ? TRADE_PITCHERS : []);
    var vals = all.map(function(p) {{ return p.dollars || 0; }});
    vals.sort(function(a,b) {{ return b - a; }});
    // remove dupes for ranking
    var unique = [];
    vals.forEach(function(v) {{ if (!unique.length || unique[unique.length-1] !== v) unique.push(v); }});
    _dollarRankCache = unique;
  }}
  var idx = 0;
  for (var i = 0; i < _dollarRankCache.length; i++) {{
    if (d >= _dollarRankCache[i]) {{ idx = i; break; }}
  }}
  var n = _dollarRankCache.length;
  var rank = idx + 1;
  return _phase3RankColor(rank, n);
}}

/* ── Simulate waiver move: deep-copy league, apply drops + adds to team ── */
function _wwSimulate() {{
  if (!PHASE3_LEAGUE || _wwState.teamId == null) return null;
  if (!_wwState.drops.length && !_wwState.adds.length) return null;

  var league = JSON.parse(JSON.stringify(PHASE3_LEAGUE.teams));
  var tid = _wwState.teamId;

  league.forEach(function(team) {{
    if (team.team_id !== tid) return;

    // Remove dropped players
    var dropIds = {{}};
    _wwState.drops.forEach(function(d) {{ dropIds[d.espn_id] = true; }});
    var origPitIds = team.pitchers.map(function(p){{ return p.espn_id; }}).sort().join();
    team.hitters  = team.hitters.filter(function(p)  {{ return !dropIds[p.espn_id]; }});
    team.pitchers = team.pitchers.filter(function(p) {{ return !dropIds[p.espn_id]; }});

    // Add FA pickups
    _wwState.adds.forEach(function(fa) {{
      if (fa.is_pitcher) {{
        team.pitchers.push({{
          espn_id:  'fa_' + (fa.name || '').replace(/[^A-Za-z0-9]+/g, '_'),
          name:     fa.name,
          team:     fa.team,
          dollars:  fa.dollars || 0,
          W:        (fa.proj && fa.proj.W)    || 0,
          SO_p:     (fa.proj && fa.proj.K_p)  || 0,
          SV:       (fa.proj && fa.proj.SV)   || 0,
          HLD:      (fa.proj && fa.proj.HLD)  || 0,
          ERA:      (fa.proj && fa.proj.ERA)  || 0,
          WHIP:     (fa.proj && fa.proj.WHIP) || 0,
          IP:       (fa.proj && fa.proj.IP)   || ((fa.role === 'sp' || ((fa.proj && fa.proj.IP)||0) >= 100) ? 160 : 65)
        }});
      }} else {{
        team.hitters.push({{
          espn_id:  'fa_' + (fa.name || '').replace(/[^A-Za-z0-9]+/g, '_'),
          name:     fa.name,
          team:     fa.team,
          dollars:  fa.dollars || 0,
          elig:     fa.elig || (fa.fg_pos ? _fgPosToSlots(fa.fg_pos) : [12]),
          R:        (fa.proj && fa.proj.R)    || 0,
          HR:       (fa.proj && fa.proj.HR)   || 0,
          RBI:      (fa.proj && fa.proj.RBI)  || 0,
          SO_h:     (fa.proj && fa.proj.K_h)  || 0,
          SB:       (fa.proj && fa.proj.SB)   || 0,
          OBP:      (fa.proj && fa.proj.OBP)  || 0,
          PA:       (fa.proj && fa.proj.PA)   || 600
        }});
      }}
    }});

    // Re-optimize hitter lineup
    var starters = _phase3OptimizeHitters(team.hitters);
    var hAgg = _phase3AggHit(starters);
    team.stats.R    = Math.round(hAgg.R    * 10) / 10;
    team.stats.HR   = Math.round(hAgg.HR   * 10) / 10;
    team.stats.RBI  = Math.round(hAgg.RBI  * 10) / 10;
    team.stats.SO_h = Math.round(hAgg.SO_h * 10) / 10;
    team.stats.SB   = Math.round(hAgg.SB   * 10) / 10;
    team.stats.OBP  = Math.round(hAgg.OBP  * 10000) / 10000;
    // Only recompute pitcher stats if pitcher roster actually changed
    var newPitIds = team.pitchers.map(function(p){{ return p.espn_id; }}).sort().join();
    if (origPitIds !== newPitIds) {{
      var pAgg = _phase3AggPit(team.pitchers);
      team.stats.W    = Math.round(pAgg.W    * 10) / 10;
      team.stats.SO_p = Math.round(pAgg.SO_p * 10) / 10;
      team.stats.SV   = Math.round(pAgg.SV   * 10) / 10;
      team.stats.HLD  = Math.round(pAgg.HLD  * 10) / 10;
      team.stats.ERA  = Math.round(pAgg.ERA  * 1000) / 1000;
      team.stats.WHIP = Math.round(pAgg.WHIP * 1000) / 1000;
    }}
  }});

  _phase3RecomputeZ(league);
  return league;
}}

/* ── Render Impact (compact delta for the user's team) ── */
function _wwRenderImpact() {{
  var newLeague = _wwSimulate();
  var delta = document.getElementById('ww-delta');
  if (!newLeague || !delta) return;

  var tid = _wwState.teamId;
  var oldT = PHASE3_LEAGUE.teams.find(function(t) {{ return t.team_id === tid; }});
  var newT = newLeague.find(function(t) {{ return t.team_id === tid; }});
  if (!oldT || !newT) return;

  window._wwLastLeague = newLeague;

  var n = newLeague.length;
  var rkC = _phase3RankColor(newT.rank_total, n);
  var zd = newT.z_total - oldT.z_total;
  var rd = oldT.rank_total - newT.rank_total;
  var zCol = zd > 0.005 ? '#4caf50' : zd < -0.005 ? '#e05555' : '#888';
  var rCol = rd > 0     ? '#4caf50' : rd < 0      ? '#e05555' : '#888';
  var zStr = (zd >= 0 ? '+' : '\u2212') + Math.abs(zd).toFixed(2);
  var rStr = rd === 0 ? '\u25A0 0'
           : (rd > 0 ? '\u25B2 ' : '\u25BC ') + Math.abs(rd);

  var html = '<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;padding:10px 14px;'
           + 'background:#1a1a1a;border-radius:8px;border-left:3px solid ' + rkC + '">'
           + '<div><span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;'
           + 'letter-spacing:.05em">Rank</span><br>'
           + '<span style="font-size:1.3rem;font-weight:700;color:' + rkC + '">#' + newT.rank_total + '</span></div>'
           + '<div><span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;'
           + 'letter-spacing:.05em">Z Total</span><br>'
           + '<span style="font-size:1.1rem;font-weight:700;color:#ddd">' + newT.z_total.toFixed(2) + '</span></div>'
           + '<div><span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;'
           + 'letter-spacing:.05em">\u0394 Z</span><br>'
           + '<span style="font-size:1.1rem;font-weight:700;color:' + zCol + '">' + zStr + '</span></div>'
           + '<div><span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;'
           + 'letter-spacing:.05em">\u0394 Rank</span><br>'
           + '<span style="font-size:1.1rem;font-weight:700;color:' + rCol + '">' + rStr + '</span></div>'
           + '</div>';
  delta.innerHTML = html;

  // Reset toggle states
  document.getElementById('ww-table-wrap').style.display = 'none';
  document.getElementById('ww-table-wrap').innerHTML = '';
  document.getElementById('ww-mc-wrap').style.display = 'none';
  document.getElementById('ww-mc-wrap').innerHTML = '';
  var tbtn = document.getElementById('ww-standings-btn');
  if (tbtn) tbtn.innerHTML = '\u25BC Show full updated standings';
  var mbtn = document.getElementById('ww-mc-btn');
  if (mbtn) mbtn.innerHTML = '\u25BC Show finish-probability sim (before / after)';
}}

/* ── Toggle standings table (reuses _phase3RenderTable) ── */
function wwToggleTable() {{
  var tw  = document.getElementById('ww-table-wrap');
  var btn = document.getElementById('ww-standings-btn');
  if (!tw) return;
  var open = tw.style.display !== 'none';
  if (open) {{
    tw.style.display = 'none';
    btn.innerHTML = '\u25BC Show full updated standings';
  }} else {{
    var newLeague = window._wwLastLeague || _wwSimulate();
    if (!newLeague) return;
    // Reuse the existing _phase3RenderTable by temporarily swapping the target element
    _wwRenderStandingsTable(newLeague, 'ww-table-wrap');
    tw.style.display = '';
    btn.innerHTML = '\u25B2 Hide full updated standings';
  }}
}}

/* Render standings table into a given container (shared by add/drop and stream) */
function _wwRenderStandingsTable(newLeague, containerId, highlightTeamId) {{
  var tw = document.getElementById(containerId);
  if (!tw) return;
  var n = newLeague.length;
  function findOld(tid) {{ return PHASE3_LEAGUE.teams.find(function(t) {{ return t.team_id === tid; }}); }}
  var sorted = newLeague.slice().sort(function(a,b) {{ return a.rank_total - b.rank_total; }});
  var lower = {{}};
  PHASE3_LEAGUE.lower_better.forEach(function(c) {{ lower[c] = true; }});
  var hCats = PHASE3_LEAGUE.hit_cats || [];
  var pCats = PHASE3_LEAGUE.pit_cats || [];
  var catLabel = {{'R':'R','HR':'HR','RBI':'RBI','SO_h':'K','SB':'SB','OBP':'OBP',
                  'W':'W','SO_p':'K','SV':'SV','HLD':'HLD','ERA':'ERA','WHIP':'WHIP'}};
  var thSt = 'padding:5px 8px;text-align:center;color:var(--muted);font-size:.78rem;white-space:nowrap';

  var html = '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:.85rem">'
           + '<thead><tr style="border-bottom:1px solid #333">'
           + '<th style="text-align:left;padding:6px 10px;color:var(--muted)">#</th>'
           + '<th style="text-align:left;padding:6px 10px;color:var(--muted)">Team</th>'
           + '<th style="text-align:center;padding:6px 10px;color:var(--muted)">Z Tot</th>'
           + '<th style="text-align:center;padding:6px 10px;color:var(--muted)">\u0394Z</th>'
           + '<th style="text-align:center;padding:6px 10px;color:var(--muted)">\u0394Rk</th>'
           + '<th style="border-left:2px solid #444;' + thSt + '">H Z</th>';
  hCats.forEach(function(c) {{ html += '<th style="' + thSt + '">' + (catLabel[c]||c) + '</th>'; }});
  html += '<th style="border-left:2px solid #444;' + thSt + '">P Z</th>';
  pCats.forEach(function(c) {{ html += '<th style="' + thSt + '">' + (catLabel[c]||c) + '</th>'; }});
  html += '</tr></thead><tbody>';

  /* Compute per-cat ranks for color-coding */
  var allCats = hCats.concat(pCats);
  var catRanks = {{}};
  allCats.forEach(function(cat) {{
    var isLow = lower[cat];
    var pairs = newLeague.map(function(t) {{ return {{tid: t.team_id, v: t.stats[cat]||0}}; }});
    pairs.sort(function(a,b) {{ return isLow ? a.v - b.v : b.v - a.v; }});
    var rk = {{}};
    pairs.forEach(function(p, i) {{ rk[p.tid] = i + 1; }});
    catRanks[cat] = rk;
  }});

  sorted.forEach(function(t) {{
    var old = findOld(t.team_id);
    var rkC = _phase3RankColor(t.rank_total, n);
    var zd  = t.z_total - old.z_total;
    var rd  = old.rank_total - t.rank_total;
    var zCol = zd > 0.005 ? '#4caf50' : zd < -0.005 ? '#e05555' : '#888';
    var rCol = rd > 0     ? '#4caf50' : rd < 0      ? '#e05555' : '#888';
    var zStr = (zd >= 0 ? '+' : '\u2212') + Math.abs(zd).toFixed(2);
    var rStr = rd === 0 ? '\u25A0 0'
             : (rd > 0 ? '\u25B2 ' : '\u25BC ') + Math.abs(rd);
    var isUser = t.team_id === (highlightTeamId != null ? highlightTeamId : _wwState.teamId);
    var nameAccent = isUser ? '#4caf50' : '#ddd';
    var bg = isUser ? '#1a1a1a' : 'transparent';

    function fmtRaw(cat, v) {{
      if (cat === 'OBP') return v.toFixed(3);
      if (cat === 'ERA' || cat === 'WHIP') return v.toFixed(2);
      return Math.round(v).toString();
    }}

    function zCell(cat, borderLeft) {{
      var nz = t.z[cat] || 0;
      var oz = old.z[cat] || 0;
      var nv = t.stats[cat] || 0;
      var ov = old.stats[cat] || 0;
      var rawD = nv - ov;
      var zd2 = nz - oz;
      var isLower = lower[cat];
      var rawGood = isLower ? (rawD < -0.0005) : (rawD > 0.0005);
      var rawBad  = isLower ? (rawD > 0.0005)  : (rawD < -0.0005);
      var rawChanged = Math.abs(rawD) > 0.0005;
      var rawDStr = '';
      if (rawChanged) {{
        var rc = rawGood ? '#4caf50' : rawBad ? '#e05555' : '#888';
        var rawSign = rawD >= 0 ? '+' : '\u2212';
        rawDStr = '<div style="font-size:.66rem;color:' + rc + ';line-height:1;margin-top:2px">'
                + rawSign + fmtRaw(cat, Math.abs(rawD)) + '</div>';
      }}
      var zStr2 = '<div style="font-size:.68rem;color:#666;line-height:1;margin-top:2px">z ' + nz.toFixed(2) + '</div>';
      var rkNum = catRanks[cat][t.team_id];
      var rkStr2 = '<div style="font-size:.55rem;color:#555;line-height:1;margin-top:1px">#' + rkNum + '</div>';
      var bl = borderLeft ? 'border-left:2px solid #444;' : '';
      return '<td style="' + bl + 'text-align:center;padding:4px 6px">'
           + '<div style="font-size:.92rem;line-height:1.15;font-weight:600;color:' + _phase3RankColor(catRanks[cat][t.team_id], n) + '">' + fmtRaw(cat, nv) + '</div>'
           + zStr2 + rkStr2
           + rawDStr + '</td>';
    }}

    html += '<tr style="background:' + bg + ';border-bottom:1px solid #222">'
          + '<td style="padding:5px 10px;color:' + rkC + ';font-weight:700">#' + t.rank_total + '</td>'
          + '<td style="padding:5px 10px;color:' + nameAccent + ';font-weight:600;white-space:nowrap">' + t.name + '</td>'
          + '<td style="padding:5px 10px;text-align:center;color:' + rkC + ';font-weight:700">'
          +   t.z_total.toFixed(2) + '</td>'
          + '<td style="padding:5px 10px;text-align:center;color:' + zCol + '">' + zStr + '</td>'
          + '<td style="padding:5px 10px;text-align:center;color:' + rCol + '">' + rStr + '</td>';
    var hzd = (t.z_hit||0) - (old.z_hit||0);
    var hzCol = hzd > 0.005 ? '#4caf50' : hzd < -0.005 ? '#e05555' : '#888';
    var hzDStr = Math.abs(hzd) > 0.005
               ? '<div style="font-size:.68rem;color:' + hzCol + ';line-height:1">'
                 + (hzd > 0 ? '+' : '\u2212') + Math.abs(hzd).toFixed(2) + '</div>'
               : '';
    html += '<td style="border-left:2px solid #444;text-align:center;padding:4px 6px;font-weight:600">'
          + '<div style="font-size:.92rem;line-height:1.15;color:' + _phase3RankColor(t.rank_hit, n) + '">' + (t.z_hit||0).toFixed(2) + '</div>' + hzDStr + '</td>';
    hCats.forEach(function(c) {{ html += zCell(c, false); }});
    var pzd = (t.z_pit||0) - (old.z_pit||0);
    var pzCol = pzd > 0.005 ? '#4caf50' : pzd < -0.005 ? '#e05555' : '#888';
    var pzDStr = Math.abs(pzd) > 0.005
               ? '<div style="font-size:.68rem;color:' + pzCol + ';line-height:1">'
                 + (pzd > 0 ? '+' : '\u2212') + Math.abs(pzd).toFixed(2) + '</div>'
               : '';
    html += '<td style="border-left:2px solid #444;text-align:center;padding:4px 6px;font-weight:600">'
          + '<div style="font-size:.92rem;line-height:1.15;color:' + _phase3RankColor(t.rank_pit, n) + '">' + (t.z_pit||0).toFixed(2) + '</div>' + pzDStr + '</td>';
    pCats.forEach(function(c) {{ html += zCell(c, false); }});
    html += '</tr>';
  }});
  html += '</tbody></table></div>';
  tw.innerHTML = html;
}}

/* ── Toggle MC sim (before/after) ── */
function wwToggleMc() {{
  var mw  = document.getElementById('ww-mc-wrap');
  var btn = document.getElementById('ww-mc-btn');
  if (!mw) return;
  var open = mw.style.display !== 'none';
  if (open) {{
    mw.style.display = 'none';
    btn.innerHTML = '\u25BC Show finish-probability sim (before / after)';
  }} else {{
    _wwRenderMc('ww-mc-wrap', window._wwLastLeague, _wwState.teamId);
    mw.style.display = '';
    btn.innerHTML = '\u25B2 Hide finish-probability sim';
  }}
}}

/* Shared MC renderer for both add/drop and stream modes */
function _wwRenderMc(containerId, newLeague, highlightTid) {{
  var mw = document.getElementById(containerId);
  if (!mw || !newLeague) return;
  mw.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:.75rem">Running 50,000 trials\u2026</div>';
  setTimeout(function() {{
    var baseSim = _mcGetBaseline();
    var newSim  = _mcRunSim(newLeague);
    var hl = highlightTid != null ? [highlightTid] : [];

    // Build before/after side by side
    var baseExp = baseSim[highlightTid] ? baseSim[highlightTid].expFinish : 0;
    var newExp  = newSim[highlightTid]  ? newSim[highlightTid].expFinish  : 0;
    var delta   = newExp - baseExp;
    var dCol    = delta < -0.05 ? '#4caf50' : delta > 0.05 ? '#e05555' : '#888';
    var dSign   = delta < 0 ? '\u25B2' : (delta > 0 ? '\u25BC' : '\u25A0');

    var teamName = '';
    PHASE3_LEAGUE.teams.forEach(function(t) {{ if (t.team_id === highlightTid) teamName = t.name; }});

    var summary = '<div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;'
                + 'padding:10px 14px;background:#1a1a1a;border-radius:8px;margin-bottom:12px">'
                + '<div style="font-weight:700;color:#ddd;font-size:.85rem">' + teamName + '</div>'
                + '<div><span style="color:var(--muted);font-size:.68rem">Before: </span>'
                + '<span style="font-weight:700;color:#ddd">' + baseExp.toFixed(2) + '</span></div>'
                + '<div><span style="color:var(--muted);font-size:.68rem">After: </span>'
                + '<span style="font-weight:700;color:#ddd">' + newExp.toFixed(2) + '</span></div>'
                + '<div><span style="color:var(--muted);font-size:.68rem">\u0394: </span>'
                + '<span style="font-weight:700;color:' + dCol + '">' + dSign + ' '
                + Math.abs(delta).toFixed(2) + '</span></div>'
                + '</div>';

    var grid = '<div style="display:flex;gap:14px;flex-wrap:wrap">'
             + '<div style="flex:1;min-width:320px">'
             + '<div style="font-size:.66rem;color:var(--muted);font-weight:700;'
             + 'text-transform:uppercase;letter-spacing:.05em;padding:0 0 6px 4px">Before</div>'
             + _mcRenderHeatmap(PHASE3_LEAGUE.teams, baseSim, hl)
             + '</div>'
             + '<div style="flex:1;min-width:320px">'
             + '<div style="font-size:.66rem;color:var(--muted);font-weight:700;'
             + 'text-transform:uppercase;letter-spacing:.05em;padding:0 0 6px 4px">After waiver move</div>'
             + _mcRenderHeatmap(newLeague, newSim, hl)
             + '</div>'
             + '</div>';
    mw.innerHTML = summary + grid;
  }}, 20);
}}

/* ════════════════════════════════════════════════════════════════════
   ── STREAM PITCHERS MODE ─────────────────────────────────────────
   ════════════════════════════════════════════════════════════════════ */

function wwStreamSetTeam(val) {{
  var tid = val === '' ? null : parseInt(val, 10);
  _wwStreamState.teamId = tid;
  _wwStreamState.drop = null;
  _wwStreamState.streamerProfile = null;
  var body = document.getElementById('ww-stream-body');
  if (!tid) {{ if (body) body.style.display = 'none'; return; }}
  if (body) body.style.display = '';
  _wwStreamComputeProfile();
  _wwStreamRender();
}}

function _wwStreamGetTeam() {{
  if (!PHASE3_LEAGUE || _wwStreamState.teamId == null) return null;
  return PHASE3_LEAGUE.teams.find(function(t) {{ return t.team_id === _wwStreamState.teamId; }});
}}

/* Compute the "average streamer" from top 8 unrostered SP by $ value.
   Only includes pitchers with SP role AND 100+ projected IP (filters out
   relievers who happen to have SP eligibility). ALSO excludes anyone on
   the MLB 60-day IL (p.il) — high-$ injured arms like Shane Bieber would
   otherwise inflate the streamer baseline even though they aren't realistic
   pickups right now.
   Returns per-start stats scaled to 4 starts/week for the remaining season. */
function _wwStreamComputeProfile() {{
  // Find top 8 unrostered, healthy SP by dollar value, requiring 100+ proj IP
  var sps = TRADE_PITCHERS.filter(function(p) {{
    return p.team_id == null && p.role === 'sp'
        && !p.il
        && p.proj && p.proj.IP >= 100;
  }});
  sps.sort(function(a,b) {{ return (b.dollars||0) - (a.dollars||0); }});
  var top5 = sps.slice(0, 8);

  if (!top5.length) {{
    _wwStreamState.streamerProfile = null;
    return;
  }}

  // Average their RoS projections
  var avg = {{ W:0, K_p:0, ERA:0, WHIP:0, IP:0, dollars:0 }};
  top5.forEach(function(p) {{
    avg.W    += (p.proj && p.proj.W)    || 0;
    avg.K_p  += (p.proj && p.proj.K_p)  || 0;
    avg.ERA  += (p.proj && p.proj.ERA)  || 0;
    avg.WHIP += (p.proj && p.proj.WHIP) || 0;
    avg.IP   += (p.proj && p.proj.IP)   || 0;
    avg.dollars += (p.dollars || 0);
  }});
  var cnt = top5.length;
  avg.W /= cnt; avg.K_p /= cnt;
  avg.ERA /= cnt; avg.WHIP /= cnt; avg.IP /= cnt; avg.dollars /= cnt;

  // Per-start decomposition: compute per-start rates from the top-8 average,
  // then multiply by total streamer starts (4/week * weeksLeft).
  // Use ESPN matchup schedule data when available; fall back to date calc.
  var weeksLeft;
  if (PHASE3_LEAGUE && PHASE3_LEAGUE.matchup_total && PHASE3_LEAGUE.matchup_current) {{
    // ESPN H2H: weeks remaining AFTER the current week finishes
    // (i.e. "if I start streaming next week, how many weeks do I get?")
    weeksLeft = Math.max(1, PHASE3_LEAGUE.matchup_total - PHASE3_LEAGUE.matchup_current);
  }} else {{
    var today = new Date();
    var seasonEnd = new Date(today.getFullYear(), 8, 28); // Sept 28 fallback
    var msPerWeek = 7 * 24 * 3600 * 1000;
    weeksLeft = Math.max(1, Math.round((seasonEnd - today) / msPerWeek));
  }}
  var streamerStarts = 4 * weeksLeft;

  // Derive per-start rates from the average top-8 SP full-season projection
  var IP_PER_START = 5.3;
  var avgStartsRoS = Math.max(1, avg.IP / IP_PER_START);
  var wPerStart  = avg.W   / avgStartsRoS;
  var kPerStart  = avg.K_p / avgStartsRoS;
  var ipPerStart = IP_PER_START;

  // Scale counting stats and IP by total streamer starts;
  // rate stats (ERA, WHIP) stay as the top-8 average (they're per-inning rates).
  // SV and HLD are forced to 0: streaming SPs never earn saves or holds.
  var profile = {{
    W:    wPerStart  * streamerStarts,
    SO_p: kPerStart  * streamerStarts,
    SV:   0,
    HLD:  0,
    ERA:  avg.ERA,
    WHIP: avg.WHIP,
    IP:   ipPerStart * streamerStarts,
    dollars: avg.dollars,
    top5Names: top5.map(function(p) {{ return p.name; }}),
    weeksLeft: weeksLeft,
    startsPerWeek: 4,
    totalStarts: streamerStarts
  }};
  _wwStreamState.streamerProfile = profile;
}}

function _wwStreamRender() {{
  var team = _wwStreamGetTeam();
  if (!team) return;
  var rosterEl = document.getElementById('ww-stream-roster');
  var previewEl = document.getElementById('ww-stream-preview');
  var impactEl = document.getElementById('ww-stream-impact');
  var statsEl = document.getElementById('ww-stream-stats');

  if (!rosterEl) return;

  // Show roster with drop buttons
  var hitters = (team.hitters || []).slice().sort(function(a,b) {{ return (b.dollars||0)-(a.dollars||0); }});
  var pitchers = (team.pitchers || []).slice().sort(function(a,b) {{ return (b.dollars||0)-(a.dollars||0); }});

  var html = '';
  var hasDropped = !!_wwStreamState.drop;

  function pRow(p, isPitcher) {{
    var isDropped = hasDropped && String(p.espn_id) === String(_wwStreamState.drop.espn_id);
    var dc = _dollarRankColor(p.dollars || 0);
    var opacity = isDropped ? 'opacity:0.35;' : '';
    var role = isPitcher ? ((p.IP||0) >= 100 ? 'SP' : 'RP') : '';
    var posLabel = isPitcher ? role : _eligLabel(p.elig);
    var posBadge = posLabel ? '<span style="color:#777;font-size:.58rem;font-weight:700;margin-left:4px">' + posLabel + '</span>' : '';
    var r = '<div style="display:flex;align-items:center;gap:8px;padding:5px 8px;'
          + 'background:#1a1a1a;border-radius:5px;margin-bottom:3px;' + opacity + '">'
          + '<span style="flex:1;font-size:.82rem;font-weight:600;color:#ddd">'
          + (p.name || '') + posBadge + '</span>'
          + '<span style="font-size:.68rem;color:#888">' + (p.team || '') + '</span>'
          + '<span style="font-size:.82rem;font-weight:700;color:' + dc + ';width:52px;text-align:right">'
          + '$' + (p.dollars||0).toFixed(1) + '</span>';
    if (!isDropped && !hasDropped) {{
      r += '<button data-eid="' + (p.espn_id+'').replace(/"/g,'&quot;') + '"'
         + ' onclick="wwStreamDrop(this.dataset.eid)"'
         + ' style="background:#3a1a1a;border:1px solid #6b2e2e;color:#e05555;cursor:pointer;'
         + 'font-size:.68rem;padding:2px 8px;border-radius:4px;font-weight:700">Drop</button>';
    }} else if (isDropped) {{
      r += '<button onclick="wwStreamUndrop()"'
         + ' style="background:#1a3a1a;border:1px solid #2e6b2e;color:#9cd39c;cursor:pointer;'
         + 'font-size:.68rem;padding:2px 8px;border-radius:4px;font-weight:700">Undo</button>';
    }}
    r += '</div>';
    return r;
  }}

  html += '<div style="font-size:.7rem;color:#4caf50;font-weight:700;margin-bottom:4px">Hitters</div>';
  hitters.forEach(function(p) {{ html += pRow(p, false); }});
  html += '<div style="font-size:.7rem;color:#4caf50;font-weight:700;margin:8px 0 4px">Pitchers</div>';
  pitchers.forEach(function(p) {{ html += pRow(p, true); }});
  rosterEl.innerHTML = html;

  // Show streamer profile preview
  var prof = _wwStreamState.streamerProfile;
  if (prof && previewEl && statsEl) {{
    previewEl.style.display = '';
    var nameList = prof.top5Names.join(', ');
    var dropP = _wwStreamState.drop;
    // Net gains = streamer totals minus dropped pitcher's projections
    var netW  = dropP ? prof.W    - (dropP.W    || 0) : prof.W;
    var netK  = dropP ? prof.SO_p - (dropP.SO_p || 0) : prof.SO_p;
    var netIP = dropP ? prof.IP   - (dropP.IP   || 0) : prof.IP;
    var netLabel = dropP ? 'Net gain (vs dropping ' + (dropP.name||'player') + ')' : 'Streamer RoS totals';
    statsEl.innerHTML = '<div style="font-size:.78rem;color:#ddd;margin-bottom:6px">'
      + 'Based on: <span style="color:#4caf50">' + nameList + '</span></div>'
      + '<div style="font-size:.75rem;color:#bbb;margin-bottom:8px">'
      + prof.weeksLeft + ' weeks left &times; ' + prof.startsPerWeek + ' starts/wk = '
      + '<strong style="color:#fff">' + prof.totalStarts + ' total starts</strong></div>'
      + '<div style="font-size:.65rem;color:#999;letter-spacing:.03em;margin-bottom:3px">' + netLabel + '</div>'
      + '<div style="display:flex;gap:12px;flex-wrap:wrap;font-size:.78rem">'
      + '<span><span style="color:var(--muted)">W:</span> <strong>' + (netW>=0?'+':'') + netW.toFixed(1) + '</strong></span>'
      + '<span><span style="color:var(--muted)">K:</span> <strong>' + (netK>=0?'+':'') + netK.toFixed(1) + '</strong></span>'
      + '<span><span style="color:var(--muted)">ERA:</span> <strong>' + prof.ERA.toFixed(2) + '</strong></span>'
      + '<span><span style="color:var(--muted)">WHIP:</span> <strong>' + prof.WHIP.toFixed(2) + '</strong></span>'
      + '<span><span style="color:var(--muted)">IP:</span> <strong>' + (netIP>=0?'+':'') + netIP.toFixed(1) + '</strong></span>'
      + '</div>';
  }}

  // Show impact if player dropped (or simulate without drop for preview)
  if (hasDropped) {{
    _wwStreamRenderImpact();
    if (impactEl) impactEl.style.display = '';
  }} else {{
    if (impactEl) impactEl.style.display = 'none';
  }}
}}

function wwStreamDrop(espnId) {{
  var team = _wwStreamGetTeam();
  if (!team) return;
  var all = (team.hitters || []).concat(team.pitchers || []);
  var p = all.find(function(x) {{ return String(x.espn_id) === String(espnId); }});
  if (!p) return;
  _wwStreamState.drop = p;
  _wwStreamRender();
}}

function wwStreamUndrop() {{
  _wwStreamState.drop = null;
  _wwStreamRender();
}}

/* Simulate streaming: drop one player, add the streamer profile as a pitcher */
function _wwStreamSimulate() {{
  if (!PHASE3_LEAGUE || _wwStreamState.teamId == null) return null;
  var prof = _wwStreamState.streamerProfile;
  if (!prof) return null;
  if (!_wwStreamState.drop) return null;

  var league = JSON.parse(JSON.stringify(PHASE3_LEAGUE.teams));
  var tid = _wwStreamState.teamId;

  league.forEach(function(team) {{
    if (team.team_id !== tid) return;

    // Remove dropped player
    var dropId = _wwStreamState.drop.espn_id;
    team.hitters  = team.hitters.filter(function(p)  {{ return p.espn_id !== dropId; }});
    team.pitchers = team.pitchers.filter(function(p) {{ return p.espn_id !== dropId; }});

    // Add streamer pitcher with the profile stats.
    // variance_scale reduces MC sim variance to reflect diversification:
    // streaming 96 independent starts (vs ~28 for one SP) means the
    // aggregate outcome has lower variance by ~1/sqrt(96/28) ≈ 0.54.
    var typicalSPStarts = 28;
    var vScale = Math.sqrt(typicalSPStarts / Math.max(1, prof.totalStarts));
    team.pitchers.push({{
      espn_id:  'streamer_composite',
      name:     'Streaming SP (4/wk)',
      team:     'FA',
      dollars:  prof.dollars || 0,
      W:        prof.W,
      SO_p:     prof.SO_p,
      SV:       prof.SV,
      HLD:      prof.HLD,
      ERA:      prof.ERA,
      WHIP:     prof.WHIP,
      IP:       prof.IP,
      variance_scale: vScale
    }});

    // Re-optimize + re-aggregate
    var starters = _phase3OptimizeHitters(team.hitters);
    var hAgg = _phase3AggHit(starters);
    var pAgg = _phase3AggPit(team.pitchers);
    team.stats = {{
      R:    Math.round(hAgg.R    * 10) / 10,
      HR:   Math.round(hAgg.HR   * 10) / 10,
      RBI:  Math.round(hAgg.RBI  * 10) / 10,
      SO_h: Math.round(hAgg.SO_h * 10) / 10,
      SB:   Math.round(hAgg.SB   * 10) / 10,
      OBP:  Math.round(hAgg.OBP  * 10000) / 10000,
      W:    Math.round(pAgg.W    * 10) / 10,
      SO_p: Math.round(pAgg.SO_p * 10) / 10,
      SV:   Math.round(pAgg.SV   * 10) / 10,
      HLD:  Math.round(pAgg.HLD  * 10) / 10,
      ERA:  Math.round(pAgg.ERA  * 1000) / 1000,
      WHIP: Math.round(pAgg.WHIP * 1000) / 1000,
    }};
  }});

  _phase3RecomputeZ(league);
  return league;
}}

function _wwStreamRenderImpact() {{
  var newLeague = _wwStreamSimulate();
  var delta = document.getElementById('ww-stream-delta');
  if (!newLeague || !delta) return;

  var tid = _wwStreamState.teamId;
  var oldT = PHASE3_LEAGUE.teams.find(function(t) {{ return t.team_id === tid; }});
  var newT = newLeague.find(function(t) {{ return t.team_id === tid; }});
  if (!oldT || !newT) return;

  window._wwStreamLastLeague = newLeague;

  var n = newLeague.length;
  var rkC = _phase3RankColor(newT.rank_total, n);
  var zd = newT.z_total - oldT.z_total;
  var rd = oldT.rank_total - newT.rank_total;
  var zCol = zd > 0.005 ? '#4caf50' : zd < -0.005 ? '#e05555' : '#888';
  var rCol = rd > 0     ? '#4caf50' : rd < 0      ? '#e05555' : '#888';
  var zStr = (zd >= 0 ? '+' : '\u2212') + Math.abs(zd).toFixed(2);
  var rStr = rd === 0 ? '\u25A0 0'
           : (rd > 0 ? '\u25B2 ' : '\u25BC ') + Math.abs(rd);

  delta.innerHTML = '<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;padding:10px 14px;'
    + 'background:#1a1a1a;border-radius:8px;border-left:3px solid ' + rkC + '">'
    + '<div><span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;'
    + 'letter-spacing:.05em">Rank</span><br>'
    + '<span style="font-size:1.3rem;font-weight:700;color:' + rkC + '">#' + newT.rank_total + '</span></div>'
    + '<div><span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;'
    + 'letter-spacing:.05em">Z Total</span><br>'
    + '<span style="font-size:1.1rem;font-weight:700;color:#ddd">' + newT.z_total.toFixed(2) + '</span></div>'
    + '<div><span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;'
    + 'letter-spacing:.05em">\u0394 Z</span><br>'
    + '<span style="font-size:1.1rem;font-weight:700;color:' + zCol + '">' + zStr + '</span></div>'
    + '<div><span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;'
    + 'letter-spacing:.05em">\u0394 Rank</span><br>'
    + '<span style="font-size:1.1rem;font-weight:700;color:' + rCol + '">' + rStr + '</span></div>'
    + '</div>';

  // Reset toggle states
  document.getElementById('ww-stream-table-wrap').style.display = 'none';
  document.getElementById('ww-stream-table-wrap').innerHTML = '';
  document.getElementById('ww-stream-mc-wrap').style.display = 'none';
  document.getElementById('ww-stream-mc-wrap').innerHTML = '';
  var tbtn = document.getElementById('ww-stream-standings-btn');
  if (tbtn) tbtn.innerHTML = '\u25BC Show full updated standings';
  var mbtn = document.getElementById('ww-stream-mc-btn');
  if (mbtn) mbtn.innerHTML = '\u25BC Show finish-probability sim (before / after)';
}}

function wwStreamToggleTable() {{
  var tw  = document.getElementById('ww-stream-table-wrap');
  var btn = document.getElementById('ww-stream-standings-btn');
  if (!tw) return;
  var open = tw.style.display !== 'none';
  if (open) {{
    tw.style.display = 'none';
    btn.innerHTML = '\u25BC Show full updated standings';
  }} else {{
    var newLeague = window._wwStreamLastLeague || _wwStreamSimulate();
    if (!newLeague) return;
    _wwRenderStandingsTable(newLeague, 'ww-stream-table-wrap', _wwStreamState.teamId);
    tw.style.display = '';
    btn.innerHTML = '\u25B2 Hide full updated standings';
  }}
}}

function wwStreamToggleMc() {{
  var mw  = document.getElementById('ww-stream-mc-wrap');
  var btn = document.getElementById('ww-stream-mc-btn');
  if (!mw) return;
  var open = mw.style.display !== 'none';
  if (open) {{
    mw.style.display = 'none';
    btn.innerHTML = '\u25BC Show finish-probability sim (before / after)';
  }} else {{
    _wwRenderMc('ww-stream-mc-wrap', window._wwStreamLastLeague, _wwStreamState.teamId);
    mw.style.display = '';
    btn.innerHTML = '\u25B2 Hide finish-probability sim';
  }}
}}
</script>
"""
    return inner

if __name__ == "__main__":
    main()