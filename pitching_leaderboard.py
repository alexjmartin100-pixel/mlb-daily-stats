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

        # Supplement Chadwick with xMLBAMID from FanGraphs DataFrame directly
        # (handles new/recently-promoted pitchers not yet in the Chadwick register)
        if "xMLBAMID" in fg.columns:
            _pre = len(fg_to_mlbam)
            for _, cr in fg.iterrows():
                try:
                    fgk = int(float(cr.get("playerid") or cr.get("IDfg") or 0))
                    mid = int(float(cr.get("xMLBAMID") or 0))
                    if fgk > 0 and mid > 0:
                        fg_to_mlbam.setdefault(fgk, mid)
                except (ValueError, TypeError):
                    pass
            print(f"  [PLB] fg_to_mlbam: {_pre} (Chadwick) → {len(fg_to_mlbam)} (after xMLBAMID supplement)")
        else:
            # xMLBAMID not in pybaseball DataFrame — call FG API directly (type=8 = Stuff+ leaderboard)
            try:
                xmap_rows = fg_api({
                    "pos": "all", "stats": "pit", "lg": "all", "qual": "0",
                    "season": year, "season1": year,
                    "month": "0", "team": "0",
                    "pageitems": "2000", "pagenum": "1", "ind": "0",
                    "type": "8",
                }, "pitcher xMLBAMID map")
                _pre = len(fg_to_mlbam)
                for r2 in (xmap_rows or []):
                    try:
                        fgk = int(float(r2.get("playerid") or 0))
                        mid = int(float(r2.get("xMLBAMID") or 0))
                        if fgk > 0 and mid > 0:
                            fg_to_mlbam.setdefault(fgk, mid)
                    except (ValueError, TypeError):
                        pass
                print(f"  [PLB] fg_to_mlbam: {_pre} (Chadwick) → {len(fg_to_mlbam)} (after FG API xMLBAMID)")
            except Exception as e3:
                print(f"  [PLB] FG API xMLBAMID supplement failed: {e3}")

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
            gm_li = _flt(row.get("gmLI") or row.get("gmLi"), 2)

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
                "gm_li":       gm_li,
                "era":         _flt(row.get("ERA"), 2),
                "whip":        _flt(row.get("WHIP"), 2),
                "siera":       _flt(row.get("SIERA") or row.get("Sierra"), 2),
                "stuff_plus":  stuff_plus,
                "loc_plus":    loc_plus,
                "k":           _int(row.get("SO") or row.get("K") or 0),
                "k_pct":       _pct(row.get("K%")),
                "bb_pct":      _pct(row.get("BB%")),
                "k_bb_pct":    round(_pct(row.get("K%")) - _pct(row.get("BB%")), 1) if _pct(row.get("K%")) is not None and _pct(row.get("BB%")) is not None else None,
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
                "war":   _flt(row.get("WAR"), 1),
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
