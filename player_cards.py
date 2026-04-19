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
# Correct ARI
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
        player_index.append({"id": mid, "n": name, "t": team, "k": "p"})
        player_data[str(mid)] = {
            "type":    "p",
            "name":    name,
            "team":    team,
            "pos":     p.get("pos") or ("SP" if p.get("is_sp") else "RP"),
            "bats":    p.get("bats"),
            "throws":  p.get("throws"),
            "age":     p.get("age"),
            "ht":      p.get("height"),
            "wt":      p.get("weight"),
            "qual":    p.get("qualified", False),
            "dv":      p_dollar_map.get(mid),
            "war":     p.get("war"),
            "is_sp":   p.get("is_sp", False),
            # standard stats
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
            # percentile stats
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
            # arsenal + rate plus
            "stf":     p.get("stuff_plus"),
            "loc":     p.get("loc_plus"),
            "ars":     p.get("pitch_arsenal", []),
            # percentiles
            "pct":     p.get("pct", {}),
        }

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
            for p in payload.get("pitchers_sp", []) + payload.get("pitchers_rp", []):
                mid = p.get("id")
                if mid and p.get("name"):
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
  <!-- Search -->
  <input id="pc-search" type="text" placeholder="Search hitter or pitcher by name or team…"
    autocomplete="off"
    oninput="_pcSearch(this.value)"
    style="width:100%;box-sizing:border-box;padding:10px 14px;margin-bottom:4px;
           background:#1a1a1a;border:1px solid #333;border-radius:8px;
           color:#eee;font-size:.95rem;outline:none"/>
  <div id="pc-dropdown"
    style="display:none;background:#1e1e1e;border:1px solid #333;border-radius:8px;
           max-height:260px;overflow-y:auto;margin-bottom:12px;
           box-shadow:0 4px 16px rgba(0,0,0,.5)"></div>

  <!-- Card area -->
  <div id="pc-card"></div>

  <!-- Save-as-image button. Hidden until a player card is rendered. The
       status area below the button shows step-by-step progress and any
       errors (critical for iOS debugging — no dev console available). -->
  <div id="pc-save-wrap" style="display:none;margin-top:12px;text-align:center">
    <button id="pc-save-btn" type="button" onclick="pcSaveAsImage()"
            style="background:#1e1e1e;border:1px solid #444;color:#eee;
                   padding:12px 22px;border-radius:8px;font-size:.95rem;
                   font-weight:600;cursor:pointer;min-width:180px;min-height:44px">
      &#x1F4F7; Save as image
    </button>
    <div id="pc-save-status" style="font-size:.82rem;color:#eee;
         margin-top:10px;padding:8px 12px;border-radius:6px;text-align:left;
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

  // ── Search ────────────────────────────────────────────────────────────────────
  window._pcSearch = function(q) {{
    var dd = document.getElementById('pc-dropdown');
    if (!dd) return;
    q = (q||'').trim().toLowerCase();
    if (q.length < 2) {{ dd.style.display='none'; return; }}
    var matches = _pcIdx.filter(function(p) {{
      return p.n.toLowerCase().indexOf(q) !== -1
          || (p.t||'').toLowerCase().indexOf(q) !== -1;
    }}).slice(0,12);
    if (!matches.length) {{ dd.style.display='none'; return; }}
    dd.innerHTML = matches.map(function(p) {{
      return '<div class="pc-dd-item" onmousedown="_pcShow(' + p.id + ')">'
        + '<span style="font-weight:700">' + p.n + '</span>'
        + '<span style="color:#888;font-size:.78rem;margin-left:8px">' + (p.t||'') + '</span>'
        + '</div>';
    }}).join('');
    dd.style.display = 'block';
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

  window._pcShow = function(id, year) {{
    document.getElementById('pc-dropdown').style.display='none';
    year = year || 2026;
    _pcCurrentId = id;
    _pcCurrentYear = year;
    var d = (year === 2026)
      ? _pcData[String(id)]
      : ((_pcHist[String(year)] || {{}})[String(id)] || null);
    if (!d) return;
    var teamId = _TEAM_IDS[d.team] || '';
    // Live display uses MLB's direct CDN URL — loads fast everywhere, no
    // CORS needed for plain <img> display. The save-capture pre-fetch
    // below separately routes through weserv.nl (which sends CORS) to
    // get a data URL that html-to-image can embed. Decoupling the two
    // means display never fails because of a proxy hiccup.
    var _mlbPhotoUrl = 'https://img.mlbstatic.com/mlb-photos/image/upload/'
      + 'd_people:generic:headshot:67:current.png/w_600,q_auto:best/v1/people/'
      + id + '/headshot/67/current';
    var photoUrl      = _mlbPhotoUrl;
    var photoProxyUrl = 'https://images.weserv.nl/?url=' + encodeURIComponent(_mlbPhotoUrl);
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

    // ── Year dropdown ──────────────────────────────────────────────────────
    var availYrs = _pcAvailYears[String(id)] || [2026];
    var yearDropdown = '';
    if (availYrs.length > 1) {{
      var opts = availYrs.map(function(y) {{
        return '<option value="' + y + '"' + (y===year?' selected':'') + '>' + y + '</option>';
      }}).join('');
      yearDropdown = '<select onchange="_pcShow(' + id + ',parseInt(this.value))" '
        + 'style="background:#1a1a1a;color:#eee;border:1px solid #444;border-radius:6px;'
        + 'padding:2px 6px;font-size:.72rem;font-weight:700;cursor:pointer;margin-left:8px;'
        + 'outline:none">' + opts + '</select>';
    }}

    // ── Dollar value badge (right of qualified marker) — 2026 only ─────────
    var dvBadge = '';
    if (year === 2026 && d.dv != null) {{
      var dvSign = d.dv >= 0 ? '$' : '-$';
      dvBadge = '<span style="font-size:1.15rem;font-weight:900;color:#4caf50;'
        + 'background:#1b3a1b;border:1px solid #4caf50;padding:1px 8px;border-radius:6px;margin-left:8px">'
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
      +     dvBadge
      +     yearDropdown
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
    if (d.type === 'p') {{
      statRows = [
        ['xERA',      d.xera,  d.pct.xera,   function(v){{return v!=null?v.toFixed(2):null;}},'xera'],
        ['xBA',       d.xba,   d.pct.xba,    f3,'xba'],
        ['FB Velo',   d.fbv,   d.pct.fb_velo,fMph,'fb_velo'],
        ['Avg Exit Velo', d.aev, d.pct.avg_ev, fMph,'avg_ev'],
        ['wOBA',      d.woba,  d.pct.woba,   f3,'woba'],
        ['xwOBA',     d.xwoba, d.pct.xwoba,  f3,'xwoba'],
        ['Chase%',    d.ch,    d.pct.chase_pct, fPct,'chase_pct'],
        ['Whiff%',    d.wh,    d.pct.whiff_pct, fPct,'whiff_pct'],
        ['K%',        d.kp,    d.pct.k_pct,     fPct,'k_pct'],
        ['BB%',       d.bbp,   d.pct.bb_pct,    fPct,'bb_pct'],
        ['Barrel%',   d.brl,   d.pct.barrel_pct,fPct,'barrel_pct'],
        ['Hard Hit%', d.hh,    d.pct.hard_hit_pct,fPct,'hard_hit_pct'],
        ['GB%',       d.gb,    d.pct.gb_pct,    fPct,'gb_pct'],
      ];
    }} else {{
    statRows = [
      ['xWOBA',     d.xwoba, d.pct.xwoba,   function(v){{return v!=null?v.toFixed(3):null;}},'xwoba'],
      ['xBA',       d.xba,   d.pct.xba,     function(v){{return v!=null?v.toFixed(3):null;}},'xba'],
      ['xSLG',      d.xslg,  d.pct.xslg,    function(v){{return v!=null?v.toFixed(3):null;}},'xslg'],
      ['Avg EV',    d.avg_ev,d.pct.avg_ev,  function(v){{return v!=null?v.toFixed(1)+' mph':null;}},'avg_ev'],
      ['Max EV',    d.max_ev,d.pct.max_ev,  function(v){{return v!=null?v.toFixed(1)+' mph':null;}},'max_ev'],
      ['Barrel%',   d.brl,   d.pct.barrel_pct, function(v){{return v!=null?v.toFixed(1)+'%':null;}},'barrel_pct'],
      ['Hard Hit%', d.hh,    d.pct.hard_hit_pct,function(v){{return v!=null?v.toFixed(1)+'%':null;}},'hard_hit_pct'],
      ['LA Sweet-Spot%',d.ss,   d.pct.sweet_spot_pct,function(v){{return v!=null?v.toFixed(1)+'%':null;}},'sweet_spot_pct'],
      ['Bat Speed', d.bs,    d.pct.bat_speed,function(v){{return v!=null?v.toFixed(1)+' mph':null;}},'bat_speed'],
      ['Squared Up%',d.sq,   d.pct.squared_up_pct,function(v){{return v!=null?v.toFixed(1)+'%':null;}},'squared_up_pct'],
      ['Chase%',    d.ch,    d.pct.chase_pct,function(v){{return v!=null?v.toFixed(1)+'%':null;}},'chase_pct'],
      ['Whiff%',    d.wh,    d.pct.whiff_pct,function(v){{return v!=null?v.toFixed(1)+'%':null;}},'whiff_pct'],
      ['K%',        d.kp,    d.pct.k_pct,   function(v){{return v!=null?v.toFixed(1)+'%':null;}},'k_pct'],
      ['BB%',       d.bbp,   d.pct.bb_pct,  function(v){{return v!=null?v.toFixed(1)+'%':null;}},'bb_pct'],
      ['Sprint Speed',d.spd,   d.pct.sprint_speed,function(v){{return v!=null?v.toFixed(1)+' ft/s':null;}},'sprint_speed'],
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
    function pctBar(label, rawVal, pct, fmtFn, leaderKey) {{
      var valStr = fmtFn(rawVal);
      if (valStr == null) valStr = '–';
      var pctDisp = (pct != null) ? Math.round(pct) : null;
      var isGold = d.qual && !!leaderMap[leaderKey];
      // Leader among qualified hitters = 100th percentile
      if (isGold && pctDisp != null) pctDisp = 100;
      var barHtml;
      if (pctDisp == null) {{
        barHtml = '<div style="display:flex;align-items:center;gap:6px">'
                + '<div style="flex:1;height:8px;border-radius:4px;background:#2a2a2a"></div>'
                + '<span style="font-size:.68rem;color:#555;min-width:62px;text-align:right">–</span>'
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
          + '<span style="font-size:.7rem;font-weight:700;color:' + (isGold?'#f0c040':'#ccc') + ';min-width:62px;text-align:right">' + valStr + '</span>'
          + '</div>';
      }}
      var labelCol = isGold ? '#f0c040' : '#aaa';

      return '<div style="margin-bottom:8px">'
        + '<div style="margin-bottom:2px">'
        +   '<span style="font-size:.68rem;color:' + labelCol + ';font-weight:600">' + label + '</span>'
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
      + statRows.map(function(r){{return pctBar(r[0],r[1],r[2],r[3],r[4]);}}).join('')
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
    // Show the Save-as-image button now that there's a card to capture
    var _saveWrap = document.getElementById('pc-save-wrap');
    if (_saveWrap) _saveWrap.style.display = '';
    // Stash the player's name on the button so the saved filename matches
    var _saveBtn = document.getElementById('pc-save-btn');
    if (_saveBtn) _saveBtn.setAttribute('data-player-name', (d && d.name) || 'player-card');
    var _saveStatus = document.getElementById('pc-save-status');
    if (_saveStatus) _saveStatus.textContent = '';
    // Stash the proxy URL on the img as a data attribute so the save flow
    // can convert it to a data URL at capture time (not display time).
    // Keeps the live display on MLB's fast direct CDN — if the proxy
    // fetch later fails, live display is unaffected.
    (function preloadHeadshot(){{
      var hsImg = document.getElementById(pcImgId);
      if (!hsImg) return;
      hsImg.setAttribute('data-proxy-url', photoProxyUrl);
      // Kick off the fetch immediately so the data URL is ready when the
      // user taps Save. Result is stashed as data-png-src on the img.
      fetch(photoProxyUrl, {{mode: 'cors', cache: 'force-cache'}})
        .then(function(r){{ return r.ok ? r.blob() : Promise.reject(new Error('HTTP ' + r.status)); }})
        .then(function(blob){{
          return new Promise(function(res, rej){{
            var fr = new FileReader();
            fr.onload = function(){{ res(fr.result); }};
            fr.onerror = function(){{ rej(new Error('FileReader failed')); }};
            fr.readAsDataURL(blob);
          }});
        }})
        .then(function(dataUrl){{
          // Only stash if the card is still showing the same player.
          if (document.getElementById(pcImgId) === hsImg) {{
            hsImg.setAttribute('data-png-src', dataUrl);
          }}
        }})
        .catch(function(){{ /* save path will re-try the fetch */ }});
    }})();
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
      _pcStatus(status, 'Library loaded, preparing image…');
      var restoreFns = [];
      card.querySelectorAll('*').forEach(function(el){{
        var bg = el.style && (el.style.background || el.style.backgroundImage) || '';
        if (bg.indexOf('gradient') >= 0 && (el.offsetWidth === 0 || el.offsetHeight === 0)) {{
          var prevDisp = el.style.display;
          el.style.setProperty('display', 'flex', 'important');
          restoreFns.push(function(){{ el.style.display = prevDisp; }});
        }}
      }});
      // Swap headshot src → data URL (stashed as data-png-src by the
      // background pre-fetch). If pre-fetch hasn't finished, try to fetch
      // it synchronously here. If that also fails, capture without the
      // headshot — better than a hung save.
      var hsImg = card.querySelector('img[id^="pc-headshot"]');
      function swapHeadshotIfPossible(){{
        if (!hsImg) return Promise.resolve();
        var pngSrc = hsImg.getAttribute('data-png-src');
        if (pngSrc) {{
          var origSrc = hsImg.src;
          hsImg.src = pngSrc;
          restoreFns.push(function(){{ hsImg.src = origSrc; }});
          _pcStatus(status, 'Headshot ready (pre-fetched)');
          return Promise.resolve();
        }}
        var proxyUrl = hsImg.getAttribute('data-proxy-url');
        if (!proxyUrl) return Promise.resolve();
        _pcStatus(status, 'Fetching headshot via proxy…');
        return Promise.race([
          fetch(proxyUrl, {{mode: 'cors', cache: 'force-cache'}})
            .then(function(r){{ return r.ok ? r.blob() : Promise.reject(new Error('HTTP ' + r.status)); }})
            .then(function(blob){{
              return new Promise(function(res, rej){{
                var fr = new FileReader();
                fr.onload = function(){{ res(fr.result); }};
                fr.onerror = function(){{ rej(new Error('FileReader failed')); }};
                fr.readAsDataURL(blob);
              }});
            }})
            .then(function(dataUrl){{
              var origSrc = hsImg.src;
              hsImg.src = dataUrl;
              restoreFns.push(function(){{ hsImg.src = origSrc; }});
            }}),
          // 4s timeout — if proxy is slow, skip the headshot rather than
          // blocking the save entirely.
          new Promise(function(res){{ setTimeout(res, 4000); }})
        ]).catch(function(e){{
          _pcStatus(status, 'Headshot fetch failed (continuing): ' + (e.message || ''), false);
        }});
      }}
      var restoreDom = function(){{ restoreFns.forEach(function(f){{ try{{ f(); }} catch(e){{}} }}); }};
      // Do the headshot swap first, then capture. Everything is chained
      // through a single promise so errors in either step flow to the same
      // catch handler.
      swapHeadshotIfPossible().then(function(){{
      _pcStatus(status, 'Rendering card…');
      return window.htmlToImage.toPng(card, {{
        backgroundColor: '#0a0a0a',
        pixelRatio: 2,
        cacheBust: true,
        skipFonts: false
      }});}})
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
    lazy_html = (
        '\n<div id="playercards-panel" class="tab-panel" data-lazy="1"></div>\n'
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