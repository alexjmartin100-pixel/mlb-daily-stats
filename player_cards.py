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

from fantasy import render_fantasy_tab

__all__ = ['_TEAM_ID_MAP', 'inject_fantasy_tab', 'inject_player_cards_tab', 'render_player_cards_tab']


_TEAM_ID_MAP = {
    "ARI":108+1, "ATL":144, "BAL":110, "BOS":111, "CHC":112,
    "CWS":145,   "CIN":113, "CLE":114, "COL":115, "DET":116,
    "HOU":117,   "KC":118,  "LAA":108, "LAD":119, "MIA":146,
    "MIL":158,   "MIN":142, "NYM":121, "NYY":147, "OAK":133,
    "PHI":143,   "PIT":134, "SD":135,  "SF":137,  "SEA":136,
    "STL":138,   "TB":139,  "TEX":140, "TOR":141, "WSH":120,
    "AZ":109,    "KC ":118,
}
# Correct ARI mapping
_TEAM_ID_MAP["ARI"] = 109


def render_player_cards_tab(lb_data: list, dollar_map: dict = None,
                             lb_pitch_data: dict = None, p_dollar_map: dict = None,
                             historical_lb: dict = None) -> str:
    """
    Build the Player Cards tab panel HTML + JS.
    lb_data is the hitter list (with compute_hitter_percentiles applied).
    lb_pitch_data is {"starters":[...], "relievers":[...]} (with compute_pitcher_percentiles applied).
    dollar_map / p_dollar_map are mlbam_id → RoS dollar value.
    """
    if dollar_map is None:
        dollar_map = {}
    if p_dollar_map is None:
        p_dollar_map = {}
    import json

    # ── Build compact player index for autocomplete ──────────────────────
    player_index = []
    player_data  = {}
    for p in lb_data:
        mid  = p.get("id")
        name = p.get("name", "")
        team = p.get("team", "")
        if not mid or not name:
            continue
        player_index.append({"id": mid, "n": name, "t": team, "k": "h"})
        # Compact stat payload (only what the card needs)
        player_data[str(mid)] = {
            "type":  "h",
            "name":  name,
            "team":  team,
            "pos":   p.get("pos"),
            "bats":  p.get("bats"),
            "throws":p.get("throws"),
            "age":   p.get("age"),
            "ht":    p.get("height"),
            "wt":    p.get("weight"),
            "qual":  p.get("qualified", False),
            "dv":    dollar_map.get(mid),
            "ev":    p.get("earned"),   # earned (season-to-date) $ value
            "war":   p.get("war"),
            # standard stats
            "g":     p.get("g"),
            "pa":    p.get("pa"),
            "ab":    p.get("ab"),
            "r":     p.get("r"),
            "hr":    p.get("hr"),
            "rbi":   p.get("rbi"),
            "sb":    p.get("sb"),
            "avg":   p.get("avg"),
            "obp":   p.get("obp"),
            "ops":   p.get("ops"),
            # statcast
            "xwoba": p.get("xwoba"),
            "xba":   p.get("xba"),
            "xslg":  p.get("xslg"),
            "avg_ev":p.get("avg_ev"),
            "max_ev":p.get("max_ev"),
            "brl":   p.get("barrel_pct"),
            "hh":    p.get("hard_hit_pct"),
            "la":    p.get("launch_angle_avg"),
            "ss":    p.get("sweet_spot_pct"),
            "bs":    p.get("bat_speed"),
            "sq":    p.get("squared_up_pct"),
            "ch":    p.get("chase_pct"),
            "wh":    p.get("whiff_pct"),
            "kp":    p.get("k_pct"),
            "bbp":   p.get("bb_pct"),
            "spd":   p.get("sprint_speed"),
            # batted ball
            "pull":  p.get("pull_pct"),
            "cent":  p.get("center_pct"),
            "oppo":  p.get("oppo_pct"),
            "gb":    p.get("gb_pct"),
            "ld":    p.get("ld_pct"),
            "fb":    p.get("fb_pct"),
            "pu":    p.get("pu_pct"),
            # percentiles
            "pct": p.get("pct", {}),
        }

    # ── Add pitchers (starters + relievers combined) ─────────────────────
    # Dedup rule: if a player already exists in player_data as a hitter,
    # they're a position player who crossed the pitching leaderboard
    # minimum by throwing mop-up innings — treat them as a hitter only.
    # The previous behavior was to overwrite the hitter payload, which
    # is why e.g. searching "Carlos Cortes" showed the name twice and
    # both clicks rendered pitching stats.
    #
    # Exception: Shohei Ohtani (660271). He's genuinely both, so attach
    # his pitcher payload to his hitter record as `pitcher_alt`. The
    # card renderer surfaces a "Show pitching" toggle in that case.
    _OHTANI_ID = 660271

    def _build_pitcher_record(p, dv=None):
        return {
            "type":    "p",
            "name":    p.get("name", ""),
            "team":    p.get("team", ""),
            "pos":     p.get("pos") or ("SP" if p.get("is_sp") else "RP"),
            "bats":    p.get("bats"),
            "throws":  p.get("throws"),
            "age":     p.get("age"),
            "ht":      p.get("height"),
            "wt":      p.get("weight"),
            "qual":    p.get("qualified", False),
            "dv":      dv,
            "ev":      p.get("earned"),   # earned (season-to-date) $ value
            "war":     p.get("war"),
            "is_sp":   p.get("is_sp", False),
            "g":       p.get("g"),
            "gs":      p.get("gs"),
            "ip":      p.get("ip_f"),
            "w":       p.get("w"),
            "era":     p.get("era"),
            "whip":    p.get("whip"),
            "k":       p.get("k"),
            "siera":   p.get("siera"),
            "kbb":     p.get("k_bb_pct"),
            "sv":      p.get("sv"),
            "svo":     p.get("sv_opp"),
            "hld":     p.get("hld"),
            "xera":    p.get("xera"),
            "xba":     p.get("xba"),
            "fbv":     p.get("fb_velo"),
            "aev":     p.get("avg_ev"),
            "woba":    p.get("woba"),
            "xwoba":   p.get("xwoba"),
            "ch":      p.get("chase_pct"),
            "wh":      p.get("whiff_pct"),
            "kp":      p.get("k_pct"),
            "bbp":     p.get("bb_pct"),
            "brl":     p.get("barrel_pct"),
            "hh":      p.get("hard_hit_pct"),
            "gb":      p.get("gb_pct"),
            "stf":     p.get("stuff_plus"),
            "loc":     p.get("loc_plus"),
            "ars":     p.get("pitch_arsenal", []),
            "pct":     p.get("pct", {}),
        }

    _all_pitchers = []
    if lb_pitch_data:
        _all_pitchers = (list(lb_pitch_data.get("starters", [])) +
                         list(lb_pitch_data.get("relievers", [])))
    for p in _all_pitchers:
        mid  = p.get("id")
        name = p.get("name", "")
        team = p.get("team", "")
        if not mid or not name:
            continue
        pitcher_rec = _build_pitcher_record(p, dv=p_dollar_map.get(mid))
        if str(mid) in player_data:
            # Already a hitter. Only Ohtani retains the pitcher record
            # (attached as `pitcher_alt`); everyone else is dropped.
            if mid == _OHTANI_ID:
                player_data[str(mid)]["pitcher_alt"] = pitcher_rec
            continue
        player_index.append({"id": mid, "n": name, "t": team, "k": "p"})
        player_data[str(mid)] = pitcher_rec

    # ── Build historical year data ────────────────────────────────────────
    def _compact_hitter(p):
        """Build compact stat dict for a hitter (reused across years)."""
        return {
            "type":"h","name":p.get("name",""),"team":p.get("team",""),
            "pos":p.get("pos"),"bats":p.get("bats"),"throws":p.get("throws"),
            "age":p.get("age"),"ht":p.get("height"),"wt":p.get("weight"),
            "qual":p.get("qualified",False),"war":p.get("war"),
            "g":p.get("g"),"pa":p.get("pa"),"ab":p.get("ab"),
            "r":p.get("r"),"hr":p.get("hr"),"rbi":p.get("rbi"),"sb":p.get("sb"),
            "avg":p.get("avg"),"obp":p.get("obp"),"ops":p.get("ops"),
            "xwoba":p.get("xwoba"),"xba":p.get("xba"),"xslg":p.get("xslg"),
            "avg_ev":p.get("avg_ev"),"max_ev":p.get("max_ev"),
            "brl":p.get("barrel_pct"),"hh":p.get("hard_hit_pct"),
            "la":p.get("launch_angle_avg"),"ss":p.get("sweet_spot_pct"),
            "bs":p.get("bat_speed"),"sq":p.get("squared_up_pct"),
            "ch":p.get("chase_pct"),"wh":p.get("whiff_pct"),
            "kp":p.get("k_pct"),"bbp":p.get("bb_pct"),"spd":p.get("sprint_speed"),
            "pull":p.get("pull_pct"),"cent":p.get("center_pct"),"oppo":p.get("oppo_pct"),
            "gb":p.get("gb_pct"),"ld":p.get("ld_pct"),"fb":p.get("fb_pct"),"pu":p.get("pu_pct"),
            "pct":p.get("pct",{}),
        }

    def _compact_pitcher(p):
        """Build compact stat dict for a pitcher (reused across years)."""
        return {
            "type":"p","name":p.get("name",""),"team":p.get("team",""),
            "pos":p.get("pos") or ("SP" if p.get("is_sp") else "RP"),
            "bats":p.get("bats"),"throws":p.get("throws"),
            "age":p.get("age"),"ht":p.get("height"),"wt":p.get("weight"),
            "qual":p.get("qualified",False),"war":p.get("war"),
            "is_sp":p.get("is_sp",False),
            "g":p.get("g"),"gs":p.get("gs"),"ip":p.get("ip_f"),
            "w":p.get("w"),"era":p.get("era"),"whip":p.get("whip"),
            "k":p.get("k"),"siera":p.get("siera"),"kbb":p.get("k_bb_pct"),
            "sv":p.get("sv"),"svo":p.get("sv_opp"),"hld":p.get("hld"),
            "xera":p.get("xera"),"xba":p.get("xba"),
            "fbv":p.get("fb_velo"),"aev":p.get("avg_ev"),
            "woba":p.get("woba"),"xwoba":p.get("xwoba"),
            "ch":p.get("chase_pct"),"wh":p.get("whiff_pct"),
            "kp":p.get("k_pct"),"bbp":p.get("bb_pct"),
            "brl":p.get("barrel_pct"),"hh":p.get("hard_hit_pct"),"gb":p.get("gb_pct"),
            "stf":p.get("stuff_plus"),"loc":p.get("loc_plus"),
            "ars":p.get("pitch_arsenal",[]),
            "pct":p.get("pct",{}),
        }

    hist_data = {}  # year → {player_id: compact_stats}
    if historical_lb:
        for yr, payload in historical_lb.items():
            yr_data = {}
            for p in payload.get("hitters", []):
                mid = p.get("id")
                if mid and p.get("name"):
                    yr_data[str(mid)] = _compact_hitter(p)
            # Same dedup rule as the current-year loop. Position players
            # who pitched are kept as hitters only. Ohtani gets his
            # pitcher payload attached as pitcher_alt.
            for p in payload.get("pitchers_sp", []) + payload.get("pitchers_rp", []):
                mid = p.get("id")
                if not mid or not p.get("name"):
                    continue
                if str(mid) in yr_data:
                    if mid == _OHTANI_ID:
                        yr_data[str(mid)]["pitcher_alt"] = _compact_pitcher(p)
                    continue
                yr_data[str(mid)] = _compact_pitcher(p)
            hist_data[str(yr)] = yr_data

    # Build per-player available years list
    avail_years = {}  # player_id → [2024, 2025, 2026] (sorted)
    for pid in player_data:
        yrs = [2026]
        for yr_str, yr_d in hist_data.items():
            if pid in yr_d:
                yrs.append(int(yr_str))
        avail_years[pid] = sorted(yrs)

    # ── Add historical-only players (played a prior year but not 2026) ──
    # So they can be searched and their card falls back to the most recent
    # year we have data for (e.g. injured players, prospects yet to debut).
    _hist_only_seen = set()
    _hist_years_desc = sorted(hist_data.keys(), key=lambda y: -int(y))
    for yr_str in _hist_years_desc:
        for pid, p_compact in hist_data[yr_str].items():
            if pid in player_data or pid in _hist_only_seen:
                continue
            _hist_only_seen.add(pid)
            try:
                _pid_int = int(pid)
            except (TypeError, ValueError):
                continue
            # Use the most-recent-year snapshot for the search index
            # (name/team may have changed year-to-year).
            player_index.append({
                "id": _pid_int,
                "n":  p_compact.get("name", ""),
                "t":  p_compact.get("team", ""),
                "k":  p_compact.get("type", "h"),
            })
            avail_years[pid] = sorted(
                int(y) for y in hist_data.keys() if pid in hist_data[y]
            )

    hist_json = json.dumps(hist_data, separators=(',', ':'))
    avail_json = json.dumps(avail_years, separators=(',', ':'))

    # ── Compute league leaders (among qualified hitters) ──────────────────
    _higher_better = ["r","hr","rbi","sb","avg","obp","ops",
                      "xwoba","xba","xslg","avg_ev","max_ev",
                      "barrel_pct","hard_hit_pct","sweet_spot_pct",
                      "bat_speed","squared_up_pct","bb_pct","sprint_speed"]
    _lower_better = ["chase_pct","whiff_pct","k_pct"]
    _leaders = {}
    for sk in _higher_better + _lower_better:
        best_val = None
        best_ids = []
        want_low = sk in _lower_better
        for p in lb_data:
            if not p.get("qualified", False):
                continue
            v = p.get(sk)
            if v is None:
                continue
            if best_val is None or (want_low and v < best_val) or (not want_low and v > best_val):
                best_val = v
                best_ids = [p.get("id")]
            elif v == best_val:
                best_ids.append(p.get("id"))
        for bid in best_ids:
            _leaders.setdefault(str(bid), []).append(sk)

    # Pitcher leaders — computed SEPARATELY within SP and RP subgroups, so
    # the best starter and the best reliever in each category both get gold.
    # NOTE: chase% is higher-better for pitchers.
    # SV/HLD are excluded from the SP pool — starting pitchers should never
    # get gold on SV or HLD regardless of whether they happen to lead SPs.
    _p_higher_sp = ["w","k","whiff_pct","k_pct","gb_pct","fb_velo",
                    "chase_pct","k_bb_pct","stuff_plus","loc_plus"]
    _p_higher_rp = _p_higher_sp + ["sv","hld"]
    _p_lower  = ["era","whip","xera","xba","xwoba","woba","siera",
                 "bb_pct","barrel_pct","hard_hit_pct","avg_ev"]
    _sp_list = [p for p in _all_pitchers if p.get("is_sp", False)]
    _rp_list = [p for p in _all_pitchers if not p.get("is_sp", False)]
    for _pool, _higher in ((_sp_list, _p_higher_sp), (_rp_list, _p_higher_rp)):
        for sk in _higher + _p_lower:
            best_val = None
            best_ids = []
            want_low = sk in _p_lower
            for p in _pool:
                if not p.get("qualified", False):
                    continue
                v = p.get(sk)
                if v is None:
                    continue
                if best_val is None or (want_low and v < best_val) or (not want_low and v > best_val):
                    best_val = v
                    best_ids = [p.get("id")]
                elif v == best_val:
                    best_ids.append(p.get("id"))
            for bid in best_ids:
                _leaders.setdefault(str(bid), []).append(sk)

    leaders_json = json.dumps(_leaders, separators=(',', ':'))

    idx_json  = json.dumps(player_index, separators=(',', ':'))
    data_json = json.dumps(player_data,  separators=(',', ':'))

    # ── Pre-fetch every player's headshot as a base64 data URL ────────────
    # iOS Safari has proven unreliable at fetching cross-origin images at
    # save time (either via img tags with crossorigin or fetch() with
    # mode:'cors'), which kept the saved PNG coming back with a blank
    # headshot circle. Doing the fetch in Python at build time eliminates
    # every runtime CORS / network variable — the data URL is embedded
    # straight into the HTML.
    #
    # Uses a small image size (w_120 low-quality JPEG, ~3-5KB per player)
    # to keep the HTML payload reasonable. Parallelised with 20 workers
    # so the whole fetch completes in 15-30 seconds even for ~700 players.
    import base64, concurrent.futures as _cf
    import requests as _requests_pc
    _PC_HEADSHOT_SIZE = 'w_180,q_auto:low'
    def _fetch_headshot_b64(pid):
        try:
            url = ('https://img.mlbstatic.com/mlb-photos/image/upload/'
                   'd_people:generic:headshot:67:current.png/'
                   f'{_PC_HEADSHOT_SIZE}/v1/people/{pid}/headshot/67/current')
            r = _requests_pc.get(url, timeout=6)
            if not r.ok or len(r.content) < 200:
                return pid, None
            ctype = r.headers.get('content-type', 'image/jpeg')
            b64 = base64.b64encode(r.content).decode('ascii')
            return pid, f'data:{ctype};base64,{b64}'
        except Exception:
            return pid, None

    _photo_ids = []
    for entry in player_index:
        pid = entry.get('id')
        if pid and pid not in _photo_ids:
            _photo_ids.append(pid)
    _photos = {}
    _pc_ok = 0
    _pc_fail = 0
    try:
        with _cf.ThreadPoolExecutor(max_workers=20) as _ex:
            for pid, du in _ex.map(_fetch_headshot_b64, _photo_ids):
                if du:
                    _photos[str(pid)] = du
                    _pc_ok += 1
                else:
                    _pc_fail += 1
        print(f"  [headshots] pre-fetched {_pc_ok} / {len(_photo_ids)} "
              f"({_pc_fail} failed)")
    except Exception as _e:
        print(f"  [headshots] pre-fetch failed: {_e}")

    photos_json = json.dumps(_photos, separators=(',', ':'))

    # ── Externalize the headshot blob ─────────────────────────────────────
    # Previously baked into the inline JS as `var _pcPhotos = {...}`, which
    # bloated mlb_daily_stats.html to ~22 MB and forced the browser to parse
    # ~13 MB of base64 in a single chunk during tab hydration — multi-second
    # freezes on phones. Now we write it to pc_photos.js and load it via
    # <script src="pc_photos.js" defer> alongside the main HTML (see
    # inject_player_cards_tab below), so the dashboard becomes interactive
    # while photos load in the background. window._pcPhotos has the same
    # shape the inline var used to.
    _photos_js_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "pc_photos.js"
    )
    try:
        with open(_photos_js_path, "w", encoding="utf-8") as _fph:
            _fph.write("window._pcPhotos=" + photos_json + ";\n")
        print(f"  [headshots] wrote pc_photos.js ({len(photos_json):,} chars)")
    except Exception as _e:
        print(f"  [headshots] failed to write pc_photos.js: {_e}")

    inner = f"""
<style>
.pc-dd-item{{padding:9px 12px;cursor:pointer;font-size:.88rem;
  border-bottom:1px solid #2a2a2a;color:#ddd;transition:background .12s}}
.pc-dd-item:hover{{background:#2a2a2a}}
</style>
<!-- ═══════════════ PLAYER CARDS TAB ═══════════════════════════════════ -->
<div id="playercards-panel" class="tab-panel">
<div style="max-width:680px;margin:0 auto">

  <!-- Search -->
  <!-- iOS Safari quirks:
       — font-size < 16px triggers focus-zoom (which can hide the dropdown
         behind the keyboard), so we use 16px exactly.
       — autocorrect/autocapitalize on mobile can insert characters that the
         handler sees as spaces/capitals, throwing off the 2-char threshold.
       — oninput is the only handler — the previous duplicate onkeyup was
         doubling work per keystroke. The `window._pcSearch &&` guard
         protects against typing during the brief window between the panel
         being hydrated (input becomes visible) and the IIFE finishing
         (the function gets defined). Without the guard, those keystrokes
         throw a silent ReferenceError and look like "search is broken." -->
  <input id="pc-search" type="search" placeholder="Search hitter or pitcher by name or team…"
    autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"
    inputmode="search"
    oninput="window._pcSearch && _pcSearch(this.value)"
    style="width:100%;box-sizing:border-box;padding:10px 14px;margin-bottom:4px;
           background:#1a1a1a;border:1px solid #333;border-radius:8px;
           color:#eee;font-size:16px;outline:none"/>
  <div id="pc-dropdown"
    style="display:none;background:#1e1e1e;border:1px solid #333;border-radius:8px;
           max-height:260px;overflow-y:auto;margin-bottom:12px;
           box-shadow:0 4px 16px rgba(0,0,0,.5)"></div>

  <!-- Year selector — lives OUTSIDE #pc-card so it remains interactive
       after the card's content is replaced with a static <img> by the
       auto-capture flow. Hidden until a player is selected. -->
  <div id="pc-year-wrap" style="display:none;margin:0 0 10px;text-align:right">
    <label style="font-size:.72rem;color:var(--muted);font-weight:700;
                  text-transform:uppercase;letter-spacing:.04em;margin-right:6px">
      Season:
    </label>
    <select id="pc-year-sel"
            style="background:#1a1a1a;color:#eee;border:1px solid #444;
                   border-radius:6px;padding:3px 8px;font-size:.78rem;
                   font-weight:700;cursor:pointer;outline:none"></select>
    <!-- Dual-role toggle (Ohtani's hitter ↔ pitcher). Lives outside
         #pc-card for the same reason as the year selector — the
         auto-capture step that rasterizes #pc-card would kill any
         click handler attached inside the card. Hidden by default. -->
    <button id="pc-view-toggle" type="button" style="display:none;
            background:#1a2a3a;color:#8ab4f8;border:1px solid #4a7bb8;
            border-radius:6px;padding:3px 10px;font-size:.78rem;
            font-weight:700;cursor:pointer;outline:none;margin-left:8px;
            line-height:1.4;vertical-align:middle"></button>
  </div>

  <!-- Card area. The flip wrapper sets up 3D perspective; #pc-card itself
       is what rotates on view toggles and stays the rasterization target. -->
  <div id="pc-card-flip" style="perspective:1400px">
    <div id="pc-card"
         style="transition:transform .55s cubic-bezier(.4,0,.2,1);
                transform-style:preserve-3d;backface-visibility:hidden"></div>
  </div>

  <!-- Hint + status area. The card itself becomes a long-pressable <img>
       automatically a moment after a player is selected. No save button
       needed — iOS Safari's native "Save Image" appears on long-press. -->
  <div id="pc-save-wrap" style="display:none;margin-top:10px;text-align:center">
    <div id="pc-save-hint" style="font-size:.78rem;color:var(--muted);
         font-style:italic">&#x1F4F7; Long-press the card to save it to your photos</div>
    <div id="pc-save-status" style="font-size:.8rem;color:#eee;
         margin-top:8px;padding:6px 10px;border-radius:6px;text-align:left;
         white-space:pre-wrap;display:none;background:#1a1a1a;
         border:1px solid #333;max-width:420px;margin-left:auto;
         margin-right:auto;font-family:ui-monospace,Menlo,monospace;
         line-height:1.4"></div>
  </div>

</div>
</div>

<script>
(function(){{
  var _pcIdx  = {idx_json};
  var _pcData = {data_json};
  var _pcLeaders = {leaders_json};
  var _pcHist = {hist_json};
  var _pcAvailYears = {avail_json};
  // Server-side pre-fetched headshot data URLs, keyed by player id.
  // iOS Safari couldn't reliably fetch cross-origin headshots at save
  // time, so we bake them in at build time. As of the perf fix, the
  // ~13 MB photo blob lives in pc_photos.js (loaded with <script defer>
  // from inject_player_cards_tab) instead of inline here, so the HTML
  // stays small. We read window._pcPhotos lazily at card-render time;
  // if the deferred script hasn't loaded yet, the MLB CDN URL fallback
  // (a few lines below in _pcShow) keeps the card looking right.
  var _pcCurrentYear = 2026;
  var _pcCurrentId = null;

  var _TEAM_IDS = {{
    ARI:109,ATL:144,BAL:110,BOS:111,CHC:112,CWS:145,CHW:145,CIN:113,CLE:114,
    COL:115,DET:116,HOU:117,KC:118,KCR:118,LAA:108,LAD:119,MIA:146,MIL:158,
    MIN:142,NYM:121,NYY:147,OAK:133,ATH:133,PHI:143,PIT:134,SD:135,SDP:135,SF:137,SFG:137,
    SEA:136,STL:138,TB:139,TBR:139,TEX:140,TOR:141,WSH:120,WSN:120,AZ:109
  }};

  var _PITCH_NAMES = {{
    FF:'4-Seam',SI:'Sinker',FC:'Cutter',SL:'Slider',ST:'Sweeper',SV:'Slurve',
    CH:'Change',FS:'Splitter',FO:'Fork',CU:'Curve',KC:'K-Curve',CS:'Slow Curve',
    KN:'Knuckle',EP:'Eephus',FA:'Fastball',SC:'Screw'
  }};
  var _PITCH_COLORS = {{
    FF:'#e74c3c',SI:'#c0392b',FA:'#e74c3c',FC:'#e67e22',
    SL:'#f1c40f',ST:'#f39c12',SV:'#d35400',
    CH:'#2ecc71',FS:'#27ae60',FO:'#16a085',
    CU:'#3498db',KC:'#2980b9',CS:'#1abc9c',
    KN:'#9b59b6',EP:'#8e44ad',SC:'#95a5a6'
  }};

  // Stat direction for prior-year delta arrows. Keys are the leaderKey
  // strings used on each slider row. Any key NOT in these maps is treated
  // as higher-is-better. Note some stats flip between hitters and pitchers
  // (K%, Whiff%, Chase%, BB% all behave differently by player type).
  var _LOWER_BETTER_H = {{
    chase_pct:1, whiff_pct:1, k_pct:1
  }};
  var _LOWER_BETTER_P = {{
    xera:1, xba:1, woba:1, xwoba:1, avg_ev:1,
    bb_pct:1, barrel_pct:1, hard_hit_pct:1
  }};

  // ── Search ────────────────────────────────────────────────────────────────────
  // Defensive: the function is called from both oninput and onkeyup so it can
  // be invoked rapidly. Guard every step so no stray input state wrecks it.
  window._pcSearch = function(q) {{
    try {{
      var dd = document.getElementById('pc-dropdown');
      if (!dd) return;
      q = (q||'').trim().toLowerCase();
      if (q.length < 2) {{ dd.style.display='none'; return; }}
      var matches = _pcIdx.filter(function(p) {{
        return p.n.toLowerCase().indexOf(q) !== -1
            || (p.t||'').toLowerCase().indexOf(q) !== -1;
      }}).slice(0,12);
      if (!matches.length) {{ dd.style.display='none'; return; }}
      // Use onclick (which iOS synthesizes reliably) + ontouchend with an
      // explicit preventDefault fallback so the dropdown item fires even
      // when Safari's fastclick heuristics get weird.
      dd.innerHTML = matches.map(function(p) {{
        return '<div class="pc-dd-item" data-pid="' + p.id + '" '
          + 'onclick="_pcShow(Number(this.dataset.pid))" '
          + 'ontouchend="event.preventDefault();_pcShow(Number(this.dataset.pid))" '
          + 'style="-webkit-tap-highlight-color:rgba(255,255,255,.1)">'
          + '<span style="font-weight:700">' + p.n + '</span>'
          + '<span style="color:#888;font-size:.78rem;margin-left:8px">' + (p.t||'') + '</span>'
          + '</div>';
      }}).join('');
      dd.style.display = 'block';
    }} catch (e) {{
      console.error('[pc-search] error:', e);
    }}
  }};

  // Clear dropdown when typing clears the box
  document.addEventListener('click', function(e) {{
    var el = e.target;
    var inSearch = el.id==='pc-search' || (el.closest && el.closest('#pc-dropdown'));
    if (!inSearch) {{
      var dd2 = document.getElementById('pc-dropdown');
      if (dd2) dd2.style.display='none';
    }}
  }});

  // Tracks which view a dual-role player is showing: 'base' (default
  // — hitter for Ohtani, pitcher for everyone else) or 'alt' (secondary
  // view, currently only Ohtani's pitcher card).
  var _pcCurrentView = 'base';

  // Flip the card on view-toggle clicks. We rotate #pc-card to 90°
  // (edge-on), swap the rendered content at that mid-point (invisible),
  // snap to -90° with the new content in place, then ease back to 0°.
  // That avoids any mirrored back-face frame. Auto-capture is skipped
  // while _pcFlipping is true and re-fired at the end.
  var _pcFlipping = false;
  window._pcFlipTo = function(id, year, view) {{
    var card = document.getElementById('pc-card');
    if (!card) {{ _pcShow(id, year, view); return; }}
    _pcFlipping = true;
    card.style.transition = 'transform .28s cubic-bezier(.4,0,.2,1)';
    card.style.transform = 'rotateY(90deg)';
    setTimeout(function(){{
      _pcShow(id, year, view);
      card.style.transition = 'none';
      card.style.transform = 'rotateY(-90deg)';
      // eslint-disable-next-line no-unused-expressions
      card.offsetWidth;  // force reflow so the no-transition snap applies
      card.style.transition = 'transform .28s cubic-bezier(.4,0,.2,1)';
      card.style.transform = 'rotateY(0deg)';
      setTimeout(function(){{
        _pcFlipping = false;
        var d = (year === 2026)
          ? _pcData[String(id)]
          : ((_pcHist[String(year)] || {{}})[String(id)] || null);
        if (d && view === 'alt' && d.pitcher_alt) d = d.pitcher_alt;
        _pcAutoCaptureCard((d && d.name) || 'player-card', id);
      }}, 290);
    }}, 290);
  }};

  window._pcShow = function(id, year, view) {{
    document.getElementById('pc-dropdown').style.display='none';
    var availYrs = _pcAvailYears[String(id)] || [2026];
    if (!year) year = availYrs[availYrs.length - 1];
    // Switching PLAYER resets view to base so we always start on the
    // default card, not whatever view was last selected for someone else.
    if (id !== _pcCurrentId) _pcCurrentView = 'base';
    if (view) _pcCurrentView = view;
    _pcCurrentId = id;
    _pcCurrentYear = year;
    var d = (year === 2026)
      ? _pcData[String(id)]
      : ((_pcHist[String(year)] || {{}})[String(id)] || null);
    if (!d) {{
      year = availYrs[availYrs.length - 1];
      _pcCurrentYear = year;
      d = (year === 2026)
        ? _pcData[String(id)]
        : ((_pcHist[String(year)] || {{}})[String(id)] || null);
    }}
    if (!d) return;

    // Dual-role swap: if alt view requested AND this year has alt data,
    // swap to it. Base record stays in _pcData; we just render the alt.
    var _hasAlt = !!(d && d.pitcher_alt);
    if (_pcCurrentView === 'alt' && _hasAlt) {{
      d = d.pitcher_alt;
    }} else if (_pcCurrentView === 'alt' && !_hasAlt) {{
      _pcCurrentView = 'base';
    }}

    // Prior-year data — alt-to-alt comparison when viewing alt.
    var _priorYear = year - 1;
    var priorD = (_priorYear === 2026)
      ? _pcData[String(id)]
      : ((_pcHist[String(_priorYear)] || {{}})[String(id)] || null);
    if (_pcCurrentView === 'alt') {{
      priorD = (priorD && priorD.pitcher_alt) ? priorD.pitcher_alt : null;
    }}

    var teamId = _TEAM_IDS[d.team] || '';
    // Prefer the server-side pre-fetched data URL (baked into HTML at
    // build time). It's a same-origin string that html-to-image can
    // rasterize natively — no CORS, no network, no iOS Safari quirks.
    // Falls back to the MLB CDN URL if for some reason the player's
    // headshot wasn't in the pre-fetch batch.
    var _mlbPhotoUrl = 'https://img.mlbstatic.com/mlb-photos/image/upload/'
      + 'd_people:generic:headshot:67:current.png/w_600,q_auto:best/v1/people/'
      + id + '/headshot/67/current';
    var photoUrl = (window._pcPhotos && window._pcPhotos[String(id)]) || _mlbPhotoUrl;
    var logoUrl = teamId
      ? 'https://www.mlbstatic.com/team-logos/' + teamId + '.svg'
      : '';
    var logoBgUrl = teamId
      ? 'https://www.mlbstatic.com/team-logos/' + teamId + '.svg'
      : '';

    // Bio
    var bt = (d.bats||'?') + '/' + (d.throws||'?');
    var ht = d.ht || '–';
    var wt = d.wt ? d.wt + ' lbs' : '–';
    var age = d.age ? d.age + ' yrs' : '–';
    var pos = d.pos || '–';
    var qual = d.qual
      ? ''
      : '<span style="background:#2a1a1a;color:#888;border:1px solid #444;'
        + 'font-size:.6rem;font-weight:700;padding:1px 6px;border-radius:10px;'
        + 'letter-spacing:.04em">[NQ]</span>';

    // ── Year label (static, inside card) + External dropdown (live) ───
    // The label is a plain text badge inside the card so the year shows
    // up in the saved image. The interactive dropdown lives OUTSIDE the
    // card (#pc-year-wrap) so year switching still works after the card
    // is rasterized to a static <img>.
    // availYrs already resolved above (used for default-year fallback)
    var yearDropdown =
      '<span style="background:#1a1a1a;color:#ddd;border:1px solid #444;'
      + 'border-radius:6px;padding:2px 10px;font-size:.78rem;font-weight:700;'
      + 'margin-left:8px;display:inline-block;line-height:1.4;'
      + 'vertical-align:middle">' + year + '</span>';
    (function updateExternalYearSel(){{
      var wrap = document.getElementById('pc-year-wrap');
      var sel  = document.getElementById('pc-year-sel');
      if (!wrap || !sel) return;
      if (availYrs.length <= 1) {{ wrap.style.display = 'none'; return; }}
      wrap.style.display = '';
      var opts = availYrs.map(function(y) {{
        return '<option value="' + y + '"' + (y===year?' selected':'') + '>' + y + '</option>';
      }}).join('');
      sel.innerHTML = opts;
      // Preserve current view (hitter vs pitcher) across year changes.
      sel.onchange = function(){{ _pcShow(id, parseInt(sel.value, 10), _pcCurrentView); }};
    }})();

    // ── Dual-role toggle (Ohtani's hitter ↔ pitcher card) ───────────────
    var _baseRec = (year === 2026)
      ? _pcData[String(id)]
      : ((_pcHist[String(year)] || {{}})[String(id)] || null);
    // Update the EXTERNAL toggle button (outside #pc-card so its click
    // handler survives the auto-capture rasterization step that turns
    // the card into an <img>).
    (function updateViewToggle(){{
      var btn = document.getElementById('pc-view-toggle');
      if (!btn) return;
      if (!_baseRec || !_baseRec.pitcher_alt) {{
        btn.style.display = 'none';
        btn.onclick = null;
        return;
      }}
      var showingAlt = (_pcCurrentView === 'alt');
      var targetView = showingAlt ? 'base' : 'alt';
      btn.style.display = '';
      btn.innerHTML = '&#8646; ' + (showingAlt ? 'Show hitting' : 'Show pitching');
      btn.onclick = function(){{ _pcFlipTo(id, year, targetView); }};
    }})();
    // Header HTML still concats `+ dualToggle +`; keep this empty so
    // nothing renders inside the rasterized card.
    var dualToggle = '';

    // ── Dollar value badges (right of qualified marker) — 2026 only ────────
    // Earned $ (season-to-date, amber) sits to the LEFT of Proj $ (RoS, green).
    // Each badge carries a small E/P label + tooltip so they're unambiguous.
    var evBadge = '';
    if (year === 2026 && d.ev != null) {{
      var evSign = d.ev >= 0 ? '$' : '-$';
      evBadge = '<span title="Earned $ — value produced season-to-date" '
        + 'style="font-size:1.15rem;font-weight:900;color:#d8a13a;'
        + 'background:#3a2f17;border:1px solid #d8a13a;padding:1px 8px;border-radius:6px;margin-left:8px">'
        + '<span style="font-size:.62rem;font-weight:700;opacity:.8;vertical-align:middle;margin-right:2px">E</span>'
        + evSign + Math.abs(d.ev).toFixed(1) + '</span>';
    }}
    var dvBadge = '';
    if (year === 2026 && d.dv != null) {{
      var dvSign = d.dv >= 0 ? '$' : '-$';
      dvBadge = '<span title="Projected $ — rest-of-season auction value" '
        + 'style="font-size:1.15rem;font-weight:900;color:#4caf50;'
        + 'background:#1b3a1b;border:1px solid #4caf50;padding:1px 8px;border-radius:6px;margin-left:8px">'
        + '<span style="font-size:.62rem;font-weight:700;opacity:.8;vertical-align:middle;margin-right:2px">P</span>'
        + dvSign + Math.abs(d.dv).toFixed(1) + '</span>';
    }}

    // ── fWAR badge ───────────────────────────────────────────────────────
    var warBadge = '';
    if (d.war != null) {{
      warBadge = '<div style="font-size:.72rem;font-weight:700;color:#8ab4f8;margin-top:3px">'
        + d.war.toFixed(1) + ' fWAR</div>';
    }}

    // ── Header ────────────────────────────────────────────────────────────
    var pcImgId = 'pc-headshot-' + id;
    var header =
      '<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">'
      + '<div style="width:120px;height:120px;flex-shrink:0;border-radius:50%;overflow:hidden;background:linear-gradient(to bottom, #a0a0a3 0%, #ececf0 100%)">'
      + '<img id="' + pcImgId + '" src="' + photoUrl + '" loading="lazy" '
      +   'onerror="this.parentElement.style.display=\\x27none\\x27" '
      +   'style="width:100%;height:100%;object-fit:contain;object-position:center center"/>'
      + '</div>'
      + '<div style="flex:1;min-width:0">'
      +   '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">'
      +     '<span style="font-size:1.05rem;font-weight:800;color:#eee">' + d.name + '</span>'
      +     qual
      +     evBadge
      +     dvBadge
      +     yearDropdown
      +     dualToggle
      +   warBadge
      +   '</div>'
      +   '<div style="display:flex;align-items:center;gap:5px;margin-top:3px">'
      +     tm(d.team)
      +     '<span style="color:#888">·</span>'
      +     '<span style="font-size:.74rem;color:#eee">' + pos + '</span>'
      +     '<span style="color:#888">·</span>'
      +     '<span style="font-size:.74rem;color:#eee">B/T: ' + bt + '</span>'
      +   '</div>'
      +   '<div style="font-size:.68rem;color:#ddd;margin-top:2px">'
      +     age + ' · ' + ht + ' · ' + wt
      +   '</div>'
      + '</div>'
      + '</div>';

    // ── Standard stats strip ──────────────────────────────────────────────
    function fmt3(v) {{ return v != null ? v.toFixed(3) : '–'; }}
    function fmt2(v) {{ return v != null ? v.toFixed(2) : '–'; }}
    function fmt1(v) {{ return v != null ? v.toFixed(1) : '–'; }}
    function fmtN(v) {{ return v != null ? v : '–'; }}
    var myLeaders = (year === 2026 && d.qual && _pcLeaders[String(id)]) ? _pcLeaders[String(id)] : [];
    var leaderMap = {{}};
    myLeaders.forEach(function(k){{ leaderMap[k]=true; }});
    var std_items;
    if (d.type === 'p') {{
      var svStr = (d.sv != null && d.svo != null) ? (d.sv + '/' + d.svo) : fmtN(d.sv);
      // Starting pitchers never get gold on SV/HLD (not meaningful for SPs).
      var isSP = d.is_sp === true;
      std_items = [
        ['GP',    fmtN(d.g),    false],
        ['GS',    fmtN(d.gs),   false],
        ['IP',    fmt1(d.ip),   false],
        ['W',     fmtN(d.w),    !!leaderMap.w],
        ['ERA',   fmt2(d.era),  !!leaderMap.era],
        ['WHIP',  fmt2(d.whip), !!leaderMap.whip],
        ['K',     fmtN(d.k),    !!leaderMap.k],
        ['SIERA', fmt2(d.siera),!!leaderMap.siera],
        ['K-BB%', (d.kbb!=null?d.kbb.toFixed(1)+'%':'–'), !!leaderMap.k_bb_pct],
        ['SV/O',  svStr,        isSP ? false : !!leaderMap.sv],
        ['HLD',   fmtN(d.hld),  isSP ? false : !!leaderMap.hld],
      ];
    }} else {{
    std_items = [
      ['G',   fmtN(d.g),  false],  ['PA',  fmtN(d.pa), false],  ['AB',  fmtN(d.ab), false],
      ['R',   fmtN(d.r),  !!leaderMap.r],   ['HR',  fmtN(d.hr),  !!leaderMap.hr],
      ['RBI', fmtN(d.rbi),!!leaderMap.rbi],  ['SB',  fmtN(d.sb),  !!leaderMap.sb],
      ['AVG', fmt3(d.avg), !!leaderMap.avg],
      ['OBP', fmt3(d.obp), !!leaderMap.obp], ['OPS', fmt3(d.ops), !!leaderMap.ops],
    ];
    }}
    var std_html =
      '<div style="background:#111;border:1px solid #222;border-radius:8px;padding:6px 8px;margin-bottom:10px">'
      + '<div style="font-size:.55rem;font-weight:700;color:#999;letter-spacing:.06em;margin-bottom:4px">' + year + ' STATS</div>'
      + '<div style="display:flex;flex-wrap:wrap;gap:0">'
      + std_items.map(function(x) {{
          var col = x[2] ? '#f0c040' : '#ddd';
          return '<div style="text-align:center;padding:2px 4px;min-width:36px;flex:1">'
            + '<div style="font-size:.8rem;font-weight:800;color:' + col + '">' + x[1] + '</div>'
            + '<div style="font-size:.5rem;color:' + (x[2]?'#b8982e':'#999') + ';margin-top:1px;letter-spacing:.04em">' + x[0] + '</div>'
            + '</div>';
        }}).join('') + '</div></div>';

    // ── Percentile bars (Savant style) ─────────────────────────────────────
    // [label, rawVal, pctVal, fmtFn, leaderKey]
    var f3  = function(v){{return v!=null?v.toFixed(3):null;}};
    var fMph= function(v){{return v!=null?v.toFixed(1)+' mph':null;}};
    var fPct= function(v){{return v!=null?v.toFixed(1)+'%':null;}};
    var statRows;
    // Helper: safely pull a field off priorD (null when no prior-year data).
    var _pv = function(k){{ return (priorD && priorD[k] != null) ? priorD[k] : null; }};
    if (d.type === 'p') {{
      statRows = [
        ['xERA',      d.xera,  d.pct.xera,   function(v){{return v!=null?v.toFixed(2):null;}},'xera', _pv('xera')],
        ['xBA',       d.xba,   d.pct.xba,    f3,'xba', _pv('xba')],
        ['FB Velo',   d.fbv,   d.pct.fb_velo,fMph,'fb_velo', _pv('fbv')],
        ['Avg Exit Velo', d.aev, d.pct.avg_ev, fMph,'avg_ev', _pv('aev')],
        ['wOBA',      d.woba,  d.pct.woba,   f3,'woba', _pv('woba')],
        ['xwOBA',     d.xwoba, d.pct.xwoba,  f3,'xwoba', _pv('xwoba')],
        ['Chase%',    d.ch,    d.pct.chase_pct, fPct,'chase_pct', _pv('ch')],
        ['Whiff%',    d.wh,    d.pct.whiff_pct, fPct,'whiff_pct', _pv('wh')],
        ['K%',        d.kp,    d.pct.k_pct,     fPct,'k_pct', _pv('kp')],
        ['BB%',       d.bbp,   d.pct.bb_pct,    fPct,'bb_pct', _pv('bbp')],
        ['Barrel%',   d.brl,   d.pct.barrel_pct,fPct,'barrel_pct', _pv('brl')],
        ['Hard Hit%', d.hh,    d.pct.hard_hit_pct,fPct,'hard_hit_pct', _pv('hh')],
        ['GB%',       d.gb,    d.pct.gb_pct,    fPct,'gb_pct', _pv('gb')],
      ];
    }} else {{
    statRows = [
      ['xWOBA',     d.xwoba, d.pct.xwoba,   function(v){{return v!=null?v.toFixed(3):null;}},'xwoba', _pv('xwoba')],
      ['xBA',       d.xba,   d.pct.xba,     function(v){{return v!=null?v.toFixed(3):null;}},'xba', _pv('xba')],
      ['xSLG',      d.xslg,  d.pct.xslg,    function(v){{return v!=null?v.toFixed(3):null;}},'xslg', _pv('xslg')],
      ['Avg EV',    d.avg_ev,d.pct.avg_ev,  function(v){{return v!=null?v.toFixed(1)+' mph':null;}},'avg_ev', _pv('avg_ev')],
      ['Max EV',    d.max_ev,d.pct.max_ev,  function(v){{return v!=null?v.toFixed(1)+' mph':null;}},'max_ev', _pv('max_ev')],
      ['Barrel%',   d.brl,   d.pct.barrel_pct, function(v){{return v!=null?v.toFixed(1)+'%':null;}},'barrel_pct', _pv('brl')],
      ['Hard Hit%', d.hh,    d.pct.hard_hit_pct,function(v){{return v!=null?v.toFixed(1)+'%':null;}},'hard_hit_pct', _pv('hh')],
      ['LA Sweet-Spot%',d.ss,   d.pct.sweet_spot_pct,function(v){{return v!=null?v.toFixed(1)+'%':null;}},'sweet_spot_pct', _pv('ss')],
      ['Bat Speed', d.bs,    d.pct.bat_speed,function(v){{return v!=null?v.toFixed(1)+' mph':null;}},'bat_speed', _pv('bs')],
      ['Squared Up%',d.sq,   d.pct.squared_up_pct,function(v){{return v!=null?v.toFixed(1)+'%':null;}},'squared_up_pct', _pv('sq')],
      ['Chase%',    d.ch,    d.pct.chase_pct,function(v){{return v!=null?v.toFixed(1)+'%':null;}},'chase_pct', _pv('ch')],
      ['Whiff%',    d.wh,    d.pct.whiff_pct,function(v){{return v!=null?v.toFixed(1)+'%':null;}},'whiff_pct', _pv('wh')],
      ['K%',        d.kp,    d.pct.k_pct,   function(v){{return v!=null?v.toFixed(1)+'%':null;}},'k_pct', _pv('kp')],
      ['BB%',       d.bbp,   d.pct.bb_pct,  function(v){{return v!=null?v.toFixed(1)+'%':null;}},'bb_pct', _pv('bbp')],
      ['Sprint Speed',d.spd,   d.pct.sprint_speed,function(v){{return v!=null?v.toFixed(1)+' ft/s':null;}},'sprint_speed', _pv('spd')],
    ];
    }}

    function pctColor(p) {{
      // Vibrant: 1=deep blue #1e3fba → 50=grey #888 → 100=vibrant red #e02020
      if (p >= 50) {{
        var t = (p - 50) / 50;
        return 'rgb(' + Math.round(136 + 88*t) + ',' + Math.round(136 - 104*t) + ',' + Math.round(136 - 104*t) + ')';
      }} else {{
        var t = (p - 1) / 49;
        return 'rgb(' + Math.round(30 + 106*t) + ',' + Math.round(63 + 73*t) + ',' + Math.round(186 - 50*t) + ')';
      }}
    }}
    function pctBar(label, rawVal, pct, fmtFn, leaderKey, priorVal) {{
      var valStr = fmtFn(rawVal);
      if (valStr == null) valStr = '–';
      var pctDisp = (pct != null) ? Math.round(pct) : null;
      var isGold = d.qual && !!leaderMap[leaderKey];
      // Leader among qualified hitters = 100th percentile
      if (isGold && pctDisp != null) pctDisp = 100;

      // Prior-year compact label, e.g. "'25 .312". Only rendered if we
      // actually have a prior-year value for this stat.
      var priorStr = (priorVal != null) ? fmtFn(priorVal) : null;
      var priorYY  = "'" + String(_priorYear).slice(-2);

      // Delta arrow: show ▲ or ▼ next to the current value if we have a
      // prior-year number to compare against. Color reflects *improvement*,
      // which depends on whether the stat is higher-better or lower-better
      // (and that can differ between hitters and pitchers — e.g. K% is
      // lower-better for hitters, higher-better for pitchers).
      var arrowHtml = '';
      if (rawVal != null && priorVal != null) {{
        var isP = d.type === 'p';
        var isLowerBetter = isP
          ? !!_LOWER_BETTER_P[leaderKey]
          : !!_LOWER_BETTER_H[leaderKey];
        var diff = rawVal - priorVal;
        if (diff !== 0) {{
          var up = diff > 0;
          var improved = isLowerBetter ? !up : up;
          var arrowCol = improved ? '#2ecc71' : '#e74c3c';
          var arrowCh  = up ? '\u25B2' : '\u25BC';  // ▲ / ▼
          arrowHtml = '<span style="color:' + arrowCol + ';font-size:.62rem;'
                    + 'margin-right:3px;vertical-align:middle">'
                    + arrowCh + '</span>';
        }}
      }}
      var priorCell = priorStr
        ? ('<div style="font-size:.68rem;font-weight:600;color:#999;'
            + 'margin-top:2px;line-height:1">'
            + '<span style="color:#777">' + priorYY + '</span> ' + priorStr
            + '</div>')
        : '';
      var valCol = (pctDisp == null) ? '#555' : (isGold ? '#f0c040' : '#eee');
      var valFontSz = (pctDisp == null) ? '.82rem' : '.9rem';
      var valWeight = (pctDisp == null) ? '500' : '800';
      var valCell =
        '<div style="min-width:78px;text-align:right;line-height:1">'
        + '<div style="font-size:' + valFontSz + ';font-weight:' + valWeight
        + ';color:' + valCol + '">' + arrowHtml + valStr + '</div>'
        + priorCell
        + '</div>';

      var barHtml;
      if (pctDisp == null) {{
        barHtml = '<div style="display:flex;align-items:center;gap:6px">'
                + '<div style="flex:1;height:8px;border-radius:4px;background:#2a2a2a"></div>'
                + valCell
                + '</div>';
      }} else {{
        var col = pctColor(pctDisp);
        var fillW = Math.min(Math.max(pctDisp, 3), 100);
        barHtml =
          '<div style="display:flex;align-items:center;gap:6px">'
          + '<div style="flex:1;height:8px;border-radius:4px;background:#2a2a2a;'
          + 'position:relative;overflow:visible">'
          + '<div style="height:100%;width:' + fillW + '%;border-radius:4px;background:' + col + '"></div>'
          + '<div style="position:absolute;top:50%;left:' + fillW + '%;'
          + 'transform:translate(-50%,-50%);width:28px;height:28px;'
          + 'border-radius:14px;background:' + col + ';'
          + 'border:1.5px solid rgba(255,255,255,.7);'
          + 'display:flex;align-items:center;justify-content:center;'
          + 'font-size:.9rem;font-weight:800;color:#fff;line-height:1">'
          + pctDisp + '</div>'
          + '</div>'
          + valCell
          + '</div>';
      }}
      var labelCol = isGold ? '#f0c040' : '#fff';

      return '<div style="margin-bottom:8px">'
        + '<div style="margin-bottom:2px">'
        +   '<span style="font-size:.88rem;color:' + labelCol + ';font-weight:800">' + label + '</span>'
        + '</div>'
        + barHtml
        + '</div>';
    }}

    var _pctHdr = (d.type === 'p')
      ? 'Percentile rank among all pitchers'
      : 'Percentile rank among all hitters';
    var _profHdr = (d.type === 'p') ? 'PITCHING PROFILE' : 'STATCAST PROFILE';
    if (year !== 2026) _profHdr = year + ' ' + _profHdr;
    var bars_html =
      '<div style="background:#111;border:1px solid #222;border-radius:8px;'
      + 'padding:10px 12px 4px;margin-bottom:10px">'
      + '<div style="font-size:.6rem;font-weight:700;color:#666;letter-spacing:.06em;'
      + 'margin-bottom:8px">' + _profHdr
      + '<span style="float:right;font-weight:400;color:#555">' + _pctHdr + '</span></div>'
      + statRows.map(function(r){{return pctBar(r[0],r[1],r[2],r[3],r[4],r[5]);}}).join('')
      + '</div>';

    // ── Bottom section: batted ball (hitters) OR pitch arsenal (pitchers) ─
    function fmtPct(v) {{ return v != null ? v.toFixed(1)+'%' : '–'; }}
    function fmtDeg(v) {{ return v != null ? v.toFixed(1)+'°' : '–'; }}
    var bb_html = '';
    if (d.type === 'p') {{
      var ars = d.ars || [];
      var arsRows = '';
      if (ars.length) {{
        arsRows = ars.map(function(pt){{
          var col = _PITCH_COLORS[pt.code] || '#95a5a6';
          var nm  = _PITCH_NAMES[pt.code] || pt.code;
          var veloStr = pt.velo != null ? pt.velo.toFixed(1) + ' mph' : '–';
          var usageStr = pt.usage != null ? pt.usage.toFixed(1) + '%' : '–';
          return '<div style="display:flex;align-items:center;gap:8px;padding:3px 0">'
            + '<div style="width:8px;height:8px;border-radius:4px;background:' + col + ';flex-shrink:0"></div>'
            + '<div style="min-width:78px;font-size:.72rem;font-weight:700;color:#ddd">' + nm + '</div>'
            + '<div style="flex:1;font-size:.68rem;color:#aaa">' + pt.code + '</div>'
            + '<div style="font-size:.72rem;color:#ccc;min-width:54px;text-align:right">' + usageStr + '</div>'
            + '<div style="font-size:.72rem;color:#ccc;min-width:70px;text-align:right">' + veloStr + '</div>'
            + '</div>';
        }}).join('');
      }} else {{
        arsRows = '<div style="font-size:.72rem;color:#666;text-align:center;padding:6px">No arsenal data</div>';
      }}
      var stfStr = d.stf != null ? d.stf : '–';
      var locStr = d.loc != null ? d.loc : '–';
      var stfIsGold = !!leaderMap.stuff_plus;
      var locIsGold = !!leaderMap.loc_plus;
      // Red-to-blue gradient based on SP/RP-split percentile (matches slider colors)
      var stfCol = stfIsGold ? '#f0c040'
                  : (d.stf != null && d.pct.stuff_plus != null ? pctColor(d.pct.stuff_plus) : '#ddd');
      var locCol = locIsGold ? '#f0c040'
                  : (d.loc != null && d.pct.loc_plus != null ? pctColor(d.pct.loc_plus) : '#ddd');
      var stfLblCol = stfIsGold ? '#b8982e' : '#888';
      var locLblCol = locIsGold ? '#b8982e' : '#888';
      bb_html =
        '<div style="background:#111;border:1px solid #222;border-radius:8px;padding:8px 12px;margin-bottom:10px">'
        + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
        + '<div style="font-size:.6rem;font-weight:700;color:#999;letter-spacing:.06em">PITCH ARSENAL</div>'
        + '<div style="display:flex;gap:12px">'
        +   '<div style="text-align:center"><div style="font-size:.82rem;font-weight:800;color:' + stfCol + '">' + stfStr + '</div><div style="font-size:.52rem;color:' + stfLblCol + ';letter-spacing:.04em">STUFF+</div></div>'
        +   '<div style="text-align:center"><div style="font-size:.82rem;font-weight:800;color:' + locCol + '">' + locStr + '</div><div style="font-size:.52rem;color:' + locLblCol + ';letter-spacing:.04em">LOC+</div></div>'
        + '</div></div>'
        + '<div style="display:flex;justify-content:space-between;font-size:.52rem;color:#666;letter-spacing:.04em;padding:0 0 3px;border-bottom:1px solid #222;margin-bottom:2px">'
        +   '<span>PITCH</span><span style="margin-left:auto;margin-right:54px">USAGE</span><span>VELO</span>'
        + '</div>'
        + arsRows
        + '</div>';
    }} else if (d.pull!=null || d.gb!=null) {{
      var bbItems = [
        ['Pull%',fmtPct(d.pull)],['Center%',fmtPct(d.cent)],['Oppo%',fmtPct(d.oppo)],
        ['LA°',fmtDeg(d.la)],
        ['GB%',fmtPct(d.gb)],['LD%',fmtPct(d.ld)],['FB%',fmtPct(d.fb)],['PU%',fmtPct(d.pu)],
      ];
      bb_html =
        '<div style="background:#111;border:1px solid #222;border-radius:8px;padding:8px 12px;margin-bottom:10px">'
        + '<div style="font-size:.6rem;font-weight:700;color:#999;letter-spacing:.06em;margin-bottom:6px">BATTED BALL PROFILE</div>'
        + '<div style="display:flex;flex-wrap:wrap">'
        + bbItems.map(function(x){{
            return '<div style="text-align:center;padding:3px 6px;min-width:52px;flex:1">'
              + '<div style="font-size:.82rem;font-weight:700;color:#ddd">' + x[1] + '</div>'
              + '<div style="font-size:.55rem;color:#999;margin-top:1px;letter-spacing:.04em">' + x[0] + '</div>'
              + '</div>';
          }}).join('')
        + '</div></div>';
    }}
    // ── Assemble card ─────────────────────────────────────────────────────
    var logoBadge = '';
    if (logoBgUrl) {{
      // data-team holds the team abbreviation so the Save-as-image onclone
      // callback can swap the SVG src (which html2canvas renders badly) for
      // an ESPN PNG equivalent (which it handles fine).
      var _teamAbbrLo = (d.team || '').toString().toLowerCase();
      logoBadge = '<img src="' + logoBgUrl + '" loading="lazy" data-team="' + _teamAbbrLo + '" style="position:absolute;top:13px;right:6px;width:120px;height:120px;object-fit:contain;opacity:.85;z-index:1;filter:drop-shadow(1px 0 0 #fff) drop-shadow(-1px 0 0 #fff) drop-shadow(0 1px 0 #fff) drop-shadow(0 -1px 0 #fff)" onerror="this.style.display=\\x27none\\x27"/>';
    }}
    document.getElementById('pc-card').innerHTML =
      '<div style="background:#141414;border:1px solid #2a2a2a;border-radius:10px;padding:16px;'
      + 'position:relative;overflow:hidden">'
      + logoBadge
      + header + std_html + bars_html + bb_html
      + '</div>';
    // Show the save-hint ("long-press to save") under the card.
    var _saveWrap = document.getElementById('pc-save-wrap');
    if (_saveWrap) _saveWrap.style.display = '';
    var _saveStatus = document.getElementById('pc-save-status');
    if (_saveStatus) {{ _saveStatus.textContent = ''; _saveStatus.style.display = 'none'; }}
    // Auto-convert the just-rendered HTML card into a long-pressable <img>.
    // iOS Safari's native "Save Image to Photos" menu only appears on <img>
    // elements, so we rasterize the card immediately and swap it in.
    // `_pcCurrentId` tracks which player is being displayed so if the user
    // picks a different player mid-capture, we don't clobber their card.
    // Reset the card transform on normal show calls (player/year
    // switches). During a flip, _pcFlipTo manages the transform.
    if (!_pcFlipping) {{
      var _cardEl = document.getElementById('pc-card');
      if (_cardEl) {{ _cardEl.style.transform = 'rotateY(0deg)'; }}
    }}
    // Defer auto-capture until any in-flight flip animation finishes —
    // capturing a rotated card would bake the rotation into the saved <img>.
    if (_pcFlipping) return;
    _pcAutoCaptureCard((d && d.name) || 'player-card', id);
  }};

  // Rasterize #pc-card via html-to-image and replace its contents with a
  // single <img> so iOS users can long-press → Save to Photos. Falls back
  // to leaving the HTML card as-is (with a visible error in the status
  // box) if capture fails.
  window._pcAutoCaptureCard = function(playerName, capturedForId){{
    var card = document.getElementById('pc-card');
    var status = document.getElementById('pc-save-status');
    if (!card || !card.firstChild) return;
    window._pcLoadHtmlToImage(function(err){{
      if (err) {{
        if (status) {{ status.style.display = 'block'; status.textContent = '❌ Could not load image library: ' + (err.message || ''); }}
        return;
      }}
      // If the user has since picked a different player, abort.
      if (capturedForId !== _pcCurrentId) return;
      // Gradient-zero-dim guard: same fix we use in the manual save flow.
      var restoreFns = [];
      card.querySelectorAll('*').forEach(function(el){{
        var bg = el.style && (el.style.background || el.style.backgroundImage) || '';
        if (bg.indexOf('gradient') >= 0 && (el.offsetWidth === 0 || el.offsetHeight === 0)) {{
          var prevDisp = el.style.display;
          el.style.setProperty('display', 'flex', 'important');
          restoreFns.push(function(){{ el.style.display = prevDisp; }});
        }}
      }});
      var w = card.offsetWidth;
      window.htmlToImage.toPng(card, {{
        backgroundColor: '#0a0a0a',
        pixelRatio: 2,
        cacheBust: false,
        skipFonts: false
      }}).then(function(dataUrl){{
        restoreFns.forEach(function(f){{ try{{ f(); }} catch(e){{}} }});
        // Another abort check — player may have changed while rendering.
        if (capturedForId !== _pcCurrentId) return;
        // Swap the card contents with a single <img>. The img is styled
        // to fill the same footprint as the HTML card; browsers show the
        // native long-press context menu on <img> elements.
        card.innerHTML = '<img src="' + dataUrl + '" alt="' +
          (playerName || 'Player Card').replace(/"/g, '&quot;') + '" ' +
          'style="width:' + w + 'px;max-width:100%;display:block;' +
          'border-radius:10px;user-select:none;-webkit-user-select:none;' +
          '-webkit-touch-callout:default" />';
      }}).catch(function(e){{
        restoreFns.forEach(function(f){{ try{{ f(); }} catch(e2){{}} }});
        if (status) {{
          status.style.display = 'block';
          status.textContent = '❌ Image capture failed: ' + (e && e.message || 'unknown');
        }}
      }});
    }});
  }};

  // Lazy-load html2canvas on first use so the 150KB library doesn't bloat
  // the initial page payload. Cached by the browser for subsequent clicks.
  window._pcLoadHtml2Canvas = function(cb){{
    if (window.html2canvas) {{ cb(null); return; }}
    var s = document.createElement('script');
    s.src = 'https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js';
    s.onload = function(){{ cb(null); }};
    s.onerror = function(){{ cb(new Error('Failed to load html2canvas')); }};
    document.head.appendChild(s);
  }};

  // html-to-image is a more iOS-friendly alternative to html2canvas.
  // html2canvas uses its own rasterizer that re-fetches images with
  // crossOrigin="anonymous" — iOS Safari often hangs indefinitely on this.
  // html-to-image renders via <foreignObject> inline SVG, which iOS handles
  // cleanly. ~20KB library.
  window._pcLoadHtmlToImage = function(cb){{
    if (window.htmlToImage) {{ cb(null); return; }}
    var called = false;
    var s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/html-to-image@1.11.11/dist/html-to-image.js';
    s.onload  = function(){{ if (!called) {{ called = true; cb(null); }} }};
    s.onerror = function(){{ if (!called) {{ called = true; cb(new Error('Script load failed')); }} }};
    // 8-second safety timeout in case the script tag never fires either
    // event (iOS network edge cases).
    setTimeout(function(){{
      if (!called) {{ called = true; cb(window.htmlToImage ? null : new Error('Script load timed out')); }}
    }}, 8000);
    document.head.appendChild(s);
  }};

  // Save-as-image: render #pc-card to a PNG and offer it via the Web Share
  // API (iOS/Android modern) with a download fallback. useCORS:true re-
  // fetches images with crossorigin so MLB/Savant headshots + logos don't
  // taint the canvas. backgroundColor matches the page to avoid a white halo.
  // Convert the MLB team-logo SVG into a PNG data URL. The raw SVG doesn't
  // render cleanly in html2canvas, and ESPN's PNG alternative has no CORS
  // headers (so crossOrigin="anonymous" img loads fail). MLB's SVG IS
  // CORS-enabled (access-control-allow-origin: *), so we fetch it as text,
  // draw to a canvas via a Blob URL, then export a PNG data URL.
  // Data URLs have no cross-origin constraints at all — html2canvas renders
  // them perfectly.
  function _pcSvgToPngDataUrl(svgUrl){{
    return fetch(svgUrl, {{mode: 'cors'}})
      .then(function(r){{ return r.ok ? r.text() : Promise.reject(new Error('HTTP ' + r.status)); }})
      .then(function(svgText){{
        return new Promise(function(resolve, reject){{
          var blob = new Blob([svgText], {{type: 'image/svg+xml'}});
          var url = URL.createObjectURL(blob);
          var im = new Image();
          im.onload = function(){{
            var size = 256;
            var c = document.createElement('canvas');
            c.width = size; c.height = size;
            var ctx = c.getContext('2d');
            ctx.drawImage(im, 0, 0, size, size);
            URL.revokeObjectURL(url);
            try {{ resolve(c.toDataURL('image/png')); }}
            catch(e){{ reject(e); }}
          }};
          im.onerror = function(){{
            URL.revokeObjectURL(url);
            reject(new Error('svg img load failed'));
          }};
          im.src = url;
        }});
      }});
  }}

  // Helpers to append visible progress/error lines to the status box.
  // iOS Safari users can't easily access a dev console, so we surface every
  // step on-screen instead. They can screenshot and send to debug.
  function _pcStatus(status, msg, isErr){{
    if (!status) return;
    status.style.display = 'block';
    var line = (isErr ? '❌ ' : '• ') + msg;
    status.textContent = status.textContent
      ? status.textContent + '\\n' + line
      : line;
    if (isErr) status.style.borderColor = '#c04040';
  }}

  window.pcSaveAsImage = function(){{
    var status = document.getElementById('pc-save-status');
    var btn = document.getElementById('pc-save-btn');
    // Reset the status box on every click so we don't accumulate history.
    if (status) {{ status.textContent = ''; status.style.borderColor = '#333'; }}
    _pcStatus(status, 'Click received');
    console.log('[pcSaveAsImage] click received');
    var card = document.getElementById('pc-card');
    if (!card) {{ _pcStatus(status, 'ERROR: #pc-card not found', true); return; }}
    if (!card.firstChild) {{ _pcStatus(status, 'Pick a player first.', true); return; }}
    _pcStatus(status, 'Card ready, starting capture…');
    if (btn) btn.disabled = true;

    // Global error listener for the duration of this capture — catches
    // anything that slips past our try/catch/catch chain.
    var errHandler = function(e){{
      _pcStatus(status, 'Window error: ' + (e.message || e.type), true);
    }};
    var rejHandler = function(e){{
      _pcStatus(status, 'Unhandled rejection: ' + (e.reason && e.reason.message || String(e.reason)), true);
    }};
    window.addEventListener('error', errHandler);
    window.addEventListener('unhandledrejection', rejHandler);
    var cleanup = function(){{
      window.removeEventListener('error', errHandler);
      window.removeEventListener('unhandledrejection', rejHandler);
      if (btn) btn.disabled = false;
    }};

    // Safety-net: if html2canvas hangs, we need to show SOMETHING after 15s.
    var captureDone = false;
    setTimeout(function(){{
      if (captureDone) return;
      _pcStatus(status, 'Timed out after 15s — html2canvas never returned', true);
      cleanup();
    }}, 15000);
    (function startCapture(){{
    _pcStatus(status, 'Loading capture library…');
    window._pcLoadHtmlToImage(function(err){{
      if (err) {{
        _pcStatus(status, 'Library load failed: ' + (err.message || 'unknown'), true);
        captureDone = true; cleanup(); return;
      }}
      _pcStatus(status, 'Library loaded, rendering card…');
      // Fix gradient-backed zero-dimension elements (rasterizer crash guard).
      var restoreFns = [];
      card.querySelectorAll('*').forEach(function(el){{
        var bg = el.style && (el.style.background || el.style.backgroundImage) || '';
        if (bg.indexOf('gradient') >= 0 && (el.offsetWidth === 0 || el.offsetHeight === 0)) {{
          var prevDisp = el.style.display;
          el.style.setProperty('display', 'flex', 'important');
          restoreFns.push(function(){{ el.style.display = prevDisp; }});
        }}
      }});
      var restoreDom = function(){{ restoreFns.forEach(function(f){{ try{{ f(); }} catch(e){{}} }}); }};
      // Headshot is already a data URL on window._pcPhotos (loaded via the
      // deferred pc_photos.js). html-to-image embeds it natively, no
      // runtime network work needed. If the deferred script somehow hasn't
      // loaded by capture time, the MLB CDN URL fallback kicks in upstream.
      window.htmlToImage.toPng(card, {{
        backgroundColor: '#0a0a0a',
        pixelRatio: 2,
        cacheBust: true,
        skipFonts: false
      }})
        .then(function(dataUrl){{
          captureDone = true;
          restoreDom();
          _pcStatus(status, 'Image generated (' + Math.round(dataUrl.length/1024) + ' KB), opening modal…');
          var name = (btn && btn.getAttribute('data-player-name')) || 'player-card';
          try {{ _pcShowSaveModal(dataUrl, name); }}
          catch(e){{ _pcStatus(status, 'Modal failed: ' + e.message, true); cleanup(); return; }}
          setTimeout(function(){{ if (status) status.style.display = 'none'; }}, 500);
          cleanup();
        }})
        .catch(function(e){{
          captureDone = true;
          restoreDom();
          _pcStatus(status, 'Render failed: ' + (e && e.message || 'unknown'), true);
          cleanup();
        }});
    }});
    }})();
  }};

  // Show a modal overlay with the captured image. On iOS the user long-
  // presses the <img> → Safari's "Save Image to Photos" menu appears,
  // which is the reliable way to get a PNG into the camera roll. On
  // desktop they can right-click → Save Image As, or drag to their
  // desktop. The modal closes on tap outside or the ✕ button.
  function _pcShowSaveModal(dataUrl, playerName){{
    var isIOS = /iPhone|iPad|iPod/i.test(navigator.userAgent || '');
    var instr = isIOS
      ? 'Long-press the image, then tap <strong>Save to Photos</strong>.'
      : 'Right-click the image and choose <strong>Save Image As…</strong>, or drag it to your desktop.';
    var modal = document.createElement('div');
    modal.id = 'pc-save-modal';
    modal.setAttribute('role', 'dialog');
    modal.style.cssText =
      'position:fixed;inset:0;background:rgba(0,0,0,.82);z-index:9999;' +
      'display:flex;flex-direction:column;align-items:center;justify-content:flex-start;' +
      'padding:16px;overflow-y:auto;-webkit-overflow-scrolling:touch;' +
      'backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px)';
    modal.innerHTML =
      '<div style="max-width:560px;width:100%;color:#eee;text-align:center;' +
      'padding-bottom:80px">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;' +
        'padding:12px 4px 16px">' +
          '<div style="font-size:.88rem;font-weight:700">' + (playerName || 'Player Card') + '</div>' +
          '<button type="button" aria-label="Close" ' +
          'style="background:#1e1e1e;border:1px solid #555;color:#fff;' +
          'width:36px;height:36px;border-radius:50%;font-size:1.1rem;' +
          'cursor:pointer" onclick="_pcCloseSaveModal()">&times;</button>' +
        '</div>' +
        '<div style="font-size:.82rem;color:#bbb;margin-bottom:14px;' +
        'padding:10px 14px;background:rgba(255,255,255,.06);border-radius:8px;' +
        'border:1px solid rgba(255,255,255,.1)">' + instr + '</div>' +
        '<img src="' + dataUrl + '" alt="' + (playerName || 'player card') + '" ' +
        'style="max-width:100%;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.6);' +
        'display:block;margin:0 auto" />' +
        '<button type="button" onclick="_pcCloseSaveModal()" ' +
        'style="margin-top:18px;background:#1e1e1e;border:1px solid #555;' +
        'color:#eee;padding:10px 20px;border-radius:8px;cursor:pointer;' +
        'font-size:.9rem;font-weight:600">Close</button>' +
      '</div>';
    modal.addEventListener('click', function(e){{
      if (e.target === modal) _pcCloseSaveModal();
    }});
    document.body.appendChild(modal);
    // Prevent background scroll while modal is open
    document.body.style.overflow = 'hidden';
  }}
  window._pcCloseSaveModal = function(){{
    var m = document.getElementById('pc-save-modal');
    if (m) m.remove();
    document.body.style.overflow = '';
  }};

}})();</script>
"""
    return inner


def inject_player_cards_tab(html: str, lb_data: list, fantasy_data: dict = None,
                            lb_pitch_data: dict = None,
                            historical_lb: dict = None) -> str:
    """Inject Player Cards tab button and panel into the dashboard HTML."""
    if not lb_data:
        return html

    # Build mlbam → dollar value lookups
    _dollar_map = {}
    _p_dollar_map = {}
    if fantasy_data:
        for entry in fantasy_data.get("fut_h", []):
            pl = entry.get("player", {})
            mlbam = pl.get("mlbam")
            if mlbam:
                try:
                    _dollar_map[int(mlbam)] = entry.get("dollar", 0)
                except (ValueError, TypeError):
                    pass
        for entry in fantasy_data.get("fut_p", []):
            pl = entry.get("player", {})
            mlbam = pl.get("mlbam")
            if mlbam:
                try:
                    _p_dollar_map[int(mlbam)] = entry.get("dollar", 0)
                except (ValueError, TypeError):
                    pass

    # Tab button — insert after the Fantasy tab button
    btn_html = "\n  <button class=\"tab-btn\" onclick=\"showTab('playercards',this)\">&#x1F4C8; Player Cards</button>"
    # Find fantasy tab button to insert after
    fantasy_anchor = "showTab('fantasy'"
    if fantasy_anchor in html:
        idx     = html.index(fantasy_anchor)
        end_btn = html.index("</button>", idx) + len("</button>")
        html    = html[:end_btn] + btn_html + html[end_btn:]
    else:
        # fallback: after compare button
        compare_anchor = "showTab('compare'"
        if compare_anchor in html:
            idx     = html.index(compare_anchor)
            end_btn = html.index("</button>", idx) + len("</button>")
            html    = html[:end_btn] + btn_html + html[end_btn:]

    panel_html = render_player_cards_tab(lb_data, _dollar_map,
                                          lb_pitch_data=lb_pitch_data,
                                          p_dollar_map=_p_dollar_map,
                                          historical_lb=historical_lb)
    # Lazy-render: keep a tiny placeholder in the DOM and stash the real
    # content inside a <template> so the browser doesn't parse/layout it
    # until the user actually clicks the Player Cards tab. hydrateTab() in
    # html_template.py clones the template and executes any inline scripts.
    #
    # The <script src="pc_photos.js" defer> sits OUTSIDE the template so it
    # downloads with the rest of the page, not when the tab is clicked.
    # `defer` makes it run after HTML parsing without blocking page paint.
    # By the time the user opens a card, window._pcPhotos is populated.
    lazy_html = (
        '\n<script src="pc_photos.js" defer></script>\n'
        '<div id="playercards-panel" class="tab-panel" data-lazy="1"></div>\n'
        '<template id="playercards-panel-template">\n' + panel_html + '\n</template>\n'
    )
    html       = html.replace("</body>", lazy_html + "\n</body>")
    return html


def inject_fantasy_tab(html: str, fantasy_data: dict, pos_lookup: dict | None = None,
                       il_pitcher_names: set | None = None) -> str:
    """Inject the Fantasy dollar-values tab button and panel into the dashboard HTML."""
    panel_html = render_fantasy_tab(fantasy_data, pos_lookup=pos_lookup,
                                    il_pitcher_names=il_pitcher_names)

    # Insert tab button after the Season Leaders button
    lb_anchor = "showTab('leaderboard'"
    if lb_anchor in html:
        idx     = html.index(lb_anchor)
        end_btn = html.index("</button>", idx) + len("</button>")
        btn_html = "\n  <button class=\"tab-btn\" onclick=\"showTab('fantasy',this)\">&#x1F4B0; Fantasy</button>"
        html    = html[:end_btn] + btn_html + html[end_btn:]

    # Lazy-render: same pattern as player cards — placeholder + template.
    # Dramatically cuts initial parse/layout time on mobile.
    lazy_html = (
        '\n<div id="fantasy-panel" class="tab-panel" data-lazy="1"></div>\n'
        '<template id="fantasy-panel-template">\n' + panel_html + '\n</template>\n'
    )
    html = html.replace("</body>", lazy_html + "\n</body>")
    return html