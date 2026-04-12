import subprocess, sys, os, json, unicodedata, time
from datetime import date, timedelta, datetime
import pybaseball          # type: ignore
import pandas as pd
import numpy as np
import requests
import statsapi            # type: ignore  (MLB-StatsAPI)

import io

from config import *

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=760,minimum-scale=0.3,maximum-scale=5">
<title>MLB Daily Stats · __DATE_DISPLAY__</title>

<!-- PWA: installable as app icon on iOS & Android -->
<link rel="manifest" href="manifest.json">
<meta name="theme-color" content="#e31837">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="MLB Stats">
<link rel="apple-touch-icon" href="icon-192.png">
<script>
// Register service worker (enables PWA install prompt on Android/Chrome)
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function() {
    navigator.serviceWorker.register('sw.js').catch(function(){});
  });
}
</script>

<script>
// Auto-refresh: reload the page at 10:15 AM each day (5 min after script runs).
// Works for both local HTML files and hosted pages.
(function(){
  function msUntil(h, m) {
    var now = new Date();
    var target = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m, 0, 0);
    if (target <= now) target.setDate(target.getDate() + 1);
    return target - now;
  }
  function scheduleRefresh() {
    var ms = msUntil(10, 15);
    var hrs = Math.floor(ms / 3600000);
    var min = Math.floor((ms % 3600000) / 60000);
    var el = document.getElementById('refresh-status');
    if (el) el.textContent = 'Auto-refresh in ' + hrs + 'h ' + min + 'm';
    // Update countdown every minute
    setTimeout(function(){ scheduleRefresh(); }, 60000);
    // Reload at target time
    setTimeout(function(){ location.reload(true); }, ms);
  }
  document.addEventListener('DOMContentLoaded', scheduleRefresh);
})();
</script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0f1923;--card:#182130;--card2:#1e2c3d;--border:#243447;
  --text:#dce8f0;--muted:#6b8599;--accent:#e31837;--gold:#f0c040;
  --green:#2ecc71;--orange:#e8832a;--blue:#3d9be9;--red:#e74c3c;
  --radius:8px;
}
html{font-size:14px;background:var(--bg);color:var(--text);
     font-family:system-ui,-apple-system,'Segoe UI',sans-serif;line-height:1.4}
.site-header{
  background:linear-gradient(135deg,#080e14 0%,#140609 100%);
  border-bottom:3px solid var(--accent);
  padding:15px 26px;display:flex;align-items:center;gap:15px;
}
.hdr-logo{font-size:1.9rem;line-height:1}
.hdr-title{font-size:1.35rem;font-weight:800;color:#fff}
.hdr-badge{background:var(--accent);color:#fff;font-size:.6rem;font-weight:800;
  padding:2px 7px;border-radius:99px;letter-spacing:.7px;margin-left:7px;vertical-align:middle;}
.hdr-meta{font-size:.74rem;color:var(--muted);margin-top:3px}
.hdr-meta strong{color:#aabcc8}
.tab-bar{display:flex;align-items:flex-end;background:var(--card);
  border-bottom:2px solid var(--border);padding:0 26px;
  overflow-x:auto;overflow-y:hidden;scrollbar-width:none;touch-action:pan-x;}
.tab-bar::-webkit-scrollbar{display:none;}
.tab-btn{background:none;border:none;color:var(--muted);
  padding:12px 24px 10px;font-size:.88rem;font-weight:600;cursor:pointer;
  border-bottom:3px solid transparent;margin-bottom:-2px;
  transition:color .15s,border-color .15s;display:flex;align-items:center;gap:6px;}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{color:#fff;border-bottom-color:var(--accent)}
.tab-count{background:rgba(255,255,255,.1);border-radius:99px;
  padding:1px 7px;font-size:.63rem;font-weight:700;letter-spacing:.4px;}
.tab-btn.active .tab-count{background:var(--accent)}
.tab-panel{display:none;padding:20px 18px 40px}
.tab-panel.active{display:block}
main{max-width:1900px;margin:0 auto}
.controls{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.controls input{background:var(--card2);border:1px solid var(--border);color:var(--text);
  border-radius:var(--radius);padding:6px 11px;font-size:.8rem;width:210px;
  outline:none;transition:border-color .2s;}
.controls input:focus{border-color:var(--blue)}
.row-count{font-size:.73rem;color:var(--muted)}
.sort-hint{font-size:.7rem;color:var(--muted);font-style:italic}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:10px;
  padding:7px 11px;background:var(--card);border-radius:var(--radius);
  border:1px solid var(--border);}
.leg-item{display:flex;align-items:center;gap:4px;font-size:.69rem;color:var(--muted)}
.leg-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.note{font-size:.7rem;color:var(--muted);padding:5px 11px;
  border-left:3px solid var(--blue);background:rgba(52,152,219,.07);
  border-radius:0 var(--radius) var(--radius) 0;margin-bottom:11px;}
.table-wrap{overflow-x:auto;border-radius:var(--radius);border:1px solid var(--border)}
table{width:100%;border-collapse:collapse;font-size:.79rem}
thead th{background:var(--card2);color:var(--muted);text-transform:uppercase;
  font-size:.63rem;letter-spacing:.9px;font-weight:700;padding:9px 9px;
  text-align:left;white-space:nowrap;border-bottom:1px solid var(--border);
  user-select:none;position:sticky;top:0;z-index:1;}
thead th.sortable{cursor:pointer;transition:color .15s}
thead th.sortable:hover{color:#fff}
thead th.sort-asc::after{content:" ▲";color:var(--accent);font-size:.58rem}
thead th.sort-desc::after{content:" ▼";color:var(--accent);font-size:.58rem}
thead th.r{text-align:right}
thead th.sc{color:#5d9bc8 !important}
tbody tr{border-bottom:1px solid var(--border);transition:background .1s}
tbody tr:last-child{border-bottom:none}
tbody tr:hover{background:rgba(0,0,0,.04)}
tbody tr:nth-child(even){background:rgba(0,0,0,.025)}
tbody td{padding:8px 9px;vertical-align:middle;color:#e8f2ff}
tbody td.r{text-align:right;font-variant-numeric:tabular-nums;color:#e8f2ff;font-weight:500}
td.nm{font-weight:600;white-space:nowrap;color:var(--text);font-size:.83rem;
  position:sticky;left:0;z-index:2;background:var(--bg);}
tbody tr:nth-child(even) td.nm{background:color-mix(in srgb,var(--card) 40%,var(--bg));}
tbody tr:hover td.nm{background:color-mix(in srgb,var(--card2) 60%,var(--bg));}
thead th:first-child{position:sticky;left:0;z-index:3;background:var(--card2);}
.tm{display:inline-block;border-radius:4px;padding:1px 6px;font-size:.65rem;font-weight:800;
  letter-spacing:.5px;white-space:nowrap;border:1px solid transparent;}
.c-barrel{color:var(--gold);font-weight:700}
.c-great{color:var(--green);font-weight:600}
.c-good{color:#27ae60}
.c-warn{color:var(--orange)}
.c-neg{color:var(--red)}
.c-dim{color:#8aa0ae}
.c-blue{color:#1a6699}
/* Arsenal */
.arsenal{display:flex;flex-direction:column;gap:4px;min-width:230px}
.pt-row{display:grid;grid-template-columns:52px 32px 1fr auto;
  gap:5px;align-items:center;font-size:.72rem;line-height:1.3}
.pt-badge{display:inline-block;font-size:.59rem;font-weight:700;letter-spacing:.3px;
  padding:1px 5px;border-radius:3px;text-align:center;background:rgba(255,255,255,.06);}
.pt-pct{color:var(--muted);text-align:right;font-size:.68rem}
.pt-velo{white-space:nowrap;display:flex;align-items:center;gap:2px}
.pt-stuff{color:var(--muted);font-size:.68rem;text-align:right;white-space:nowrap}
.va{color:var(--red) !important;font-weight:700}
.vb{color:var(--blue) !important;font-weight:700}
.vn{color:var(--text)}
.c-gold{color:var(--gold);font-weight:700}
.vd{color:var(--muted)}
.sv{color:#2e6e9e;font-size:.67rem}
.gs{color:#1a5a80}
.empty{text-align:center;padding:48px;color:var(--muted)}
.empty .ico{font-size:2.2rem;margin-bottom:8px}
footer{text-align:center;padding:18px;color:var(--muted);font-size:.69rem;
  border-top:1px solid var(--border);margin-top:40px;}
.ta-section-hdr{font-size:.82rem;font-weight:700;color:var(--muted);
  text-transform:uppercase;letter-spacing:.9px;padding:10px 2px 6px;
  border-bottom:1px solid var(--border);margin-bottom:10px;
  display:flex;align-items:center;gap:7px}
.tab-btn.ta-btn{color:#a07800}
.tab-btn.ta-btn.active{color:var(--gold);border-bottom-color:var(--gold)}
.tab-btn.ta-btn.active .tab-count{background:var(--gold);color:#0f1923}
.tab-btn.lb-btn{color:#1a6699}
.tab-btn.lb-btn.active{color:#155080;border-bottom-color:#1a6699}
.tab-btn.lb-btn.active .tab-count{background:#1a6699;color:#fff}
/* Toggle group (pitcher type / TA view) */
.toggle-group{display:flex;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;width:fit-content;margin-bottom:14px;}
.tgl-btn{background:transparent;border:none;border-right:1px solid var(--border);color:var(--muted);padding:6px 18px;font-size:.82rem;font-weight:600;cursor:pointer;transition:background .15s,color .15s;}
.tgl-btn:last-child{border-right:none}
.tgl-btn.active{background:var(--accent);color:#fff;}
/* Leaderboard */
#lb-panel .note{margin-bottom:9px}
#lb-panel .controls{margin-bottom:11px}
#lb-panel .qual-toggle{display:flex;align-items:center;gap:7px;font-size:.77rem;color:var(--muted);cursor:pointer;user-select:none;}
#lb-panel .qual-toggle input{cursor:pointer;accent-color:var(--accent)}
.lb-th-inv{} /* lower-is-better marker — no visual distinction */
/* Compare Players tab */
.tab-btn.cmp-btn{color:#2e7d32}
.tab-btn.cmp-btn.active{color:#4caf50;border-bottom-color:#4caf50}
.tab-btn.cmp-btn.active .tab-count{background:#4caf50;color:#0f1923}
.cmp-search-wrap{display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap}
.cmp-input-wrap{position:relative;display:inline-block;width:300px}
.cmp-input-wrap input{width:100%;box-sizing:border-box}
.cmp-dropdown{position:absolute;top:100%;left:0;width:100%;background:var(--card);border:1px solid var(--border);
  border-radius:0 0 var(--radius) var(--radius);max-height:240px;overflow-y:auto;z-index:200;
  box-shadow:0 6px 18px rgba(0,0,0,.45)}
.cmp-di{padding:8px 12px;cursor:pointer;font-size:.82rem;border-bottom:1px solid var(--border);
  color:var(--text);display:flex;align-items:center;gap:8px}
.cmp-di:hover,.cmp-di.active{background:var(--card2)}
.cmp-di:last-child{border-bottom:none}
.cmp-remove{background:rgba(231,76,60,.15);border:1px solid rgba(231,76,60,.35);color:#e74c3c;
  border-radius:4px;padding:2px 8px;font-size:.73rem;cursor:pointer;white-space:nowrap;line-height:1.7}
.cmp-remove:hover{background:rgba(231,76,60,.3)}
.cmp-empty{text-align:center;padding:48px 20px;color:var(--muted);font-size:.88rem}
/* Column visibility picker */
.col-vis-hidden{display:none!important}
.col-picker-wrap{position:relative;display:inline-block}
.col-picker-panel{position:absolute;top:calc(100% + 4px);right:0;background:var(--card);
  border:1px solid var(--border);border-radius:var(--radius);padding:10px 12px;
  min-width:230px;z-index:400;box-shadow:0 6px 20px rgba(0,0,0,.5)}
.col-picker-actions{display:flex;gap:8px;margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid var(--border)}
.col-picker-actions button{flex:1;padding:4px 8px;font-size:.74rem;background:var(--card2);
  border:1px solid var(--border);color:var(--text);border-radius:4px;cursor:pointer}
.col-picker-actions button:hover{background:var(--accent);color:#fff;border-color:var(--accent)}
.col-picker-grid{display:grid;grid-template-columns:1fr 1fr;gap:3px 14px}
.col-picker-item{display:flex;align-items:center;gap:5px;font-size:.78rem;color:var(--text);
  cursor:pointer;padding:3px 0;white-space:nowrap}
.col-picker-item input{cursor:pointer;accent-color:var(--accent);flex-shrink:0}
@media(max-width:640px){
  .site-header{padding:11px 13px}.hdr-title{font-size:1rem}
  .tab-panel{padding:13px 8px}.tab-btn{padding:10px 12px;font-size:.78rem}
}
</style>
</head>
<body>

<header class="site-header">
  <div class="hdr-logo">⚾</div>
  <div>
    <div class="hdr-title">MLB Daily Stats <span class="hdr-badge">STATCAST</span></div>
    <div class="hdr-meta">
      <strong>__DATE_DISPLAY__</strong>
      &nbsp;·&nbsp; __N_GAMES__ game(s)
      &nbsp;·&nbsp; Updated __TS__
      &nbsp;·&nbsp; <span id="refresh-status" style="color:var(--muted);font-size:0.85em">⟳ Auto-refresh</span>
    </div>
  </div>
</header>

<div class="tab-bar">
  <button class="tab-btn ta-btn active" onclick="showTab('teamalex',this)">
    👑 <span id="ta-team-name-tab">My Team</span> <span class="tab-count" id="ta-tc">—</span>
  </button>
  <button class="tab-btn" onclick="showTab('gamelog',this)">
    ⚾ Game Log <span class="tab-count" id="gl-tc">—</span>
  </button>
  <button class="tab-btn lb-btn" onclick="showTab('leaderboard',this)">
    📊 Season Leaders <span class="tab-count" id="lb-tc">—</span>
  </button>
</div>

<main>

<!-- ══ GAME LOG ══ -->
<div id="gamelog-panel" class="tab-panel">

  <!-- Sub-tabs: Hitters / Starters / Relievers -->
  <div class="toggle-group" style="margin-bottom:16px">
    <button class="tgl-btn active gl-btn" id="gl-h-btn" onclick="showGameLog('h',this)">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="16" height="16" style="vertical-align:middle;display:inline-block;margin-bottom:2px"><circle cx="4" cy="20" r="2.2" fill="#6B3A2A"/><line x1="5" y1="19" x2="13" y2="11" stroke="#6B3A2A" stroke-width="2.2" stroke-linecap="round"/><line x1="13" y1="11" x2="19" y2="5" stroke="#6B3A2A" stroke-width="5" stroke-linecap="round"/></svg> Hitters <span id="h-tc" style="opacity:.6;font-size:.75em">—</span>
    </button>
    <button class="tgl-btn gl-btn" id="gl-sp-btn" onclick="showGameLog('sp',this)">
      ⚾ Starters <span id="p-sp-tc" style="opacity:.6;font-size:.75em"></span>
    </button>
    <button class="tgl-btn gl-btn" id="gl-rp-btn" onclick="showGameLog('rp',this)">
      🔥 Relievers <span id="p-rp-tc" style="opacity:.6;font-size:.75em"></span>
    </button>
  </div>

  <!-- Hitters sub-panel -->
  <div id="gl-h-section">
    <div class="legend">
      <div class="leg-item"><span class="leg-dot" style="background:#2ecc71"></span>HR = grand slam</div>
    </div>
    <div class="controls">
      <input id="h-search" type="text" placeholder="Search player or team…" oninput="filterH()">
      <span class="row-count" id="h-cnt"></span>
      <span class="sort-hint">Click headers to sort</span>
    </div>
    <div class="table-wrap">
      <table id="h-tbl">
        <thead><tr>
          <th class="sortable"   data-k="name"      onclick="srtH(this,'name')">Player</th>
          <th class="sortable"   data-col="team" data-k="team"      onclick="srtH(this,'team')">Team</th>
          <th class="sortable"   data-col="opp" data-k="opp"       onclick="srtH(this,'opp')">Opp</th>
          <th class="sortable r" data-col="h" data-k="h"          onclick="srtH(this,'h')">H/AB</th>
          <th class="sortable r" data-col="r" data-k="r"          onclick="srtH(this,'r')">R</th>
          <th class="sortable r" data-col="hr" data-k="hr"        onclick="srtH(this,'hr')">HR</th>
          <th class="sortable r" data-col="rbi" data-k="rbi"      onclick="srtH(this,'rbi')">RBI</th>
          <th class="sortable r" data-col="k" data-k="k"          onclick="srtH(this,'k')">K</th>
          <th class="sortable r" data-col="bb" data-k="bb"        onclick="srtH(this,'bb')">BB</th>
          <th class="sortable r" data-col="sb" data-k="sb"        onclick="srtH(this,'sb')">SB</th>
          <th class="sortable r" data-col="hard_hits" data-k="hard_hits" onclick="srtH(this,'hard_hits')">Hard Hits</th>
          <th class="sortable r" data-col="barrels" data-k="barrels"   onclick="srtH(this,'barrels')">Barrels</th>
          <th class="sortable r" data-col="max_ev" data-k="max_ev"    onclick="srtH(this,'max_ev')">Max EV</th>
        </tr></thead>
        <tbody id="h-body"></tbody>
      </table>
    </div>
  </div>

  <!-- Pitchers sub-panel (starters + relievers toggled by gl-btn) -->
  <div id="gl-pit-section" style="display:none">
    <div class="note">
      ⓘ &nbsp;<strong>Stuff+</strong> and <strong>Loc+</strong> are per-game values from FanGraphs (season avg when unavailable).
      Arsenal: game velocity <span class="vd">(season avg)</span> —
      fastball shown in <span style="color:var(--red);font-weight:700">red</span> if &gt;1 mph above season avg, in <span style="color:var(--blue);font-weight:700">blue</span> if &gt;1 mph below.
    </div>
    <div class="controls">
      <input id="p-search" type="text" placeholder="Search pitcher or team…" oninput="filterP()">
      <span class="row-count" id="p-cnt"></span>
      <span class="sort-hint">Click headers to sort</span>
    </div>

    <!-- Starters table -->
    <div id="p-sp-wrap" class="table-wrap">
      <table id="sp-tbl">
        <thead><tr>
          <th class="sortable"      data-k="name"          onclick="srtSP(this,'name')">Pitcher</th>
          <th class="sortable"      data-col="team" data-k="team"          onclick="srtSP(this,'team')">Team</th>
          <th class="sortable"      data-col="opp" data-k="opp"           onclick="srtSP(this,'opp')">Opp</th>
          <th class="sortable r"    data-col="ip_float" data-k="ip_float"      onclick="srtSP(this,'ip_float')">IP</th>
          <th class="sortable r"    data-col="hits" data-k="hits"          onclick="srtSP(this,'hits')">H</th>
          <th class="sortable r"    data-col="r" data-k="r"             onclick="srtSP(this,'r')">R</th>
          <th class="sortable r"    data-col="bb" data-k="bb"            onclick="srtSP(this,'bb')">BB</th>
          <th class="sortable r"    data-col="k" data-k="k"             onclick="srtSP(this,'k')">K</th>
          <th class="sortable r"    data-col="w" data-k="w"             onclick="srtSP(this,'w')">W</th>
          <th class="sortable r"    data-col="whiffs" data-k="whiffs"        onclick="srtSP(this,'whiffs')">Whiffs</th>
          <th class="sortable r"    data-col="hard_hits" data-k="hard_hits"     onclick="srtSP(this,'hard_hits')">Hard Hits</th>
          <th class="sortable r"    data-col="barrels" data-k="barrels"       onclick="srtSP(this,'barrels')">Barrels</th>
          <th class="sortable r sc" data-col="stuff_plus" data-k="stuff_plus"    onclick="srtSP(this,'stuff_plus')">Stuff+</th>
          <th class="sortable r sc" data-col="location_plus" data-k="location_plus" onclick="srtSP(this,'location_plus')">Loc+</th>
          <th>Arsenal</th>
        </tr></thead>
        <tbody id="sp-body"></tbody>
      </table>
    </div>

    <!-- Relievers table (hidden until RP sub-tab selected) -->
    <div id="p-rp-wrap" class="table-wrap" style="display:none">
      <table id="rp-tbl">
        <thead><tr>
          <th class="sortable"      data-k="name"          onclick="srtRP(this,'name')">Pitcher</th>
          <th class="sortable"      data-col="team" data-k="team"          onclick="srtRP(this,'team')">Team</th>
          <th class="sortable"      data-col="opp" data-k="opp"           onclick="srtRP(this,'opp')">Opp</th>
          <th class="sortable r"    data-col="ip_float" data-k="ip_float"      onclick="srtRP(this,'ip_float')">IP</th>
          <th class="sortable r"    data-col="hits" data-k="hits"          onclick="srtRP(this,'hits')">H</th>
          <th class="sortable r"    data-col="r" data-k="r"             onclick="srtRP(this,'r')">R</th>
          <th class="sortable r"    data-col="bb" data-k="bb"            onclick="srtRP(this,'bb')">BB</th>
          <th class="sortable r"    data-col="k" data-k="k"             onclick="srtRP(this,'k')">K</th>
          <th class="sortable r"    data-col="sv" data-k="sv"            onclick="srtRP(this,'sv')">SV</th>
          <th class="sortable r"    data-col="hld" data-k="hld"           onclick="srtRP(this,'hld')">HLD</th>
          <th class="sortable r"    data-col="bs" data-k="bs"            onclick="srtRP(this,'bs')">BS</th>
          <th class="sortable r"    data-col="w" data-k="w"             onclick="srtRP(this,'w')">W</th>
          <th class="sortable r"    data-col="whiffs" data-k="whiffs"        onclick="srtRP(this,'whiffs')">Whiffs</th>
          <th class="sortable r"    data-col="hard_hits" data-k="hard_hits"     onclick="srtRP(this,'hard_hits')">Hard Hits</th>
          <th class="sortable r"    data-col="barrels" data-k="barrels"       onclick="srtRP(this,'barrels')">Barrels</th>
          <th class="sortable r sc" data-col="stuff_plus" data-k="stuff_plus"    onclick="srtRP(this,'stuff_plus')">Stuff+</th>
          <th class="sortable r sc" data-col="location_plus" data-k="location_plus" onclick="srtRP(this,'location_plus')">Loc+</th>
          <th>Arsenal</th>
        </tr></thead>
        <tbody id="rp-body"></tbody>
      </table>
    </div>
  </div>

</div>

<!-- ══ TEAM ALEX ══ -->
<div id="teamalex-panel" class="tab-panel active">
  <div style="display:flex;align-items:center;gap:11px;margin-bottom:18px;flex-wrap:wrap">
    <span style="font-size:1.6rem">👑</span>
    <div style="flex:1">
      <div style="font-size:1.05rem;font-weight:800;color:var(--gold);display:flex;align-items:center;gap:6px">
        <span id="ta-team-name-hdr">My Team</span>
        <button id="ta-name-edit-btn" onclick="startTeamNameEdit()" title="Edit team name" style="display:none;background:none;border:none;color:var(--muted);cursor:pointer;padding:0;font-size:.85rem;opacity:.7;line-height:1">✏️</button>
      </div>
      <div id="ta-roster-count" style="font-size:.72rem;color:var(--muted)">24-player roster</div>
    </div>
    <div id="ta-auth-area" style="display:flex;gap:8px;align-items:center"></div>
  </div>

  <!-- Hitters section with Yesterday / Season toggle -->
  <div class="ta-section-hdr"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="16" height="16" style="vertical-align:middle;display:inline-block;margin-bottom:2px"><circle cx="4" cy="20" r="2.2" fill="#6B3A2A"/><line x1="5" y1="19" x2="13" y2="11" stroke="#6B3A2A" stroke-width="2.2" stroke-linecap="round"/><line x1="13" y1="11" x2="19" y2="5" stroke="#6B3A2A" stroke-width="5" stroke-linecap="round"/></svg> Hitters <span class="tab-count" id="ta-h-tc">—</span></div>
  <div class="toggle-group" style="margin-bottom:10px">
    <button class="tgl-btn active" id="ta-h-yday-btn" onclick="showTAHView('yday',this)">Yesterday</button>
    <button class="tgl-btn" id="ta-h-season-btn" onclick="showTAHView('season',this)">Season</button>
  </div>

  <!-- Yesterday game stats table -->
  <div id="ta-h-yday-wrap" class="table-wrap" style="margin-bottom:24px">
    <table id="ta-h-tbl">
      <thead><tr>
        <th class="sortable"   data-k="name"      onclick="srtTA(this,'h','name')">Player</th>
        <th class="sortable"   data-col="team" data-k="team"      onclick="srtTA(this,'h','team')">Team</th>
        <th class="sortable"   data-col="opp" data-k="opp"       onclick="srtTA(this,'h','opp')">Opp</th>
        <th class="sortable r" data-col="h" data-k="h"          onclick="srtTA(this,'h','h')">H/AB</th>
        <th class="sortable r" data-col="r" data-k="r"          onclick="srtTA(this,'h','r')">R</th>
        <th class="sortable r" data-col="hr" data-k="hr"        onclick="srtTA(this,'h','hr')">HR</th>
        <th class="sortable r" data-col="rbi" data-k="rbi"      onclick="srtTA(this,'h','rbi')">RBI</th>
        <th class="sortable r" data-col="k" data-k="k"          onclick="srtTA(this,'h','k')">K</th>
        <th class="sortable r" data-col="bb" data-k="bb"        onclick="srtTA(this,'h','bb')">BB</th>
        <th class="sortable r" data-col="sb" data-k="sb"        onclick="srtTA(this,'h','sb')">SB</th>
        <th class="sortable r" data-col="hard_hits" data-k="hard_hits" onclick="srtTA(this,'h','hard_hits')">Hard Hits</th>
        <th class="sortable r" data-col="barrels" data-k="barrels"   onclick="srtTA(this,'h','barrels')">Barrels</th>
        <th class="sortable r" data-col="max_ev" data-k="max_ev"    onclick="srtTA(this,'h','max_ev')">Max EV</th>
      </tr></thead>
      <tbody id="ta-h-body"></tbody>
    </table>
  </div>

  <!-- Season stats table (hidden by default) -->
  <div id="ta-h-season-wrap" class="table-wrap" style="display:none;margin-bottom:24px">
    <table id="ta-lb-tbl">
      <thead><tr>
        <th class="sortable"   data-k="name"           onclick="srtTALB(this,'name')">Player</th>
        <th class="sortable r" data-col="pa" data-k="pa"             onclick="srtTALB(this,'pa')">PA</th>
        <th class="sortable r" data-col="r" data-k="r"              onclick="srtTALB(this,'r')">R</th>
        <th class="sortable r" data-col="hr" data-k="hr"             onclick="srtTALB(this,'hr')">HR</th>
        <th class="sortable r" data-col="rbi" data-k="rbi"            onclick="srtTALB(this,'rbi')">RBI</th>
        <th class="sortable r" data-col="sb" data-k="sb"             onclick="srtTALB(this,'sb')">SB</th>
        <th class="sortable r" data-col="avg" data-k="avg"            onclick="srtTALB(this,'avg')">AVG</th>
        <th class="sortable r" data-col="obp" data-k="obp"            onclick="srtTALB(this,'obp')">OBP</th>
        <th class="sortable r" data-col="woba" data-k="woba"           onclick="srtTALB(this,'woba')">wOBA</th>
        <th class="sortable r" data-col="xwoba" data-k="xwoba"          onclick="srtTALB(this,'xwoba')">xwOBA</th>
        <th class="sortable r lb-th-inv" data-col="chase_pct" data-k="chase_pct"    onclick="srtTALB(this,'chase_pct')">Chase%</th>
        <th class="sortable r lb-th-inv" data-col="whiff_pct" data-k="whiff_pct"    onclick="srtTALB(this,'whiff_pct')">Whiff%</th>
        <th class="sortable r lb-th-inv" data-col="k_pct" data-k="k_pct"        onclick="srtTALB(this,'k_pct')">K%</th>
        <th class="sortable r lb-th-inv" data-col="so" data-k="so"           onclick="srtTALB(this,'so')">SO</th>
        <th class="sortable r" data-col="bb_pct" data-k="bb_pct"         onclick="srtTALB(this,'bb_pct')">BB%</th>
        <th class="sortable r" data-col="hard_hit_pct" data-k="hard_hit_pct"   onclick="srtTALB(this,'hard_hit_pct')">Hard Hit%</th>
        <th class="sortable r" data-col="barrel_pct" data-k="barrel_pct"     onclick="srtTALB(this,'barrel_pct')">Barrel%</th>
        <th class="sortable r" data-col="barrels" data-k="barrels"        onclick="srtTALB(this,'barrels')">Barrels</th>
        <th class="sortable r" data-col="sweet_spot_pct" data-k="sweet_spot_pct" onclick="srtTALB(this,'sweet_spot_pct')">Swt Spot%</th>
        <th class="sortable r" data-col="avg_ev" data-k="avg_ev"         onclick="srtTALB(this,'avg_ev')">Avg EV</th>
        <th class="sortable r" data-col="max_ev" data-k="max_ev"         onclick="srtTALB(this,'max_ev')">Max EV</th>
        <th class="sortable r" data-col="bat_speed" data-k="bat_speed"      onclick="srtTALB(this,'bat_speed')">Bat Spd</th>
        <th class="sortable r" data-col="sprint_speed" data-k="sprint_speed"   onclick="srtTALB(this,'sprint_speed')">Sprt Spd</th>
        <th class="sortable r" data-col="war" data-k="war"               onclick="srtTALB(this,'war')">fWAR</th>
      </tr></thead>
      <tbody id="ta-lb-body"></tbody>
    </table>
  </div>

  <div class="ta-section-hdr">⚾ Starting Pitchers <span class="tab-count" id="ta-sp-tc">—</span></div>
  <div class="toggle-group" style="margin-bottom:10px">
    <button class="tgl-btn active" id="ta-sp-yday-btn" onclick="showTASPView('yday',this)">Yesterday</button>
    <button class="tgl-btn" id="ta-sp-season-btn" onclick="showTASPView('season',this)">Season</button>
  </div>
  <!-- Yesterday game stats -->
  <div id="ta-sp-yday-wrap" class="table-wrap" style="margin-bottom:24px">
    <table id="ta-sp-tbl">
      <thead><tr>
        <th class="sortable"      data-k="name"          onclick="srtTA(this,'sp','name')">Pitcher</th>
        <th class="sortable"      data-col="team" data-k="team"          onclick="srtTA(this,'sp','team')">Team</th>
        <th class="sortable"      data-col="opp" data-k="opp"           onclick="srtTA(this,'sp','opp')">Opp</th>
        <th class="sortable r"    data-col="ip_float" data-k="ip_float"      onclick="srtTA(this,'sp','ip_float')">IP</th>
        <th class="sortable r"    data-col="hits" data-k="hits"          onclick="srtTA(this,'sp','hits')">H</th>
        <th class="sortable r"    data-col="r" data-k="r"             onclick="srtTA(this,'sp','r')">R</th>
        <th class="sortable r"    data-col="bb" data-k="bb"            onclick="srtTA(this,'sp','bb')">BB</th>
        <th class="sortable r"    data-col="k" data-k="k"             onclick="srtTA(this,'sp','k')">K</th>
        <th class="sortable r"    data-col="w" data-k="w"             onclick="srtTA(this,'sp','w')">W</th>
        <th class="sortable r"    data-col="whiffs" data-k="whiffs"        onclick="srtTA(this,'sp','whiffs')">Whiffs</th>
        <th class="sortable r"    data-col="hard_hits" data-k="hard_hits"     onclick="srtTA(this,'sp','hard_hits')">Hard Hits</th>
        <th class="sortable r"    data-col="barrels" data-k="barrels"       onclick="srtTA(this,'sp','barrels')">Barrels</th>
        <th class="sortable r sc" data-col="stuff_plus" data-k="stuff_plus"    onclick="srtTA(this,'sp','stuff_plus')">Stuff+</th>
        <th class="sortable r sc" data-col="location_plus" data-k="location_plus" onclick="srtTA(this,'sp','location_plus')">Loc+</th>
        <th>Arsenal</th>
      </tr></thead>
      <tbody id="ta-sp-body"></tbody>
    </table>
  </div>
  <!-- Season stats -->
  <div id="ta-sp-season-wrap" class="table-wrap" style="display:none;margin-bottom:24px">
    <table id="ta-sp-lb-tbl">
      <thead><tr>
        <th class="sortable"   data-k="name"         onclick="srtTASPLB(this,'name')">Pitcher</th>
        <th class="sortable r" data-col="ip_f" data-k="ip_f"         onclick="srtTASPLB(this,'ip_f')">IP</th>
        <th class="sortable r" data-col="w" data-k="w"            onclick="srtTASPLB(this,'w')">W</th>
        <th class="sortable r lb-th-inv" data-col="era" data-k="era"  onclick="srtTASPLB(this,'era')">ERA</th>
        <th class="sortable r lb-th-inv" data-col="whip" data-k="whip" onclick="srtTASPLB(this,'whip')">WHIP</th>
        <th class="sortable r" data-col="k" data-k="k"            onclick="srtTASPLB(this,'k')">K</th>
        <th class="sortable r lb-th-inv" data-col="xera" data-k="xera" onclick="srtTASPLB(this,'xera')">xERA</th>
        <th class="sortable r lb-th-inv" data-col="siera" data-k="siera" onclick="srtTASPLB(this,'siera')">SIERA</th>
        <th class="sortable r" data-col="stuff_plus" data-k="stuff_plus"   onclick="srtTASPLB(this,'stuff_plus')">Stf+</th>
        <th class="sortable r" data-col="loc_plus" data-k="loc_plus"     onclick="srtTASPLB(this,'loc_plus')">Loc+</th>
        <th class="sortable r" data-col="k_bb_pct" data-k="k_bb_pct"    onclick="srtTASPLB(this,'k_bb_pct')">K-BB%</th>
        <th class="sortable r" data-col="k_pct" data-k="k_pct"        onclick="srtTASPLB(this,'k_pct')">K%</th>
        <th class="sortable r lb-th-inv" data-col="bb_pct" data-k="bb_pct" onclick="srtTASPLB(this,'bb_pct')">BB%</th>
        <th class="sortable r" data-col="chase_pct" data-k="chase_pct"    onclick="srtTASPLB(this,'chase_pct')">Chase%</th>
        <th class="sortable r" data-col="whiff_pct" data-k="whiff_pct"    onclick="srtTASPLB(this,'whiff_pct')">Whiff%</th>
        <th class="sortable r lb-th-inv" data-col="barrel_pct" data-k="barrel_pct"   onclick="srtTASPLB(this,'barrel_pct')">Barrel%</th>
        <th class="sortable r lb-th-inv" data-col="hard_hit_pct" data-k="hard_hit_pct" onclick="srtTASPLB(this,'hard_hit_pct')">Hard Hit%</th>
        <th class="sortable r" data-col="gb_pct" data-k="gb_pct"       onclick="srtTASPLB(this,'gb_pct')">GB%</th>
        <th class="sortable r lb-th-inv" data-col="woba" data-k="woba"  onclick="srtTASPLB(this,'woba')">wOBA</th>
        <th class="sortable r lb-th-inv" data-col="xwoba" data-k="xwoba" onclick="srtTASPLB(this,'xwoba')">xwOBA</th>
        <th class="sortable r lb-th-inv" data-col="avg_ev" data-k="avg_ev" onclick="srtTASPLB(this,'avg_ev')">Avg EV</th>
        <th class="sortable r" data-col="fb_velo" data-k="fb_velo"      onclick="srtTASPLB(this,'fb_velo')">FB Velo</th>
        <th class="sortable r" data-col="war" data-k="war"            onclick="srtTASPLB(this,'war')">fWAR</th>
      </tr></thead>
      <tbody id="ta-sp-lb-body"></tbody>
    </table>
  </div>

  <div class="ta-section-hdr">🔥 Relief Pitchers <span class="tab-count" id="ta-rp-tc">—</span></div>
  <div class="toggle-group" style="margin-bottom:10px">
    <button class="tgl-btn active" id="ta-rp-yday-btn" onclick="showTARPView('yday',this)">Yesterday</button>
    <button class="tgl-btn" id="ta-rp-season-btn" onclick="showTARPView('season',this)">Season</button>
  </div>
  <!-- Yesterday game stats -->
  <div id="ta-rp-yday-wrap" class="table-wrap" style="margin-bottom:24px">
    <table id="ta-rp-tbl">
      <thead><tr>
        <th class="sortable"      data-k="name"          onclick="srtTA(this,'rp','name')">Pitcher</th>
        <th class="sortable"      data-col="team" data-k="team"          onclick="srtTA(this,'rp','team')">Team</th>
        <th class="sortable"      data-col="opp" data-k="opp"           onclick="srtTA(this,'rp','opp')">Opp</th>
        <th class="sortable r"    data-col="ip_float" data-k="ip_float"      onclick="srtTA(this,'rp','ip_float')">IP</th>
        <th class="sortable r"    data-col="hits" data-k="hits"          onclick="srtTA(this,'rp','hits')">H</th>
        <th class="sortable r"    data-col="r" data-k="r"             onclick="srtTA(this,'rp','r')">R</th>
        <th class="sortable r"    data-col="bb" data-k="bb"            onclick="srtTA(this,'rp','bb')">BB</th>
        <th class="sortable r"    data-col="k" data-k="k"             onclick="srtTA(this,'rp','k')">K</th>
        <th class="sortable r"    data-col="sv" data-k="sv"            onclick="srtTA(this,'rp','sv')">SV</th>
        <th class="sortable r"    data-col="hld" data-k="hld"           onclick="srtTA(this,'rp','hld')">HLD</th>
        <th class="sortable r"    data-col="bs" data-k="bs"            onclick="srtTA(this,'rp','bs')">BS</th>
        <th class="sortable r"    data-col="w" data-k="w"             onclick="srtTA(this,'rp','w')">W</th>
        <th class="sortable r"    data-col="whiffs" data-k="whiffs"        onclick="srtTA(this,'rp','whiffs')">Whiffs</th>
        <th class="sortable r"    data-col="hard_hits" data-k="hard_hits"     onclick="srtTA(this,'rp','hard_hits')">Hard Hits</th>
        <th class="sortable r"    data-col="barrels" data-k="barrels"       onclick="srtTA(this,'rp','barrels')">Barrels</th>
        <th class="sortable r sc" data-col="stuff_plus" data-k="stuff_plus"    onclick="srtTA(this,'rp','stuff_plus')">Stuff+</th>
        <th class="sortable r sc" data-col="location_plus" data-k="location_plus" onclick="srtTA(this,'rp','location_plus')">Loc+</th>
        <th>Arsenal</th>
      </tr></thead>
      <tbody id="ta-rp-body"></tbody>
    </table>
  </div>
  <!-- Season stats -->
  <div id="ta-rp-season-wrap" class="table-wrap" style="display:none;margin-bottom:24px">
    <table id="ta-rp-lb-tbl">
      <thead><tr>
        <th class="sortable"   data-k="name"         onclick="srtTARPLB(this,'name')">Pitcher</th>
        <th class="sortable r" data-col="ip_f" data-k="ip_f"         onclick="srtTARPLB(this,'ip_f')">IP</th>
        <th class="sortable r" data-col="w" data-k="w"            onclick="srtTARPLB(this,'w')">W</th>
        <th class="sortable r" data-col="sv" data-k="sv"           onclick="srtTARPLB(this,'sv')">SV/SVO</th>
        <th class="sortable r" data-col="hld" data-k="hld"          onclick="srtTARPLB(this,'hld')">HLD</th>
        <th class="sortable r" data-col="gm_li" data-k="gm_li"       onclick="srtTARPLB(this,'gm_li')">gmLI</th>
        <th class="sortable r lb-th-inv" data-col="era" data-k="era"  onclick="srtTARPLB(this,'era')">ERA</th>
        <th class="sortable r lb-th-inv" data-col="whip" data-k="whip" onclick="srtTARPLB(this,'whip')">WHIP</th>
        <th class="sortable r" data-col="k" data-k="k"            onclick="srtTARPLB(this,'k')">K</th>
        <th class="sortable r lb-th-inv" data-col="xera" data-k="xera" onclick="srtTARPLB(this,'xera')">xERA</th>
        <th class="sortable r lb-th-inv" data-col="siera" data-k="siera" onclick="srtTARPLB(this,'siera')">SIERA</th>
        <th class="sortable r" data-col="stuff_plus" data-k="stuff_plus"   onclick="srtTARPLB(this,'stuff_plus')">Stf+</th>
        <th class="sortable r" data-col="loc_plus" data-k="loc_plus"     onclick="srtTARPLB(this,'loc_plus')">Loc+</th>
        <th class="sortable r" data-col="k_bb_pct" data-k="k_bb_pct"    onclick="srtTARPLB(this,'k_bb_pct')">K-BB%</th>
        <th class="sortable r" data-col="k_pct" data-k="k_pct"        onclick="srtTARPLB(this,'k_pct')">K%</th>
        <th class="sortable r lb-th-inv" data-col="bb_pct" data-k="bb_pct" onclick="srtTARPLB(this,'bb_pct')">BB%</th>
        <th class="sortable r" data-col="chase_pct" data-k="chase_pct"    onclick="srtTARPLB(this,'chase_pct')">Chase%</th>
        <th class="sortable r" data-col="whiff_pct" data-k="whiff_pct"    onclick="srtTARPLB(this,'whiff_pct')">Whiff%</th>
        <th class="sortable r lb-th-inv" data-col="barrel_pct" data-k="barrel_pct"   onclick="srtTARPLB(this,'barrel_pct')">Barrel%</th>
        <th class="sortable r lb-th-inv" data-col="hard_hit_pct" data-k="hard_hit_pct" onclick="srtTARPLB(this,'hard_hit_pct')">Hard Hit%</th>
        <th class="sortable r" data-col="gb_pct" data-k="gb_pct"       onclick="srtTARPLB(this,'gb_pct')">GB%</th>
        <th class="sortable r lb-th-inv" data-col="woba" data-k="woba"  onclick="srtTARPLB(this,'woba')">wOBA</th>
        <th class="sortable r lb-th-inv" data-col="xwoba" data-k="xwoba" onclick="srtTARPLB(this,'xwoba')">xwOBA</th>
        <th class="sortable r lb-th-inv" data-col="avg_ev" data-k="avg_ev" onclick="srtTARPLB(this,'avg_ev')">Avg EV</th>
        <th class="sortable r" data-col="fb_velo" data-k="fb_velo"      onclick="srtTARPLB(this,'fb_velo')">FB Velo</th>
      </tr></thead>
      <tbody id="ta-rp-lb-body"></tbody>
    </table>
  </div>
  <div class="note" style="margin-top:14px">
    Yesterday view: only roster members who played yesterday. Season view: all roster members with stats. Season cell colors = league rank among all qualified pitchers.
  </div>
</div>

<!-- ══ SEASON LEADERBOARD ══ -->
<div id="leaderboard-panel" class="tab-panel">
  <div class="toggle-group" style="margin-bottom:14px">
    <button class="tgl-btn active" onclick="showLBType('h',this)"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="16" height="16" style="vertical-align:middle;display:inline-block;margin-bottom:2px"><circle cx="4" cy="20" r="2.2" fill="#6B3A2A"/><line x1="5" y1="19" x2="13" y2="11" stroke="#6B3A2A" stroke-width="2.2" stroke-linecap="round"/><line x1="13" y1="11" x2="19" y2="5" stroke="#6B3A2A" stroke-width="5" stroke-linecap="round"/></svg> Hitters</button>
    <button class="tgl-btn" onclick="showLBType('sp',this)">⚾ SP</button>
    <button class="tgl-btn" onclick="showLBType('rp',this)">🔥 RP</button>
    <button class="tgl-btn" onclick="showLBType('cmp',this)">🔍 Compare</button>
  </div>

  <!-- ── Hitters view ── -->
  <div id="lb-h-wrap">
    <div class="note">
      ⓘ &nbsp;Season batting leaderboard — FanGraphs + Baseball Savant.
      <strong>Default view:</strong> qualified hitters only (≥3.1 PA/team game).
      Use search to find any player.
      Cell colors = league rank among qualified hitters:
      <span style="color:#f0c040;font-weight:700">Gold</span> = #1 &nbsp;·&nbsp;
      <span style="color:#c0392b;font-weight:700">Dark red</span> = top &nbsp;·&nbsp;
      <span style="color:#1a3a8a;font-weight:700">Dark blue</span> = bottom.
    </div>
    <div class="controls">
      <input id="lb-search" type="text" placeholder="Search any player or team…" oninput="filterLB()">
      <label class="qual-toggle" id="lb-qual-lbl">
        <input type="checkbox" id="lb-qual-chk" checked onchange="filterLB()"> Qualified only
      </label>
      <span class="row-count" id="lb-cnt"></span>
      <span class="sort-hint">Click headers to sort</span>
      <div class="col-picker-wrap">
        <button class="tgl-btn col-picker-btn" onclick="toggleColPicker('h',this)" style="font-size:.74rem;padding:5px 10px;margin-left:auto">⚙ Columns ▾</button>
        <div id="col-picker-h" class="col-picker-panel" style="display:none"></div>
      </div>
    </div>
    <div class="table-wrap">
      <table id="lb-tbl">
        <thead><tr>
          <th class="sortable"   data-k="name"           onclick="srtLB(this,'name')">Player</th>
          <th>Team</th>
          <th class="sortable r" data-col="pa" data-k="pa"             onclick="srtLB(this,'pa')">PA</th>
          <th class="sortable r" data-col="r" data-k="r"              onclick="srtLB(this,'r')">R</th>
          <th class="sortable r" data-col="hr" data-k="hr"             onclick="srtLB(this,'hr')">HR</th>
          <th class="sortable r" data-col="rbi" data-k="rbi"            onclick="srtLB(this,'rbi')">RBI</th>
          <th class="sortable r" data-col="sb" data-k="sb"             onclick="srtLB(this,'sb')">SB</th>
          <th class="sortable r" data-col="avg" data-k="avg"            onclick="srtLB(this,'avg')">AVG</th>
          <th class="sortable r" data-col="obp" data-k="obp"            onclick="srtLB(this,'obp')">OBP</th>
          <th class="sortable r" data-col="woba" data-k="woba"           onclick="srtLB(this,'woba')">wOBA</th>
          <th class="sortable r" data-col="xwoba" data-k="xwoba"          onclick="srtLB(this,'xwoba')">xwOBA</th>
          <th class="sortable r lb-th-inv" data-col="chase_pct" data-k="chase_pct"    onclick="srtLB(this,'chase_pct')">Chase%</th>
          <th class="sortable r lb-th-inv" data-col="whiff_pct" data-k="whiff_pct"    onclick="srtLB(this,'whiff_pct')">Whiff%</th>
          <th class="sortable r lb-th-inv" data-col="k_pct" data-k="k_pct"        onclick="srtLB(this,'k_pct')">K%</th>
          <th class="sortable r lb-th-inv" data-col="so" data-k="so"           onclick="srtLB(this,'so')">SO</th>
          <th class="sortable r" data-col="bb_pct" data-k="bb_pct"         onclick="srtLB(this,'bb_pct')">BB%</th>
          <th class="sortable r" data-col="hard_hit_pct" data-k="hard_hit_pct"   onclick="srtLB(this,'hard_hit_pct')">Hard Hit%</th>
          <th class="sortable r" data-col="barrel_pct" data-k="barrel_pct"     onclick="srtLB(this,'barrel_pct')">Barrel%</th>
          <th class="sortable r" data-col="barrels" data-k="barrels"        onclick="srtLB(this,'barrels')">Barrels</th>
          <th class="sortable r" data-col="sweet_spot_pct" data-k="sweet_spot_pct" onclick="srtLB(this,'sweet_spot_pct')">Swt Spot%</th>
          <th class="sortable r" data-col="avg_ev" data-k="avg_ev"         onclick="srtLB(this,'avg_ev')">Avg EV</th>
          <th class="sortable r" data-col="max_ev" data-k="max_ev"         onclick="srtLB(this,'max_ev')">Max EV</th>
          <th class="sortable r" data-col="bat_speed" data-k="bat_speed"      onclick="srtLB(this,'bat_speed')">Bat Spd</th>
          <th class="sortable r" data-col="sprint_speed" data-k="sprint_speed"   onclick="srtLB(this,'sprint_speed')">Sprt Spd</th>
          <th class="sortable r" data-col="war" data-k="war"               onclick="srtLB(this,'war')">fWAR</th>
        </tr></thead>
        <tbody id="lb-body"></tbody>
      </table>
    </div>
  </div>

  <!-- ── SP view ── -->
  <div id="lb-sp-wrap" style="display:none">
    <div class="note">
      ⓘ &nbsp;Season SP leaderboard — FanGraphs + Baseball Savant.
      <strong>Default view:</strong> qualified starters only (≥1 IP/team game).
      Cell colors = league rank among qualified starters.
    </div>
    <div class="controls">
      <input id="lb-sp-search" type="text" placeholder="Search pitcher or team…" oninput="filterLBSP()">
      <label class="qual-toggle" id="lb-sp-qual-lbl">
        <input type="checkbox" id="lb-sp-qual-chk" checked onchange="filterLBSP()"> Qualified only
      </label>
      <span class="row-count" id="lb-sp-cnt"></span>
      <span class="sort-hint">Click headers to sort</span>
      <div class="col-picker-wrap">
        <button class="tgl-btn col-picker-btn" onclick="toggleColPicker('sp',this)" style="font-size:.74rem;padding:5px 10px;margin-left:auto">⚙ Columns ▾</button>
        <div id="col-picker-sp" class="col-picker-panel" style="display:none"></div>
      </div>
    </div>
    <div class="table-wrap">
      <table id="lb-sp-tbl">
        <thead><tr>
          <th class="sortable"   data-k="name"         onclick="srtLBSP(this,'name')">Pitcher</th>
          <th>Team</th>
          <th class="sortable r" data-col="ip_f" data-k="ip_f"         onclick="srtLBSP(this,'ip_f')">IP</th>
          <th class="sortable r" data-col="w" data-k="w"            onclick="srtLBSP(this,'w')">W</th>
          <th class="sortable r lb-th-inv" data-col="era" data-k="era"  onclick="srtLBSP(this,'era')">ERA</th>
          <th class="sortable r lb-th-inv" data-col="whip" data-k="whip" onclick="srtLBSP(this,'whip')">WHIP</th>
          <th class="sortable r" data-col="k" data-k="k"            onclick="srtLBSP(this,'k')">K</th>
          <th class="sortable r lb-th-inv" data-col="xera" data-k="xera" onclick="srtLBSP(this,'xera')">xERA</th>
          <th class="sortable r lb-th-inv" data-col="siera" data-k="siera" onclick="srtLBSP(this,'siera')">SIERA</th>
          <th class="sortable r" data-col="stuff_plus" data-k="stuff_plus"   onclick="srtLBSP(this,'stuff_plus')">Stf+</th>
          <th class="sortable r" data-col="loc_plus" data-k="loc_plus"     onclick="srtLBSP(this,'loc_plus')">Loc+</th>
          <th class="sortable r" data-col="k_bb_pct" data-k="k_bb_pct"    onclick="srtLBSP(this,'k_bb_pct')">K-BB%</th>
          <th class="sortable r" data-col="k_pct" data-k="k_pct"        onclick="srtLBSP(this,'k_pct')">K%</th>
          <th class="sortable r lb-th-inv" data-col="bb_pct" data-k="bb_pct" onclick="srtLBSP(this,'bb_pct')">BB%</th>
          <th class="sortable r" data-col="chase_pct" data-k="chase_pct"    onclick="srtLBSP(this,'chase_pct')">Chase%</th>
          <th class="sortable r" data-col="whiff_pct" data-k="whiff_pct"    onclick="srtLBSP(this,'whiff_pct')">Whiff%</th>
          <th class="sortable r lb-th-inv" data-col="barrel_pct" data-k="barrel_pct"   onclick="srtLBSP(this,'barrel_pct')">Barrel%</th>
          <th class="sortable r lb-th-inv" data-col="hard_hit_pct" data-k="hard_hit_pct" onclick="srtLBSP(this,'hard_hit_pct')">Hard Hit%</th>
          <th class="sortable r" data-col="gb_pct" data-k="gb_pct"       onclick="srtLBSP(this,'gb_pct')">GB%</th>
          <th class="sortable r lb-th-inv" data-col="woba" data-k="woba"  onclick="srtLBSP(this,'woba')">wOBA</th>
          <th class="sortable r lb-th-inv" data-col="xwoba" data-k="xwoba" onclick="srtLBSP(this,'xwoba')">xwOBA</th>
          <th class="sortable r lb-th-inv" data-col="avg_ev" data-k="avg_ev" onclick="srtLBSP(this,'avg_ev')">Avg EV</th>
          <th class="sortable r" data-col="fb_velo" data-k="fb_velo"      onclick="srtLBSP(this,'fb_velo')">FB Velo</th>
          <th class="sortable r" data-col="war" data-k="war"            onclick="srtLBSP(this,'war')">fWAR</th>
        </tr></thead>
        <tbody id="lb-sp-body"></tbody>
      </table>
    </div>
  </div>

  <!-- ── RP view ── -->
  <div id="lb-rp-wrap" style="display:none">
    <div class="note">
      ⓘ &nbsp;Season RP leaderboard — FanGraphs + Baseball Savant.
      <strong>Default view:</strong> qualified relievers only (≥0.5 IP/team game).
      Cell colors = league rank among qualified relievers.
    </div>
    <div class="controls">
      <input id="lb-rp-search" type="text" placeholder="Search pitcher or team…" oninput="filterLBRP()">
      <label class="qual-toggle" id="lb-rp-qual-lbl">
        <input type="checkbox" id="lb-rp-qual-chk" checked onchange="filterLBRP()"> Qualified only
      </label>
      <span class="row-count" id="lb-rp-cnt"></span>
      <span class="sort-hint">Click headers to sort</span>
      <div class="col-picker-wrap">
        <button class="tgl-btn col-picker-btn" onclick="toggleColPicker('rp',this)" style="font-size:.74rem;padding:5px 10px;margin-left:auto">⚙ Columns ▾</button>
        <div id="col-picker-rp" class="col-picker-panel" style="display:none"></div>
      </div>
    </div>
    <div class="table-wrap">
      <table id="lb-rp-tbl">
        <thead><tr>
          <th class="sortable"   data-k="name"         onclick="srtLBRP(this,'name')">Pitcher</th>
          <th>Team</th>
          <th class="sortable r" data-col="ip_f" data-k="ip_f"         onclick="srtLBRP(this,'ip_f')">IP</th>
          <th class="sortable r" data-col="w" data-k="w"            onclick="srtLBRP(this,'w')">W</th>
          <th class="sortable r" data-col="sv" data-k="sv"           onclick="srtLBRP(this,'sv')">SV/SVO</th>
          <th class="sortable r" data-col="hld" data-k="hld"          onclick="srtLBRP(this,'hld')">HLD</th>
          <th class="sortable r" data-col="gm_li" data-k="gm_li"       onclick="srtLBRP(this,'gm_li')">gmLI</th>
          <th class="sortable r lb-th-inv" data-col="era" data-k="era"  onclick="srtLBRP(this,'era')">ERA</th>
          <th class="sortable r lb-th-inv" data-col="whip" data-k="whip" onclick="srtLBRP(this,'whip')">WHIP</th>
          <th class="sortable r" data-col="k" data-k="k"            onclick="srtLBRP(this,'k')">K</th>
          <th class="sortable r lb-th-inv" data-col="xera" data-k="xera" onclick="srtLBRP(this,'xera')">xERA</th>
          <th class="sortable r lb-th-inv" data-col="siera" data-k="siera" onclick="srtLBRP(this,'siera')">SIERA</th>
          <th class="sortable r" data-col="stuff_plus" data-k="stuff_plus"   onclick="srtLBRP(this,'stuff_plus')">Stf+</th>
          <th class="sortable r" data-col="loc_plus" data-k="loc_plus"     onclick="srtLBRP(this,'loc_plus')">Loc+</th>
          <th class="sortable r" data-col="k_bb_pct" data-k="k_bb_pct"    onclick="srtLBRP(this,'k_bb_pct')">K-BB%</th>
          <th class="sortable r" data-col="k_pct" data-k="k_pct"        onclick="srtLBRP(this,'k_pct')">K%</th>
          <th class="sortable r lb-th-inv" data-col="bb_pct" data-k="bb_pct" onclick="srtLBRP(this,'bb_pct')">BB%</th>
          <th class="sortable r" data-col="chase_pct" data-k="chase_pct"    onclick="srtLBRP(this,'chase_pct')">Chase%</th>
          <th class="sortable r" data-col="whiff_pct" data-k="whiff_pct"    onclick="srtLBRP(this,'whiff_pct')">Whiff%</th>
          <th class="sortable r lb-th-inv" data-col="barrel_pct" data-k="barrel_pct"   onclick="srtLBRP(this,'barrel_pct')">Barrel%</th>
          <th class="sortable r lb-th-inv" data-col="hard_hit_pct" data-k="hard_hit_pct" onclick="srtLBRP(this,'hard_hit_pct')">Hard Hit%</th>
          <th class="sortable r" data-col="gb_pct" data-k="gb_pct"       onclick="srtLBRP(this,'gb_pct')">GB%</th>
          <th class="sortable r lb-th-inv" data-col="woba" data-k="woba"  onclick="srtLBRP(this,'woba')">wOBA</th>
          <th class="sortable r lb-th-inv" data-col="xwoba" data-k="xwoba" onclick="srtLBRP(this,'xwoba')">xwOBA</th>
          <th class="sortable r lb-th-inv" data-col="avg_ev" data-k="avg_ev" onclick="srtLBRP(this,'avg_ev')">Avg EV</th>
          <th class="sortable r" data-col="fb_velo" data-k="fb_velo"      onclick="srtLBRP(this,'fb_velo')">FB Velo</th>
        </tr></thead>
        <tbody id="lb-rp-body"></tbody>
      </table>
    </div>
  </div>

  <!-- ── Compare Players (subtab within Season Leaders) ── -->
  <div id="lb-cmp-wrap" style="display:none">
    <div class="toggle-group" style="margin-bottom:14px">
      <button class="tgl-btn active" id="cmp-h-btn" onclick="showCmpType(\'h\',this)"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="16" height="16" style="vertical-align:middle;display:inline-block;margin-bottom:2px"><circle cx="4" cy="20" r="2.2" fill="#6B3A2A"/><line x1="5" y1="19" x2="13" y2="11" stroke="#6B3A2A" stroke-width="2.2" stroke-linecap="round"/><line x1="13" y1="11" x2="19" y2="5" stroke="#6B3A2A" stroke-width="5" stroke-linecap="round"/></svg> Hitters</button>
      <button class="tgl-btn" id="cmp-p-btn" onclick="showCmpType(\'p\',this)">⚾ Pitchers</button>
    </div>
    <div class="cmp-search-wrap">
      <div class="cmp-input-wrap">
        <input id="cmp-search" type="text" placeholder="Search and add a player…" oninput="cmpSearch()" autocomplete="off">
        <div id="cmp-dropdown" class="cmp-dropdown" style="display:none"></div>
      </div>
      <button class="tgl-btn" onclick="clearCmp()" style="font-size:.75rem;padding:6px 14px">Clear All</button>
      <div class="col-picker-wrap">
        <button class="tgl-btn col-picker-btn" id="cmp-col-btn" onclick="toggleColPicker(cmpType==='h'?'h':'rp',this)" style="font-size:.74rem;padding:5px 10px">⚙ Columns ▾</button>
        <div id="col-picker-cmp" class="col-picker-panel" style="display:none"></div>
      </div>
      <span class="row-count" id="cmp-cnt"></span>
    </div>

    <!-- Hitters comparison table -->
    <div id="cmp-h-wrap">
      <div class="table-wrap">
        <table id="cmp-h-tbl">
          <thead><tr>
            <th>Player</th>
            <th>Team</th>
            <th class="r" data-col="r">R</th>
            <th class="r" data-col="hr">HR</th>
            <th class="r" data-col="rbi">RBI</th>
            <th class="r" data-col="sb">SB</th>
            <th class="r" data-col="obp">OBP</th>
            <th class="r" data-col="woba">wOBA</th>
            <th class="r" data-col="xwoba">xwOBA</th>
            <th class="r" data-col="chase_pct">Chase%</th>
            <th class="r" data-col="whiff_pct">Whiff%</th>
            <th class="r" data-col="k_pct">K%</th>
            <th class="r" data-col="so">SO</th>
            <th class="r" data-col="bb_pct">BB%</th>
            <th class="r" data-col="hard_hit_pct">Hard Hit%</th>
            <th class="r" data-col="barrel_pct">Barrel%</th>
            <th class="r" data-col="barrels">Barrels</th>
            <th class="r" data-col="sweet_spot_pct">Swt Spot%</th>
            <th class="r" data-col="avg_ev">Avg EV</th>
            <th class="r" data-col="max_ev">Max EV</th>
            <th class="r" data-col="bat_speed">Bat Spd</th>
            <th class="r" data-col="sprint_speed">Sprt Spd</th>
            <th></th>
          </tr></thead>
          <tbody id="cmp-h-body">
            <tr><td colspan="23"><div class="cmp-empty">Search for a hitter above and click to add them.</div></td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Pitchers comparison table -->
    <div id="cmp-p-wrap" style="display:none">
      <div class="table-wrap">
        <table id="cmp-p-tbl">
          <thead><tr>
            <th>Pitcher</th>
            <th>Team</th>
            <th class="r" data-col="role">Role</th>
            <th class="r" data-col="ip_f">IP</th>
            <th class="r" data-col="w">W</th>
            <th class="r" data-col="sv">SV/SVO</th>
            <th class="r" data-col="hld">HLD</th>
            <th class="r" data-col="gm_li">gmLI</th>
            <th class="r" data-col="era">ERA</th>
            <th class="r" data-col="whip">WHIP</th>
            <th class="r" data-col="k">K</th>
            <th class="r" data-col="xera">xERA</th>
            <th class="r" data-col="siera">SIERA</th>
            <th class="r" data-col="stuff_plus">Stf+</th>
            <th class="r" data-col="loc_plus">Loc+</th>
            <th class="r" data-col="k_bb_pct">K-BB%</th>
            <th class="r" data-col="k_pct">K%</th>
            <th class="r" data-col="bb_pct">BB%</th>
            <th class="r" data-col="chase_pct">Chase%</th>
            <th class="r" data-col="whiff_pct">Whiff%</th>
            <th class="r" data-col="barrel_pct">Barrel%</th>
            <th class="r" data-col="hard_hit_pct">Hard Hit%</th>
            <th class="r" data-col="gb_pct">GB%</th>
            <th class="r" data-col="woba">wOBA</th>
            <th class="r" data-col="xwoba">xwOBA</th>
            <th class="r" data-col="avg_ev">Avg EV</th>
            <th class="r" data-col="fb_velo">FB Velo</th>
            <th></th>
          </tr></thead>
          <tbody id="cmp-p-body">
            <tr><td colspan="28"><div class="cmp-empty">Search for a pitcher above and click to add them.</div></td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>

</main>

<footer>
  Generated __TS__
</footer>

<!-- Firebase v9 compat (Auth + Firestore for roster sync) -->
<script src="https://www.gstatic.com/firebasejs/9.23.0/firebase-app-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/9.23.0/firebase-auth-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/9.23.0/firebase-firestore-compat.js"></script>

<script>
const HITTERS    = __HITTERS_JSON__;
const ALL_PITCHERS = __ALL_PITCHERS_JSON__;
const STARTERS   = ALL_PITCHERS.filter(p=>p.ip_float>=3||p.is_starter);
const RELIEVERS  = ALL_PITCHERS.filter(p=>p.ip_float<3&&!p.is_starter);
let TA_HITTERS = __TA_H_JSON__;
let TA_STARTERS= __TA_SP_JSON__;
let TA_RELIEVERS=__TA_RP_JSON__;
let TA_ROSTER_NORMS=new Set(__TA_NAMES_JSON__);
const DEFAULT_TA_NAMES=__TA_NAMES_JSON__; // baked-in defaults — seeds Firestore on first login

// ── Category leaders (gold highlight) ─────────────────────────────────────
const H_LEAD_COLS=['r','hr','rbi','k','bb','sb','sba','hard_hits','barrels','max_ev'];
const SP_LEAD_COLS=['ip_float','k','w','whiffs','hard_hits','barrels','stuff_plus','location_plus'];
const RP_LEAD_COLS=['ip_float','k','sv','hld','bs','w','whiffs','hard_hits','barrels','stuff_plus','location_plus'];
function maxOf(arr,col){
  let m=-Infinity;
  arr.forEach(r=>{if(r[col]!=null&&!isNaN(r[col])&&r[col]>m)m=r[col];});
  return m>0?m:null;
}
function minOf(arr,col){
  let m=Infinity;
  arr.forEach(r=>{if(r[col]!=null&&!isNaN(r[col])&&r[col]<m)m=r[col];});
  return m<Infinity?m:null;
}
const hL={};H_LEAD_COLS.forEach(c=>hL[c]=maxOf(HITTERS,c));
const spL={};SP_LEAD_COLS.forEach(c=>spL[c]=maxOf(STARTERS,c));
const rpL={};RP_LEAD_COLS.forEach(c=>rpL[c]=maxOf(RELIEVERS,c));
// Pitcher H/R/BB/hard_hits/barrels: lower is better → gold goes to lowest
const spMin={hits:minOf(STARTERS,'hits'),r:minOf(STARTERS,'r'),bb:minOf(STARTERS,'bb'),hard_hits:minOf(STARTERS,'hard_hits'),barrels:minOf(STARTERS,'barrels')};
const rpMin={hits:minOf(RELIEVERS,'hits'),r:minOf(RELIEVERS,'r'),bb:minOf(RELIEVERS,'bb'),hard_hits:minOf(RELIEVERS,'hard_hits'),barrels:minOf(RELIEVERS,'barrels')};
// Game log color coding disabled — plain values only
const gl=(v,max)=>null;
const glMin=(v,min)=>null;
// EV gradient: red (high) → blue (low)
const _evVals=HITTERS.filter(h=>h.max_ev!=null&&h.bip>0).map(h=>h.max_ev);
const _evMin=_evVals.length?Math.min(..._evVals):90, _evMax=_evVals.length?Math.max(..._evVals):115;

// Inverted-sort columns (lower = better): first click → ascending
const LB_INV_SORT=new Set(['k_pct','chase_pct','whiff_pct','so']);

// Game Log tab + sub-tab counts
document.getElementById('gl-tc').textContent=HITTERS.length+STARTERS.length+RELIEVERS.length;
document.getElementById('p-sp-tc').textContent=STARTERS.length;
document.getElementById('p-rp-tc').textContent=RELIEVERS.length;

let hD=[...HITTERS], spD=[...STARTERS], rpD=[...RELIEVERS];
let hSC='barrels', hSD=-1, spSC='ip_float', spSD=-1, rpSC='sv', rpSD=-1;
let pitchType='sp';  // current pitcher sub-view: 'sp' or 'rp'
let glType='h';      // current game log sub-tab: 'h', 'sp', or 'rp'

function showTab(nm,btn){
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(nm+'-panel').classList.add('active');
  // Hide fantasy sub-panels (trade machine + season projections) when
  // switching away from the fantasy tab — they live as siblings of
  // #fantasy-panel rather than children, so .tab-panel display:none
  // doesn't catch them automatically.
  if(nm!=='fantasy'){
    var tw=document.getElementById('fant-trade-wrap');
    if(tw) tw.style.display='none';
    var pw=document.getElementById('fant-proj-wrap');
    if(pw) pw.style.display='none';
  }
}

function showGameLog(type, btn) {
  glType = type;
  document.querySelectorAll('.gl-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  const isH = type === 'h';
  document.getElementById('gl-h-section').style.display   = isH ? '' : 'none';
  document.getElementById('gl-pit-section').style.display = isH ? 'none' : '';
  if (!isH) {
    pitchType = type;
    document.getElementById('p-sp-wrap').style.display = type==='sp' ? '' : 'none';
    document.getElementById('p-rp-wrap').style.display = type==='rp' ? '' : 'none';
    filterP();
  }
}

function filterP(){
  const q=document.getElementById('p-search').value.toLowerCase().trim();
  if(pitchType==='sp'){
    spD=q?STARTERS.filter(p=>p.name.toLowerCase().includes(q)||p.team.toLowerCase().includes(q)||p.opp.toLowerCase().includes(q)):[...STARTERS];
    if(spSC)spD.sort((a,b)=>cmp(a,b,spSC,spSD));
    document.getElementById('p-cnt').textContent=`${spD.length} starter${spD.length===1?'':'s'}`;
    renderSP();
  } else {
    rpD=q?RELIEVERS.filter(p=>p.name.toLowerCase().includes(q)||p.team.toLowerCase().includes(q)||p.opp.toLowerCase().includes(q)):[...RELIEVERS];
    if(rpSC)rpD.sort((a,b)=>cmp(a,b,rpSC,rpSD));
    document.getElementById('p-cnt').textContent=`${rpD.length} reliever${rpD.length===1?'':'s'}`;
    renderRP();
  }
}

const D  = ()=>'<span class="c-dim">—</span>';
// MLB team color map: abbreviation → [primaryColor, textColor]
const TEAM_COLORS = {
  ARI:['#A71930','#fff'], ATL:['#CE1141','#fff'], BAL:['#DF4601','#fff'],
  BOS:['#BD3039','#fff'], CHC:['#0E3386','#fff'], CWS:['#27251F','#fff'],
  CIN:['#C6011F','#fff'], CLE:['#00385D','#fff'], COL:['#33006F','#fff'],
  DET:['#0C2340','#fff'], HOU:['#002D62','#fff'], KC:['#004687','#fff'],
  LAA:['#BA0021','#fff'], LAD:['#005A9C','#fff'], MIA:['#00A3E0','#fff'],
  MIL:['#12284B','#fff'], MIN:['#002B5C','#fff'], NYM:['#002D72','#fff'],
  NYY:['#003087','#fff'], OAK:['#003831','#fff'], PHI:['#E81828','#fff'],
  PIT:['#FDB827','#1a1a1a'], SD:['#2F241D','#fff'], SEA:['#0C2C56','#fff'],
  SF:['#FD5A1E','#fff'],  STL:['#C41E3A','#fff'], TB:['#092C5C','#fff'],
  TEX:['#003278','#fff'], TOR:['#134A8E','#fff'], WSH:['#AB0003','#fff'],
};
// Aliases for alternate abbreviations
TEAM_COLORS.CHW=TEAM_COLORS.CWS;
TEAM_COLORS.KCR=TEAM_COLORS.KC;
TEAM_COLORS.TBR=TEAM_COLORS.TB;
TEAM_COLORS.ANA=TEAM_COLORS.LAA;
TEAM_COLORS.FLA=TEAM_COLORS.MIA;
TEAM_COLORS.MON=TEAM_COLORS.WSH;
TEAM_COLORS.WSN=TEAM_COLORS.WSH;
TEAM_COLORS.ATH=['#003831','#EFB21E'];
const tm = t => {
  const tc = TEAM_COLORS[t];
  if(tc) return `<span class="tm" style="background:${tc[0]};color:${tc[1]};border-color:${tc[0]}44">${t}</span>`;
  return `<span class="tm" style="background:rgba(255,255,255,.12);color:var(--text)">${t}</span>`;
};

// Counting stats: show 0 when absent; rate/measurement stats: keep dash
const fHR  = v=>v>0?`${v}`:'0';
const fK_h = v=>v>0?`${v}`:'0';
const fBB_h= v=>v>0?`${v}`:'0';
const fSB  = v=>v>0?`${v}`:'0';
const fHrd = v=>v>0?`${v}`:'0';
const fBar = v=>v>0?`${v}`:'0';
const fEV  = (v,b)=>{if(v==null||b===0)return D();return `${v}`;};
const fIP  = (v,s)=>s;
const fH_p = v=>v>0?`${v}`:'0';
const fR   = v=>v>0?`${v}`:'0';
const fBB_p= v=>v>0?`${v}`:'0';
const fK_p = v=>v>0?`${v}`:'0';
const fWh  = v=>v>0?`${v}`:'0';
const fKBB = v=>`${v}%`;
const fSP  = v=>v==null?D():`${v}`;
const fLP  = v=>v==null?D():`${v}`;
const glIP = (v,s,max)=>null;

function pitchArsenal(types){
  if(!types||!types.length) return D();
  return '<div class="arsenal">'+types.map(pt=>{
    const c=pt.color||'#888';
    let veloHtml='<span class="vd">—</span>';
    if(pt.velo!=null){
      const gvCls=pt.velo_above?'va':pt.velo_below?'vb':'vn';
      const gv=`<span class="${gvCls}">${pt.velo}</span>`;
      const sv=pt.season_velo!=null?`<span class="vd sv"> (${pt.season_velo})</span>`:'';
      veloHtml=`${gv}${sv}<span class="vd" style="font-size:.65rem"> mph</span>`;
    }
    const stuffHtml=pt.game_stuff!=null?`<span class="gs">S+:${pt.game_stuff}</span>`:'';
    return `<div class="pt-row">
      <span class="pt-badge" style="color:${c};border:1px solid ${c}44">${pt.name}</span>
      <span class="pt-pct">${pt.pct}%</span>
      <span class="pt-velo">${veloHtml}</span>
      <span class="pt-stuff">${stuffHtml}</span>
    </div>`;
  }).join('')+'</div>';
}

function renderH(){
  const tb=document.getElementById('h-body');
  const ct=document.getElementById('h-cnt');
  document.getElementById('h-tc').textContent=hD.length;
  if(!hD.length){tb.innerHTML='<tr><td colspan="13"><div class="empty"><div class="ico">😴</div><p>No data.</p></div></td></tr>';ct.textContent='';return;}
  ct.textContent=`${hD.length} player${hD.length===1?'':'s'}`;
  tb.innerHTML=hD.map(h=>`<tr>
    <td class="nm">${h.name}</td>
    <td>${tm(h.team)}</td>
    <td><span class="c-dim" style="font-size:.7rem">vs</span> ${tm(h.opp)}</td>
    <td class="r">${(h.h||0)}/${(h.ab||0)}</td>
    <td class="r">${gl(h.r,hL.r)||(h.r>0?`${h.r}`:'0')}</td>
    <td class="r">${h.grand_slam&&h.hr>0?`<span style="color:#2ecc71;font-weight:700">${h.hr}</span>`:fHR(h.hr)}</td>
    <td class="r">${gl(h.rbi,hL.rbi)||(h.rbi>0?`${h.rbi}`:'0')}</td>
    <td class="r">${gl(h.k,hL.k)||fK_h(h.k)}</td>
    <td class="r">${gl(h.bb,hL.bb)||fBB_h(h.bb)}</td>
    <td class="r">${(h.sb||0)}${h.sba>0?`<span class="c-dim" style="font-size:.68rem">/${h.sba}</span>`:''}</td>
    <td class="r">${gl(h.hard_hits,hL.hard_hits)||fHrd(h.hard_hits)}</td>
    <td class="r">${gl(h.barrels,hL.barrels)||fBar(h.barrels)}</td>
    <td class="r">${gl(h.max_ev,hL.max_ev)||fEV(h.max_ev,h.bip)}</td>
  </tr>`).join('');
}

function renderSP(){
  const tb=document.getElementById('sp-body');
  if(!spD.length){tb.innerHTML='<tr><td colspan="15"><div class="empty"><div class="ico">😴</div><p>No data.</p></div></td></tr>';return;}
  tb.innerHTML=spD.map(p=>`<tr>
    <td class="nm">${p.name}</td>
    <td>${tm(p.team)}</td>
    <td><span class="c-dim" style="font-size:.7rem">vs</span> ${tm(p.opp)}</td>
    <td class="r">${glIP(p.ip_float,p.ip,spL.ip_float)||p.ip}</td>
    <td class="r">${glMin(p.hits,spMin.hits)||fH_p(p.hits)}</td>
    <td class="r">${glMin(p.r,spMin.r)||fR(p.r)}</td>
    <td class="r">${glMin(p.bb,spMin.bb)||fBB_p(p.bb)}</td>
    <td class="r">${gl(p.k,spL.k)||fK_p(p.k)}</td>
    <td class="r">${gl(p.w,spL.w)||(p.w>0?`${p.w}`:'0')}</td>
    <td class="r">${gl(p.whiffs,spL.whiffs)||fWh(p.whiffs)}</td>
    <td class="r">${glMin(p.hard_hits,spMin.hard_hits)||fHrd(p.hard_hits)}</td>
    <td class="r">${glMin(p.barrels,spMin.barrels)||fBar(p.barrels)}</td>
    <td class="r">${gl(p.stuff_plus,spL.stuff_plus)||fSP(p.stuff_plus)}</td>
    <td class="r">${gl(p.location_plus,spL.location_plus)||fLP(p.location_plus)}</td>
    <td>${pitchArsenal(p.pitch_types)}</td>
  </tr>`).join('');
}

function renderRP(){
  const tb=document.getElementById('rp-body');
  if(!rpD.length){tb.innerHTML='<tr><td colspan="19"><div class="empty"><div class="ico">😴</div><p>No relief data.</p></div></td></tr>';return;}
  tb.innerHTML=rpD.map(p=>`<tr>
    <td class="nm">${p.name}</td>
    <td>${tm(p.team)}</td>
    <td><span class="c-dim" style="font-size:.7rem">vs</span> ${tm(p.opp)}</td>
    <td class="r">${glIP(p.ip_float,p.ip,rpL.ip_float)||p.ip}</td>
    <td class="r">${glMin(p.hits,rpMin.hits)||fH_p(p.hits)}</td>
    <td class="r">${glMin(p.r,rpMin.r)||fR(p.r)}</td>
    <td class="r">${glMin(p.bb,rpMin.bb)||fBB_p(p.bb)}</td>
    <td class="r">${gl(p.k,rpL.k)||fK_p(p.k)}</td>
    <td class="r">${gl(p.sv,rpL.sv)||(p.sv>0?`${p.sv}`:'0')}</td>
    <td class="r">${gl(p.hld,rpL.hld)||(p.hld>0?`${p.hld}`:'0')}</td>
    <td class="r">${gl(p.bs,rpL.bs)||(p.bs>0?`${p.bs}`:'0')}</td>
    <td class="r">${gl(p.w,rpL.w)||(p.w>0?`${p.w}`:'0')}</td>
    <td class="r">${gl(p.whiffs,rpL.whiffs)||fWh(p.whiffs)}</td>
    <td class="r">${glMin(p.hard_hits,rpMin.hard_hits)||fHrd(p.hard_hits)}</td>
    <td class="r">${glMin(p.barrels,rpMin.barrels)||fBar(p.barrels)}</td>
    <td class="r">${gl(p.stuff_plus,rpL.stuff_plus)||fSP(p.stuff_plus)}</td>
    <td class="r">${gl(p.location_plus,rpL.location_plus)||fLP(p.location_plus)}</td>
    <td>${pitchArsenal(p.pitch_types)}</td>
  </tr>`).join('');
}

function cmp(a,b,col,dir){
  let av=a[col],bv=b[col];
  if(av==null)av=-Infinity;if(bv==null)bv=-Infinity;
  return typeof av==='string'?dir*av.localeCompare(bv):dir*(av-bv);
}
function clrSort(id){document.querySelectorAll(`#${id} thead th`).forEach(t=>t.classList.remove('sort-asc','sort-desc'));}
function srtH(th,col){if(hSC===col)hSD*=-1;else{hSC=col;hSD=-1;}clrSort('h-tbl');th.classList.add(hSD===1?'sort-asc':'sort-desc');hD.sort((a,b)=>cmp(a,b,col,hSD));renderH();}
function srtSP(th,col){if(spSC===col)spSD*=-1;else{spSC=col;spSD=-1;}clrSort('sp-tbl');th.classList.add(spSD===1?'sort-asc':'sort-desc');spD.sort((a,b)=>cmp(a,b,col,spSD));document.getElementById('p-cnt').textContent=`${spD.length} starter${spD.length===1?'':'s'}`;renderSP();}
function srtRP(th,col){if(rpSC===col)rpSD*=-1;else{rpSC=col;rpSD=-1;}clrSort('rp-tbl');th.classList.add(rpSD===1?'sort-asc':'sort-desc');rpD.sort((a,b)=>cmp(a,b,col,rpSD));document.getElementById('p-cnt').textContent=`${rpD.length} reliever${rpD.length===1?'':'s'}`;renderRP();}

function filterH(){
  const q=document.getElementById('h-search').value.toLowerCase().trim();
  hD=q?HITTERS.filter(h=>h.name.toLowerCase().includes(q)||h.team.toLowerCase().includes(q)||h.opp.toLowerCase().includes(q)):[...HITTERS];
  if(hSC)hD.sort((a,b)=>cmp(a,b,hSC,hSD));renderH();
}
function filterSP(){filterP();}
function filterRP(){filterP();}

hD.sort((a,b)=>cmp(a,b,'barrels',-1));
spD.sort((a,b)=>cmp(a,b,'ip_float',-1));
rpD.sort((a,b)=>cmp(a,b,'sv',-1));
document.querySelector('#h-tbl th[data-k="barrels"]')?.classList.add('sort-desc');
document.querySelector('#sp-tbl th[data-k="ip_float"]')?.classList.add('sort-desc');
document.querySelector('#rp-tbl th[data-k="sv"]')?.classList.add('sort-desc');
renderH();renderSP();renderRP();

// ── Team Alex ─────────────────────────────────────────────────────────────
let taHD=[...TA_HITTERS], taSPD=[...TA_STARTERS], taRPD=[...TA_RELIEVERS];
let taHSC='barrels', taHSD=-1, taSPSC='ip_float', taSPSD=-1, taRPSC='sv', taRPSD=-1;
let taHView='yday';  // 'yday' or 'season'

document.getElementById('ta-tc').textContent=TA_HITTERS.length+TA_STARTERS.length+TA_RELIEVERS.length;
document.getElementById('ta-h-tc').textContent=TA_HITTERS.length;
document.getElementById('ta-sp-tc').textContent=TA_STARTERS.length;
document.getElementById('ta-rp-tc').textContent=TA_RELIEVERS.length;
document.getElementById('ta-roster-count').textContent=DEFAULT_TA_NAMES.length+'-player roster';

// taNorm defined here; TA_LB/taLBD initialized below after LB_ALL is defined
function taNorm(s){
  return (s||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/\./g,'').trim();
}
// Placeholder declarations so srtTALB / renderTALB can reference them
let taLBD=[], taLBSC='hr', taLBSD=-1;

function showTAHView(view,btn){
  taHView=view;
  document.querySelectorAll('#teamalex-panel .toggle-group .tgl-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('ta-h-yday-wrap').style.display=view==='yday'?'':'none';
  document.getElementById('ta-h-season-wrap').style.display=view==='season'?'':'none';
  if(view==='season') renderTALB();
}

function renderTALB(){
  const tb=document.getElementById('ta-lb-body');
  if(!taLBD.length){
    tb.innerHTML='<tr><td colspan="24"><div class="empty"><div class="ico">📊</div><p>No season data for your team yet.</p></div></td></tr>';return;
  }
  tb.innerHTML=taLBD.map(p=>`<tr>
    <td class="nm">${p.name} ${p.team?tm(p.team):''}${!p.qualified?'<span class="c-dim" style="font-size:.65rem;margin-left:4px">[NQ]</span>':''}</td>
    <td class="r" data-col="pa">${fmtInt('pa',  p.pa)}</td>
    <td class="r" data-col="r">${fmtInt('r',   p.r)}</td>
    <td class="r" data-col="hr">${fmtInt('hr',  p.hr)}</td>
    <td class="r" data-col="rbi">${fmtInt('rbi', p.rbi)}</td>
    <td class="r" data-col="sb">${fmtSB(p)}</td>
    <td class="r" data-col="avg">${fmtRate('avg',   p.avg)}</td>
    <td class="r" data-col="obp">${fmtRate('obp',   p.obp)}</td>
    <td class="r" data-col="woba">${fmtRate('woba',  p.woba)}</td>
    <td class="r" data-col="xwoba">${fmtRate('xwoba', p.xwoba)}</td>
    <td class="r" data-col="chase_pct">${fmtPct('chase_pct',    p.chase_pct)}</td>
    <td class="r" data-col="whiff_pct">${fmtPct('whiff_pct',    p.whiff_pct)}</td>
    <td class="r" data-col="k_pct">${fmtPct('k_pct',        p.k_pct)}</td>
    <td class="r" data-col="so">${fmtInt('so',           p.so)}</td>
    <td class="r" data-col="bb_pct">${fmtPct('bb_pct',       p.bb_pct)}</td>
    <td class="r" data-col="hard_hit_pct">${fmtPct('hard_hit_pct', p.hard_hit_pct)}</td>
    <td class="r" data-col="barrel_pct">${fmtPct('barrel_pct',   p.barrel_pct)}</td>
    <td class="r" data-col="barrels">${fmtInt('barrels',      p.barrels)}</td>
    <td class="r" data-col="sweet_spot_pct">${fmtPct('sweet_spot_pct',p.sweet_spot_pct)}</td>
    <td class="r" data-col="avg_ev">${fmtEV( 'avg_ev',       p.avg_ev)}</td>
    <td class="r" data-col="max_ev">${fmtEV( 'max_ev',       p.max_ev)}</td>
    <td class="r" data-col="bat_speed">${fmtSpd('bat_speed',    p.bat_speed)}</td>
    <td class="r" data-col="sprint_speed">${fmtSpd('sprint_speed', p.sprint_speed)}</td>
    <td class="r" data-col="war">${p.war!=null?lbCell('war',p.war,p.war.toFixed(1)):D2()}</td>
  </tr>`).join('');
}

function srtTALB(th,col){
  if(taLBSC===col)taLBSD*=-1;else{taLBSC=col;taLBSD=LB_INV_SORT.has(col)?1:-1;}
  clrSort('ta-lb-tbl');th.classList.add(taLBSD===1?'sort-asc':'sort-desc');
  taLBD.sort((a,b)=>cmp(a,b,col,taLBSD));renderTALB();
}

function renderTASPLB(){
  const tb=document.getElementById('ta-sp-lb-body');
  if(!taSPLBD.length){
    tb.innerHTML='<tr><td colspan="22"><div class="empty"><div class="ico">📊</div><p>No season SP data for your team yet.</p></div></td></tr>';return;
  }
  const D=plCellSP;
  tb.innerHTML=taSPLBD.map(p=>`<tr>
    <td class="nm">${p.name} ${p.team?tm(p.team):''}${!p.qualified?'<span class="c-dim" style="font-size:.65rem;margin-left:4px">[NQ]</span>':''}</td>
    <td class="r" data-col="ip_f">${D('ip_f',p.ip_f,p.ip_f!=null?p.ip_f.toFixed(1):null)}</td>
    <td class="r" data-col="w">${D('w',p.w,p.w)}</td>
    <td class="r" data-col="era">${D('era',p.era,p.era!=null?p.era.toFixed(2):null)}</td>
    <td class="r" data-col="whip">${D('whip',p.whip,p.whip!=null?p.whip.toFixed(2):null)}</td>
    <td class="r" data-col="k">${D('k',p.k,p.k)}</td>
    <td class="r" data-col="xera">${D('xera',p.xera,p.xera!=null?p.xera.toFixed(2):null)}</td>
    <td class="r" data-col="siera">${D('siera',p.siera,p.siera!=null?p.siera.toFixed(2):null)}</td>
    <td class="r" data-col="stuff_plus">${D('stuff_plus',p.stuff_plus,p.stuff_plus)}</td>
    <td class="r" data-col="loc_plus">${D('loc_plus',p.loc_plus,p.loc_plus)}</td>
    <td class="r" data-col="k_bb_pct">${p.k_bb_pct!=null?D('k_bb_pct',p.k_bb_pct,p.k_bb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="k_pct">${p.k_pct!=null?D('k_pct',p.k_pct,p.k_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="bb_pct">${p.bb_pct!=null?D('bb_pct',p.bb_pct,p.bb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="chase_pct">${p.chase_pct!=null?D('chase_pct',p.chase_pct,p.chase_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="whiff_pct">${p.whiff_pct!=null?D('whiff_pct',p.whiff_pct,p.whiff_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="barrel_pct">${p.barrel_pct!=null?D('barrel_pct',p.barrel_pct,p.barrel_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="hard_hit_pct">${p.hard_hit_pct!=null?D('hard_hit_pct',p.hard_hit_pct,p.hard_hit_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="gb_pct">${p.gb_pct!=null?D('gb_pct',p.gb_pct,p.gb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="woba">${p.woba!=null?D('woba',p.woba,p.woba.toFixed(3).replace('0.','.')):D2()}</td>
    <td class="r" data-col="xwoba">${p.xwoba!=null?D('xwoba',p.xwoba,p.xwoba.toFixed(3).replace('0.','.')):D2()}</td>
    <td class="r" data-col="avg_ev">${D('avg_ev',p.avg_ev,p.avg_ev!=null?p.avg_ev.toFixed(1):null)}</td>
    <td class="r" data-col="fb_velo">${D('fb_velo',p.fb_velo,p.fb_velo!=null?p.fb_velo.toFixed(1):null)}</td>
    <td class="r" data-col="war">${D('war',p.war,p.war!=null?p.war.toFixed(1):null)}</td>
  </tr>`).join('');
}

function srtTASPLB(th,col){
  if(taSPLBSC===col)taSPLBSD*=-1;else{taSPLBSC=col;taSPLBSD=PL_INV_SORT.has(col)?1:-1;}
  clrSort('ta-sp-lb-tbl');th.classList.add(taSPLBSD===1?'sort-asc':'sort-desc');
  taSPLBD.sort((a,b)=>cmp(a,b,col,taSPLBSD));renderTASPLB();
}

function renderTARPLB(){
  const tb=document.getElementById('ta-rp-lb-body');
  if(!taRPLBD.length){
    tb.innerHTML='<tr><td colspan="25"><div class="empty"><div class="ico">📊</div><p>No season RP data for Team Alex yet.</p></div></td></tr>';return;
  }
  const D=plCellRP;
  tb.innerHTML=taRPLBD.map(p=>`<tr>
    <td class="nm">${p.name} ${p.team?tm(p.team):''}${!p.qualified?'<span class="c-dim" style="font-size:.65rem;margin-left:4px">[NQ]</span>':''}</td>
    <td class="r" data-col="ip_f">${D('ip_f',p.ip_f,p.ip_f!=null?p.ip_f.toFixed(1):null)}</td>
    <td class="r" data-col="w">${D('w',p.w,p.w)}</td>
    <td class="r" data-col="sv">${p.sv_opp>0?D('sv',p.sv,p.sv+'/'+p.sv_opp):D('sv',p.sv,p.sv)}</td>
    <td class="r" data-col="hld">${D('hld',p.hld,p.hld)}</td>
    <td class="r" data-col="gm_li">${p.gm_li!=null?D('gm_li',p.gm_li,p.gm_li.toFixed(2)):D2()}</td>
    <td class="r" data-col="era">${D('era',p.era,p.era!=null?p.era.toFixed(2):null)}</td>
    <td class="r" data-col="whip">${D('whip',p.whip,p.whip!=null?p.whip.toFixed(2):null)}</td>
    <td class="r" data-col="k">${D('k',p.k,p.k)}</td>
    <td class="r" data-col="xera">${D('xera',p.xera,p.xera!=null?p.xera.toFixed(2):null)}</td>
    <td class="r" data-col="siera">${D('siera',p.siera,p.siera!=null?p.siera.toFixed(2):null)}</td>
    <td class="r" data-col="stuff_plus">${D('stuff_plus',p.stuff_plus,p.stuff_plus)}</td>
    <td class="r" data-col="loc_plus">${D('loc_plus',p.loc_plus,p.loc_plus)}</td>
    <td class="r" data-col="k_bb_pct">${p.k_bb_pct!=null?D('k_bb_pct',p.k_bb_pct,p.k_bb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="k_pct">${p.k_pct!=null?D('k_pct',p.k_pct,p.k_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="bb_pct">${p.bb_pct!=null?D('bb_pct',p.bb_pct,p.bb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="chase_pct">${p.chase_pct!=null?D('chase_pct',p.chase_pct,p.chase_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="whiff_pct">${p.whiff_pct!=null?D('whiff_pct',p.whiff_pct,p.whiff_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="barrel_pct">${p.barrel_pct!=null?D('barrel_pct',p.barrel_pct,p.barrel_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="hard_hit_pct">${p.hard_hit_pct!=null?D('hard_hit_pct',p.hard_hit_pct,p.hard_hit_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="gb_pct">${p.gb_pct!=null?D('gb_pct',p.gb_pct,p.gb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="woba">${p.woba!=null?D('woba',p.woba,p.woba.toFixed(3).replace('0.','.')):D2()}</td>
    <td class="r" data-col="xwoba">${p.xwoba!=null?D('xwoba',p.xwoba,p.xwoba.toFixed(3).replace('0.','.')):D2()}</td>
    <td class="r" data-col="avg_ev">${D('avg_ev',p.avg_ev,p.avg_ev!=null?p.avg_ev.toFixed(1):null)}</td>
    <td class="r" data-col="fb_velo">${D('fb_velo',p.fb_velo,p.fb_velo!=null?p.fb_velo.toFixed(1):null)}</td>
  </tr>`).join('');
}

function srtTARPLB(th,col){
  if(taRPLBSC===col)taRPLBSD*=-1;else{taRPLBSC=col;taRPLBSD=PL_INV_SORT.has(col)?1:-1;}
  clrSort('ta-rp-lb-tbl');th.classList.add(taRPLBSD===1?'sort-asc':'sort-desc');
  taRPLBD.sort((a,b)=>cmp(a,b,col,taRPLBSD));renderTARPLB();
}

function renderTAH(){
  const tb=document.getElementById('ta-h-body');
  document.getElementById('ta-h-tc').textContent=taHD.length;
  if(!taHD.length){
    tb.innerHTML='<tr><td colspan="13"><div class="empty"><div class="ico">😴</div><p>No roster hitters appeared yesterday.</p></div></td></tr>';
    return;
  }
  tb.innerHTML=taHD.map(h=>`<tr>
    <td class="nm">${h.name}</td>
    <td>${tm(h.team)}</td>
    <td><span class="c-dim" style="font-size:.7rem">vs</span> ${tm(h.opp)}</td>
    <td class="r">${(h.h||0)}/${(h.ab||0)}</td>
    <td class="r">${gl(h.r,hL.r)||(h.r>0?`${h.r}`:'0')}</td>
    <td class="r">${h.grand_slam&&h.hr>0?`<span style="color:#2ecc71;font-weight:700">${h.hr}</span>`:fHR(h.hr)}</td>
    <td class="r">${gl(h.rbi,hL.rbi)||(h.rbi>0?`${h.rbi}`:'0')}</td>
    <td class="r">${gl(h.k,hL.k)||fK_h(h.k)}</td>
    <td class="r">${gl(h.bb,hL.bb)||fBB_h(h.bb)}</td>
    <td class="r">${(h.sb||0)}${h.sba>0?`<span class="c-dim" style="font-size:.68rem">/${h.sba}</span>`:''}</td>
    <td class="r">${gl(h.hard_hits,hL.hard_hits)||fHrd(h.hard_hits)}</td>
    <td class="r">${gl(h.barrels,hL.barrels)||fBar(h.barrels)}</td>
    <td class="r">${gl(h.max_ev,hL.max_ev)||fEV(h.max_ev,h.bip)}</td>
  </tr>`).join('');
}

function renderTASP(){
  const tb=document.getElementById('ta-sp-body');
  document.getElementById('ta-sp-tc').textContent=taSPD.length;
  if(!taSPD.length){
    tb.innerHTML='<tr><td colspan="15"><div class="empty"><div class="ico">😴</div><p>No roster starters pitched yesterday.</p></div></td></tr>';
    return;
  }
  tb.innerHTML=taSPD.map(p=>`<tr>
    <td class="nm">${p.name}</td>
    <td>${tm(p.team)}</td>
    <td><span class="c-dim" style="font-size:.7rem">vs</span> ${tm(p.opp)}</td>
    <td class="r">${glIP(p.ip_float,p.ip,spL.ip_float)||p.ip}</td>
    <td class="r">${glMin(p.hits,spMin.hits)||fH_p(p.hits)}</td>
    <td class="r">${glMin(p.r,spMin.r)||fR(p.r)}</td>
    <td class="r">${glMin(p.bb,spMin.bb)||fBB_p(p.bb)}</td>
    <td class="r">${gl(p.k,spL.k)||fK_p(p.k)}</td>
    <td class="r">${gl(p.w,spL.w)||(p.w>0?`${p.w}`:'0')}</td>
    <td class="r">${gl(p.whiffs,spL.whiffs)||fWh(p.whiffs)}</td>
    <td class="r">${glMin(p.hard_hits,spMin.hard_hits)||fHrd(p.hard_hits)}</td>
    <td class="r">${glMin(p.barrels,spMin.barrels)||fBar(p.barrels)}</td>
    <td class="r">${gl(p.stuff_plus,spL.stuff_plus)||fSP(p.stuff_plus)}</td>
    <td class="r">${gl(p.location_plus,spL.location_plus)||fLP(p.location_plus)}</td>
    <td>${pitchArsenal(p.pitch_types)}</td>
  </tr>`).join('');
}

function renderTARP(){
  const tb=document.getElementById('ta-rp-body');
  document.getElementById('ta-rp-tc').textContent=taRPD.length;
  if(!taRPD.length){
    tb.innerHTML='<tr><td colspan="19"><div class="empty"><div class="ico">😴</div><p>No roster relievers pitched yesterday.</p></div></td></tr>';
    return;
  }
  tb.innerHTML=taRPD.map(p=>`<tr>
    <td class="nm">${p.name}</td>
    <td>${tm(p.team)}</td>
    <td><span class="c-dim" style="font-size:.7rem">vs</span> ${tm(p.opp)}</td>
    <td class="r">${glIP(p.ip_float,p.ip,rpL.ip_float)||p.ip}</td>
    <td class="r">${glMin(p.hits,rpMin.hits)||fH_p(p.hits)}</td>
    <td class="r">${glMin(p.r,rpMin.r)||fR(p.r)}</td>
    <td class="r">${glMin(p.bb,rpMin.bb)||fBB_p(p.bb)}</td>
    <td class="r">${gl(p.k,rpL.k)||fK_p(p.k)}</td>
    <td class="r">${gl(p.sv,rpL.sv)||(p.sv>0?`${p.sv}`:'0')}</td>
    <td class="r">${gl(p.hld,rpL.hld)||(p.hld>0?`${p.hld}`:'0')}</td>
    <td class="r">${gl(p.bs,rpL.bs)||(p.bs>0?`${p.bs}`:'0')}</td>
    <td class="r">${gl(p.w,rpL.w)||(p.w>0?`${p.w}`:'0')}</td>
    <td class="r">${gl(p.whiffs,rpL.whiffs)||fWh(p.whiffs)}</td>
    <td class="r">${glMin(p.hard_hits,rpMin.hard_hits)||fHrd(p.hard_hits)}</td>
    <td class="r">${glMin(p.barrels,rpMin.barrels)||fBar(p.barrels)}</td>
    <td class="r">${gl(p.stuff_plus,rpL.stuff_plus)||fSP(p.stuff_plus)}</td>
    <td class="r">${gl(p.location_plus,rpL.location_plus)||fLP(p.location_plus)}</td>
    <td>${pitchArsenal(p.pitch_types)}</td>
  </tr>`).join('');
}

function srtTA(th,type,col){
  if(type==='h'){
    if(taHSC===col)taHSD*=-1;else{taHSC=col;taHSD=-1;}
    clrSort('ta-h-tbl');th.classList.add(taHSD===1?'sort-asc':'sort-desc');
    taHD.sort((a,b)=>cmp(a,b,col,taHSD));renderTAH();
  } else if(type==='sp'){
    if(taSPSC===col)taSPSD*=-1;else{taSPSC=col;taSPSD=-1;}
    clrSort('ta-sp-tbl');th.classList.add(taSPSD===1?'sort-asc':'sort-desc');
    taSPD.sort((a,b)=>cmp(a,b,col,taSPSD));renderTASP();
  } else {
    if(taRPSC===col)taRPSD*=-1;else{taRPSC=col;taRPSD=-1;}
    clrSort('ta-rp-tbl');th.classList.add(taRPSD===1?'sort-asc':'sort-desc');
    taRPD.sort((a,b)=>cmp(a,b,col,taRPSD));renderTARP();
  }
}

taHD.sort((a,b)=>cmp(a,b,'barrels',-1));
taSPD.sort((a,b)=>cmp(a,b,'ip_float',-1));
taRPD.sort((a,b)=>cmp(a,b,'sv',-1));
renderTAH();renderTASP();renderTARP();

function showTASPView(view,btn){
  document.querySelectorAll('#ta-sp-yday-btn,#ta-sp-season-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('ta-sp-yday-wrap').style.display=view==='yday'?'':'none';
  document.getElementById('ta-sp-season-wrap').style.display=view==='season'?'':'none';
  if(view==='season') renderTASPLB();
}
function showTARPView(view,btn){
  document.querySelectorAll('#ta-rp-yday-btn,#ta-rp-season-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('ta-rp-yday-wrap').style.display=view==='yday'?'':'none';
  document.getElementById('ta-rp-season-wrap').style.display=view==='season'?'':'none';
  if(view==='season') renderTARPLB();
}

// ── Season Leaderboard data ────────────────────────────────────────────────
const LB_ALL  = __LB_JSON__;
const LB_QUAL = LB_ALL.filter(p=>p.qualified);
const LB_SP_ALL  = __LB_SP_JSON__;
const LB_SP_QUAL = LB_SP_ALL.filter(p=>p.qualified);
const LB_RP_ALL  = __LB_RP_JSON__;
const LB_RP_QUAL = LB_RP_ALL.filter(p=>p.qualified);
// Build lookup map for season gmLI by MLBAM id — used to annotate yesterday game-log tables
const rpLIMap = {}; LB_RP_ALL.forEach(p=>{ if(p.gm_li!=null) rpLIMap[p.id]=p.gm_li; });
RELIEVERS.forEach(p=>{ p.gm_li = rpLIMap[p.id]??null; });
TA_RELIEVERS.forEach(p=>{ p.gm_li = rpLIMap[p.id]??null; });
// Re-render yesterday RP tables now that gmLI is populated (deferred so D2/helpers are fully initialized)
setTimeout(()=>{ renderRP(); renderTARP(); }, 0);

// Pre-compute k_bb_pct for any pitcher missing it (k% - bb%) — must run BEFORE buildCfg
[...LB_SP_ALL,...LB_RP_ALL].forEach(p=>{
  if(p.k_bb_pct==null&&p.k_pct!=null&&p.bb_pct!=null)
    p.k_bb_pct=Math.round((p.k_pct-p.bb_pct)*10)/10;
});

// Now that LB_ALL/LB_SP_ALL/LB_RP_ALL are defined, initialize TA season data
const TA_LB    = LB_ALL.filter(p=>TA_ROSTER_NORMS.has(taNorm(p.name)));
taLBD = [...TA_LB];
const TA_SP_LB = LB_SP_ALL.filter(p=>TA_ROSTER_NORMS.has(taNorm(p.name)));
const TA_RP_LB = LB_RP_ALL.filter(p=>TA_ROSTER_NORMS.has(taNorm(p.name)));
let taSPLBD=[...TA_SP_LB], taSPLBSC='ip_f', taSPLBSD=-1;
let taRPLBD=[...TA_RP_LB], taRPLBSC='sv',   taRPLBSD=-1;

document.getElementById('lb-tc').textContent = LB_QUAL.length;

// ── Generic rank-color engine ──────────────────────────────────────────────
function buildCfg(cfg_obj, qual_arr){
  Object.keys(cfg_obj).forEach(col=>{
    const cfg = cfg_obj[col];
    const vals = qual_arr.map(p=>p[col]).filter(v=>v!==null&&v!==undefined&&!isNaN(v));
    cfg.sorted = [...vals].sort((a,b)=>cfg.inv?a-b:b-a);
    cfg.vals   = vals;
    cfg.best   = cfg.sorted.length ? cfg.sorted[0] : null;
  });
}

function mkRankColor(cfg_obj, col, val){
  if(val===null||val===undefined) return null;
  const cfg = cfg_obj[col];
  if(!cfg||!cfg.sorted||!cfg.sorted.length) return null;
  if(val===cfg.best) return '#f0c040';
  const better = cfg.inv
    ? cfg.vals.filter(v=>v<val-0.00001).length
    : cfg.vals.filter(v=>v>val+0.00001).length;
  const total = cfg.vals.length;
  if(total<=1) return null;
  const t = better/(total-1);
  // Red → white (middle) → blue, like a diverging heatmap
  let r,g,b;
  if(t<0.5){
    // best side: red(255,60,50) → white(235,235,235)
    const s=t*2;
    r=Math.round(255+(235-255)*s);
    g=Math.round(60+(235-60)*s);
    b=Math.round(50+(235-50)*s);
  } else {
    // worst side: white(235,235,235) → blue(50,110,255)
    const s=(t-0.5)*2;
    r=Math.round(235+(50-235)*s);
    g=Math.round(235+(110-235)*s);
    b=Math.round(235+(255-235)*s);
  }
  return `rgb(${r},${g},${b})`;
}

// ── Hitter leaderboard column config ──────────────────────────────────────
// inv=true → lower is better (for hitters)
const LB_COL_CFG = {
  pa:            {inv:false}, avg:           {inv:false},
  r:             {inv:false}, hr:            {inv:false}, rbi:          {inv:false},
  sb:            {inv:false}, obp:           {inv:false}, woba:         {inv:false},
  xwoba:         {inv:false}, chase_pct:     {inv:true},  whiff_pct:    {inv:true},
  k_pct:         {inv:true},  so:            {inv:true},  bb_pct:       {inv:false},
  hard_hit_pct:  {inv:false}, barrel_pct:    {inv:false}, barrels:      {inv:false},
  sweet_spot_pct:{inv:false}, avg_ev:        {inv:false}, max_ev:       {inv:false},
  bat_speed:     {inv:false}, sprint_speed:  {inv:false},
  war:           {inv:false},
};
buildCfg(LB_COL_CFG, LB_QUAL);

function lbRankColor(col, val){ return mkRankColor(LB_COL_CFG, col, val); }

// ── Pitcher leaderboard column configs ────────────────────────────────────
// Note: Chase% and Whiff% NOT inverted for pitchers (higher = better for pitcher)
const PL_SP_COL_CFG = {
  ip_f:{inv:false}, w:{inv:false},
  era:{inv:true},  whip:{inv:true},  k:{inv:false}, xera:{inv:true},  siera:{inv:true},
  stuff_plus:{inv:false}, loc_plus:{inv:false},
  k_bb_pct:{inv:false}, k_pct:{inv:false}, bb_pct:{inv:true},
  chase_pct:{inv:false}, whiff_pct:{inv:false},
  barrel_pct:{inv:true}, hard_hit_pct:{inv:true}, gb_pct:{inv:false},
  woba:{inv:true}, xwoba:{inv:true}, avg_ev:{inv:true}, fb_velo:{inv:false},
  war:{inv:false},
};
const PL_RP_COL_CFG = {
  ip_f:{inv:false}, w:{inv:false}, sv:{inv:false}, hld:{inv:false}, gm_li:{inv:false},
  era:{inv:true},  whip:{inv:true},  k:{inv:false}, xera:{inv:true},  siera:{inv:true},
  stuff_plus:{inv:false}, loc_plus:{inv:false},
  k_bb_pct:{inv:false}, k_pct:{inv:false}, bb_pct:{inv:true},
  chase_pct:{inv:false}, whiff_pct:{inv:false},
  barrel_pct:{inv:true}, hard_hit_pct:{inv:true}, gb_pct:{inv:false},
  woba:{inv:true}, xwoba:{inv:true}, avg_ev:{inv:true}, fb_velo:{inv:false},
};
buildCfg(PL_SP_COL_CFG, LB_SP_QUAL);
buildCfg(PL_RP_COL_CFG, LB_RP_QUAL);

// ── Inverted sort sets ─────────────────────────────────────────────────────
// For pitchers, bb_pct/barrel_pct/hard_hit_pct/woba/xwoba/avg_ev/era/xera/siera/whip = first click ascending
const PL_INV_SORT = new Set(['era','whip','xera','siera','bb_pct','barrel_pct','hard_hit_pct','woba','xwoba','avg_ev']);

// ── Shared display helpers ─────────────────────────────────────────────────
const D2=()=>'<span class="c-dim">—</span>';

function lbCell(col, val, dispVal){
  if(val===null||val===undefined) return D2();
  const color=lbRankColor(col,val);
  const fw=color?';font-weight:600':'';
  const style=color?` style="color:${color}${fw}"`:'';
  return `<span${style}>${dispVal!==undefined?dispVal:val}</span>`;
}
function plCellSP(col, val, disp){
  if(val===null||val===undefined) return D2();
  const color=mkRankColor(PL_SP_COL_CFG, col, val);
  const fw=color?';font-weight:600':'';
  const style=color?` style="color:${color}${fw}"`:'';
  return `<span${style}>${disp!==undefined?disp:val}</span>`;
}
function plCellRP(col, val, disp){
  if(val===null||val===undefined) return D2();
  const color=mkRankColor(PL_RP_COL_CFG, col, val);
  const fw=color?';font-weight:600':'';
  const style=color?` style="color:${color}${fw}"`:'';
  return `<span${style}>${disp!==undefined?disp:val}</span>`;
}

function fmtPct(col,v){return v==null?D2():lbCell(col,v,v.toFixed(1)+'%');}
function fmtEV(col,v){return v==null?D2():lbCell(col,v,v.toFixed(1));}
function fmtSpd(col,v){return v==null?D2():lbCell(col,v,v.toFixed(1));}
function fmtInt(col,v){return v==null?D2():lbCell(col,v,v);}
function fmtRate(col,v){
  if(v==null) return D2();
  const s=v.toFixed(3).replace('0.','.');
  const color=lbRankColor(col,v);
  const style=color?` style="color:${color};font-weight:600"`:'';
  return `<span${style}>${s}</span>`;
}
function fmtSB(p){
  const color=lbRankColor('sb',p.sb);
  const style=color?` style="color:${color};font-weight:600"`:'';
  if(p.sba>0) return `<span${style}>${p.sb}</span><span class="c-dim" style="font-size:.68rem">/${p.sba}</span>`;
  return `<span${style}>${p.sb}</span>`;
}

// ── Leaderboard type toggle ────────────────────────────────────────────────
let lbType='h';
function showLBType(type, btn){
  lbType=type;
  document.querySelectorAll('#leaderboard-panel > .toggle-group > .tgl-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('lb-h-wrap').style.display   = type==='h'   ? '' : 'none';
  document.getElementById('lb-sp-wrap').style.display  = type==='sp'  ? '' : 'none';
  document.getElementById('lb-rp-wrap').style.display  = type==='rp'  ? '' : 'none';
  document.getElementById('lb-cmp-wrap').style.display = type==='cmp' ? '' : 'none';
  if(type!=='cmp'){
    const cnt = type==='h' ? LB_QUAL.length : type==='sp' ? LB_SP_QUAL.length : LB_RP_QUAL.length;
    document.getElementById('lb-tc').textContent = cnt;
  }
  if(type==='sp') renderLBSP();
  if(type==='rp') renderLBRP();
}

// ── Column visibility state (declared early so render fns can reference them) ──
var colVisH={}, colVisSP={}, colVisRP={};
var pickH=[], pickSP=[], pickRP=[];  // selection order (leftmost = index 0)

// ── Hitter leaderboard ─────────────────────────────────────────────────────
let lbD=[...LB_QUAL], lbSC='hr', lbSD=-1;

function renderLB(){
  const tb=document.getElementById('lb-body');
  const ct=document.getElementById('lb-cnt');
  if(!lbD.length){
    tb.innerHTML='<tr><td colspan="26"><div class="empty"><div class="ico">📊</div><p>No leaderboard data yet.</p></div></td></tr>';
    ct.textContent='';return;
  }
  ct.textContent=`${lbD.length} player${lbD.length===1?'':'s'}`;
  tb.innerHTML=lbD.map(p=>`<tr>
    <td class="nm">${p.name}${!p.qualified?'<span class="c-dim" style="font-size:.65rem;margin-left:4px">[NQ]</span>':''}</td>
    <td>${p.team?tm(p.team):''}</td>
    <td class="r" data-col="pa">${fmtInt('pa',  p.pa)}</td>
    <td class="r" data-col="r">${fmtInt('r',   p.r)}</td>
    <td class="r" data-col="hr">${fmtInt('hr',  p.hr)}</td>
    <td class="r" data-col="rbi">${fmtInt('rbi', p.rbi)}</td>
    <td class="r" data-col="sb">${fmtSB(p)}</td>
    <td class="r" data-col="avg">${fmtRate('avg',   p.avg)}</td>
    <td class="r" data-col="obp">${fmtRate('obp',   p.obp)}</td>
    <td class="r" data-col="woba">${fmtRate('woba',  p.woba)}</td>
    <td class="r" data-col="xwoba">${fmtRate('xwoba', p.xwoba)}</td>
    <td class="r" data-col="chase_pct">${fmtPct('chase_pct',    p.chase_pct)}</td>
    <td class="r" data-col="whiff_pct">${fmtPct('whiff_pct',    p.whiff_pct)}</td>
    <td class="r" data-col="k_pct">${fmtPct('k_pct',        p.k_pct)}</td>
    <td class="r" data-col="so">${fmtInt('so',           p.so)}</td>
    <td class="r" data-col="bb_pct">${fmtPct('bb_pct',       p.bb_pct)}</td>
    <td class="r" data-col="hard_hit_pct">${fmtPct('hard_hit_pct', p.hard_hit_pct)}</td>
    <td class="r" data-col="barrel_pct">${fmtPct('barrel_pct',   p.barrel_pct)}</td>
    <td class="r" data-col="barrels">${fmtInt('barrels',      p.barrels)}</td>
    <td class="r" data-col="sweet_spot_pct">${fmtPct('sweet_spot_pct',p.sweet_spot_pct)}</td>
    <td class="r" data-col="avg_ev">${fmtEV( 'avg_ev',       p.avg_ev)}</td>
    <td class="r" data-col="max_ev">${fmtEV( 'max_ev',       p.max_ev)}</td>
    <td class="r" data-col="bat_speed">${fmtSpd('bat_speed',    p.bat_speed)}</td>
    <td class="r" data-col="sprint_speed">${fmtSpd('sprint_speed', p.sprint_speed)}</td>
    <td class="r" data-col="war">${p.war!=null?lbCell('war',p.war,p.war.toFixed(1)):D2()}</td>
  </tr>`).join('');
  applyColVis('lb-tbl',colVisH);
  _reorderTableCols('lb-tbl',_buildOrder('h'));
}

function filterLB(){
  const q   = document.getElementById('lb-search').value.toLowerCase().trim();
  const qual = document.getElementById('lb-qual-chk').checked;
  document.getElementById('lb-qual-lbl').style.opacity = q ? '0.4' : '1';
  let base = q ? LB_ALL : (qual ? LB_QUAL : LB_ALL);
  if(q) base = base.filter(p=>p.name.toLowerCase().includes(q)||(p.team||'').toLowerCase().includes(q));
  lbD=[...base];
  if(lbSC) lbD.sort((a,b)=>cmp(a,b,lbSC,lbSD));
  renderLB();
}

function srtLB(th,col){
  if(lbSC===col)lbSD*=-1;else{lbSC=col;lbSD=LB_INV_SORT.has(col)?1:-1;}
  clrSort('lb-tbl');th.classList.add(lbSD===1?'sort-asc':'sort-desc');
  lbD.sort((a,b)=>cmp(a,b,col,lbSD));renderLB();
}

lbD.sort((a,b)=>cmp(a,b,'hr',-1));
document.querySelector('#lb-tbl th[data-k="hr"]')?.classList.add('sort-desc');
renderLB();

// ── SP Leaderboard ─────────────────────────────────────────────────────────
let lbSpD=[...LB_SP_QUAL], lbSpSC='ip_f', lbSpSD=-1;

function renderLBSP(){
  const tb=document.getElementById('lb-sp-body');
  const ct=document.getElementById('lb-sp-cnt');
  if(!lbSpD.length){
    tb.innerHTML='<tr><td colspan="25"><div class="empty"><div class="ico">📊</div><p>No SP leaderboard data yet.</p></div></td></tr>';
    ct.textContent='';return;
  }
  ct.textContent=`${lbSpD.length} pitcher${lbSpD.length===1?'':'s'}`;
  const D=plCellSP;
  tb.innerHTML=lbSpD.map(p=>`<tr>
    <td class="nm">${p.name}${!p.qualified?'<span class="c-dim" style="font-size:.65rem;margin-left:4px">[NQ]</span>':''}</td>
    <td>${p.team?tm(p.team):''}</td>
    <td class="r" data-col="ip_f">${D('ip_f',        p.ip_f,        p.ip_f!=null?p.ip_f.toFixed(1):null)}</td>
    <td class="r" data-col="w">${D('w',           p.w,           p.w)}</td>
    <td class="r" data-col="era">${D('era',         p.era,         p.era!=null?p.era.toFixed(2):null)}</td>
    <td class="r" data-col="whip">${D('whip',        p.whip,        p.whip!=null?p.whip.toFixed(2):null)}</td>
    <td class="r" data-col="k">${D('k',           p.k,           p.k)}</td>
    <td class="r" data-col="xera">${D('xera',        p.xera,        p.xera!=null?p.xera.toFixed(2):null)}</td>
    <td class="r" data-col="siera">${D('siera',       p.siera,       p.siera!=null?p.siera.toFixed(2):null)}</td>
    <td class="r" data-col="stuff_plus">${D('stuff_plus',  p.stuff_plus,  p.stuff_plus)}</td>
    <td class="r" data-col="loc_plus">${D('loc_plus',    p.loc_plus,    p.loc_plus)}</td>
    <td class="r" data-col="k_bb_pct">${p.k_bb_pct!=null?D('k_bb_pct',p.k_bb_pct,p.k_bb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="k_pct">${p.k_pct!=null?D('k_pct',p.k_pct,p.k_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="bb_pct">${p.bb_pct!=null?D('bb_pct',p.bb_pct,p.bb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="chase_pct">${p.chase_pct!=null?D('chase_pct',p.chase_pct,p.chase_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="whiff_pct">${p.whiff_pct!=null?D('whiff_pct',p.whiff_pct,p.whiff_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="barrel_pct">${p.barrel_pct!=null?D('barrel_pct',p.barrel_pct,p.barrel_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="hard_hit_pct">${p.hard_hit_pct!=null?D('hard_hit_pct',p.hard_hit_pct,p.hard_hit_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="gb_pct">${p.gb_pct!=null?D('gb_pct',p.gb_pct,p.gb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="woba">${p.woba!=null?D('woba',p.woba,p.woba.toFixed(3).replace('0.','.')):D2()}</td>
    <td class="r" data-col="xwoba">${p.xwoba!=null?D('xwoba',p.xwoba,p.xwoba.toFixed(3).replace('0.','.')):D2()}</td>
    <td class="r" data-col="avg_ev">${D('avg_ev',      p.avg_ev,      p.avg_ev!=null?p.avg_ev.toFixed(1):null)}</td>
    <td class="r" data-col="fb_velo">${D('fb_velo',     p.fb_velo,     p.fb_velo!=null?p.fb_velo.toFixed(1):null)}</td>
    <td class="r" data-col="war">${D('war',          p.war,         p.war!=null?p.war.toFixed(1):null)}</td>
  </tr>`).join('');
  applyColVis('lb-sp-tbl',colVisSP);
  _reorderTableCols('lb-sp-tbl',_buildOrder('sp'));
}

function filterLBSP(){
  const q    = document.getElementById('lb-sp-search').value.toLowerCase().trim();
  const qual = document.getElementById('lb-sp-qual-chk').checked;
  document.getElementById('lb-sp-qual-lbl').style.opacity = q ? '0.4' : '1';
  let base = q ? LB_SP_ALL : (qual ? LB_SP_QUAL : LB_SP_ALL);
  if(q) base = base.filter(p=>p.name.toLowerCase().includes(q)||(p.team||'').toLowerCase().includes(q));
  lbSpD=[...base];
  if(lbSpSC) lbSpD.sort((a,b)=>cmp(a,b,lbSpSC,lbSpSD));
  renderLBSP();
}

function srtLBSP(th,col){
  if(lbSpSC===col)lbSpSD*=-1;else{lbSpSC=col;lbSpSD=PL_INV_SORT.has(col)?1:-1;}
  clrSort('lb-sp-tbl');th.classList.add(lbSpSD===1?'sort-asc':'sort-desc');
  lbSpD.sort((a,b)=>cmp(a,b,col,lbSpSD));renderLBSP();
}

// ── RP Leaderboard ─────────────────────────────────────────────────────────
let lbRpD=[...LB_RP_QUAL], lbRpSC='sv', lbRpSD=-1;

function renderLBRP(){
  const tb=document.getElementById('lb-rp-body');
  const ct=document.getElementById('lb-rp-cnt');
  if(!lbRpD.length){
    tb.innerHTML='<tr><td colspan="27"><div class="empty"><div class="ico">📊</div><p>No RP leaderboard data yet.</p></div></td></tr>';
    ct.textContent='';return;
  }
  ct.textContent=`${lbRpD.length} pitcher${lbRpD.length===1?'':'s'}`;
  const D=plCellRP;
  tb.innerHTML=lbRpD.map(p=>`<tr>
    <td class="nm">${p.name}${!p.qualified?'<span class="c-dim" style="font-size:.65rem;margin-left:4px">[NQ]</span>':''}</td>
    <td>${p.team?tm(p.team):''}</td>
    <td class="r" data-col="ip_f">${D('ip_f',        p.ip_f,        p.ip_f!=null?p.ip_f.toFixed(1):null)}</td>
    <td class="r" data-col="w">${D('w',           p.w,           p.w)}</td>
    <td class="r" data-col="sv">${p.sv_opp>0?D('sv',p.sv,p.sv+'/'+p.sv_opp):D('sv',p.sv,p.sv)}</td>
    <td class="r" data-col="hld">${D('hld',         p.hld,         p.hld)}</td>
    <td class="r" data-col="gm_li">${p.gm_li!=null?D('gm_li',p.gm_li,p.gm_li.toFixed(2)):D2()}</td>
    <td class="r" data-col="era">${D('era',         p.era,         p.era!=null?p.era.toFixed(2):null)}</td>
    <td class="r" data-col="whip">${D('whip',        p.whip,        p.whip!=null?p.whip.toFixed(2):null)}</td>
    <td class="r" data-col="k">${D('k',           p.k,           p.k)}</td>
    <td class="r" data-col="xera">${D('xera',        p.xera,        p.xera!=null?p.xera.toFixed(2):null)}</td>
    <td class="r" data-col="siera">${D('siera',       p.siera,       p.siera!=null?p.siera.toFixed(2):null)}</td>
    <td class="r" data-col="stuff_plus">${D('stuff_plus',  p.stuff_plus,  p.stuff_plus)}</td>
    <td class="r" data-col="loc_plus">${D('loc_plus',    p.loc_plus,    p.loc_plus)}</td>
    <td class="r" data-col="k_bb_pct">${p.k_bb_pct!=null?D('k_bb_pct',p.k_bb_pct,p.k_bb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="k_pct">${p.k_pct!=null?D('k_pct',p.k_pct,p.k_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="bb_pct">${p.bb_pct!=null?D('bb_pct',p.bb_pct,p.bb_pct.toFixed(1)+ '%'):D2()}</td>
    <td class="r" data-col="chase_pct">${p.chase_pct!=null?D('chase_pct',p.chase_pct,p.chase_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="whiff_pct">${p.whiff_pct!=null?D('whiff_pct',p.whiff_pct,p.whiff_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="barrel_pct">${p.barrel_pct!=null?D('barrel_pct',p.barrel_pct,p.barrel_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="hard_hit_pct">${p.hard_hit_pct!=null?D('hard_hit_pct',p.hard_hit_pct,p.hard_hit_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="gb_pct">${p.gb_pct!=null?D('gb_pct',p.gb_pct,p.gb_pct.toFixed(1)+'%'):D2()}</td>
    <td class="r" data-col="woba">${p.woba!=null?D('woba',p.woba,p.woba.toFixed(3).replace('0.','.')):D2()}</td>
    <td class="r" data-col="xwoba">${p.xwoba!=null?D('xwoba',p.xwoba,p.xwoba.toFixed(3).replace('0.','.')):D2()}</td>
    <td class="r" data-col="avg_ev">${D('avg_ev',      p.avg_ev,      p.avg_ev!=null?p.avg_ev.toFixed(1):null)}</td>
    <td class="r" data-col="fb_velo">${D('fb_velo',     p.fb_velo,     p.fb_velo!=null?p.fb_velo.toFixed(1):null)}</td>
  </tr>`).join('');
  applyColVis('lb-rp-tbl',colVisRP);
  _reorderTableCols('lb-rp-tbl',_buildOrder('rp'));
}

function filterLBRP(){
  const q    = document.getElementById('lb-rp-search').value.toLowerCase().trim();
  const qual = document.getElementById('lb-rp-qual-chk').checked;
  document.getElementById('lb-rp-qual-lbl').style.opacity = q ? '0.4' : '1';
  let base = q ? LB_RP_ALL : (qual ? LB_RP_QUAL : LB_RP_ALL);
  if(q) base = base.filter(p=>p.name.toLowerCase().includes(q)||(p.team||'').toLowerCase().includes(q));
  lbRpD=[...base];
  if(lbRpSC) lbRpD.sort((a,b)=>cmp(a,b,lbRpSC,lbRpSD));
  renderLBRP();
}

function srtLBRP(th,col){
  if(lbRpSC===col)lbRpSD*=-1;else{lbRpSC=col;lbRpSD=PL_INV_SORT.has(col)?1:-1;}
  clrSort('lb-rp-tbl');th.classList.add(lbRpSD===1?'sort-asc':'sort-desc');
  lbRpD.sort((a,b)=>cmp(a,b,col,lbRpSD));renderLBRP();
}

// ── Compare Players ────────────────────────────────────────────────────────
let cmpType='h';
const cmpHSet=new Set();
const cmpPSet=new Set();
let cmpDdIdx=-1;

// Combined pitcher pool (SP + RP) for compare search
const LB_P_ALL=[...LB_SP_ALL,...LB_RP_ALL];
const LB_P_QUAL_CMP=[...LB_SP_QUAL,...LB_RP_QUAL];

// Combined pitcher col config for rank coloring across both roles
const PL_CMP_COL_CFG={
  ip_f:{inv:false},w:{inv:false},sv:{inv:false},hld:{inv:false},gm_li:{inv:false},
  era:{inv:true},whip:{inv:true},k:{inv:false},xera:{inv:true},siera:{inv:true},
  stuff_plus:{inv:false},loc_plus:{inv:false},
  k_bb_pct:{inv:false},k_pct:{inv:false},bb_pct:{inv:true},
  chase_pct:{inv:false},whiff_pct:{inv:false},
  barrel_pct:{inv:true},hard_hit_pct:{inv:true},gb_pct:{inv:false},
  woba:{inv:true},xwoba:{inv:true},avg_ev:{inv:true},fb_velo:{inv:false},
};
buildCfg(PL_CMP_COL_CFG, LB_P_QUAL_CMP);

function showCmpType(type,btn){
  cmpType=type;
  document.querySelectorAll('#lb-cmp-wrap .toggle-group .tgl-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('cmp-h-wrap').style.display=type==='h'?'':'none';
  document.getElementById('cmp-p-wrap').style.display=type==='p'?'':'none';
  document.getElementById('cmp-search').value='';
  document.getElementById('cmp-dropdown').style.display='none';
  cmpDdIdx=-1;
  updateCmpCount();
}

function updateCmpCount(){
  const n=cmpType==='h'?cmpHSet.size:cmpPSet.size;
  document.getElementById('cmp-cnt').textContent=n?`${n} player${n===1?'':'s'} selected`:'';
}

function cmpSearch(){
  const q=document.getElementById('cmp-search').value.toLowerCase().trim();
  const dd=document.getElementById('cmp-dropdown');
  if(!q){dd.style.display='none';cmpDdIdx=-1;return;}
  const pool=cmpType==='h'?LB_ALL:LB_P_ALL;
  const already=cmpType==='h'?cmpHSet:cmpPSet;
  const matches=pool.filter(p=>p.name.toLowerCase().includes(q)||(p.team||'').toLowerCase().includes(q)).slice(0,20);
  if(!matches.length){dd.style.display='none';cmpDdIdx=-1;return;}
  dd.innerHTML=matches.map(p=>{
    const added=already.has(p.id);
    const role=cmpType==='p'?(p.is_sp?'SP':'RP'):'';
    return `<div class="cmp-di" data-id="${p.id}" onmousedown="cmpAdd(${p.id})">`
      +(p.team?tm(p.team):'')+` <span>${p.name}</span>`
      +(role?` <span style="color:var(--muted);font-size:.72rem">${role}</span>`:'')
      +(!p.qualified?' <span style="color:var(--muted);font-size:.72rem">[NQ]</span>':'')
      +(added?'<span style="color:var(--muted);font-size:.72rem;margin-left:auto">Added</span>':'')
      +'</div>';
  }).join('');
  dd.style.display='';
  cmpDdIdx=-1;
}

function cmpAdd(id){
  const already=cmpType==='h'?cmpHSet:cmpPSet;
  already.add(id);
  document.getElementById('cmp-search').value='';
  document.getElementById('cmp-dropdown').style.display='none';
  cmpDdIdx=-1;
  renderCmp();
  updateCmpCount();
}

function cmpRemove(id){
  (cmpType==='h'?cmpHSet:cmpPSet).delete(id);
  renderCmp();
  updateCmpCount();
}

function clearCmp(){
  (cmpType==='h'?cmpHSet:cmpPSet).clear();
  renderCmp();
  updateCmpCount();
}

function plCmpCell(col,val,disp){
  if(val===null||val===undefined) return D2();
  const color=mkRankColor(PL_CMP_COL_CFG,col,val);
  const fw=color?';font-weight:600':'';
  const style=color?` style="color:${color}${fw}"`:'';
  return `<span${style}>${disp!==undefined&&disp!==null?disp:val}</span>`;
}

function renderCmp(){
  if(cmpType==='h'){
    const tb=document.getElementById('cmp-h-body');
    const ids=[...cmpHSet];
    if(!ids.length){
      tb.innerHTML='<tr><td colspan="23"><div class="cmp-empty">Search for a hitter above and click to add them.</div></td></tr>';
      return;
    }
    tb.innerHTML=ids.map(id=>{
      const p=LB_ALL.find(x=>x.id===id);
      if(!p) return '';
      return `<tr>
        <td class="nm">${p.name}${!p.qualified?'<span class="c-dim" style="font-size:.65rem;margin-left:3px">[NQ]</span>':''}</td>
        <td>${p.team?tm(p.team):''}</td>
        <td class="r" data-col="r">${fmtInt('r',p.r)}</td>
        <td class="r" data-col="hr">${fmtInt('hr',p.hr)}</td>
        <td class="r" data-col="rbi">${fmtInt('rbi',p.rbi)}</td>
        <td class="r" data-col="sb">${fmtSB(p)}</td>
        <td class="r" data-col="obp">${fmtRate('obp',p.obp)}</td>
        <td class="r" data-col="woba">${fmtRate('woba',p.woba)}</td>
        <td class="r" data-col="xwoba">${fmtRate('xwoba',p.xwoba)}</td>
        <td class="r" data-col="chase_pct">${fmtPct('chase_pct',p.chase_pct)}</td>
        <td class="r" data-col="whiff_pct">${fmtPct('whiff_pct',p.whiff_pct)}</td>
        <td class="r" data-col="k_pct">${fmtPct('k_pct',p.k_pct)}</td>
        <td class="r" data-col="so">${fmtInt('so',p.so)}</td>
        <td class="r" data-col="bb_pct">${fmtPct('bb_pct',p.bb_pct)}</td>
        <td class="r" data-col="hard_hit_pct">${fmtPct('hard_hit_pct',p.hard_hit_pct)}</td>
        <td class="r" data-col="barrel_pct">${fmtPct('barrel_pct',p.barrel_pct)}</td>
        <td class="r" data-col="barrels">${fmtInt('barrels',p.barrels)}</td>
        <td class="r" data-col="sweet_spot_pct">${fmtPct('sweet_spot_pct',p.sweet_spot_pct)}</td>
        <td class="r" data-col="avg_ev">${fmtEV('avg_ev',p.avg_ev)}</td>
        <td class="r" data-col="max_ev">${fmtEV('max_ev',p.max_ev)}</td>
        <td class="r" data-col="bat_speed">${fmtSpd('bat_speed',p.bat_speed)}</td>
        <td class="r" data-col="sprint_speed">${fmtSpd('sprint_speed',p.sprint_speed)}</td>
        <td class="r"><button class="cmp-remove" onclick="cmpRemove(${id})">✕ Remove</button></td>
      </tr>`;
    }).join('');
    applyColVis('cmp-h-tbl',colVisH);
  } else {
    const tb=document.getElementById('cmp-p-body');
    const ids=[...cmpPSet];
    if(!ids.length){
      tb.innerHTML='<tr><td colspan="28"><div class="cmp-empty">Search for a pitcher above and click to add them.</div></td></tr>';
      return;
    }
    const D=plCmpCell;
    tb.innerHTML=ids.map(id=>{
      const p=LB_P_ALL.find(x=>x.id===id);
      if(!p) return '';
      const role=p.is_sp?'SP':'RP';
      const roleColor=role==='SP'?'rgba(61,155,233,.25)':'rgba(232,131,42,.25)';
      const roleTxt=role==='SP'?'#3d9be9':'#e8832a';
      return `<tr>
        <td class="nm">${p.name}${!p.qualified?'<span class="c-dim" style="font-size:.65rem;margin-left:3px">[NQ]</span>':''}</td>
        <td>${p.team?tm(p.team):''}</td>
        <td class="r" data-col="role"><span class="tm" style="background:${roleColor};color:${roleTxt};border-color:transparent;font-size:.7rem">${role}</span></td>
        <td class="r" data-col="ip_f">${D('ip_f',p.ip_f,p.ip_f!=null?p.ip_f.toFixed(1):null)}</td>
        <td class="r" data-col="w">${D('w',p.w,p.w)}</td>
        <td class="r" data-col="sv">${p.sv_opp>0?D('sv',p.sv,p.sv+'/'+p.sv_opp):D('sv',p.sv,p.sv)}</td>
        <td class="r" data-col="hld">${D('hld',p.hld,p.hld)}</td>
        <td class="r" data-col="gm_li">${p.gm_li!=null?D('gm_li',p.gm_li,p.gm_li.toFixed(2)):D2()}</td>
        <td class="r" data-col="era">${D('era',p.era,p.era!=null?p.era.toFixed(2):null)}</td>
        <td class="r" data-col="whip">${D('whip',p.whip,p.whip!=null?p.whip.toFixed(2):null)}</td>
        <td class="r" data-col="k">${D('k',p.k,p.k)}</td>
        <td class="r" data-col="xera">${D('xera',p.xera,p.xera!=null?p.xera.toFixed(2):null)}</td>
        <td class="r" data-col="siera">${D('siera',p.siera,p.siera!=null?p.siera.toFixed(2):null)}</td>
        <td class="r" data-col="stuff_plus">${D('stuff_plus',p.stuff_plus,p.stuff_plus)}</td>
        <td class="r" data-col="loc_plus">${D('loc_plus',p.loc_plus,p.loc_plus)}</td>
        <td class="r" data-col="k_bb_pct">${p.k_bb_pct!=null?D('k_bb_pct',p.k_bb_pct,p.k_bb_pct.toFixed(1)+'%'):D2()}</td>
        <td class="r" data-col="k_pct">${p.k_pct!=null?D('k_pct',p.k_pct,p.k_pct.toFixed(1)+'%'):D2()}</td>
        <td class="r" data-col="bb_pct">${p.bb_pct!=null?D('bb_pct',p.bb_pct,p.bb_pct.toFixed(1)+'%'):D2()}</td>
        <td class="r" data-col="chase_pct">${p.chase_pct!=null?D('chase_pct',p.chase_pct,p.chase_pct.toFixed(1)+'%'):D2()}</td>
        <td class="r" data-col="whiff_pct">${p.whiff_pct!=null?D('whiff_pct',p.whiff_pct,p.whiff_pct.toFixed(1)+'%'):D2()}</td>
        <td class="r" data-col="barrel_pct">${p.barrel_pct!=null?D('barrel_pct',p.barrel_pct,p.barrel_pct.toFixed(1)+'%'):D2()}</td>
        <td class="r" data-col="hard_hit_pct">${p.hard_hit_pct!=null?D('hard_hit_pct',p.hard_hit_pct,p.hard_hit_pct.toFixed(1)+'%'):D2()}</td>
        <td class="r" data-col="gb_pct">${p.gb_pct!=null?D('gb_pct',p.gb_pct,p.gb_pct.toFixed(1)+'%'):D2()}</td>
        <td class="r" data-col="woba">${p.woba!=null?D('woba',p.woba,p.woba.toFixed(3).replace('0.','.')):D2()}</td>
        <td class="r" data-col="xwoba">${p.xwoba!=null?D('xwoba',p.xwoba,p.xwoba.toFixed(3).replace('0.','.')):D2()}</td>
        <td class="r" data-col="avg_ev">${D('avg_ev',p.avg_ev,p.avg_ev!=null?p.avg_ev.toFixed(1):null)}</td>
        <td class="r" data-col="fb_velo">${D('fb_velo',p.fb_velo,p.fb_velo!=null?p.fb_velo.toFixed(1):null)}</td>
        <td class="r"><button class="cmp-remove" onclick="cmpRemove(${id})">✕ Remove</button></td>
      </tr>`;
    }).join('');
    applyColVis('cmp-p-tbl',colVisRP);
  }
}

// Keyboard nav for compare dropdown
document.getElementById('cmp-search').addEventListener('keydown',function(e){
  const dd=document.getElementById('cmp-dropdown');
  const items=[...dd.querySelectorAll('.cmp-di')];
  if(!items.length) return;
  if(e.key==='ArrowDown'){
    e.preventDefault();
    cmpDdIdx=Math.min(cmpDdIdx+1,items.length-1);
    items.forEach((el,i)=>el.classList.toggle('active',i===cmpDdIdx));
  } else if(e.key==='ArrowUp'){
    e.preventDefault();
    cmpDdIdx=Math.max(cmpDdIdx-1,0);
    items.forEach((el,i)=>el.classList.toggle('active',i===cmpDdIdx));
  } else if(e.key==='Enter'){
    e.preventDefault();
    if(cmpDdIdx>=0&&items[cmpDdIdx]) cmpAdd(+items[cmpDdIdx].dataset.id);
  } else if(e.key==='Escape'){
    dd.style.display='none'; cmpDdIdx=-1;
  }
});

// ── Column Visibility ─────────────────────────────────────────────────────
const COL_H_DEFS=[
  {k:'pa',label:'PA'},
  {k:'r',label:'R'},{k:'hr',label:'HR'},{k:'rbi',label:'RBI'},{k:'sb',label:'SB'},
  {k:'avg',label:'AVG'},{k:'obp',label:'OBP'},{k:'woba',label:'wOBA'},{k:'xwoba',label:'xwOBA'},
  {k:'chase_pct',label:'Chase%'},{k:'whiff_pct',label:'Whiff%'},
  {k:'k_pct',label:'K%'},{k:'so',label:'SO'},{k:'bb_pct',label:'BB%'},
  {k:'hard_hit_pct',label:'Hard Hit%'},{k:'barrel_pct',label:'Barrel%'},
  {k:'barrels',label:'Barrels'},{k:'sweet_spot_pct',label:'Swt Spot%'},
  {k:'avg_ev',label:'Avg EV'},{k:'max_ev',label:'Max EV'},
  {k:'bat_speed',label:'Bat Spd'},{k:'sprint_speed',label:'Sprt Spd'},
  {k:'war',label:'fWAR'},
];
const COL_SP_DEFS=[
  {k:'ip_f',label:'IP'},{k:'w',label:'W'},
  {k:'era',label:'ERA'},{k:'whip',label:'WHIP'},{k:'k',label:'K'},{k:'xera',label:'xERA'},{k:'siera',label:'SIERA'},
  {k:'stuff_plus',label:'Stf+'},{k:'loc_plus',label:'Loc+'},
  {k:'k_bb_pct',label:'K-BB%'},{k:'k_pct',label:'K%'},{k:'bb_pct',label:'BB%'},
  {k:'chase_pct',label:'Chase%'},{k:'whiff_pct',label:'Whiff%'},
  {k:'barrel_pct',label:'Barrel%'},{k:'hard_hit_pct',label:'Hard Hit%'},
  {k:'gb_pct',label:'GB%'},{k:'woba',label:'wOBA'},{k:'xwoba',label:'xwOBA'},
  {k:'avg_ev',label:'Avg EV'},{k:'fb_velo',label:'FB Velo'},
  {k:'war',label:'fWAR'},
];
const COL_RP_DEFS=[
  {k:'ip_f',label:'IP'},{k:'w',label:'W'},{k:'sv',label:'SV/SVO'},{k:'hld',label:'HLD'},{k:'gm_li',label:'gmLI'},
  {k:'era',label:'ERA'},{k:'whip',label:'WHIP'},{k:'k',label:'K'},{k:'xera',label:'xERA'},{k:'siera',label:'SIERA'},
  {k:'stuff_plus',label:'Stf+'},{k:'loc_plus',label:'Loc+'},
  {k:'k_bb_pct',label:'K-BB%'},{k:'k_pct',label:'K%'},{k:'bb_pct',label:'BB%'},
  {k:'chase_pct',label:'Chase%'},{k:'whiff_pct',label:'Whiff%'},
  {k:'barrel_pct',label:'Barrel%'},{k:'hard_hit_pct',label:'Hard Hit%'},
  {k:'gb_pct',label:'GB%'},{k:'woba',label:'wOBA'},{k:'xwoba',label:'xwOBA'},
  {k:'avg_ev',label:'Avg EV'},{k:'fb_velo',label:'FB Velo'},
  {k:'role',label:'Role'},
];
// (colVisH/colVisSP/colVisRP declared earlier as var — just populate them here)
COL_H_DEFS.forEach(d=>{colVisH[d.k]=true;});
COL_SP_DEFS.forEach(d=>{colVisSP[d.k]=true;});
COL_RP_DEFS.forEach(d=>{colVisRP[d.k]=true;});
// Init pick arrays to default order (all selected)
COL_H_DEFS.forEach(d=>pickH.push(d.k));
COL_SP_DEFS.forEach(d=>pickSP.push(d.k));
COL_RP_DEFS.forEach(d=>pickRP.push(d.k));
// Apply correct column order/visibility now that defs are fully initialized
applyColVis('lb-tbl',colVisH);    _reorderTableCols('lb-tbl',_buildOrder('h'));
applyColVis('lb-sp-tbl',colVisSP); _reorderTableCols('lb-sp-tbl',_buildOrder('sp'));
applyColVis('lb-rp-tbl',colVisRP); _reorderTableCols('lb-rp-tbl',_buildOrder('rp'));

// ── Column display order helpers ─────────────────────────────────────────────
function _pick(type){ return type==='h'?pickH:type==='sp'?pickSP:pickRP; }
// Derive full physical column order: selected columns (pick order) first,
// then all unselected columns in default definition order.
function _buildOrder(type){
  const {defs}=_colInfo(type);
  const p=_pick(type);
  const result=[...p];
  defs.forEach(d=>{ if(!p.includes(d.k)) result.push(d.k); });
  return result;
}

// Physically reorder <th>/<td> cells in a table to match orderedKeys.
// Cells without data-col (Name/Player) are untouched and stay first.
function _reorderTableCols(tableId, orderedKeys){
  if(!orderedKeys) return;  // guard: called before colOrder arrays init
  const tbl=document.getElementById(tableId);
  if(!tbl) return;
  tbl.querySelectorAll('tr').forEach(row=>{
    const movable={};
    [...row.children].forEach(c=>{
      const k=c.getAttribute('data-col');
      if(k && orderedKeys.includes(k)) movable[k]=c;
    });
    Object.values(movable).forEach(c=>c.remove());
    orderedKeys.forEach(k=>{ if(movable[k]) row.appendChild(movable[k]); });
  });
}

function _colInfo(type){
  try{
    if(type==='h')  return {vis:colVisH,  defs:COL_H_DEFS,  tables:['lb-tbl','cmp-h-tbl']};
    if(type==='sp') return {vis:colVisSP, defs:COL_SP_DEFS, tables:['lb-sp-tbl']};
    if(type==='rp') return {vis:colVisRP, defs:COL_RP_DEFS, tables:['lb-rp-tbl','cmp-p-tbl']};
  }catch(e){return {vis:{},defs:[],tables:[]};} // guard: COL_*_DEFS not yet initialized
}

function applyColVis(tableId, vis){
  if(!vis||typeof vis!=='object') return;
  const tbl=document.getElementById(tableId);
  if(!tbl) return;
  tbl.querySelectorAll('[data-col]').forEach(el=>{
    const k=el.getAttribute('data-col');
    if(k in vis) el.classList.toggle('col-vis-hidden', vis[k]===false);
  });
}

function _pickerHTML(type){
  const {vis,defs}=_colInfo(type);
  const items=defs.filter(d=>d.k!=='role').map(d=>`<label class="col-picker-item"><input type="checkbox" data-ck="${type}:${d.k}" ${vis[d.k]!==false?'checked':''} onchange="toggleCol('${type}','${d.k}',this.checked)">${d.label}</label>`).join('');
  // add Role checkbox for rp (compare only)
  const roleItem=type==='rp'?`<label class="col-picker-item"><input type="checkbox" data-ck="${type}:role" ${colVisRP.role!==false?'checked':''} onchange="toggleCol('${type}','role',this.checked)">Role</label>`:'';
  return `<div class="col-picker-actions"><button onclick="selectAllCols('${type}')">Select All</button><button onclick="deselectAllCols('${type}')">Deselect All</button></div><div class="col-picker-grid">${items}${roleItem}</div>`;
}

function toggleColPicker(type,btn){
  const panel=btn.nextElementSibling;
  if(!panel) return;
  const isOpen=panel.style.display!=='none';
  // close all pickers first
  document.querySelectorAll('.col-picker-panel').forEach(p=>p.style.display='none');
  if(!isOpen){
    panel.innerHTML=_pickerHTML(type);
    panel.dataset.pickerType=type;
    panel.style.display='';
  }
}

function toggleCol(type,key,checked){
  const {vis,tables}=_colInfo(type);
  const pick=_pick(type);
  vis[key]=checked;
  if(checked){
    if(!pick.includes(key)) pick.push(key);  // add to END → becomes rightmost selected
  } else {
    const i=pick.indexOf(key);
    if(i!==-1) pick.splice(i,1);            // remove from selection order
  }
  const order=_buildOrder(type);
  tables.forEach(t=>{ applyColVis(t,vis); _reorderTableCols(t,order); });
}

function selectAllCols(type){
  const {vis,defs,tables}=_colInfo(type);
  const pick=_pick(type);
  defs.forEach(d=>{vis[d.k]=true;});
  pick.length=0; defs.forEach(d=>pick.push(d.k)); // reset pick to default order
  const order=_buildOrder(type);
  tables.forEach(t=>{ applyColVis(t,vis); _reorderTableCols(t,order); });
  document.querySelectorAll(`.col-picker-panel[data-picker-type="${type}"]`).forEach(p=>{p.innerHTML=_pickerHTML(type);p.dataset.pickerType=type;});
}

function deselectAllCols(type){
  const {vis,defs,tables}=_colInfo(type);
  const pick=_pick(type);
  defs.forEach(d=>{vis[d.k]=false;});
  pick.length=0;  // clear selection order — first clicked will be leftmost
  const order=_buildOrder(type); // = all defs keys in default order (all hidden)
  tables.forEach(t=>{ applyColVis(t,vis); _reorderTableCols(t,order); });
  document.querySelectorAll(`.col-picker-panel[data-picker-type="${type}"]`).forEach(p=>{p.innerHTML=_pickerHTML(type);p.dataset.pickerType=type;});
}

// Close picker on outside click
document.addEventListener('click',function(e){
  if(!e.target.closest('.col-picker-wrap')&&!e.target.closest('.col-picker-btn')){
    document.querySelectorAll('.col-picker-panel').forEach(p=>p.style.display='none');
  }
},true);

document.addEventListener('click',function(e){
  if(!e.target.closest('#cmp-search')&&!e.target.closest('#cmp-dropdown')){
    const dd=document.getElementById('cmp-dropdown');
    if(dd){dd.style.display='none';} cmpDdIdx=-1;
  }
});

// ══════════════════════════════════════════════════════════════════════════
// Roster persistence — localStorage (always) + Firestore (when logged in)
// ══════════════════════════════════════════════════════════════════════════
const _fbCfg = __FIREBASE_CONFIG__;
let _fbAuth = null, _fbDb = null, _fbUser = null;
let _rosterNames = []; // current roster as array of raw display names (starts empty)
const _LS_ROSTER_KEY    = 'mlb_my_team_roster';
const _LS_TEAM_NAME_KEY = 'mlb_my_team_name';

// ── localStorage load (runs immediately, before Firebase) ──
(function _loadLocalRoster(){
  try {
    var raw = localStorage.getItem(_LS_ROSTER_KEY);
    if (raw) {
      var names = JSON.parse(raw);
      if (Array.isArray(names) && names.length) {
        _rosterNames = names;
        _rebuildTA(names);
      }
    }
    var tn = localStorage.getItem(_LS_TEAM_NAME_KEY);
    if (tn) { _teamName = tn; _applyTeamName(tn); }
  } catch(e) {}
})();

function _saveRosterLocal(names){
  try { localStorage.setItem(_LS_ROSTER_KEY, JSON.stringify(names)); } catch(e) {}
}
function _saveTeamNameLocal(name){
  try { localStorage.setItem(_LS_TEAM_NAME_KEY, name); } catch(e) {}
}

(function _initFirebase(){
  _updateAuthUI();   // show Edit Roster immediately (works without login)
  if (!_fbCfg.apiKey || _fbCfg.apiKey.startsWith('REPLACE')) return;
  try {
    firebase.initializeApp(_fbCfg);
    _fbAuth = firebase.auth();
    _fbDb   = firebase.firestore();
    _fbAuth.setPersistence(firebase.auth.Auth.Persistence.LOCAL).catch(()=>{});
    _fbAuth.onAuthStateChanged(async user => {
      _fbUser = user;
      _updateAuthUI();
      if (user) await _loadRoster(user.uid);
    });
  } catch(e) {
    console.error('Firebase init error:', e);
    _updateAuthUI();
  }
})();

function _updateAuthUI(){
  const area = document.getElementById('ta-auth-area');
  if (!area) return;
  const editBtn=document.getElementById('ta-name-edit-btn');
  // Always show the Edit Roster button — roster works with localStorage even
  // without a Firebase login. Login just adds cross-device sync.
  var btns = `<button onclick="openRosterModal()" style="background:var(--accent);color:#fff;border:none;border-radius:7px;padding:6px 13px;font-size:.8rem;font-weight:700;cursor:pointer">✏️ Edit Roster</button>`;
  if (_fbUser) {
    const email = _fbUser.email || '';
    btns += `<button onclick="_doLogout()" title="Logged in as ${email}" style="background:none;border:1px solid var(--border);border-radius:7px;padding:5px 10px;font-size:.73rem;color:var(--muted);cursor:pointer">⇠ Logout</button>`;
    if(editBtn) editBtn.style.display='inline';
  } else if (_fbAuth) {
    btns += `<button onclick="openLoginOverlay()" style="background:none;border:1px solid var(--border);border-radius:7px;padding:5px 11px;font-size:.78rem;color:var(--muted);cursor:pointer">🔑 Sync</button>`;
    if(editBtn) editBtn.style.display='inline';
  } else {
    if(editBtn) editBtn.style.display='inline';
  }
  area.innerHTML = btns;
}

let _teamName = 'My Team';

function _applyTeamName(name){
  ['ta-team-name-tab','ta-team-name-hdr'].forEach(id=>{
    const el=document.getElementById(id);
    if(el) el.textContent=name;
  });
  const btn=document.getElementById('ta-name-edit-btn');
  if(btn) btn.style.display='inline';
}

function startTeamNameEdit(){
  const hdr=document.getElementById('ta-team-name-hdr');
  if(!hdr) return;
  const cur=hdr.textContent;
  hdr.outerHTML=`<input id="ta-name-input" value="${cur}" maxlength="30"
    style="font-size:1.05rem;font-weight:800;color:var(--gold);background:transparent;border:none;border-bottom:2px solid var(--accent);outline:none;width:160px;padding:0"
    onblur="saveTeamName(this.value)"
    onkeydown="if(event.key==='Enter')this.blur();">`;
  const inp=document.getElementById('ta-name-input');
  if(inp){inp.focus();inp.select();}
}

async function saveTeamName(val){
  const name=(val||'').trim()||'My Team';
  _teamName=name;
  _saveTeamNameLocal(name);
  const inp=document.getElementById('ta-name-input');
  if(inp) inp.outerHTML=`<span id="ta-team-name-hdr">${name}</span>`;
  const tab=document.getElementById('ta-team-name-tab');
  if(tab) tab.textContent=name;
  if(_fbUser) await _fbDb.collection('users').doc(_fbUser.uid).set({teamName:name},{merge:true});
}

async function _loadRoster(uid){
  try {
    const doc = await _fbDb.collection('users').doc(uid).get();
    let names;
    if (doc.exists && Array.isArray(doc.data().roster) && doc.data().roster.length > 0) {
      // Firestore has a saved roster — use it (authoritative when logged in)
      names = doc.data().roster;
    } else if (_rosterNames && _rosterNames.length > 0) {
      // First login but localStorage already has a roster — push it to Firestore
      names = _rosterNames;
      await _saveRoster(uid, names);
    } else {
      names = [];
    }
    // Load saved team name
    if (doc.exists && doc.data().teamName) {
      _teamName = doc.data().teamName;
      _applyTeamName(_teamName);
    }
    _rosterNames = names;
    _saveRosterLocal(names);   // keep localStorage in sync
    _rebuildTA(names);
  } catch(e) {
    console.error('Firestore load error:', e);
  }
}

async function _saveRoster(uid, names){
  try {
    await _fbDb.collection('users').doc(uid).set({roster: names}, {merge: true});
  } catch(e) {
    console.error('Firestore save error:', e);
  }
}
async function _saveRosterUnified(names){
  _saveRosterLocal(names);
  if (_fbUser && _fbDb) await _saveRoster(_fbUser.uid, names);
}

function _rebuildTA(rosterNames){
  const norms = new Set(rosterNames.map(n => taNorm(n)));
  TA_HITTERS      = HITTERS.filter(h => norms.has(taNorm(h.name)));
  TA_STARTERS     = STARTERS.filter(p => norms.has(taNorm(p.name)));
  TA_RELIEVERS    = RELIEVERS.filter(p => norms.has(taNorm(p.name)));
  TA_ROSTER_NORMS = norms;

  // Patch season gmLI onto new TA_RELIEVERS
  TA_RELIEVERS.forEach(p => { p.gm_li = rpLIMap[p.id] ?? null; });

  // Reset sorted display arrays
  taHD  = [...TA_HITTERS].sort((a,b) => cmp(a,b,'barrels',-1));
  taSPD = [...TA_STARTERS].sort((a,b) => cmp(a,b,'ip_float',-1));
  taRPD = [...TA_RELIEVERS].sort((a,b) => cmp(a,b,'sv',-1));

  // Rebuild season LB slices
  taLBD   = LB_ALL.filter(p => TA_ROSTER_NORMS.has(taNorm(p.name)));
  taSPLBD = [...LB_SP_ALL.filter(p => TA_ROSTER_NORMS.has(taNorm(p.name)))];
  taRPLBD = [...LB_RP_ALL.filter(p => TA_ROSTER_NORMS.has(taNorm(p.name)))];

  // Update counts
  const total = TA_HITTERS.length + TA_STARTERS.length + TA_RELIEVERS.length;
  document.getElementById('ta-tc').textContent  = total;
  document.getElementById('ta-h-tc').textContent  = TA_HITTERS.length;
  document.getElementById('ta-sp-tc').textContent = TA_STARTERS.length;
  document.getElementById('ta-rp-tc').textContent = TA_RELIEVERS.length;
  const cntEl = document.getElementById('ta-roster-count');
  if (cntEl) cntEl.textContent = rosterNames.length + '-player roster';

  // Re-render all visible Team Alex tables
  renderTAH(); renderTASP(); renderTARP();
  renderTALB(); renderTASPLB(); renderTARPLB();
}

// ── Login overlay ──────────────────────────────────────────────────────────
function openLoginOverlay(){
  const ov = document.getElementById('login-overlay');
  ov.style.display = 'flex';
  document.getElementById('login-error').style.display = 'none';
  document.getElementById('login-email').value = '';
  document.getElementById('login-pass').value  = '';
  setTimeout(()=>document.getElementById('login-email').focus(), 50);
}
function closeLoginOverlay(){
  document.getElementById('login-overlay').style.display = 'none';
}
async function doLogin(){
  const email = document.getElementById('login-email').value.trim();
  const pass  = document.getElementById('login-pass').value;
  const errEl = document.getElementById('login-error');
  const btn   = document.getElementById('login-btn');
  btn.textContent = 'Signing in…'; btn.disabled = true; errEl.style.display='none';
  try {
    await _fbAuth.signInWithEmailAndPassword(email, pass);
    closeLoginOverlay();
  } catch(e) {
    errEl.textContent = _fbErr(e.code); errEl.style.display='';
  } finally {
    btn.textContent = 'Sign In'; btn.disabled = false;
  }
}
async function doSignup(){
  const email = document.getElementById('login-email').value.trim();
  const pass  = document.getElementById('login-pass').value;
  const errEl = document.getElementById('login-error');
  const btn   = document.getElementById('signup-btn');
  btn.textContent = 'Creating…'; btn.disabled = true; errEl.style.display='none';
  try {
    await _fbAuth.createUserWithEmailAndPassword(email, pass);
    closeLoginOverlay();
  } catch(e) {
    errEl.textContent = _fbErr(e.code); errEl.style.display='';
  } finally {
    btn.textContent = 'Create Account'; btn.disabled = false;
  }
}
async function _doLogout(){
  await _fbAuth.signOut();
  _fbUser = null; _rosterNames = null;
  _updateAuthUI();
}
function _fbErr(code){
  const m={'auth/invalid-email':'Invalid email address.',
    'auth/user-not-found':'No account with this email.',
    'auth/wrong-password':'Incorrect password.',
    'auth/invalid-credential':'Incorrect email or password.',
    'auth/email-already-in-use':'An account with this email already exists.',
    'auth/weak-password':'Password must be at least 6 characters.',
    'auth/too-many-requests':'Too many attempts — try again later.'};
  return m[code]||'Error: '+code;
}
document.addEventListener('keydown',function(e){
  if(e.key==='Enter'&&document.getElementById('login-overlay').style.display==='flex') doLogin();
});

// ── Roster editor modal ────────────────────────────────────────────────────
let _rTab='h', _rQ='';

function openRosterModal(){
  document.getElementById('roster-modal').style.display='flex';
  document.getElementById('roster-search').value=''; _rQ='';
  switchRosterTab('h',document.getElementById('roster-tab-h'));
}
function closeRosterModal(){
  document.getElementById('roster-modal').style.display='none';
}
function switchRosterTab(tab,btn){
  _rTab=tab;
  document.querySelectorAll('#roster-modal .tgl-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  _renderRosterList();
}
function filterRosterSearch(){
  _rQ=document.getElementById('roster-search').value.toLowerCase().trim();
  _renderRosterList();
}
function _rPool(){
  if(_rTab==='h')  return LB_ALL;
  if(_rTab==='sp') return LB_SP_ALL;
  return LB_RP_ALL;
}
function _renderRosterList(){
  const pool=_rPool();
  let players=_rQ ? pool.filter(p=>p.name.toLowerCase().includes(_rQ)||(p.team||'').toLowerCase().includes(_rQ)) : [...pool];
  // Sort: on-roster first, then alphabetical
  players.sort((a,b)=>{
    const aN=TA_ROSTER_NORMS.has(taNorm(a.name));
    const bN=TA_ROSTER_NORMS.has(taNorm(b.name));
    if(aN!==bN) return aN?-1:1;
    return a.name<b.name?-1:1;
  });
  const el=document.getElementById('roster-player-list');
  if(!players.length){
    el.innerHTML='<div style="text-align:center;color:var(--muted);padding:24px;font-size:.85rem">No players found</div>';
  } else {
    el.innerHTML=players.map((p,i)=>{
      const on=TA_ROSTER_NORMS.has(taNorm(p.name));
      const badge=p.team?`<span style="margin-left:5px">${tm(p.team)}</span>`:'';
      return `<div style="display:flex;align-items:center;justify-content:space-between;padding:7px 2px;border-bottom:1px solid var(--border)">
        <span>${p.name}${badge}</span>
        <button data-rp="${i}" style="border:none;border-radius:6px;padding:4px 13px;font-size:.76rem;font-weight:700;cursor:pointer;flex-shrink:0;${on?'background:#c0392b;color:#fff':'background:#27ae60;color:#fff'}">${on?'− Remove':'+ Add'}</button>
      </div>`;
    }).join('');
    // Use event delegation — avoids inline onclick escaping issues with names
    // containing apostrophes or other special characters
    el.querySelectorAll('button[data-rp]').forEach(btn=>{
      const idx=parseInt(btn.getAttribute('data-rp'),10);
      const p=players[idx];
      if(!p) return;
      const on=TA_ROSTER_NORMS.has(taNorm(p.name));
      btn.addEventListener('click',()=>_togglePlayer(p.name,on));
    });
  }
  const rc=_rosterNames?_rosterNames.length:0;
  document.getElementById('roster-count-info').textContent=rc+' players on roster';
}
async function _togglePlayer(name, isOn){
  let names = _rosterNames ? [..._rosterNames] : [];
  if(isOn){
    names=names.filter(n=>taNorm(n)!==taNorm(name));
  } else {
    if(!names.some(n=>taNorm(n)===taNorm(name))) names.push(name);
  }
  _rosterNames=names;
  await _saveRosterUnified(names);
  _rebuildTA(names);
  _renderRosterList();
}
// Close modal on backdrop click
document.getElementById('roster-modal').addEventListener('click',function(e){
  if(e.target===this) closeRosterModal();
});

</script>

<!-- ══ Login Overlay ══ -->
<div id="login-overlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:9999;align-items:center;justify-content:center">
  <div style="background:var(--card);border:1px solid var(--border);border-radius:12px;padding:28px 24px;width:min(92vw,380px);box-shadow:0 10px 40px rgba(0,0,0,.6)">
    <div style="font-size:1.1rem;font-weight:800;margin-bottom:4px;color:var(--gold)">👑 My Team Login</div>
    <div style="font-size:.8rem;color:var(--muted);margin-bottom:18px">Sign in to manage your roster across devices</div>
    <div id="login-error" style="display:none;color:#f55;font-size:.8rem;margin-bottom:10px;padding:8px 10px;background:rgba(255,80,80,.12);border-radius:6px"></div>
    <input id="login-email" type="email" placeholder="Email" autocomplete="email"
      style="width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:.9rem;margin-bottom:10px">
    <input id="login-pass" type="password" placeholder="Password" autocomplete="current-password"
      style="width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:.9rem;margin-bottom:14px">
    <div style="display:flex;gap:8px;margin-bottom:10px">
      <button id="login-btn" onclick="doLogin()" style="flex:1;padding:10px;border-radius:8px;border:none;background:var(--accent);color:#fff;font-size:.9rem;font-weight:700;cursor:pointer">Sign In</button>
      <button id="signup-btn" onclick="doSignup()" style="flex:1;padding:10px;border-radius:8px;border:1px solid var(--border);background:transparent;color:var(--text);font-size:.9rem;cursor:pointer">Create Account</button>
    </div>
    <div style="text-align:center">
      <button onclick="closeLoginOverlay()" style="background:none;border:none;color:var(--muted);font-size:.78rem;cursor:pointer">Cancel</button>
    </div>
  </div>
</div>

<!-- ══ Roster Editor Modal ══ -->
<div id="roster-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:9998;align-items:flex-start;justify-content:center;overflow-y:auto;padding:28px 8px">
  <div style="background:var(--card);border:1px solid var(--border);border-radius:12px;width:min(96vw,500px);box-shadow:0 10px 40px rgba(0,0,0,.6);flex-shrink:0">
    <div style="padding:16px 18px 12px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between">
      <div style="font-size:1.05rem;font-weight:800;color:var(--gold)">✏️ Edit Roster</div>
      <button onclick="closeRosterModal()" style="background:none;border:none;color:var(--muted);font-size:1.4rem;cursor:pointer;line-height:1;padding:0 4px">✕</button>
    </div>
    <div style="padding:14px 16px">
      <input id="roster-search" type="text" placeholder="Search players…" oninput="filterRosterSearch()"
        style="width:100%;box-sizing:border-box;padding:9px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:.88rem;margin-bottom:12px">
      <div class="toggle-group" style="margin-bottom:12px">
        <button class="tgl-btn active" id="roster-tab-h"  onclick="switchRosterTab('h',this)">🏏 Hitters</button>
        <button class="tgl-btn"        id="roster-tab-sp" onclick="switchRosterTab('sp',this)">⚾ SP</button>
        <button class="tgl-btn"        id="roster-tab-rp" onclick="switchRosterTab('rp',this)">🔥 RP</button>
      </div>
      <div id="roster-player-list" style="max-height:55vh;overflow-y:auto"></div>
    </div>
    <div style="padding:8px 16px 14px;border-top:1px solid var(--border);text-align:center">
      <div id="roster-count-info" style="font-size:.78rem;color:var(--muted)"></div>
    </div>
  </div>
</div>

</body>
</html>
"""

def render_html(date_display, ts, n_games, hitters, all_pitchers,
                ta_hitters, ta_starters, ta_relievers,
                lb_data=None, lb_pitch_data=None):
    # Add is_starter flag to all pitchers for client-side filtering
    starters = []
    relievers = []
    for p in all_pitchers:
        p_copy = p.copy()
        p_copy["is_starter"] = p_copy.get("ip_float", 0) >= 3
        if p_copy["is_starter"]:
            starters.append(p_copy)
        else:
            relievers.append(p_copy)

    lb_sp = (lb_pitch_data or {}).get("starters", [])
    lb_rp = (lb_pitch_data or {}).get("relievers", [])

    return (HTML_TEMPLATE
        .replace("__DATE_DISPLAY__", date_display)
        .replace("__N_GAMES__", str(n_games))
        .replace("__TS__", ts)
        .replace("__HITTERS_JSON__",  json.dumps(hitters,        default=str))
        .replace("__ALL_PITCHERS_JSON__", json.dumps(all_pitchers, default=str))
        .replace("__TA_H_JSON__",     json.dumps(ta_hitters,    default=str))
        .replace("__TA_SP_JSON__",    json.dumps(ta_starters,   default=str))
        .replace("__TA_RP_JSON__",    json.dumps(ta_relievers,  default=str))
        .replace("__LB_JSON__",       json.dumps(lb_data or [],  default=str))
        .replace("__LB_SP_JSON__",    json.dumps(lb_sp,          default=str))
        .replace("__LB_RP_JSON__",    json.dumps(lb_rp,          default=str))
        .replace("__TA_NAMES_JSON__",  json.dumps(sorted(TEAM_ALEX_NAMES)))
        .replace("__FIREBASE_CONFIG__", json.dumps(FIREBASE_WEB_CONFIG))
    )
     