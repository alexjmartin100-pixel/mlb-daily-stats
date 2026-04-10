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
                             lb_pitch_data: dict = None, p_dollar_map: dict = None) -> str:
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

</div>
</div>

<script>
(function(){{
  var _pcIdx  = {idx_json};
  var _pcData = {data_json};
  var _pcLeaders = {leaders_json};

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

  window._pcShow = function(id) {{
    document.getElementById('pc-dropdown').style.display='none';
    var d = _pcData[String(id)];
    if (!d) return;
    var teamId = _TEAM_IDS[d.team] || '';
    var photoUrl = 'https://img.mlbstatic.com/mlb-photos/image/upload/'
      + 'd_people:generic:headshot:67:current.png/w_600,q_auto:best/v1/people/'
      + id + '/headshot/67/current';
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
        + 'letter-spacing:.04em">NOT QUALIFIED</span>';

    // ── Dollar value badge (right of qualified marker) ─────────────────────
    var dvBadge = '';
    if (d.dv != null) {{
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
      + '<img id="' + pcImgId + '" src="' + photoUrl + '" '
      +   'onerror="this.parentElement.style.display=\\x27none\\x27" '
      +   'style="width:100%;height:100%;object-fit:contain;object-position:center center"/>'
      + '</div>'
      + '<div style="flex:1;min-width:0">'
      +   '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">'
      +     '<span style="font-size:1.05rem;font-weight:800;color:#eee">' + d.name + '</span>'
      +     qual
      +     dvBadge
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
    var myLeaders = (d.qual && _pcLeaders[String(id)]) ? _pcLeaders[String(id)] : [];
    var leaderMap = {{}};
    myLeaders.forEach(function(k){{ leaderMap[k]=true; }});
    var std_items;
    if (d.type === 'p') {{
      var svStr = (d.sv != null && d.svo != null) ? (d.sv + '/' + d.svo) : fmtN(d.sv);
      // For starting pitchers, SV/HLD are not meaningful — never highlight gold.
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
      + '<div style="font-size:.55rem;font-weight:700;color:#999;letter-spacing:.06em;margin-bottom:4px">2026 STATS</div>'
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
      logoBadge = '<img src="' + logoBgUrl + '" style="position:absolute;top:13px;right:6px;width:120px;height:120px;object-fit:contain;opacity:.85;z-index:1;filter:drop-shadow(1px 0 0 #fff) drop-shadow(-1px 0 0 #fff) drop-shadow(0 1px 0 #fff) drop-shadow(0 -1px 0 #fff)" onerror="this.style.display=\\x27none\\x27"/>';
    }}
    document.getElementById('pc-card').innerHTML =
      '<div style="background:#141414;border:1px solid #2a2a2a;border-radius:10px;padding:16px;'
      + 'position:relative;overflow:hidden">'
      + logoBadge
      + header + std_html + bars_html + bb_html
      + '</div>';
  }};

}})();</script>
"""
    return inner


def inject_player_cards_tab(html: str, lb_data: list, fantasy_data: dict = None,
                            lb_pitch_data: dict = None) -> str:
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
                                          p_dollar_map=_p_dollar_map)
    html       = html.replace("</body>", panel_html + "\n</body>")
    return html


def inject_fantasy_tab(html: str, fantasy_data: dict) -> str:
    """Inject the Fantasy dollar-values tab button and panel into the dashboard HTML."""
    panel_html = render_fantasy_tab(fantasy_data)

    # Insert tab button after the Compare button
    compare_anchor = "showTab('compare'"
    if compare_anchor in html:
        idx     = html.index(compare_anchor)
        end_btn = html.index("</button>", idx) + len("</button>")
        btn_html = "\n  <button class=\"tab-btn\" onclick=\"showTab('fantasy',this)\">&#x1F4B0; Fantasy</button>"
        html    = html[:end_btn] + btn_html + html[end_btn:]

    # Inject panel before </body>
    html = html.replace("</body>", panel_html + "\n</body>")
    return html


# ── Season Pitching Leaderboard ────────────────────────────────────────────
