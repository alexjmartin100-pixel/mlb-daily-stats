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
      C/1B/2B/3B/SS/CI/MI/OF(3)/UTIL/P/SP(3)/RP(2)/Bench(6), 35 IP min.
    Projection codes confirmed from FanGraphs dropdown:
      'roopsydc'  = OOPYS DC (RoS)
      'rthebatx'  = THE BAT X (RoS)
    """
    from urllib.parse import urlencode
    # points=c|1,2,3,4,9|0,1,12,2,3,4  encodes batting|pitching category IDs.
    # pos=1,1,1,1,3,1,1,1,0,1,3,2,1,6,35 encodes slot counts + MinIP.
    params = {
        "teams": 10, "lg": "MLB", "dollars": 260, "mb": 1,
        "mp": 20, "msp": 5, "mrp": 5,
        "type": player_type,
        "players": "", "proj": proj, "split": "",
        "points": "c|1,2,3,4,9,5|0,1,12,2,3,4",  # 5=OBP added to hitting cats
        "rep": 0, "drp": 0,
        "pp": "C,SS,2B,3B,OF,1B",
        "pos": "1,1,1,1,3,1,1,1,0,1,3,2,1,6,35",
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
        "pos": "1,1,1,1,3,1,1,1,0,1,3,2,1,6,35",
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
                player = {
                    "name": name, "team": team,
                    "fg_id": fgid, "mlbam": mlbam,
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
    if v >= 30:  return "#4CAF50"
    if v >= 20:  return "#8BC34A"
    if v >= 10:  return "#CDDC39"
    if v >= 5:   return "#FFC107"
    if v >= 1:   return "#FF9800"
    return "#ef5350"


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
            rank = row["rank"][c]
            color = _proj_rank_color(rank, n_teams)
            cells.append(
                f'<td data-sort="{raw_val}" '
                f'style="text-align:center;padding:6px 6px">'
                f'<div style="font-size:.82rem;font-weight:700;color:{color};'
                f'line-height:1.1">{stat_str}</div>'
                f'<div style="font-size:.6rem;color:#666;line-height:1.1;'
                f'margin-top:1px">#{rank}</div>'
                f'</td>'
            )
        # Hitter z subtotal — sort by raw z_hit value (desc default = best at top)
        cells.append(_subtotal_cell(row["z_hit"], row["rank_hit"], row["z_hit"]))
        # Pitcher category cells
        for c in p_sub:
            raw_val = row["stats"][c]
            stat_str = _fmt_proj_stat(c, raw_val)
            rank = row["rank"][c]
            color = _proj_rank_color(rank, n_teams)
            cells.append(
                f'<td data-sort="{raw_val}" '
                f'style="text-align:center;padding:6px 6px">'
                f'<div style="font-size:.82rem;font-weight:700;color:{color};'
                f'line-height:1.1">{stat_str}</div>'
                f'<div style="font-size:.6rem;color:#666;line-height:1.1;'
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

    return legend + table_html


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
            "hitters":    [_hit_record(h) for h in pt.get("hitters",  [])],
            "pitchers":   [_pit_record(p) for p in pt.get("pitchers", [])],
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
    }


def render_fantasy_tab(fdata: dict) -> str:
    """Generate the full HTML for the Fantasy tab panel."""
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
    def _build_table(players: list, cats: list, table_id: str) -> str:
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

            rows_html.append(
                f'<tr data-role="{role}">'
                f'<td class="rank-col" data-val="{rank}">{rank}</td>'
                f'<td class="name-col">{nm}</td>'
                f'<td style="white-space:nowrap">{team_cell}</td>'
                f'<td style="color:{fdol_col};font-weight:700;font-size:.95rem"'
                f' data-val="{fdol_val}">{fdol_str}</td>'
                f'{stat_cells}'
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

    tbl_h = _build_table(fdata["fut_h"], h_cats, "fant-h-tbl")
    tbl_p = _build_table(fdata["fut_p"], p_cats, "fant-p-tbl")

    # ── trade tab: embed player pool as JSON for client-side search ─────────
    import json as _json
    def _sf(v):
        try: return round(float(v or 0), 1)
        except: return 0.0
    def _sfp(v, d=3):
        try: return round(float(v or 0), d)
        except: return 0.0
    _trade_h = []
    for _e in fdata["fut_h"]:
        _p = _e["player"]
        _trade_h.append({
            "name": _p.get("name",""), "team": (_p.get("team") or "").upper(),
            "dollars": _sf(_e["dollar"]), "is_pitcher": False,
            "cats": {"R":_sf(_p.get("R")),"HR":_sf(_p.get("HR")),"RBI":_sf(_p.get("RBI")),
                     "SB":_sf(_p.get("SB")),"K":_sf(_p.get("SO")),"OBP":_sf(_p.get("OBP"))},
            "proj": {"R":_sfp(_p.get("R_p"),0),"HR":_sfp(_p.get("HR_p"),0),
                     "RBI":_sfp(_p.get("RBI_p"),0),"SB":_sfp(_p.get("SB_p"),0),
                     "K":_sfp(_p.get("SO_p"),0),"OBP":_sfp(_p.get("OBP_p"),3)}
        })
    _trade_p = []
    for _e in fdata["fut_p"]:
        _p = _e["player"]
        _trade_p.append({
            "name": _p.get("name",""), "team": (_p.get("team") or "").upper(),
            "role": _e.get("role","sp"), "dollars": _sf(_e["dollar"]), "is_pitcher": True,
            "cats": {"W":_sf(_p.get("W")),"ERA":_sf(_p.get("ERA")),"WHIP":_sf(_p.get("WHIP")),
                     "K":_sf(_p.get("SO")),"SV":_sf(_p.get("SV")),"HLD":_sf(_p.get("HLD"))},
            "proj": {"W":_sfp(_p.get("W_p"),0),"ERA":_sfp(_p.get("ERA_p"),2),
                     "WHIP":_sfp(_p.get("WHIP_p"),2),"K":_sfp(_p.get("SO_p"),0),
                     "SV":_sfp(_p.get("SV_p"),0),"HLD":_sfp(_p.get("HLD_p"),0)}
        })
    _trade_h_json = _json.dumps(_trade_h)
    _trade_p_json = _json.dumps(_trade_p)

    # ── ESPN Season Projections sub-tab ────────────────────────────────────
    # Loads the bookmarklet-exported roster snapshot (if present), runs the
    # lineup optimizer + z-score pipeline, and renders the standings HTML.
    # Falls back to an instructional placeholder if the JSON isn't there yet.
    proj_html = _render_season_projections(fdata)

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
        _name_to_team: dict = {}
        _name_to_espn: dict = {}
        for _t in _phase3["teams"]:
            for _h in _t.get("hitters", []):
                key = (_h["name"] or "").strip().lower()
                _name_to_team[key] = _t["team_id"]
                _name_to_espn[key] = _h["espn_id"]
            for _pp in _t.get("pitchers", []):
                key = (_pp["name"] or "").strip().lower()
                _name_to_team[key] = _t["team_id"]
                _name_to_espn[key] = _pp["espn_id"]
        for _rec in _trade_h + _trade_p:
            k = (_rec["name"] or "").strip().lower()
            _rec["team_id"] = _name_to_team.get(k)
            _rec["espn_id"] = _name_to_espn.get(k)
        # Re-serialise with the new team_id / espn_id fields baked in.
        _trade_h_json = _json.dumps(_trade_h)
        _trade_p_json = _json.dumps(_trade_p)
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
      &nbsp;|&nbsp; <strong>$</strong>: avg(OOPSY&nbsp;DC&nbsp;RoS,&nbsp;Bat&nbsp;X&nbsp;RoS) directly from FanGraphs&nbsp;Auction&nbsp;Calculator
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
    </div>
  </div>

  <!-- Hitters table -->
  <div id="fant-h-wrap">
    <div style="padding:4px 20px 8px">
      <input id="fant-h-search" type="text" placeholder="&#128269; Search hitters…"
             oninput="fantSearch('fant-h-tbl', this.value)"
             style="background:#1e1e1e;border:1px solid #444;color:#fff;
                    padding:6px 12px;border-radius:6px;font-size:.85rem;
                    width:240px;outline:none">
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
</div>

<!-- ══ TRADE CALCULATOR ══ -->
<div id="fant-trade-wrap" style="display:none;padding:18px 20px 0">

  <!-- Two-column layout: Sending | Compare | Receiving -->
  <div style="display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap">

    <!-- ── SENDING ── -->
    <div style="flex:1;min-width:260px">
      <div style="color:var(--accent);font-weight:700;font-size:.88rem;margin-bottom:10px;
                  padding:6px 10px;background:rgba(255,80,70,.1);border-radius:6px;
                  border-left:3px solid var(--accent)">
        &#x1F4E4; Sending
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
    <div style="margin-top:10px">
      <button id="phase3-toggle-btn" onclick="phase3ToggleTable()"
              style="background:#1a1a1a;border:1px solid #333;color:#bbb;
                     padding:6px 14px;border-radius:6px;cursor:pointer;
                     font-size:.78rem;font-weight:600">
        &#x25BC; Show full updated standings
      </button>
      <div id="phase3-table-wrap" style="display:none;margin-top:10px"></div>
    </div>
  </div>
</div>

<!-- ══ SEASON PROJECTIONS ══ -->
<div id="fant-proj-wrap" style="display:none">
  {proj_html}
</div>

<script>
/* ── Hitter / Pitcher main toggle ────────────────────────────────── */
function fantSwitch(which) {{
  document.getElementById('fant-h-wrap').style.display     = which==='h'     ? '' : 'none';
  document.getElementById('fant-p-wrap').style.display     = which==='p'     ? '' : 'none';
  document.getElementById('fant-trade-wrap').style.display = which==='trade' ? '' : 'none';
  var pw = document.getElementById('fant-proj-wrap');
  if (pw) pw.style.display = which==='proj' ? '' : 'none';
  ['h','p','trade','proj'].forEach(function(w) {{
    var btn = document.getElementById('fant-'+w+'-btn');
    if (!btn) return;
    var on = (w === which);
    btn.style.borderBottom = on ? '3px solid var(--accent)' : 'none';
    btn.style.color = on ? '#fff' : '';
  }});
}}

/* ── SP / RP filter ──────────────────────────────────────────── */
var _fpRole = 'all';
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
function applyFantColors(tblId) {{
  var tbl = document.getElementById(tblId);
  if (!tbl) return;
  var rows = Array.from(tbl.querySelectorAll('tbody tr:not([style*="display: none"])'));
  if (!rows.length) return;
  var nCols = rows[0].cells.length;
  // Color columns 3+ (skip rank=0, name=1, team=2)
  for (var c = 3; c < nCols; c++) {{
    var vals = [];
    rows.forEach(function(tr) {{
      var v = parseFloat(tr.cells[c] && tr.cells[c].dataset.val);
      if (!isNaN(v)) vals.push(v);
    }});
    if (!vals.length) continue;
    var best = Math.max.apply(null, vals);
    rows.forEach(function(tr) {{
      var cell = tr.cells[c];
      if (!cell) return;
      var v = parseFloat(cell.dataset.val);
      if (isNaN(v)) return;
      if (v === best) {{
        cell.style.color = '#f0c040';
        cell.style.fontWeight = '700';
        return;
      }}
      var better = vals.filter(function(x) {{ return x > v + 0.00001; }}).length;
      var total = vals.length;
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
        r = Math.round(235 + (50  - 235) * s2);
        g = Math.round(235 + (110 - 235) * s2);
        b = Math.round(235 + (255 - 235) * s2);
      }}
      cell.style.color = 'rgb(' + r + ',' + g + ',' + b + ')';
      cell.style.fontWeight = '600';
    }});
  }}
}}
// Apply colors on initial load
applyFantColors('fant-h-tbl');
applyFantColors('fant-p-tbl');
/* ── Trade Calculator ────────────────────────────────────────── */
var TRADE_HITTERS  = {_trade_h_json};
var TRADE_PITCHERS = {_trade_p_json};
/* Phase 3: full league state (per-team rosters + baseline z-scores).
   Null if no ESPN snapshot is present — the calculator falls back to the
   classic projected-stat-diff verdict only in that case. */
var PHASE3_LEAGUE  = {_phase3_json};
var _tradeRoster   = {{ send: [], recv: [], recvTeamId: null }};

function tradeSearch(side, q) {{
  var dd = document.getElementById('trade-' + side + '-dd');
  q = (q || '').toLowerCase().trim();
  if (!q) {{ dd.style.display = 'none'; return; }}
  var added = _tradeRoster.send.concat(_tradeRoster.recv).map(function(p) {{ return p.name; }});
  var pool  = TRADE_HITTERS.concat(TRADE_PITCHERS);
  // Phase 3 filter: send side = user's team only, recv side = selected counterparty.
  // (If PHASE3 isn't loaded the pool stays unfiltered — old behavior.)
  if (PHASE3_LEAGUE) {{
    var userTid = PHASE3_LEAGUE.user_team_id;
    var recvTid = _tradeRoster.recvTeamId;
    pool = pool.filter(function(p) {{
      if (p.team_id == null) return false;        // unrostered → drop
      if (side === 'send') return p.team_id === userTid;
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
      return '<div data-side="' + side + '" data-name="' + p.name.replace(/"/g,"&quot;") + '"'
        + ' onmousedown="tradeAdd(this.dataset.side,this.dataset.name)"'
        + ' style="padding:7px 12px;cursor:pointer;border-bottom:1px solid #252525;font-size:.83rem"'
        + '>'
        + '<span style="font-weight:600">' + p.name + '</span>'
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
  _tradeRender();
}}

function _tradePlayerRow(side, p) {{
  var d = p.dollars;
  var dStr = (d >= 0 ? '$' : '−$') + Math.abs(d).toFixed(1);
  var dCol = d >= 10 ? '#f0c040' : d >= 0 ? '#7ec87e' : '#e05555';
  var roleTag = p.role ? '<span style="opacity:.45;font-size:.68rem;margin-left:3px">' + p.role.toUpperCase() + '</span>' : '';
  return '<div style="display:flex;align-items:center;justify-content:space-between;'
    + 'padding:5px 8px;background:#1a1a1a;border-radius:5px;margin-bottom:3px">'
    + '<div><span style="font-size:.83rem;font-weight:600">' + p.name + '</span>'
    + '<span style="font-size:.73rem;color:var(--muted);margin-left:5px">' + p.team + roleTag + '</span></div>'
    + '<div style="display:flex;align-items:center;gap:7px">'
    + '<span style="color:' + dCol + ';font-weight:700;font-size:.82rem;white-space:nowrap">' + dStr + '</span>'
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
var TRADE_LOWER_BETTER = {{'ERA':true,'WHIP':true}};
// Stat order so hitter cats line up with sidebar order, pitcher cats too
var TRADE_STAT_ORDER = ['R','HR','RBI','SB','OBP','W','SV','HLD','K','ERA','WHIP'];

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

  var arrowHtml, vc;
  if (net > 0.5) {{
    arrowHtml = '<div style="font-size:3rem;color:#4caf50;line-height:1;margin:4px 0">&#x25B6;</div>'
              + '<div style="font-size:.75rem;color:#4caf50;font-weight:800;letter-spacing:.06em">YOU WIN</div>';
    vc = '#4caf50';
  }} else if (net < -0.5) {{
    arrowHtml = '<div style="font-size:3rem;color:#e05555;line-height:1;margin:4px 0;transform:rotate(180deg)">&#x25B6;</div>'
              + '<div style="font-size:.75rem;color:#e05555;font-weight:800;letter-spacing:.06em">YOU LOSE</div>';
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
    // Significance threshold depends on stat type
    var thresh = (k === 'OBP') ? 0.0005 : (TRADE_LOWER_BETTER[k] ? 0.005 : 0.5);
    var isPos = adjDiff >  thresh;
    var isNeg = adjDiff < -thresh;
    var col   = isPos ? '#4caf50' : isNeg ? '#e05555' : '#888';
    var arrow = isPos ? '&#x25B2;' : isNeg ? '&#x25BC;' : '&#x25A0;';
    var dispDiff = _fmtStatDiff(k, rawDiff);
    bHtml += '<div style="display:flex;justify-content:space-between;align-items:center;'
           + 'padding:5px 9px;background:#1a1a1a;border-radius:5px;border-left:3px solid ' + col + '">'
           + '<span style="font-size:.76rem;font-weight:700;color:#ccc">' + k + '</span>'
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
  var wrap = document.getElementById('trade-counter-wrap');
  if (!PHASE3_LEAGUE) {{ if (wrap) wrap.style.display = 'none'; return; }}
  if (!wrap) return;
  var sel = document.getElementById('trade-counter-sel');
  if (!sel) return;
  var others = PHASE3_LEAGUE.teams.filter(function(t) {{
    return t.team_id !== PHASE3_LEAGUE.user_team_id;
  }});
  var html = '<option value="">— pick a team —</option>';
  others.forEach(function(t) {{
    html += '<option value="' + t.team_id + '">' + t.name + '</option>';
  }});
  sel.innerHTML = html;
  wrap.style.display = '';
}}
phase3Init();

/* Counterparty change handler. Wipes the recv side because old players
   would belong to a different team. */
function tradeSetCounter(val) {{
  _tradeRoster.recvTeamId = val ? parseInt(val, 10) : null;
  _tradeRoster.recv = [];
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
function _phase3OptimizeHitters(hitters) {{
  if (!hitters || !hitters.length) return [];
  var SLOTS = PHASE3_LEAGUE.slots;
  var nSlots = SLOTS.length;
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
  for (var r = 0; r < n; r++) {{
    var c = assign[r];
    if (c < 0 || c >= nSlots) continue;
    if (r >= nPlayers) continue;
    if (cost[r][c] >= INELIG / 2) continue;
    starters.push(hitters[r]);
  }}
  return starters;
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

/* Build a deep-copied league with the trade applied. The two affected
   teams have their hitter / pitcher lists rewritten (subtract sent
   players, add received players), then their lineups are re-optimized
   and stats re-aggregated. Untouched teams keep their existing stats. */
function _phase3SimulateTrade() {{
  var league = JSON.parse(JSON.stringify(PHASE3_LEAGUE.teams));
  var userTid = PHASE3_LEAGUE.user_team_id;
  var oppTid  = _tradeRoster.recvTeamId;
  if (oppTid == null) return null;

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
    var rmIds = team.team_id === userTid ? sendIds : recvIds;
    var addList = team.team_id === userTid ? recvPlayers : sendPlayers;
    team.hitters  = team.hitters .filter(function(p) {{ return !rmIds[p.espn_id]; }});
    team.pitchers = team.pitchers.filter(function(p) {{ return !rmIds[p.espn_id]; }});
    addList.forEach(function(p) {{
      // Decide bucket: if it has elig list it's a hitter, else pitcher
      // (every hitter record carries elig; pitchers don't)
      if (p.elig && p.elig.length) team.hitters.push(p);
      else team.pitchers.push(p);
    }});
    // Re-optimize lineup + re-aggregate
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

/* Compute the LAP-optimized lineup dollar value + starter list for one team.
   Used for the diagnostic display so the user can SEE why z changed. */
function _phase3LineupSummary(team) {{
  var starters = _phase3OptimizeHitters(team.hitters || []);
  var dollar = 0;
  var names = [];
  starters.forEach(function(s) {{
    dollar += (s.dollars || 0);
    names.push((s.name || '?') + ' ($' + (s.dollars || 0).toFixed(1) + ')');
  }});
  return {{ dollar: dollar, names: names, count: starters.length }};
}}

/* Render the compact 2-team delta + (collapsed) full standings table */
function _phase3RenderImpact() {{
  var newLeague = _phase3SimulateTrade();
  var wrap = document.getElementById('phase3-wrap');
  if (!newLeague || !wrap) {{ if (wrap) wrap.style.display = 'none'; return; }}
  wrap.style.display = '';
  var n = PHASE3_LEAGUE.teams.length;
  var userTid = PHASE3_LEAGUE.user_team_id;
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

  // Build the optional collapsible full table only when it's currently visible
  var tw = document.getElementById('phase3-table-wrap');
  if (tw && tw.style.display !== 'none') _phase3RenderTable(newLeague);
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
  var html = '<table style="width:100%;border-collapse:collapse;font-size:.78rem">'
           + '<thead><tr style="border-bottom:1px solid #333">'
           + '<th style="text-align:left;padding:6px 8px;color:var(--muted)">#</th>'
           + '<th style="text-align:left;padding:6px 8px;color:var(--muted)">Team</th>'
           + '<th style="text-align:right;padding:6px 8px;color:var(--muted)">Z Total</th>'
           + '<th style="text-align:right;padding:6px 8px;color:var(--muted)">\u0394 Z</th>'
           + '<th style="text-align:right;padding:6px 8px;color:var(--muted)">\u0394 Rank</th>'
           + '</tr></thead><tbody>';
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
    var isUser = t.team_id === PHASE3_LEAGUE.user_team_id;
    var isOpp  = t.team_id === _tradeRoster.recvTeamId;
    var nameAccent = isUser ? '#4caf50' : isOpp ? '#e05555' : '#ddd';
    var bg = (isUser || isOpp) ? '#1a1a1a' : 'transparent';
    html += '<tr style="background:' + bg + ';border-bottom:1px solid #222">'
          + '<td style="padding:5px 8px;color:' + rkC + ';font-weight:700">#' + t.rank_total + '</td>'
          + '<td style="padding:5px 8px;color:' + nameAccent + ';font-weight:600">' + t.name + '</td>'
          + '<td style="padding:5px 8px;text-align:right;color:' + rkC + ';font-weight:700">'
          +   t.z_total.toFixed(2) + '</td>'
          + '<td style="padding:5px 8px;text-align:right;color:' + zCol + '">' + zStr + '</td>'
          + '<td style="padding:5px 8px;text-align:right;color:' + rCol + '">' + rStr + '</td>'
          + '</tr>';
  }});
  html += '</tbody></table>';
  tw.innerHTML = html;
}}
</script>
"""
    return inner

if __name__ == "__main__":
    main()