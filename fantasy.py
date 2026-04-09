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

__all__ = ['fetch_fg_projections', '_avg_proj_sets', 'fetch_fg_auction_dollar_values', '_fetch_fg_auction_full', '_avg_fg_auction', '_fant_stat', '_z_to_dollars', 'compute_fantasy_dollar_values', '_team_badge_py', '_fmt_dollar', '_dollar_color', '_z_color', '_merge_players', 'render_fantasy_tab']


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
</div>

<script>
/* ── Hitter / Pitcher main toggle ────────────────────────────────── */
function fantSwitch(which) {{
  document.getElementById('fant-h-wrap').style.display     = which==='h'     ? '' : 'none';
  document.getElementById('fant-p-wrap').style.display     = which==='p'     ? '' : 'none';
  document.getElementById('fant-trade-wrap').style.display = which==='trade' ? '' : 'none';
  ['h','p','trade'].forEach(function(w) {{
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
var _tradeRoster   = {{ send: [], recv: [] }};

function tradeSearch(side, q) {{
  var dd = document.getElementById('trade-' + side + '-dd');
  q = (q || '').toLowerCase().trim();
  if (!q) {{ dd.style.display = 'none'; return; }}
  var added = _tradeRoster.send.concat(_tradeRoster.recv).map(function(p) {{ return p.name; }});
  var pool  = TRADE_HITTERS.concat(TRADE_PITCHERS);
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
function _sumCats(players) {{
  var out = {{}};
  players.forEach(function(p) {{
    if (!p.cats) return;
    Object.keys(p.cats).forEach(function(k) {{
      out[k] = (out[k] || 0) + (parseFloat(p.cats[k]) || 0);
    }});
  }});
  return out;
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

  var sendCats = _sumCats(send);
  var recvCats = _sumCats(recv);
  var seen = {{}}, allKeys = [];
  Object.keys(sendCats).concat(Object.keys(recvCats)).forEach(function(k) {{
    if (!seen[k]) {{ seen[k]=1; allKeys.push(k); }}
  }});
  var lowerBetter = {{'ERA':true,'WHIP':true}};
  if (!allKeys.length) {{ breakEl.innerHTML=''; return; }}
  var bHtml = '<div style="display:flex;flex-direction:column;gap:4px;margin-top:8px">';
  allKeys.forEach(function(k) {{
    var sv = sendCats[k]||0, rv = recvCats[k]||0;
    var rawDiff = rv - sv;
    var adjDiff = lowerBetter[k] ? -rawDiff : rawDiff;
    var isPos = adjDiff >  0.005;
    var isNeg = adjDiff < -0.005;
    var col   = isPos ? '#4caf50' : isNeg ? '#e05555' : '#888';
    var arrow = isPos ? '&#x25B2;' : isNeg ? '&#x25BC;' : '&#x25A0;';
    var dispDiff = (rawDiff >= 0 ? '+' : '') + rawDiff.toFixed(2).replace(/\.00$/,'');
    bHtml += '<div style="display:flex;justify-content:space-between;align-items:center;'
           + 'padding:5px 9px;background:#1a1a1a;border-radius:5px;border-left:3px solid ' + col + '">'
           + '<span style="font-size:.76rem;font-weight:700;color:#ccc">' + k + '</span>'
           + '<span style="font-size:.8rem;font-weight:700;color:' + col + '">' + arrow + ' ' + dispDiff + '</span>'
           + '</div>';
  }});
  bHtml += '</div>';
  breakEl.innerHTML = bHtml;
}}
</script>
"""
    return inner

if __name__ == "__main__":
    main()
