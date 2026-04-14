import subprocess, sys, os, json, unicodedata, time
from datetime import date, timedelta, datetime
import pybaseball          # type: ignore
import pandas as pd
import numpy as np
import requests
import statsapi            # type: ignore  (MLB-StatsAPI)

import io


# ── Import all modules ──────────────────────────────────────────────────────
from config import *
from utils import *
from fangraphs import *
from data_fetch import *
from batting_leaderboard import *
from pitching_leaderboard import *
from player_cards import *
from html_template import *
from fantasy import *

# ─────────────────────────────────────────────────────────────────────────────

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

    # Patch p_info: fill in FG IDs 
    for mlbam_k, fgid in fg_mlbam_to_fgid.items():
        if mlbam_k in p_info and p_info[mlbam_k].get("fg_id") in (None, -1):
            p_info[mlbam_k]["fg_id"] = fgid
    for nm, fgid in fg_name_to_fgid.items():
        for mlbam_k, info in p_info.items():
            if norm_name(info.get("name", "")) == nm and info.get("fg_id") in (None, -1):
                info["fg_id"] = fgid

    print("\n[ 5b/6 ] FanGraphs game Stuff+/Loc+")
    game_stuff = fetch_fg_game_stuff(yesterday, year, all_pitchers, p_info, fg_name_to_fgid)

    print("\n  Attaching FG data to pitchers…")
    attach_fg_data(all_pitchers, p_info, game_stuff, fg_velo_dict, savant_velo)

    # ── Team Alex subsets ──────────────────────────────────────────────────
    ta_hitters   = [h for h in hitters
                    if ta_norm(h["name"]) in TEAM_ALEX_NAMES]
    ta_starters  = [p for p in all_pitchers
                    if p.get("ip_float", 0) >= 3
                    and ta_norm(p["name"]) in TEAM_ALEX_NAMES]
    ta_relievers = [p for p in all_pitchers
                    if p.get("ip_float", 0) < 3
                    and ta_norm(p["name"]) in TEAM_ALEX_NAMES]
    print(f"  Team Alex: {len(ta_hitters)} hitter(s), "
          f"{len(ta_starters)} SP, {len(ta_relievers)} RP")

    print("\n[ 6/6 ] Season leaderboards")
    lb_data       = fetch_season_batting_leaderboard(year)
    lb_pitch_data = fetch_season_pitching_leaderboard(year)
    print("\n[ 6b/6 ] Fantasy dollar values")
    fantasy_data = compute_fantasy_dollar_values(lb_data, lb_pitch_data, year)

    # ── Fetch historical leaderboards (2024/2025) with disk caching ────────
    # Historical data is static, so we cache it to avoid re-fetching each run.
    _hist_lb = {}
    _cache_dir = os.path.dirname(os.path.abspath(__file__))
    for _hy in [2024, 2025]:
        _h_cache = os.path.join(_cache_dir, f"lb_cache_{_hy}.json")
        if os.path.exists(_h_cache):
            try:
                with open(_h_cache, "r", encoding="utf-8") as _hf:
                    _cached = json.load(_hf)
                _hist_lb[_hy] = _cached
                _n_h = len(_cached.get("hitters", []))
                _n_p = len(_cached.get("pitchers_sp", [])) + len(_cached.get("pitchers_rp", []))
                print(f"  Historical {_hy}: loaded from cache ({_n_h} hitters, {_n_p} pitchers)")
                continue
            except Exception as _he:
                print(f"  Historical {_hy}: cache read failed ({_he}), refetching…")
        try:
            print(f"  Historical {_hy}: fetching leaderboards…")
            _h_lb = fetch_season_batting_leaderboard(_hy)
            _h_lp = fetch_season_pitching_leaderboard(_hy)
            _h_lb = compute_hitter_percentiles(_h_lb)
            _h_lp = compute_pitcher_percentiles(_h_lp)
            _payload = {
                "hitters": _h_lb,
                "pitchers_sp": _h_lp.get("starters", []),
                "pitchers_rp": _h_lp.get("relievers", []),
            }
            with open(_h_cache, "w", encoding="utf-8") as _hf:
                json.dump(_payload, _hf)
            _hist_lb[_hy] = _payload
            print(f"  Historical {_hy}: cached ({len(_h_lb)} hitters, "
                  f"{len(_payload['pitchers_sp'])+len(_payload['pitchers_rp'])} pitchers)")
        except Exception as _he:
            print(f"  Historical {_hy}: fetch failed ({_he}), skipping")

    n_games = int(df["game_pk"].nunique())

    # ── Build player name → position lookup from ESPN roster snapshot ────────
    pos_lookup = {}
    _ESPN_ELIG_MAP = {0:'C',1:'1B',2:'2B',3:'3B',4:'SS',5:'OF'}
    try:
        import json as _jmod
        _espn_path = None
        for _fn in ["espn_rosters.json"]:
            _cand = os.path.join(os.path.dirname(os.path.abspath(__file__)), _fn)
            if os.path.exists(_cand):
                _espn_path = _cand
                break
        if _espn_path:
            with open(_espn_path, "r", encoding="utf-8") as _ef:
                _espn_raw = _jmod.load(_ef)
            _raw = _espn_raw.get("raw", _espn_raw)
            _PITCHER_SLOTS = {13, 14, 15}
            for _t in _raw.get("teams", []):
                for _entry in _t.get("roster", {}).get("entries", []):
                    _ppe = _entry.get("playerPoolEntry", {}) or {}
                    _pl = _ppe.get("player", {}) or {}
                    _elig = _pl.get("eligibleSlots", []) or []
                    # Skip pitchers (slots 13=P, 14=SP, 15=RP)
                    if set(_elig) & _PITCHER_SLOTS:
                        continue
                    _nm = (_pl.get("fullName") or "").strip()
                    if _nm:
                        _pos_parts = [_ESPN_ELIG_MAP[e] for e in _elig if e in _ESPN_ELIG_MAP]
                        # Show DH only when player has no real field position
                        if not _pos_parts and 11 in _elig:
                            _pos_parts = ["DH"]
                        if _pos_parts:
                            _pos_str = "/".join(_pos_parts)
                            pos_lookup[_nm] = _pos_str
                            # Also store ASCII-normalized key so FG/Savant
                            # names with diacritics still match
                            _nm_ascii = unicodedata.normalize("NFKD", _nm)
                            _nm_ascii = "".join(
                                c for c in _nm_ascii
                                if not unicodedata.combining(c)
                            )
                            if _nm_ascii != _nm:
                                pos_lookup[_nm_ascii] = _pos_str
            print(f"  Position lookup: {len(pos_lookup)} hitters from ESPN snapshot")
    except Exception as _e:
        print(f"  Position lookup failed: {_e}")

    # ── Fetch all active 40-man roster players (for roster editor search) ────
    all_mlb_players = []
    try:
        print("  Fetching MLB 40-man rosters for roster editor…")
        _roster_url = (
            "https://statsapi.mlb.com/api/v1/teams"
            "?sportId=1&season=2026&activeStatus=ACTIVE"
            "&fields=teams,id,abbreviation"
        )
        _teams_resp = requests.get(_roster_url, timeout=20)
        _teams_resp.raise_for_status()
        _team_abbrs = {}
        for _t in _teams_resp.json().get("teams", []):
            _team_abbrs[_t["id"]] = _t.get("abbreviation", "")

        _all_ids_seen = set()
        # Fetch both 40Man (active 40-man incl. 10/15-day IL) AND 60day
        # (60-day IL players who are removed from the 40-man). Without the
        # 60day pull, players who were injured before the season started
        # never show up in the roster-editor search.
        _roster_type_counts = {}
        for _roster_type in ("40Man", "60day"):
            _rt_count = 0
            for _tid, _abbr in _team_abbrs.items():
                try:
                    _r40 = requests.get(
                        f"https://statsapi.mlb.com/api/v1/teams/{_tid}/roster"
                        f"?rosterType={_roster_type}&season=2026"
                        f"&fields=roster,person,id,fullName,primaryPosition,abbreviation",
                        timeout=15)
                    _r40.raise_for_status()
                    for _entry in _r40.json().get("roster", []):
                        _person = _entry.get("person", {})
                        _pid = _person.get("id")
                        if not _pid or _pid in _all_ids_seen:
                            continue
                        _all_ids_seen.add(_pid)
                        _ppos = (_person.get("primaryPosition") or {}).get("abbreviation", "")
                        _is_pitcher = _ppos in ("P", "SP", "RP", "TWP")
                        all_mlb_players.append({
                            "id": _pid,
                            "name": (_person.get("fullName") or "").strip(),
                            "team": _abbr,
                            "pos": _ppos,
                            "is_pitcher": _is_pitcher,
                            "il": _roster_type == "60day",
                        })
                        _rt_count += 1
                except Exception:
                    pass
            _roster_type_counts[_roster_type] = _rt_count
        print(f"  40-man rosters: {_roster_type_counts.get('40Man', 0)} active + "
              f"{_roster_type_counts.get('60day', 0)} on 60-day IL "
              f"= {len(all_mlb_players)} total from {len(_team_abbrs)} teams")
    except Exception as _e:
        print(f"  40-man roster fetch failed: {_e}")

    print("\nRendering HTML…")
    html = render_html(date_display, ts, n_games, hitters, all_pitchers,
                       ta_hitters, ta_starters, ta_relievers,
                       lb_data=lb_data, lb_pitch_data=lb_pitch_data,
                       pos_lookup=pos_lookup, all_mlb_players=all_mlb_players)
    lb_data = compute_hitter_percentiles(lb_data)
    lb_pitch_data = compute_pitcher_percentiles(lb_pitch_data)
    html = inject_fantasy_tab(html, fantasy_data, pos_lookup=pos_lookup)
    html = inject_player_cards_tab(html, lb_data, fantasy_data,
                                    lb_pitch_data=lb_pitch_data,
                                    historical_lb=_hist_lb)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "mlb_daily_stats.html")
    with open(out_path, "w", encoding="utf-8") as fout:
        fout.write(html)
    print(f"\n✅ Dashboard → {out_path}  ({len(html):,} chars)")


# ═══════════════════════════════════════════════════════════════════════════════
#  Fantasy Dollar-Value Engine
#  League: 10-team H2H, $260/team, 6x6
#  Hitting:  R / HR / RBI / SB / K / OBP
#  Pitching: W / ERA / WHIP / K / SV / HLD
# ═══════════════════════════════════════════════════════════════════════════════

_FANT = {
    "n_teams":  10,
    "budget":   260,
    # Roster: C(1)+1B(1)+2B(1)+3B(1)+SS(1)+CI(1)+MI(1)+OF(3)+UTIL(1)=11 active hitters
    #         P(1)+SP(3)+RP(2)=6 active pitchers  |  6 bench (4H + 2P estimated split)
    "h_slots":  15,   # 11 active + 4 bench hitters per team
    "p_slots":  8,    # 6 active + 2 bench pitchers per team
    "h_split":  0.67, # fraction of total budget allocated to hitters
    "h_cats":     ["R", "HR", "RBI", "SB", "K", "OBP"],
    "p_cats":     ["W", "ERA", "WHIP", "K", "SV", "HLD"],
    # Separate neg_cats for hitters vs pitchers:
    #   hitters:  K (strikeouts) is a negative category — fewer Ks wins the category
    #   pitchers: ERA and WHIP are negative — lower is better
    "h_neg_cats": {"K"},
    "p_neg_cats": {"ERA", "WHIP"},
    "neg_cats":   {"ERA", "WHIP"},  # legacy key kept for any direct references
    "min_ip":   35,   # minimum IP for a pitcher to qualify for the pool
}




if __name__ == "__main__":
    main()