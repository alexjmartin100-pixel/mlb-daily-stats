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

    n_games = int(df["game_pk"].nunique())

    print("\nRendering HTML…")
    html = render_html(date_display, ts, n_games, hitters, all_pitchers,
                       ta_hitters, ta_starters, ta_relievers,
                       lb_data=lb_data, lb_pitch_data=lb_pitch_data)
    lb_data = compute_hitter_percentiles(lb_data)
    lb_pitch_data = compute_pitcher_percentiles(lb_pitch_data)
    html = inject_fantasy_tab(html, fantasy_data)
    html = inject_player_cards_tab(html, lb_data, fantasy_data,
                                    lb_pitch_data=lb_pitch_data)

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
