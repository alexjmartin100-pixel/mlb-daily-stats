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

    # ── Step 1: FanGraphs pitching stats (JSON API) ────────────────────────────
    # Uses the FG JSON API directly — pybaseball's legacy HTML scraper returns 403.
    print("  [PLB] FanGraphs pitching stats (JSON API)…")
    qual_sp_ip = 10.0
    qual_rp_ip = 3.0
    try:
        fg_rows = fg_api({
            "pos": "all", "stats": "pit", "lg": "all", "qual": "1",
            "season": year, "season1": year,
            "month": "0", "team": "0",
            "pageitems": "2000", "pagenum": "1", "ind": "0",
            "type": "8",
        }, "pitching leaderboard")
        if not fg_rows:
            raise ValueError("FG API returned no rows")

        max_g = max((_int(r.get("G")) for r in fg_rows), default=1)
        qual_sp_ip = max(3.0, round(max_g * 1.0, 1))
        qual_rp_ip = max(1.0, round(max_g * 0.5, 1))

        for row in fg_rows:
            try:
                mlbam = int(float(row.get("xMLBAMID") or 0))
            except (ValueError, TypeError):
                continue
            if mlbam == 0:
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

            # Stuff+ / Location+ from FG JSON API
            stuff_plus = None
            loc_plus   = None
            for sc in ["sp_stuff", "Stuff+", "stuff_plus", "StuffPlus"]:
                v = row.get(sc)
                if v is not None:
                    try:
                        stuff_plus = int(round(float(v)))
                    except (ValueError, TypeError):
                        pass
                    break
            for lc in ["sp_location", "Location+", "location_plus", "Loc+"]:
                v = row.get(lc)
                if v is not None:
                    try:
                        loc_plus = int(round(float(v)))
                    except (ValueError, TypeError):
                        pass
                    break

            sv  = _int(row.get("SV", 0))
            bs  = _int(row.get("BS", 0))
            hld = _int(row.get("HLD", 0))
            gm_li = _flt(row.get("gmLI") or row.get("gmLi"), 2)

            # Use TeamNameAbb (clean) instead of Team (contains HTML)
            team_raw = str(row.get("TeamNameAbb") or row.get("TeamName") or row.get("Team") or "").strip()
            import re
            team_clean = re.sub(r'<[^>]+>', '', team_raw).strip()

            fg_chase   = _pct(row.get("O-Swing%"))
            fg_xera    = _flt(row.get("xERA"), 3)
            fg_barrel  = _pct(row.get("Barrel%"))
            fg_hh      = _pct(row.get("HardHit%"))
            fg_ev      = _flt(row.get("EV"), 1)
            fg_fbv     = _flt(row.get("FBv"), 1)
            fg_whiff   = None
            try:
                swstr = float(str(row.get("SwStr%", "")).replace("%",""))
                swing = float(str(row.get("Swing%", "")).replace("%",""))
                if swing > 0:
                    if swstr > 1.5: swstr /= 100
                    if swing > 1.5: swing /= 100
                    fg_whiff = round(swstr / swing * 100, 1)
            except (ValueError, TypeError, ZeroDivisionError):
                pass

            rec = {
                "id":          mlbam,
                "name":        str(row.get("PlayerName") or row.get("Name") or "").strip(),
                "team":        team_clean,
                "g":           g,
                "gs":          gs,
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
                "xera":        fg_xera,
                "xba":         None,
                "chase_pct":   fg_chase,
                "whiff_pct":   fg_whiff,
                "barrel_pct":  fg_barrel,
                "hard_hit_pct":fg_hh,
                "avg_ev":      fg_ev,
                "fb_velo":     fg_fbv,
                "xwoba": None, "woba": None,
                "war":   _flt(row.get("WAR"), 1),
                "age": None, "height": None, "weight": None,
                "bats": None, "throws": None, "pos": None,
                "pitch_arsenal": [],
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
        r2 = None
        for _attempt in range(4):
            try:
                r2 = requests.get(
                    "https://baseballsavant.mlb.com/leaderboard/custom",
                    params={"year": year, "type": "pitcher", "filter": "",
                            "sort": "4", "sortDir": "desc", "min": "1",
                            "selections": "xera,xba,xwoba,woba,oz_swing_percent,whiff_percent,"
                                          "brl_percent,ev95percent,avg_hit_speed",
                            "csv": "true"},
                    headers=hdrs, timeout=30)
                r2.raise_for_status()
                break
            except Exception as _retry_err:
                if _attempt < 3:
                    print(f"  [PLB] Savant step2 attempt {_attempt+1} failed ({_retry_err}), retrying in 5s…")
                    time.sleep(5)
                else:
                    raise
        r2.raise_for_status()
        sv2 = pd.read_csv(StringIO(r2.text))
        mid_col = next((c for c in ["player_id", "pitcher", "mlbam_id", "id"] if c in sv2.columns), None)
        print(f"  [PLB] Savant pitcher CSV: {len(sv2)} rows, id_col={mid_col}, cols={list(sv2.columns[:12])}")
        if mid_col:
            col_map = {
                "xera":              ("xera",         3),
                "xba":               ("xba",          3),
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
            r3b = None
            for _attempt in range(4):
                try:
                    r3b = requests.get(
                        "https://baseballsavant.mlb.com/leaderboard/custom",
                        params={"year": year, "type": "pitcher", "filter": "",
                                "sort": "4", "sortDir": "desc", "min": "1",
                                "selections": "n_ff_formatted,n_si_formatted,ff_avg_speed,si_avg_speed,fc_avg_speed",
                                "csv": "true"},
                        headers=hdrs, timeout=30)
                    r3b.raise_for_status()
                    break
                except Exception as _retry_err:
                    if _attempt < 3:
                        print(f"  [PLB] custom-fb-velo attempt {_attempt+1} failed ({_retry_err}), retrying in 5s…")
                        time.sleep(5)
                    else:
                        raise
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

    # ── Step 4: Pitch arsenal (usage% + avg velo per pitch type) ──────────────
    # Uses Savant custom leaderboard with formatted usage counts + avg speeds
    print("  [PLB] Pitch arsenal (usage% + velo)…")
    try:
        _pt_prefixes = ["ff","si","fc","sl","st","sv","cu","kc","cs",
                        "ch","fs","fo","kn","sc","fa","ep"]
        _pt_code_map = {p: p.upper() for p in _pt_prefixes}
        selections = ",".join(
            [f"n_{p}_formatted" for p in _pt_prefixes] +
            [f"{p}_avg_speed"   for p in _pt_prefixes]
        )
        r4 = None
        for _attempt in range(4):
            try:
                r4 = requests.get(
                    "https://baseballsavant.mlb.com/leaderboard/custom",
                    params={"year": year, "type": "pitcher", "filter": "",
                            "sort": "4", "sortDir": "desc", "min": "1",
                            "selections": selections, "csv": "true"},
                    headers=hdrs, timeout=30)
                r4.raise_for_status()
                break
            except Exception as _retry_err:
                if _attempt < 3:
                    print(f"  [PLB] arsenal attempt {_attempt+1} failed ({_retry_err}), retrying in 5s…")
                    time.sleep(5)
                else:
                    raise
        sv4 = pd.read_csv(StringIO(r4.text))
        mid_col = next((c for c in ["player_id","pitcher","id"] if c in sv4.columns), None)
        matched = 0
        if mid_col:
            for _, row in sv4.iterrows():
                try:
                    mid = int(row[mid_col])
                except (ValueError, TypeError):
                    continue
                if mid not in all_pitchers_d:
                    continue
                # Sum total pitch count for percentages
                total = 0.0
                pt_counts = {}
                for p in _pt_prefixes:
                    c_col = f"n_{p}_formatted"
                    if c_col in sv4.columns and pd.notna(row.get(c_col)):
                        try:
                            c = float(row[c_col])
                            if c > 0:
                                pt_counts[p] = c
                                total += c
                        except (ValueError, TypeError):
                            pass
                if total <= 0:
                    continue
                arsenal = []
                for p, cnt in pt_counts.items():
                    velo = None
                    v_col = f"{p}_avg_speed"
                    if v_col in sv4.columns and pd.notna(row.get(v_col)):
                        try:
                            velo = round(float(row[v_col]), 1)
                        except (ValueError, TypeError):
                            pass
                    arsenal.append({
                        "code":  _pt_code_map[p],
                        "usage": round(cnt / total * 100, 1),
                        "velo":  velo,
                    })
                arsenal.sort(key=lambda x: x["usage"], reverse=True)
                all_pitchers_d[mid]["pitch_arsenal"] = arsenal
                # Fill fb_velo from arsenal if not set
                if all_pitchers_d[mid].get("fb_velo") is None:
                    for ars in arsenal:
                        if ars["code"] in ("FF", "FA") and ars["velo"] is not None:
                            all_pitchers_d[mid]["fb_velo"] = ars["velo"]
                            break
                    else:
                        for ars in arsenal:
                            if ars["code"] in ("SI", "FC") and ars["velo"] is not None:
                                all_pitchers_d[mid]["fb_velo"] = ars["velo"]
                                break
                matched += 1
        print(f"  [PLB] ✓ Pitch arsenal: {matched} pitchers")
    except Exception as e:
        print(f"  [PLB] Pitch arsenal failed: {e}")

    # ── Step 5: MLB Stats API bio (age, height, weight, throws, pos) ──────────
    print("  [PLB] MLB API bio data…")
    try:
        all_ids = list(all_pitchers_d.keys())
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
                    try:
                        mid = int(person.get("id", 0))
                    except (ValueError, TypeError):
                        continue
                    if mid not in all_pitchers_d:
                        continue
                    p = all_pitchers_d[mid]
                    p["age"]    = person.get("currentAge")
                    p["height"] = person.get("height")
                    p["weight"] = person.get("weight")
                    p["bats"]   = (person.get("batSide")   or {}).get("code")
                    p["throws"] = (person.get("pitchHand") or {}).get("code")
                    p["pos"]    = (person.get("primaryPosition") or {}).get("abbreviation")
                    bio_hits += 1
            except Exception as e2:
                print(f"  [PLB] bio chunk {i//batch_size} failed: {e2}")
        print(f"  [PLB] ✓ bio: {bio_hits} pitchers")
    except Exception as e:
        print(f"  [PLB] MLB API bio failed: {e}")

    # ── Step 6: Savant percentile rankings (pre-computed by Baseball Savant) ──
    # Mirrors the hitter path in batting_leaderboard.py — prefers Savant's
    # pre-computed percentiles so player cards match the Savant player page.
    print("  [PLB] Savant percentile rankings (pitcher)…")
    _savant_pctile_map = {}
    try:
        rp_ = requests.get(
            "https://baseballsavant.mlb.com/leaderboard/percentile-rankings",
            params={"type": "pitcher", "year": year, "csv": "true"},
            headers=hdrs, timeout=30)
        rp_.raise_for_status()
        pct_df = pd.read_csv(StringIO(rp_.text))
        print(f"  [PLB] Savant pitcher pct cols: {list(pct_df.columns[:20])}")
        # Savant column → our internal pitcher stat key
        _savant_col_map = {
            "xwoba":            "xwoba",
            "xba":              "xba",
            "xera":              "xera",
            "exit_velocity":    "avg_ev",
            "brl_percent":      "barrel_pct",
            "hard_hit_percent": "hard_hit_pct",
            "k_percent":        "k_pct",
            "bb_percent":       "bb_pct",
            "whiff_percent":    "whiff_pct",
            "chase_percent":    "chase_pct",
            "gb_percent":       "gb_pct",
            "fb_velocity":      "fb_velo",
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
        print(f"  [PLB] ✓ Savant pitcher percentiles: {len(_savant_pctile_map)} players")
    except Exception as e:
        print(f"  [PLB] Savant pitcher percentile rankings failed: {e}")

    # Attach Savant percentiles to pitchers
    for mid, p in all_pitchers_d.items():
        p["_savant_pct"] = _savant_pctile_map.get(mid, {})

    sp_out = sorted(starters_d.values(),  key=lambda x: (x.get("ip_f") or 0), reverse=True)
    rp_out = sorted(relievers_d.values(), key=lambda x: (-(x.get("era") or 99), x.get("ip_f") or 0), reverse=False)
    print(f"  [PLB] Done: {len(sp_out)} SP, {len(rp_out)} RP")
    return {"starters": sp_out, "relievers": rp_out}


def compute_pitcher_percentiles(lb_pitch_data: dict) -> dict:
    """
    Use pre-fetched Baseball Savant percentile rankings (1-100 scale) for
    pitchers, mirroring compute_hitter_percentiles. Falls back to
    self-computed percentiles only for stats Savant doesn't cover.
    """
    if not lb_pitch_data:
        return lb_pitch_data
    all_p = list(lb_pitch_data.get("starters", [])) + list(lb_pitch_data.get("relievers", []))
    if not all_p:
        return lb_pitch_data

    # Lower is better for pitchers (self-computed fallback only; Savant's
    # pre-computed rankings already have the right polarity).
    # NOTE: chase% is higher-better for pitchers.
    lower_better = {"xera", "xba", "xwoba", "woba", "avg_ev",
                    "hard_hit_pct", "barrel_pct", "bb_pct"}
    stat_keys = [
        "xera", "xba", "xwoba", "woba",
        "fb_velo", "avg_ev",
        "chase_pct", "whiff_pct",
        "k_pct", "bb_pct",
        "barrel_pct", "hard_hit_pct", "gb_pct",
    ]

    stat_vals = {}
    for k in stat_keys:
        vals = sorted(p[k] for p in all_p if p.get(k) is not None)
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
        if pct < 1: pct = 1
        if pct > 100: pct = 100
        return (101 - pct) if invert else pct

    for p in all_p:
        savant_pct = p.get("_savant_pct", {}) or {}
        pct = {}
        for k in stat_keys:
            # Prefer Savant's pre-computed percentile (matches the Savant player page)
            if k in savant_pct:
                pct[k] = savant_pct[k]
            else:
                v = p.get(k)
                if v is None:
                    pct[k] = None
                else:
                    pct[k] = _pct_rank(stat_vals[k], v, k in lower_better)
        p["pct"] = pct

    return lb_pitch_data


# ── HTML Template ──────────────────────────────────────────────────────────
