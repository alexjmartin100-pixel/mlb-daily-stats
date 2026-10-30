(function(){
  // Index and 2026 cards come from data/playercards.json. Older seasons
  // are separate files fetched the first time that year is shown.
  var _boot = window._playerCardsPayload || {};
  var _pcIdx  = _boot.index || [];
  var _pcData = _boot.players || {};
  var _pcLeaders = _boot.leaders || {};
  var _pcHist = {};
  var _pcAvailYears = _boot.avail_years || {};
  var _pcHistLoads = {};
  function _pcEnsureYear(year) {
    year = parseInt(year, 10);
    if (!year || year === 2026) return Promise.resolve();
    var key = String(year);
    if (_pcHist[key]) return Promise.resolve();
    if (_pcHistLoads[key]) return _pcHistLoads[key];
    _pcHistLoads[key] = fetch('data/playercards-' + key + '.json', {cache:'no-store'})
      .then(function(res) {
        if (!res.ok) throw new Error('player history ' + key);
        return res.json();
      })
      .then(function(obj) { _pcHist[key] = obj || {}; })
      .catch(function(err) {
        console.error(err);
        _pcHist[key] = _pcHist[key] || {};
      });
    return _pcHistLoads[key];
  }
  var _pcCurrentYear = 2026;
  var _pcCurrentId = null;

  var _TEAM_IDS = {
    ARI:109,ATL:144,BAL:110,BOS:111,CHC:112,CWS:145,CHW:145,CIN:113,CLE:114,
    COL:115,DET:116,HOU:117,KC:118,KCR:118,LAA:108,LAD:119,MIA:146,MIL:158,
    MIN:142,NYM:121,NYY:147,OAK:133,ATH:133,PHI:143,PIT:134,SD:135,SDP:135,SF:137,SFG:137,
    SEA:136,STL:138,TB:139,TBR:139,TEX:140,TOR:141,WSH:120,WSN:120,AZ:109
  };

  var _PITCH_NAMES = {
    FF:'4-Seam',SI:'Sinker',FC:'Cutter',SL:'Slider',ST:'Sweeper',SV:'Slurve',
    CH:'Change',FS:'Splitter',FO:'Fork',CU:'Curve',KC:'K-Curve',CS:'Slow Curve',
    KN:'Knuckle',EP:'Eephus',FA:'Fastball',SC:'Screw'
  };
  var _PITCH_COLORS = {
    FF:'#e74c3c',SI:'#c0392b',FA:'#e74c3c',FC:'#e67e22',
    SL:'#f1c40f',ST:'#f39c12',SV:'#d35400',
    CH:'#2ecc71',FS:'#27ae60',FO:'#16a085',
    CU:'#3498db',KC:'#2980b9',CS:'#1abc9c',
    KN:'#9b59b6',EP:'#8e44ad',SC:'#95a5a6'
  };

  // Stat direction for prior-year delta arrows. Keys are the leaderKey
  // strings used on each slider row. Any key NOT in these maps is treated
  // as higher-is-better. Note some stats flip between hitters and pitchers
  // (K%, Whiff%, Chase%, BB% all behave differently by player type).
  var _LOWER_BETTER_H = {
    chase_pct:1, whiff_pct:1, k_pct:1
  };
  var _LOWER_BETTER_P = {
    xera:1, xba:1, woba:1, xwoba:1, avg_ev:1,
    bb_pct:1, barrel_pct:1, hard_hit_pct:1
  };

  // ── Search ────────────────────────────────────────────────────────────────────
  // Defensive: the function is called from both oninput and onkeyup so it can
  // be invoked rapidly. Guard every step so no stray input state wrecks it.
  window._pcSearch = function(q) {
    try {
      var dd = document.getElementById('pc-dropdown');
      if (!dd) return;
      q = (q||'').trim().toLowerCase();
      if (q.length < 2) { dd.style.display='none'; return; }
      var matches = _pcIdx.filter(function(p) {
        return p.n.toLowerCase().indexOf(q) !== -1
            || (p.t||'').toLowerCase().indexOf(q) !== -1;
      }).slice(0,12);
      if (!matches.length) { dd.style.display='none'; return; }
      // Use onclick (which iOS synthesizes reliably) + ontouchend with an
      // explicit preventDefault fallback so the dropdown item fires even
      // when Safari's fastclick heuristics get weird.
      dd.innerHTML = matches.map(function(p) {
        return '<div class="pc-dd-item" data-pid="' + p.id + '" '
          + 'onclick="_pcShow(Number(this.dataset.pid))" '
          + 'ontouchend="event.preventDefault();_pcShow(Number(this.dataset.pid))" '
          + 'style="-webkit-tap-highlight-color:rgba(255,255,255,.1)">'
          + '<span style="font-weight:700">' + p.n + '</span>'
          + '<span style="color:#888;font-size:.78rem;margin-left:8px">' + (p.t||'') + '</span>'
          + '</div>';
      }).join('');
      dd.style.display = 'block';
    } catch (e) {
      console.error('[pc-search] error:', e);
    }
  };

  // Clear dropdown when typing clears the box
  document.addEventListener('click', function(e) {
    var el = e.target;
    var inSearch = el.id==='pc-search' || (el.closest && el.closest('#pc-dropdown'));
    if (!inSearch) {
      var dd2 = document.getElementById('pc-dropdown');
      if (dd2) dd2.style.display='none';
    }
  });

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
  window._pcFlipTo = function(id, year, view) {
    var card = document.getElementById('pc-card');
    if (!card) { _pcShow(id, year, view); return; }
    _pcFlipping = true;
    card.style.transition = 'transform .28s cubic-bezier(.4,0,.2,1)';
    card.style.transform = 'rotateY(90deg)';
    setTimeout(function(){
      _pcShow(id, year, view);
      card.style.transition = 'none';
      card.style.transform = 'rotateY(-90deg)';
      // eslint-disable-next-line no-unused-expressions
      card.offsetWidth;  // force reflow so the no-transition snap applies
      card.style.transition = 'transform .28s cubic-bezier(.4,0,.2,1)';
      card.style.transform = 'rotateY(0deg)';
      setTimeout(function(){
        _pcFlipping = false;
        var d = (year === 2026)
          ? _pcData[String(id)]
          : ((_pcHist[String(year)] || {})[String(id)] || null);
        if (d && view === 'alt' && d.pitcher_alt) d = d.pitcher_alt;
        _pcAutoCaptureCard((d && d.name) || 'player-card', id);
      }, 290);
    }, 290);
  };

  window._pcShow = function(id, year, view) {
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
      : ((_pcHist[String(year)] || {})[String(id)] || null);
    if (!d) {
      year = availYrs[availYrs.length - 1];
      _pcCurrentYear = year;
      d = (year === 2026)
        ? _pcData[String(id)]
        : ((_pcHist[String(year)] || {})[String(id)] || null);
    }
    if (!d) {
      if (year !== 2026 && !_pcHist[String(year)]) {
        var cardWait = document.getElementById('pc-card');
        if (cardWait) cardWait.innerHTML = '<div style="padding:28px 8px;color:#888">Loading…</div>';
        _pcEnsureYear(year).then(function() { window._pcShow(id, year, view); });
      }
      return;
    }

    // Dual-role swap: if alt view requested AND this year has alt data,
    // swap to it. Base record stays in _pcData; we just render the alt.
    var _hasAlt = !!(d && d.pitcher_alt);
    if (_pcCurrentView === 'alt' && _hasAlt) {
      d = d.pitcher_alt;
    } else if (_pcCurrentView === 'alt' && !_hasAlt) {
      _pcCurrentView = 'base';
    }

    // Prior-year data — alt-to-alt comparison when viewing alt.
    var _priorYear = year - 1;
    if (_priorYear !== 2026 && !_pcHist[String(_priorYear)]) {
      var _priorToken = id + ':' + year;
      if (!_pcShow._priorKick) _pcShow._priorKick = {};
      if (!_pcShow._priorKick[_priorToken]) {
        _pcShow._priorKick[_priorToken] = 1;
        _pcEnsureYear(_priorYear).then(function() {
          if (_pcCurrentId === id && _pcCurrentYear === year) _pcShow(id, year, _pcCurrentView);
        });
      }
    }
    var priorD = (_priorYear === 2026)
      ? _pcData[String(id)]
      : ((_pcHist[String(_priorYear)] || {})[String(id)] || null);
    if (_pcCurrentView === 'alt') {
      priorD = (priorD && priorD.pitcher_alt) ? priorD.pitcher_alt : null;
    }

    var teamId = _TEAM_IDS[d.team] || '';
    // Same-origin headshot written at build time. loading=lazy on the
    // img tag below fetches this one file when the card is shown.
    // html-to-image can embed it because it is not cross-origin.
    var photoUrl = 'photos/' + id + '.jpg';
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
    (function updateExternalYearSel(){
      var wrap = document.getElementById('pc-year-wrap');
      var sel  = document.getElementById('pc-year-sel');
      if (!wrap || !sel) return;
      if (availYrs.length <= 1) { wrap.style.display = 'none'; return; }
      wrap.style.display = '';
      var opts = availYrs.map(function(y) {
        return '<option value="' + y + '"' + (y===year?' selected':'') + '>' + y + '</option>';
      }).join('');
      sel.innerHTML = opts;
      // Preserve current view (hitter vs pitcher) across year changes.
      sel.onchange = function(){
        var y = parseInt(sel.value, 10);
        _pcEnsureYear(y).then(function(){ _pcShow(id, y, _pcCurrentView); });
      };
    })();

    // ── Dual-role toggle (Ohtani's hitter ↔ pitcher card) ───────────────
    var _baseRec = (year === 2026)
      ? _pcData[String(id)]
      : ((_pcHist[String(year)] || {})[String(id)] || null);
    // Update the EXTERNAL toggle button (outside #pc-card so its click
    // handler survives the auto-capture rasterization step that turns
    // the card into an <img>).
    (function updateViewToggle(){
      var btn = document.getElementById('pc-view-toggle');
      if (!btn) return;
      if (!_baseRec || !_baseRec.pitcher_alt) {
        btn.style.display = 'none';
        btn.onclick = null;
        return;
      }
      var showingAlt = (_pcCurrentView === 'alt');
      var targetView = showingAlt ? 'base' : 'alt';
      btn.style.display = '';
      btn.innerHTML = '&#8646; ' + (showingAlt ? 'Show hitting' : 'Show pitching');
      btn.onclick = function(){ _pcFlipTo(id, year, targetView); };
    })();
    // Header HTML still concats `+ dualToggle +`; keep this empty so
    // nothing renders inside the rasterized card.
    var dualToggle = '';

    // ── Dollar value badges (right of qualified marker) — 2026 only ────────
    // Earned $ (season-to-date, amber) sits to the LEFT of Proj $ (RoS, green).
    // Each badge carries a small E/P label + tooltip so they're unambiguous.
    var evBadge = '';
    if (year === 2026 && d.ev != null) {
      var evSign = d.ev >= 0 ? '$' : '-$';
      // Above replacement ($1) → amber asset; at/below replacement → dimmed.
      var evHot = d.ev > 1.0;
      var evC = evHot ? '#d8a13a' : '#8a8a8a';
      var evBg = evHot ? '#3a2f17' : '#2a2a2a';
      evBadge = '<span title="Earned $ — value produced season-to-date" '
        + 'style="font-size:1.15rem;font-weight:900;color:' + evC + ';'
        + 'background:' + evBg + ';border:1px solid ' + evC + ';padding:1px 8px;border-radius:6px;margin-left:8px">'
        + '<span style="font-size:.62rem;font-weight:700;opacity:.8;vertical-align:middle;margin-right:2px">E</span>'
        + evSign + Math.abs(d.ev).toFixed(1) + '</span>';
    }
    var dvBadge = '';
    if (year === 2026 && d.dv != null) {
      var dvSign = d.dv >= 0 ? '$' : '-$';
      dvBadge = '<span title="Projected $ — rest-of-season auction value" '
        + 'style="font-size:1.15rem;font-weight:900;color:#4caf50;'
        + 'background:#1b3a1b;border:1px solid #4caf50;padding:1px 8px;border-radius:6px;margin-left:8px">'
        + '<span style="font-size:.62rem;font-weight:700;opacity:.8;vertical-align:middle;margin-right:2px">P</span>'
        + dvSign + Math.abs(d.dv).toFixed(1) + '</span>';
    }

    // ── fWAR badge ───────────────────────────────────────────────────────
    var warBadge = '';
    if (d.war != null) {
      warBadge = '<div style="font-size:.72rem;font-weight:700;color:#8ab4f8;margin-top:3px">'
        + d.war.toFixed(1) + ' fWAR</div>';
    }

    // ── Header ────────────────────────────────────────────────────────────
    var pcImgId = 'pc-headshot-' + id;
    var header =
      '<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">'
      + '<div style="width:120px;height:120px;flex-shrink:0;border-radius:50%;overflow:hidden;background:linear-gradient(to bottom, #a0a0a3 0%, #ececf0 100%)">'
      + '<img id="' + pcImgId + '" src="' + photoUrl + '" loading="lazy" '
      +   'onerror="this.parentElement.style.display=\x27none\x27" '
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
    function fmt3(v) { return v != null ? v.toFixed(3) : '–'; }
    function fmt2(v) { return v != null ? v.toFixed(2) : '–'; }
    function fmt1(v) { return v != null ? v.toFixed(1) : '–'; }
    function fmtN(v) { return v != null ? v : '–'; }
    var myLeaders = (year === 2026 && d.qual && _pcLeaders[String(id)]) ? _pcLeaders[String(id)] : [];
    var leaderMap = {};
    myLeaders.forEach(function(k){ leaderMap[k]=true; });
    var std_items;
    if (d.type === 'p') {
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
    } else {
    std_items = [
      ['G',   fmtN(d.g),  false],  ['PA',  fmtN(d.pa), false],  ['AB',  fmtN(d.ab), false],
      ['R',   fmtN(d.r),  !!leaderMap.r],   ['HR',  fmtN(d.hr),  !!leaderMap.hr],
      ['RBI', fmtN(d.rbi),!!leaderMap.rbi],  ['SB',  fmtN(d.sb),  !!leaderMap.sb],
      ['AVG', fmt3(d.avg), !!leaderMap.avg],
      ['OBP', fmt3(d.obp), !!leaderMap.obp], ['OPS', fmt3(d.ops), !!leaderMap.ops],
    ];
    }
    var std_html =
      '<div style="background:#111;border:1px solid #222;border-radius:8px;padding:6px 8px;margin-bottom:10px">'
      + '<div style="font-size:.55rem;font-weight:700;color:#999;letter-spacing:.06em;margin-bottom:4px">' + year + ' STATS</div>'
      + '<div style="display:flex;flex-wrap:wrap;gap:0">'
      + std_items.map(function(x) {
          var col = x[2] ? '#f0c040' : '#ddd';
          return '<div style="text-align:center;padding:2px 4px;min-width:36px;flex:1">'
            + '<div style="font-size:.8rem;font-weight:800;color:' + col + '">' + x[1] + '</div>'
            + '<div style="font-size:.5rem;color:' + (x[2]?'#b8982e':'#999') + ';margin-top:1px;letter-spacing:.04em">' + x[0] + '</div>'
            + '</div>';
        }).join('') + '</div></div>';

    // ── Percentile bars (Savant style) ─────────────────────────────────────
    // [label, rawVal, pctVal, fmtFn, leaderKey]
    var f3  = function(v){return v!=null?v.toFixed(3):null;};
    var fMph= function(v){return v!=null?v.toFixed(1)+' mph':null;};
    var fPct= function(v){return v!=null?v.toFixed(1)+'%':null;};
    var statRows;
    // Helper: safely pull a field off priorD (null when no prior-year data).
    var _pv = function(k){ return (priorD && priorD[k] != null) ? priorD[k] : null; };
    if (d.type === 'p') {
      statRows = [
        ['xERA',      d.xera,  d.pct.xera,   function(v){return v!=null?v.toFixed(2):null;},'xera', _pv('xera')],
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
    } else {
    statRows = [
      ['xWOBA',     d.xwoba, d.pct.xwoba,   function(v){return v!=null?v.toFixed(3):null;},'xwoba', _pv('xwoba')],
      ['xBA',       d.xba,   d.pct.xba,     function(v){return v!=null?v.toFixed(3):null;},'xba', _pv('xba')],
      ['xSLG',      d.xslg,  d.pct.xslg,    function(v){return v!=null?v.toFixed(3):null;},'xslg', _pv('xslg')],
      ['Avg EV',    d.avg_ev,d.pct.avg_ev,  function(v){return v!=null?v.toFixed(1)+' mph':null;},'avg_ev', _pv('avg_ev')],
      ['Max EV',    d.max_ev,d.pct.max_ev,  function(v){return v!=null?v.toFixed(1)+' mph':null;},'max_ev', _pv('max_ev')],
      ['Barrel%',   d.brl,   d.pct.barrel_pct, function(v){return v!=null?v.toFixed(1)+'%':null;},'barrel_pct', _pv('brl')],
      ['Hard Hit%', d.hh,    d.pct.hard_hit_pct,function(v){return v!=null?v.toFixed(1)+'%':null;},'hard_hit_pct', _pv('hh')],
      ['LA Sweet-Spot%',d.ss,   d.pct.sweet_spot_pct,function(v){return v!=null?v.toFixed(1)+'%':null;},'sweet_spot_pct', _pv('ss')],
      ['Bat Speed', d.bs,    d.pct.bat_speed,function(v){return v!=null?v.toFixed(1)+' mph':null;},'bat_speed', _pv('bs')],
      ['Squared Up%',d.sq,   d.pct.squared_up_pct,function(v){return v!=null?v.toFixed(1)+'%':null;},'squared_up_pct', _pv('sq')],
      ['Chase%',    d.ch,    d.pct.chase_pct,function(v){return v!=null?v.toFixed(1)+'%':null;},'chase_pct', _pv('ch')],
      ['Whiff%',    d.wh,    d.pct.whiff_pct,function(v){return v!=null?v.toFixed(1)+'%':null;},'whiff_pct', _pv('wh')],
      ['K%',        d.kp,    d.pct.k_pct,   function(v){return v!=null?v.toFixed(1)+'%':null;},'k_pct', _pv('kp')],
      ['BB%',       d.bbp,   d.pct.bb_pct,  function(v){return v!=null?v.toFixed(1)+'%':null;},'bb_pct', _pv('bbp')],
      ['Sprint Speed',d.spd,   d.pct.sprint_speed,function(v){return v!=null?v.toFixed(1)+' ft/s':null;},'sprint_speed', _pv('spd')],
    ];
    }

    function pctColor(p) {
      // Vibrant: 1=deep blue #1e3fba → 50=grey #888 → 100=vibrant red #e02020
      if (p >= 50) {
        var t = (p - 50) / 50;
        return 'rgb(' + Math.round(136 + 88*t) + ',' + Math.round(136 - 104*t) + ',' + Math.round(136 - 104*t) + ')';
      } else {
        var t = (p - 1) / 49;
        return 'rgb(' + Math.round(30 + 106*t) + ',' + Math.round(63 + 73*t) + ',' + Math.round(186 - 50*t) + ')';
      }
    }
    function pctBar(label, rawVal, pct, fmtFn, leaderKey, priorVal) {
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
      if (rawVal != null && priorVal != null) {
        var isP = d.type === 'p';
        var isLowerBetter = isP
          ? !!_LOWER_BETTER_P[leaderKey]
          : !!_LOWER_BETTER_H[leaderKey];
        var diff = rawVal - priorVal;
        if (diff !== 0) {
          var up = diff > 0;
          var improved = isLowerBetter ? !up : up;
          var arrowCol = improved ? '#2ecc71' : '#e74c3c';
          var arrowCh  = up ? '▲' : '▼';  // ▲ / ▼
          arrowHtml = '<span style="color:' + arrowCol + ';font-size:.62rem;'
                    + 'margin-right:3px;vertical-align:middle">'
                    + arrowCh + '</span>';
        }
      }
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
      if (pctDisp == null) {
        barHtml = '<div style="display:flex;align-items:center;gap:6px">'
                + '<div style="flex:1;height:8px;border-radius:4px;background:#2a2a2a"></div>'
                + valCell
                + '</div>';
      } else {
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
      }
      var labelCol = isGold ? '#f0c040' : '#fff';

      return '<div style="margin-bottom:8px">'
        + '<div style="margin-bottom:2px">'
        +   '<span style="font-size:.88rem;color:' + labelCol + ';font-weight:800">' + label + '</span>'
        + '</div>'
        + barHtml
        + '</div>';
    }

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
      + statRows.map(function(r){return pctBar(r[0],r[1],r[2],r[3],r[4],r[5]);}).join('')
      + '</div>';

    // ── Bottom section: batted ball (hitters) OR pitch arsenal (pitchers) ─
    function fmtPct(v) { return v != null ? v.toFixed(1)+'%' : '–'; }
    function fmtDeg(v) { return v != null ? v.toFixed(1)+'°' : '–'; }
    var bb_html = '';
    if (d.type === 'p') {
      var ars = d.ars || [];
      var arsRows = '';
      if (ars.length) {
        arsRows = ars.map(function(pt){
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
        }).join('');
      } else {
        arsRows = '<div style="font-size:.72rem;color:#666;text-align:center;padding:6px">No arsenal data</div>';
      }
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
    } else if (d.pull!=null || d.gb!=null) {
      var bbItems = [
        ['Pull%',fmtPct(d.pull)],['Center%',fmtPct(d.cent)],['Oppo%',fmtPct(d.oppo)],
        ['LA°',fmtDeg(d.la)],
        ['GB%',fmtPct(d.gb)],['LD%',fmtPct(d.ld)],['FB%',fmtPct(d.fb)],['PU%',fmtPct(d.pu)],
      ];
      bb_html =
        '<div style="background:#111;border:1px solid #222;border-radius:8px;padding:8px 12px;margin-bottom:10px">'
        + '<div style="font-size:.6rem;font-weight:700;color:#999;letter-spacing:.06em;margin-bottom:6px">BATTED BALL PROFILE</div>'
        + '<div style="display:flex;flex-wrap:wrap">'
        + bbItems.map(function(x){
            return '<div style="text-align:center;padding:3px 6px;min-width:52px;flex:1">'
              + '<div style="font-size:.82rem;font-weight:700;color:#ddd">' + x[1] + '</div>'
              + '<div style="font-size:.55rem;color:#999;margin-top:1px;letter-spacing:.04em">' + x[0] + '</div>'
              + '</div>';
          }).join('')
        + '</div></div>';
    }
    // ── Assemble card ─────────────────────────────────────────────────────
    var logoBadge = '';
    if (logoBgUrl) {
      // data-team holds the team abbreviation so the Save-as-image onclone
      // callback can swap the SVG src (which html2canvas renders badly) for
      // an ESPN PNG equivalent (which it handles fine).
      var _teamAbbrLo = (d.team || '').toString().toLowerCase();
      logoBadge = '<img src="' + logoBgUrl + '" loading="lazy" data-team="' + _teamAbbrLo + '" style="position:absolute;top:13px;right:6px;width:120px;height:120px;object-fit:contain;opacity:.85;z-index:1;filter:drop-shadow(1px 0 0 #fff) drop-shadow(-1px 0 0 #fff) drop-shadow(0 1px 0 #fff) drop-shadow(0 -1px 0 #fff)" onerror="this.style.display=\x27none\x27"/>';
    }
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
    if (_saveStatus) { _saveStatus.textContent = ''; _saveStatus.style.display = 'none'; }
    // Auto-convert the just-rendered HTML card into a long-pressable <img>.
    // iOS Safari's native "Save Image to Photos" menu only appears on <img>
    // elements, so we rasterize the card immediately and swap it in.
    // `_pcCurrentId` tracks which player is being displayed so if the user
    // picks a different player mid-capture, we don't clobber their card.
    // Reset the card transform on normal show calls (player/year
    // switches). During a flip, _pcFlipTo manages the transform.
    if (!_pcFlipping) {
      var _cardEl = document.getElementById('pc-card');
      if (_cardEl) { _cardEl.style.transform = 'rotateY(0deg)'; }
    }
    // Defer auto-capture until any in-flight flip animation finishes —
    // capturing a rotated card would bake the rotation into the saved <img>.
    if (_pcFlipping) return;
    // Debounce the (expensive) html-to-image rasterization. The interactive
    // HTML card is already on screen above; rasterizing to a saveable <img>
    // is what froze the UI on every open. Now we only do it once the user
    // settles on a card (~450ms), and cancel it the moment they open another
    // — so browsing card-to-card is instant.
    if (window._pcCaptureTimer) clearTimeout(window._pcCaptureTimer);
    (function(_capName, _capId){
      window._pcCaptureTimer = setTimeout(function(){
        if (_capId !== _pcCurrentId) return;   // moved on — skip
        _pcAutoCaptureCard(_capName, _capId);
      }, 450);
    })((d && d.name) || 'player-card', id);
  };

  // Rasterize #pc-card via html-to-image and replace its contents with a
  // single <img> so iOS users can long-press → Save to Photos. Falls back
  // to leaving the HTML card as-is (with a visible error in the status
  // box) if capture fails.
  window._pcAutoCaptureCard = function(playerName, capturedForId){
    var card = document.getElementById('pc-card');
    var status = document.getElementById('pc-save-status');
    if (!card || !card.firstChild) return;
    window._pcLoadHtmlToImage(function(err){
      if (err) {
        if (status) { status.style.display = 'block'; status.textContent = '❌ Could not load image library: ' + (err.message || ''); }
        return;
      }
      // If the user has since picked a different player, abort.
      if (capturedForId !== _pcCurrentId) return;
      // Gradient-zero-dim guard: same fix we use in the manual save flow.
      var restoreFns = [];
      card.querySelectorAll('*').forEach(function(el){
        var bg = el.style && (el.style.background || el.style.backgroundImage) || '';
        if (bg.indexOf('gradient') >= 0 && (el.offsetWidth === 0 || el.offsetHeight === 0)) {
          var prevDisp = el.style.display;
          el.style.setProperty('display', 'flex', 'important');
          restoreFns.push(function(){ el.style.display = prevDisp; });
        }
      });
      var w = card.offsetWidth;
      window.htmlToImage.toPng(card, {
        backgroundColor: '#0a0a0a',
        pixelRatio: 2,
        cacheBust: false,
        skipFonts: false
      }).then(function(dataUrl){
        restoreFns.forEach(function(f){ try{ f(); } catch(e){} });
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
      }).catch(function(e){
        restoreFns.forEach(function(f){ try{ f(); } catch(e2){} });
        if (status) {
          status.style.display = 'block';
          status.textContent = '❌ Image capture failed: ' + (e && e.message || 'unknown');
        }
      });
    });
  };

  // Lazy-load html2canvas on first use so the 150KB library doesn't bloat
  // the initial page payload. Cached by the browser for subsequent clicks.
  window._pcLoadHtml2Canvas = function(cb){
    if (window.html2canvas) { cb(null); return; }
    var s = document.createElement('script');
    s.src = 'https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js';
    s.onload = function(){ cb(null); };
    s.onerror = function(){ cb(new Error('Failed to load html2canvas')); };
    document.head.appendChild(s);
  };

  // html-to-image is a more iOS-friendly alternative to html2canvas.
  // html2canvas uses its own rasterizer that re-fetches images with
  // crossOrigin="anonymous" — iOS Safari often hangs indefinitely on this.
  // html-to-image renders via <foreignObject> inline SVG, which iOS handles
  // cleanly. ~20KB library.
  window._pcLoadHtmlToImage = function(cb){
    if (window.htmlToImage) { cb(null); return; }
    var called = false;
    var s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/html-to-image@1.11.11/dist/html-to-image.js';
    s.onload  = function(){ if (!called) { called = true; cb(null); } };
    s.onerror = function(){ if (!called) { called = true; cb(new Error('Script load failed')); } };
    // 8-second safety timeout in case the script tag never fires either
    // event (iOS network edge cases).
    setTimeout(function(){
      if (!called) { called = true; cb(window.htmlToImage ? null : new Error('Script load timed out')); }
    }, 8000);
    document.head.appendChild(s);
  };

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
  function _pcSvgToPngDataUrl(svgUrl){
    return fetch(svgUrl, {mode: 'cors'})
      .then(function(r){ return r.ok ? r.text() : Promise.reject(new Error('HTTP ' + r.status)); })
      .then(function(svgText){
        return new Promise(function(resolve, reject){
          var blob = new Blob([svgText], {type: 'image/svg+xml'});
          var url = URL.createObjectURL(blob);
          var im = new Image();
          im.onload = function(){
            var size = 256;
            var c = document.createElement('canvas');
            c.width = size; c.height = size;
            var ctx = c.getContext('2d');
            ctx.drawImage(im, 0, 0, size, size);
            URL.revokeObjectURL(url);
            try { resolve(c.toDataURL('image/png')); }
            catch(e){ reject(e); }
          };
          im.onerror = function(){
            URL.revokeObjectURL(url);
            reject(new Error('svg img load failed'));
          };
          im.src = url;
        });
      });
  }

  // Helpers to append visible progress/error lines to the status box.
  // iOS Safari users can't easily access a dev console, so we surface every
  // step on-screen instead. They can screenshot and send to debug.
  function _pcStatus(status, msg, isErr){
    if (!status) return;
    status.style.display = 'block';
    var line = (isErr ? '❌ ' : '• ') + msg;
    status.textContent = status.textContent
      ? status.textContent + '\n' + line
      : line;
    if (isErr) status.style.borderColor = '#c04040';
  }

  window.pcSaveAsImage = function(){
    var status = document.getElementById('pc-save-status');
    var btn = document.getElementById('pc-save-btn');
    // Reset the status box on every click so we don't accumulate history.
    if (status) { status.textContent = ''; status.style.borderColor = '#333'; }
    _pcStatus(status, 'Click received');
    console.log('[pcSaveAsImage] click received');
    var card = document.getElementById('pc-card');
    if (!card) { _pcStatus(status, 'ERROR: #pc-card not found', true); return; }
    if (!card.firstChild) { _pcStatus(status, 'Pick a player first.', true); return; }
    _pcStatus(status, 'Card ready, starting capture…');
    if (btn) btn.disabled = true;

    // Global error listener for the duration of this capture — catches
    // anything that slips past our try/catch/catch chain.
    var errHandler = function(e){
      _pcStatus(status, 'Window error: ' + (e.message || e.type), true);
    };
    var rejHandler = function(e){
      _pcStatus(status, 'Unhandled rejection: ' + (e.reason && e.reason.message || String(e.reason)), true);
    };
    window.addEventListener('error', errHandler);
    window.addEventListener('unhandledrejection', rejHandler);
    var cleanup = function(){
      window.removeEventListener('error', errHandler);
      window.removeEventListener('unhandledrejection', rejHandler);
      if (btn) btn.disabled = false;
    };

    // Safety-net: if html2canvas hangs, we need to show SOMETHING after 15s.
    var captureDone = false;
    setTimeout(function(){
      if (captureDone) return;
      _pcStatus(status, 'Timed out after 15s — html2canvas never returned', true);
      cleanup();
    }, 15000);
    (function startCapture(){
    _pcStatus(status, 'Loading capture library…');
    window._pcLoadHtmlToImage(function(err){
      if (err) {
        _pcStatus(status, 'Library load failed: ' + (err.message || 'unknown'), true);
        captureDone = true; cleanup(); return;
      }
      _pcStatus(status, 'Library loaded, rendering card…');
      // Fix gradient-backed zero-dimension elements (rasterizer crash guard).
      var restoreFns = [];
      card.querySelectorAll('*').forEach(function(el){
        var bg = el.style && (el.style.background || el.style.backgroundImage) || '';
        if (bg.indexOf('gradient') >= 0 && (el.offsetWidth === 0 || el.offsetHeight === 0)) {
          var prevDisp = el.style.display;
          el.style.setProperty('display', 'flex', 'important');
          restoreFns.push(function(){ el.style.display = prevDisp; });
        }
      });
      var restoreDom = function(){ restoreFns.forEach(function(f){ try{ f(); } catch(e){} }); };
      // Headshot is a same-origin file under photos/. html-to-image embeds
      // it without a cross-origin fetch.
      window.htmlToImage.toPng(card, {
        backgroundColor: '#0a0a0a',
        pixelRatio: 2,
        cacheBust: true,
        skipFonts: false
      })
        .then(function(dataUrl){
          captureDone = true;
          restoreDom();
          _pcStatus(status, 'Image generated (' + Math.round(dataUrl.length/1024) + ' KB), opening modal…');
          var name = (btn && btn.getAttribute('data-player-name')) || 'player-card';
          try { _pcShowSaveModal(dataUrl, name); }
          catch(e){ _pcStatus(status, 'Modal failed: ' + e.message, true); cleanup(); return; }
          setTimeout(function(){ if (status) status.style.display = 'none'; }, 500);
          cleanup();
        })
        .catch(function(e){
          captureDone = true;
          restoreDom();
          _pcStatus(status, 'Render failed: ' + (e && e.message || 'unknown'), true);
          cleanup();
        });
    });
    })();
  };

  // Show a modal overlay with the captured image. On iOS the user long-
  // presses the <img> → Safari's "Save Image to Photos" menu appears,
  // which is the reliable way to get a PNG into the camera roll. On
  // desktop they can right-click → Save Image As, or drag to their
  // desktop. The modal closes on tap outside or the ✕ button.
  function _pcShowSaveModal(dataUrl, playerName){
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
    modal.addEventListener('click', function(e){
      if (e.target === modal) _pcCloseSaveModal();
    });
    document.body.appendChild(modal);
    // Prevent background scroll while modal is open
    document.body.style.overflow = 'hidden';
  }
  window._pcCloseSaveModal = function(){
    var m = document.getElementById('pc-save-modal');
    if (m) m.remove();
    document.body.style.overflow = '';
  };

})();
