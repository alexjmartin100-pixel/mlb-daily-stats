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

__all__ = ['_SAVANT_PT_PREFIX', '_parse_fg_id', 'attach_fg_data', 'build_hitter_stats', 'build_pitcher_stats', 'fetch_mlb_sb', 'fetch_pitcher_box_data', 'fetch_savant_season_velo', 'fetch_statcast', 'get_player_info', 'identify_starters']


def fetch_mlb_sb(date_str: str) -> dict:
    """Returns {(mlbam_id, game_pk): [sb, cs, r, rbi]} from official MLB box scores.

    NOTE: We use the raw game_boxscore endpoint, NOT statsapi.boxscore_data().
    The boxscore_data() helper returns only a curated summary of batting stats
    and strips caughtStealing entirely — so SBA was always equaling SB before
    this fix. game_boxscore returns the full Stats API response which includes
    caughtStealing.
    """
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
                raw = statsapi.get("game_boxscore", {"gamePk": int(gpk)})
            except Exception:
                continue
            for side in ("home", "away"):
                for pk, pd_ in raw.get("teams", {}).get(side, {}).get("players", {}).items():
                    try:
                        mid = int(pd_["person"]["id"])
                    except Exception:
                        continue
                    bat = pd_.get("stats", {}).get("batting", {})
                    if not bat:
                        continue
                    sb  = int(bat.get("stolenBases", 0))
                    cs  = int(bat.get("caughtStealing", 0))
                    r   = int(bat.get("runs", 0))
                    rbi = int(bat.get("rbi", 0))
                    sb_map[(mid, int(gpk))] = [sb, cs, r, rbi]
        total_sb = sum(v[0] for v in sb_map.values())
        total_cs = sum(v[1] for v in sb_map.values())
        print(f"    {total_sb} SB / {total_cs} CS for {len(sb_map)} player(s)")
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

    # ── Authoritative names from MLB Stats API (batch call) ─────────────
    # pybaseball's Chadwick register sometimes drops suffixes (e.g.
    # "Vladimir Guerrero" instead of "Vladimir Guerrero Jr."). The MLB
    # Stats API is authoritative, so we do a single batch call and prefer
    # its fullName for all players. Also picks up any IDs pybaseball missed.
    all_unique = list(set(ids))
    BATCH = 200  # API handles ~200 comma-separated IDs per call
    for start in range(0, len(all_unique), BATCH):
        chunk = all_unique[start : start + BATCH]
        try:
            data = statsapi.get("people",
                                {"personIds": ",".join(str(i) for i in chunk)})
            for p in data.get("people", []):
                mid = int(p.get("id", 0))
                full = (p.get("fullName") or "").strip()
                if not full:
                    continue
                if mid in result:
                    result[mid]["name"] = title_name(full)   # keep fg_id, fix name
                else:
                    result[mid] = {"name": title_name(full), "fg_id": None}
        except Exception:
            pass

    # Legacy single-player fallback for any still-missing IDs
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
        sb_data      = sb_map.get((int(bid), int(gpk)), [0, 0, 0, 0])
        # Detect grand slams: HR where on_1b, on_2b, on_3b all occupied
        last_rows  = gdf.sort_values("pitch_number").groupby("at_bat_number").last()
        hr_rows    = last_rows[last_rows["events"] == "home_run"]
        grand_slam = any(
            pd.notna(row.get("on_1b")) and
            pd.notna(row.get("on_2b")) and
            pd.notna(row.get("on_3b"))
            for _, row in hr_rows.iterrows()
        )
        # Hits and at-bats from plate-appearance outcomes
        _hit_events = {"single", "double", "triple", "home_run"}
        _non_ab_events = {"walk", "intent_walk", "hit_by_pitch",
                          "sac_fly", "sac_bunt", "sac_fly_double_play",
                          "sac_bunt_double_play", "catcher_interf"}
        h_count  = int(evts.isin(_hit_events).sum())
        ab_count = int((~evts.isin(_non_ab_events)).sum())

        rows.append({
            "id":         int(bid),
            "game_pk":    int(gpk),
            "team":       bat_t,
            "opp":        opp_t,
            "h":          h_count,
            "ab":         ab_count,
            "r":          sb_data[2] if len(sb_data) > 2 else 0,
            "hr":         int((evts == "home_run").sum()),
            "rbi":        sb_data[3] if len(sb_data) > 3 else 0,
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
                pt["velo_above"] = (pt["velo"] - svelo) > 1.0   # red: game velo > season avg + 1
                pt["velo_below"] = (svelo - pt["velo"]) > 1.0   # blue: game velo < season avg - 1
            else:
                pt["velo_above"] = False
                pt["velo_below"] = False

# ── Season Batting Leaderboard ─────────────────────────────────────────────