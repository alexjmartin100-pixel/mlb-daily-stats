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
from data_fetch import *

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

    # ── Step 1: FanGraphs batting stats via JSON API (bypasses pybaseball) ───
    # pybaseball scrapes FG's HTML leaderboard and breaks when FG changes column
    # counts (e.g. "324 columns passed, passed data had 320 columns").  The JSON
    # API is stable and returns clean dicts — no HTML parsing needed.
    print("  [LB] FanGraphs batting stats (JSON API)…")
    qual_pa = 50  # fallback threshold
    try:
        fg_rows = fg_api({
            "pos": "all", "stats": "bat", "lg": "all", "qual": "1",
            "season": year, "season1": year,
            "month": "0", "team": "0",
            "pageitems": "2000", "pagenum": "1", "ind": "0",
            "type": "8",
        }, "batting leaderboard")
        if not fg_rows:
            raise ValueError("FG API returned no rows")
        # JSON API rows already include xMLBAMID — no Chadwick register needed
        max_g = max(((lambda v: (int(float(v)) if v is not None else 0))(r.get("G")) for r in fg_rows), default=1)
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

        for row in fg_rows:
            try:
                mlbam = int(float(row.get("xMLBAMID") or 0))
            except (ValueError, TypeError):
                continue
            if mlbam == 0:
                continue
            pa  = _int(row.get("PA", 0))
            sb  = _int(row.get("SB", 0))
            cs  = _int(row.get("CS", 0))
            players[mlbam] = {
                "id":      mlbam,
                "name":    str(row.get("PlayerName") or row.get("Name") or "").strip(),
                "team":    str(row.get("TeamNameAbb") or row.get("TeamName") or row.get("Team") or "").strip(),
                "g":       _int(row.get("G", 0)),
                "pa":      pa,
                "ab":      _int(row.get("AB", 0)),
                "qualified": pa >= qual_pa,
                "r":       _int(row.get("R",   0)),
                "hr":      _int(row.get("HR",  0)),
                "rbi":     _int(row.get("RBI", 0)),
                "sb":      sb,
                "sba":     sb + cs,
                "avg":     _flt(row.get("AVG"), 3),
                "obp":     _flt(row.get("OBP"), 3),
                "slg":     _flt(row.get("SLG"), 3),
                "ops":     _flt(row.get("OPS"), 3),
                "woba":    _flt(row.get("wOBA"), 3),
                "k_pct":   _pct(row.get("K%")),
                "bb_pct":  _pct(row.get("BB%")),
                "so":      _int(row.get("SO", 0)),
                "pull_pct":   _pct(row.get("Pull%")),
                "center_pct": _pct(row.get("Cent%")),
                "oppo_pct":   _pct(row.get("Oppo%")),
                "gb_pct":     _pct(row.get("GB%")),
                "ld_pct":     _pct(row.get("LD%")),
                "fb_pct":     _pct(row.get("FB%")),
                "pu_pct":     _pct(row.get("IFFB%")),
                "xwoba": None, "xba": None, "xslg": None,
                "chase_pct": None, "whiff_pct": None,
                "hard_hit_pct": None, "barrel_pct": None, "barrels": None,
                "sweet_spot_pct": None, "avg_ev": None, "max_ev": None,
                "launch_angle_avg": None,
                "bat_speed": None, "squared_up_pct": None,
                "sprint_speed": None,
                "bats": None, "throws": None,
                "height": None, "weight": None,
                "age": None, "pos": None,
                "war":       _flt(row.get("WAR"), 1),
            }
        print(f"  [LB] FG JSON API: {len(players)} hitters, qual ≥{qual_pa} PA")
    except Exception as e:
        print(f"  [LB] FanGraphs JSON API failed: {e}")

    # ── Step 1b: MLB Stats API fallback (if FanGraphs failed) ────────────────
    if not players:
        print("  [LB] FanGraphs returned no data — trying MLB Stats API fallback…")
        try:
            _mlb_url = (
                f"https://statsapi.mlb.com/api/v1/stats"
                f"?stats=season&group=hitting&season={year}"
                f"&sportId=1&limit=900&offset=0"
                f"&fields=stats,splits,stat,gamesPlayed,plateAppearances,atBats,"
                f"runs,homeRuns,rbi,stolenBases,caughtStealing,strikeOuts,"
                f"baseOnBalls,avg,obp,slg,ops,hits,doubles,triples,"
                f"player,id,fullName,currentTeam,abbreviation"
            )
            _mlb_r = requests.get(_mlb_url, headers=hdrs, timeout=30)
            _mlb_r.raise_for_status()
            _mlb_j = _mlb_r.json()
            _splits = []
            for sg in _mlb_j.get("stats", []):
                _splits.extend(sg.get("splits", []))
            # Compute qual PA threshold from max games played
            _max_g = max((s.get("stat", {}).get("gamesPlayed", 0) for s in _splits), default=1)
            qual_pa = max(5, round(_max_g * 3.1))
            for sp in _splits:
                st = sp.get("stat", {})
                pid = sp.get("player", {}).get("id")
                if not pid:
                    continue
                pa = int(st.get("plateAppearances", 0) or 0)
                sb = int(st.get("stolenBases", 0) or 0)
                cs = int(st.get("caughtStealing", 0) or 0)
                def _f(v, p=3):
                    try: return round(float(v), p)
                    except (ValueError, TypeError): return None
                players[pid] = {
                    "id":       pid,
                    "name":     sp.get("player", {}).get("fullName", f"Player #{pid}"),
                    "team":     sp.get("currentTeam", {}).get("abbreviation", ""),
                    "g":        int(st.get("gamesPlayed", 0) or 0),
                    "pa":       pa,
                    "ab":       int(st.get("atBats", 0) or 0),
                    "qualified": pa >= qual_pa,
                    "r":        int(st.get("runs", 0) or 0),
                    "hr":       int(st.get("homeRuns", 0) or 0),
                    "rbi":      int(st.get("rbi", 0) or 0),
                    "sb":       sb,
                    "sba":      sb + cs,
                    "avg":      _f(st.get("avg"), 3),
                    "obp":      _f(st.get("obp"), 3),
                    "slg":      _f(st.get("slg"), 3),
                    "ops":      _f(st.get("ops"), 3),
                    "woba": None, "k_pct": None, "bb_pct": None, "so": int(st.get("strikeOuts", 0) or 0),
                    "pull_pct": None, "center_pct": None, "oppo_pct": None,
                    "gb_pct": None, "ld_pct": None, "fb_pct": None, "pu_pct": None,
                    "xwoba": None, "xba": None, "xslg": None,
                    "chase_pct": None, "whiff_pct": None,
                    "hard_hit_pct": None, "barrel_pct": None, "barrels": None,
                    "sweet_spot_pct": None, "avg_ev": None, "max_ev": None,
                    "launch_angle_avg": None,
                    "bat_speed": None, "squared_up_pct": None,
                    "sprint_speed": None,
                    "bats": None, "throws": None,
                    "height": None, "weight": None,
                    "age": None, "pos": None,
                    "war": None,
                }
            print(f"  [LB] MLB Stats API fallback: {len(players)} hitters, qual ≥{qual_pa} PA")
        except Exception as e2:
            print(f"  [LB] MLB Stats API fallback also failed: {e2}")

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

    # ── Step 3: Savant xwOBA / xBA / xSLG / Chase% / Whiff% / Launch Angle ─
    print("  [LB] Savant xwOBA/xBA/xSLG/Chase/Whiff/LA…")
    try:
        r3 = None
        for _attempt in range(4):
            try:
                r3 = requests.get(
                    "https://baseballsavant.mlb.com/leaderboard/custom",
                    params={"year": year, "type": "batter", "filter": "",
                            "sort": "4", "sortDir": "desc", "min": "1",
                            "selections": ("xwoba,xba,xslg,"
                                           "estimated_ba_using_speedangle,"
                                           "estimated_slg_using_speedangle,"
                                           "launch_angle_avg,"
                                           "oz_swing_percent,whiff_percent"),
                            "csv": "true"},
                    headers=hdrs, timeout=30)
                r3.raise_for_status()
                break
            except Exception as _retry_err:
                if _attempt < 3:
                    print(f"  [LB] Savant step3 attempt {_attempt+1} failed ({_retry_err}), retrying in 5s…")
                    time.sleep(5)
                else:
                    raise
        r3.raise_for_status()
        sv3 = pd.read_csv(StringIO(r3.text))
        mid_col3 = next((c for c in ["player_id", "batter"] if c in sv3.columns), None)
        # Log columns for debugging
        print(f"  [LB] Savant step3 columns: {list(sv3.columns)}")
        sv3_map = {
            "xwoba":                               ("xwoba",            3),
            "estimated_ba_using_speedangle":       ("xba",              3),
            "estimated_slg_using_speedangle":      ("xslg",             3),
            "xba":                                 ("xba",              3),
            "xslg":                                ("xslg",             3),
            "launch_angle_avg":                    ("launch_angle_avg", 1),
            "oz_swing_percent":                    ("chase_pct",        1),
            "whiff_percent":                       ("whiff_pct",        1),
        }
        matched3 = 0
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
            for sv_col, (p_key, prec) in sv3_map.items():
                try:
                    if sv_col in sv3.columns and pd.notna(row.get(sv_col)):
                        p[p_key] = round(float(row[sv_col]), prec)
                except (ValueError, TypeError):
                    pass
            matched3 += 1
        print(f"  [LB] ✓ xwOBA/xBA/xSLG/Chase/Whiff/LA {len(sv3)} rows, {matched3} matched")
    except Exception as e:
        print(f"  [LB] Savant xwOBA/xBA/xSLG/Chase/Whiff/LA failed: {e}")

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
            sq_col = next((c for c in ["squared_up_per_swing","squared_up_swing_rate","squared_up_percent",
                                        "squared_up","squared_up_pct"] if c in bt.columns), None)
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
                if sq_col:
                    sv = row.get(sq_col)
                    try:
                        if pd.notna(sv):
                            val = round(float(sv), 1)
                            # Savant returns as fraction (0-1) or percent; normalize
                            players[mid]["squared_up_pct"] = val if val > 1 else round(val * 100, 1)
                    except (ValueError, TypeError):
                        pass
            print(f"  [LB] ✓ bat speed ({bs_url.split('/')[-1]}): {len(bt)} rows, {matched} matched, sq_col={sq_col}")
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

    # ── Step 6: MLB Stats API bio (bats, throws, height, weight, age, pos) ──
    print("  [LB] MLB API bio data…")
    try:
        import math
        all_ids = list(players.keys())
        batch_size = 150
        bio_hits = 0
        for i in range(0, len(all_ids), batch_size):
            chunk = all_ids[i:i+batch_size]
            id_str = ",".join(str(x) for x in chunk)
            bio_url = (
                "https://statsapi.mlb.com/api/v1/people"
                f"?personIds={id_str}"
                "&fields=people,id,birthDate,currentAge,height,weight,"
                "primaryPosition,abbreviation,batSide,code,pitchHand"
            )
            try:
                br = requests.get(bio_url, headers=hdrs, timeout=20)
                br.raise_for_status()
                for person in br.json().get("people", []):
                    mid = int(person.get("id", 0))
                    if mid not in players:
                        continue
                    p = players[mid]
                    p["age"]    = person.get("currentAge")
                    p["height"] = person.get("height")   # e.g. "6' 2""
                    p["weight"] = person.get("weight")
                    p["bats"]   = (person.get("batSide")   or {}).get("code")
                    p["throws"] = (person.get("pitchHand") or {}).get("code")
                    p["pos"]    = (person.get("primaryPosition") or {}).get("abbreviation")
                    bio_hits += 1
            except Exception as e2:
                print(f"  [LB] bio chunk {i//batch_size} failed: {e2}")
        print(f"  [LB] ✓ bio: {bio_hits} players")
    except Exception as e:
        print(f"  [LB] MLB API bio failed: {e}")

    # ── Step 7: Savant percentile rankings (pre-computed by Baseball Savant) ──
    print("  [LB] Savant percentile rankings…")
    _savant_pctile_map = {}
    try:
        rp = requests.get(
            "https://baseballsavant.mlb.com/leaderboard/percentile-rankings",
            params={"type": "batter", "year": year, "csv": "true"},
            headers=hdrs, timeout=30)
        rp.raise_for_status()
        pct_df = pd.read_csv(StringIO(rp.text))
        # Map Savant column names → our internal stat keys
        _savant_col_map = {
            "xwoba": "xwoba", "xba": "xba", "xslg": "xslg",
            "exit_velocity": "avg_ev", "max_ev": "max_ev",
            "brl_percent": "barrel_pct", "hard_hit_percent": "hard_hit_pct",
            "k_percent": "k_pct", "bb_percent": "bb_pct",
            "whiff_percent": "whiff_pct", "chase_percent": "chase_pct",
            "bat_speed": "bat_speed", "squared_up_rate": "squared_up_pct",
            "sprint_speed": "sprint_speed",
        }
        pid_col = "player_id"
        for _, row in pct_df.iterrows():
            try:
                mid = int(row[pid_col])
            except (ValueError, TypeError):
                continue
            pmap = {}
            for sv_col, p_key in _savant_col_map.items():
                try:
                    v = row.get(sv_col)
                    if pd.notna(v):
                        pmap[p_key] = int(round(float(v)))
                except (ValueError, TypeError):
                    pass
            _savant_pctile_map[mid] = pmap
        print(f"  [LB] ✓ Savant percentiles: {len(_savant_pctile_map)} players")
    except Exception as e:
        print(f"  [LB] Savant percentile rankings failed: {e}")

    # Attach Savant percentiles to players
    for mid, p in players.items():
        p["_savant_pct"] = _savant_pctile_map.get(mid, {})

    out = sorted(players.values(), key=lambda x: (x.get("hr") or 0), reverse=True)
    q = sum(1 for p in out if p["qualified"])
    print(f"  [LB] Done: {len(out)} total players, {q} qualified (≥{qual_pa} PA)")
    return out



def compute_hitter_percentiles(players: list) -> list:
    """
    Use pre-fetched Baseball Savant percentile rankings (1-100 scale).
    Falls back to self-computed percentiles only for stats Savant doesn't cover.
    """
    # Fallback stat keys (only used if Savant data missing for a player)
    lower_better = {"k_pct", "chase_pct", "whiff_pct"}
    stat_keys = [
        "xwoba", "xba", "xslg", "avg_ev", "max_ev",
        "barrel_pct", "hard_hit_pct", "sweet_spot_pct",
        "bat_speed", "squared_up_pct",
        "chase_pct", "whiff_pct", "k_pct", "bb_pct",
        "sprint_speed", "launch_angle_avg",
    ]

    # Build fallback sorted arrays for self-computation
    stat_vals = {}
    for k in stat_keys:
        vals = sorted(p[k] for p in players if p.get(k) is not None)
        stat_vals[k] = vals

    def _pct_rank(vals_sorted, v, invert):
        if not vals_sorted:
            return None
        lo, hi = 0, len(vals_sorted)
        while lo < hi:
            mid = (lo + hi) // 2
            if vals_sorted[mid] < v:
                lo = mid + 1
            else:
                hi = mid
        rank = lo
        n = len(vals_sorted)
        pct = round(rank / n * 100)
        if pct < 1:
            pct = 1
        if pct > 100:
            pct = 100
        return (101 - pct) if invert else pct

    for p in players:
        savant_pct = p.get("_savant_pct", {})
        pct = {}
        for k in stat_keys:
            # Prefer Savant's pre-computed percentile
            if k in savant_pct:
                pct[k] = savant_pct[k]
            else:
                # Fallback: self-computed
                v = p.get(k)
                if v is None:
                    pct[k] = None
                else:
                    pct[k] = _pct_rank(stat_vals[k], v, k in lower_better)
        p["pct"] = pct

    return players


# Team abbreviation → MLB team ID mapping for logos
