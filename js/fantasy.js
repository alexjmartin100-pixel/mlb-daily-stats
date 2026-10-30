/* ── Hitter / Pitcher main toggle ────────────────────────────────── */
function fantSwitch(which) {
  document.getElementById('fant-h-wrap').style.display     = which==='h'     ? '' : 'none';
  document.getElementById('fant-p-wrap').style.display     = which==='p'     ? '' : 'none';
  document.getElementById('fant-trade-wrap').style.display = which==='trade' ? '' : 'none';
  var sw = document.getElementById('fant-standings-wrap');
  if (sw) sw.style.display = which==='standings' ? '' : 'none';
  var pw = document.getElementById('fant-proj-wrap');
  if (pw) pw.style.display = which==='proj' ? '' : 'none';
  var cw = document.getElementById('fant-cmp-wrap');
  if (cw) cw.style.display = which==='cmp' ? '' : 'none';
  var ww = document.getElementById('fant-waiver-wrap');
  if (ww) ww.style.display = which==='waiver' ? '' : 'none';
  ['h','p','standings','trade','proj','cmp','waiver'].forEach(function(w) {
    var btn = document.getElementById('fant-'+w+'-btn');
    if (!btn) return;
    var on = (w === which);
    btn.style.borderBottom = on ? '3px solid var(--accent)' : 'none';
    btn.style.color = on ? '#fff' : '';
  });
}

/* ── Standings sort: click any <th> to sort the standings table ────
   First click on a column sorts teams BEST first: higher-is-better
   stats (R, HR, RBI, etc.) sort descending; lower-is-better stats
   (ERA, WHIP, K-Bat, GB, Rank) sort ascending. Subsequent clicks on
   the same column reverse the direction. The data-rev attribute on
   each <th> ("hi" or "lo") tells us which direction is "best". */
window._standingsSort = {col: 'rank', dir: 1};
function fantStandingsSort(col, type, rev) {
  var tbl = document.getElementById('fant-standings-tbl');
  if (!tbl) return;
  var st = window._standingsSort;
  if (st.col === col) {
    st.dir = -st.dir;
  } else {
    st.col = col;
    // First click on a new column: 'hi' means higher-is-better → desc (-1).
    // 'lo' means lower-is-better (or smaller rank/GB) → asc (1).
    st.dir = (rev === 'hi') ? -1 : 1;
  }
  var tbody = tbl.querySelector('tbody');
  var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
  rows.sort(function(a, b){
    var av, bv;
    if (col === 'rank') { av = +a.dataset.rank; bv = +b.dataset.rank; }
    else if (col === 'team') { av = a.dataset.team || ''; bv = b.dataset.team || ''; }
    else if (col === 'wpct') { av = +a.dataset.wpct; bv = +b.dataset.wpct; }
    else if (col === 'gb')   { av = +a.dataset.gb;   bv = +b.dataset.gb; }
    else if (col.indexOf('cat_') === 0) {
      var idx = Array.prototype.indexOf.call(tbl.querySelectorAll('thead th'),
        tbl.querySelector('th[data-sort="' + col + '"]'));
      var parseNum = function(td){
        var t = (td.textContent || '').trim();
        if (t === '—' || t === '') return null;
        // Re-prepend leading 0 on rate stats like ".315"
        if (t.charAt(0) === '.') t = '0' + t;
        var f = parseFloat(t);
        return isNaN(f) ? null : f;
      };
      av = parseNum(a.cells[idx]);
      bv = parseNum(b.cells[idx]);
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
    }
    if (av < bv) return -st.dir;
    if (av > bv) return  st.dir;
    return 0;
  });
  rows.forEach(function(r){ tbody.appendChild(r); });
  // Visual cue on active header
  tbl.querySelectorAll('thead th').forEach(function(th){
    th.classList.remove('sort-asc', 'sort-desc');
    if (th.dataset.sort === col) {
      th.classList.add(st.dir === 1 ? 'sort-asc' : 'sort-desc');
    }
  });
}
// Wire up header clicks after the tab hydrates.
document.addEventListener('click', function(e){
  var th = e.target.closest && e.target.closest('#fant-standings-tbl thead th[data-sort]');
  if (th) fantStandingsSort(th.dataset.sort, th.dataset.type, th.dataset.rev);
});

/* ── SP / RP filter ──────────────────────────────────────────── */
var _fpRole = 'all';
var _fhPos = 'all';

/* ── Filter/search engine (perf-tuned) ─────────────────────────────
   Earlier version called tr.textContent.toLowerCase() on every row plus
   applyFantColors() on every keystroke. With 600+ rows that froze the
   page for 20–30 seconds per character. Three changes:
     1. Match against the pre-lowercased data-search attribute we now
        emit at render time (no textContent reads, no per-keystroke
        re-tokenization).
     2. Don't re-run applyFantColors during filter/search — the gold
        leader colors are derived from data-val attributes, which don't
        change when rows are hidden or sorted. It only needs to run once
        on initial load.
     3. Wrap the row pass in requestAnimationFrame and debounce search
        input by ~80ms so a typed word collapses into one pass instead
        of one per character. */
function _fantApplyFilter(tblId, predicate) {
  var tbl = document.getElementById(tblId);
  if (!tbl) return;
  // Cache row list on the table the first time we touch it — tbody rows
  // don't change, so paying for querySelectorAll once is plenty.
  var rows = tbl._fantRowCache;
  if (!rows) {
    rows = Array.from(tbl.querySelectorAll('tbody tr'));
    tbl._fantRowCache = rows;
  }
  // Defer DOM writes to the next frame to coalesce reflows.
  if (tbl._fantRaf) cancelAnimationFrame(tbl._fantRaf);
  tbl._fantRaf = requestAnimationFrame(function() {
    for (var i = 0; i < rows.length; i++) {
      var tr = rows[i];
      var show = predicate(tr);
      var hidden = (tr.style.display === 'none');
      // Avoid touching style if it'd be a no-op — eliminates needless reflow.
      if (show && hidden) tr.style.display = '';
      else if (!show && !hidden) tr.style.display = 'none';
    }
  });
}

function fantHitFilter(pos) {
  _fhPos = pos;
  var srch = ((document.getElementById('fant-h-search') || {}).value || '').toLowerCase();
  _fantApplyFilter('fant-h-tbl', function(tr) {
    var pv = (tr.dataset.pos || '').split('/');
    var matchPos = (pos === 'all') || pv.indexOf(pos) >= 0;
    var matchSearch = !srch || (tr.dataset.search || '').indexOf(srch) >= 0;
    return matchPos && matchSearch;
  });
  ['all','C','1B','2B','3B','SS','OF','DH'].forEach(function(p) {
    var btn = document.getElementById('fh-'+p+'-btn');
    if (btn) {
      btn.style.borderBottom = (p === pos) ? '3px solid var(--accent)' : 'none';
      btn.style.color = (p === pos) ? '#fff' : '';
    }
  });
}

/* Debounced text-search dispatchers — coalesce keystrokes within 80ms so
   typing "Perkins" runs one filter pass instead of seven. */
var _fhSearchTimer = null, _fpSearchTimer = null;
function fantSearchHit(q) {
  if (_fhSearchTimer) clearTimeout(_fhSearchTimer);
  _fhSearchTimer = setTimeout(function() {
    var ql = (q || '').toLowerCase();
    _fantApplyFilter('fant-h-tbl', function(tr) {
      var pv = (tr.dataset.pos || '').split('/');
      var matchPos = (_fhPos === 'all') || pv.indexOf(_fhPos) >= 0;
      var matchSearch = !ql || (tr.dataset.search || '').indexOf(ql) >= 0;
      return matchPos && matchSearch;
    });
  }, 80);
}

function fantPitchFilter(role) {
  _fpRole = role;
  var srch = ((document.getElementById('fant-p-search') || {}).value || '').toLowerCase();
  _fantApplyFilter('fant-p-tbl', function(tr) {
    var matchRole = (role === 'all') || (tr.dataset.role === role);
    var matchSearch = !srch || (tr.dataset.search || '').indexOf(srch) >= 0;
    return matchRole && matchSearch;
  });
  ['all','sp','rp'].forEach(function(r) {
    var btn = document.getElementById('fp-'+r+'-btn');
    if (btn) {
      btn.style.borderBottom = (r === role) ? '3px solid var(--accent)' : 'none';
      btn.style.color = (r === role) ? '#fff' : '';
    }
  });
}

/* Generic single-table search (no role/pos filter coupling). */
function fantSearch(tblId, q) {
  var ql = (q || '').toLowerCase();
  _fantApplyFilter(tblId, function(tr) {
    return !ql || (tr.dataset.search || '').indexOf(ql) >= 0;
  });
}

function fantSearchPit(q) {
  if (_fpSearchTimer) clearTimeout(_fpSearchTimer);
  _fpSearchTimer = setTimeout(function() {
    var ql = (q || '').toLowerCase();
    _fantApplyFilter('fant-p-tbl', function(tr) {
      var matchRole = (_fpRole === 'all') || (tr.dataset.role === _fpRole);
      var matchSearch = !ql || (tr.dataset.search || '').indexOf(ql) >= 0;
      return matchRole && matchSearch;
    });
  }, 80);
}

/* ── Column sort ─────────────────────────────────────────────── */
function fantSort(tblId, th) {
  var tbl = document.getElementById(tblId);
  if (!tbl) return;
  var col = parseInt(th.dataset.col, 10);
  var tbody = tbl.querySelector('tbody');
  var rows  = Array.from(tbody.querySelectorAll('tr'));
  // Default first click = descending (highest first)
  var sortDesc = th.dataset.dir !== 'desc';
  th.dataset.dir = sortDesc ? 'desc' : 'asc';
  rows.sort(function(a, b) {
    var av = parseFloat(a.cells[col].dataset.val) || 0;
    var bv = parseFloat(b.cells[col].dataset.val) || 0;
    return sortDesc ? (bv - av) : (av - bv);
  });
  rows.forEach(function(r) { tbody.appendChild(r); });
  Array.from(tbody.querySelectorAll('tr')).forEach(function(tr, i) {
    var rc = tr.querySelector('.rank-col');
    if (rc) { rc.textContent = i + 1; rc.dataset.val = i + 1; }
  });
  // Update sort indicator arrows
  Array.from(tbl.querySelectorAll('thead th .fsi')).forEach(function(s) {
    s.innerHTML = '&#9660;'; s.style.color = 'var(--muted)'; s.style.opacity = '.35';
  });
  var activeInd = th.querySelector('.fsi');
  if (activeInd) {
    activeInd.innerHTML = sortDesc ? '&#9660;' : '&#9650;';
    activeInd.style.color = 'var(--accent)';
    activeInd.style.opacity = '1';
  }
  applyFantColors(tblId);
}

/* ── Season Projections column sort ──────────────────────────── */
/* Sorts the #proj-table by the clicked header's data-col, using each
   <td>'s data-sort attribute (numeric or alphabetic). Each header has a
   data-default direction ("asc" or "desc"); first click uses that, then
   clicks toggle. Cell colors are baked in (per-stat rank), so we don't
   need to recompute them — we only re-order rows. */
function projSort(th) {
  var tbl = document.getElementById('proj-table');
  if (!tbl) return;
  var col = parseInt(th.dataset.col, 10);
  if (isNaN(col)) return;
  var tbody = tbl.querySelector('tbody');
  if (!tbody) return;
  var rows = Array.from(tbody.querySelectorAll('tr'));

  var def = th.dataset.default || 'desc';
  var prev = th.dataset.dir || '';
  var dir;
  if (prev === 'asc')       dir = 'desc';
  else if (prev === 'desc') dir = 'asc';
  else                       dir = def;

  // Clear dir on every header, then set on the active one
  Array.from(tbl.querySelectorAll('thead th')).forEach(function(h) {
    delete h.dataset.dir;
  });
  th.dataset.dir = dir;

  function _val(tr) {
    var cell = tr.cells[col];
    if (!cell) return null;
    return cell.dataset.sort != null ? cell.dataset.sort : cell.textContent;
  }

  rows.sort(function(a, b) {
    var av = _val(a), bv = _val(b);
    var an = parseFloat(av), bn = parseFloat(bv);
    var cmp;
    if (!isNaN(an) && !isNaN(bn)) {
      cmp = an - bn;
    } else {
      cmp = String(av).localeCompare(String(bv));
    }
    return dir === 'desc' ? -cmp : cmp;
  });
  rows.forEach(function(r) { tbody.appendChild(r); });

  // Update header arrow indicators
  Array.from(tbl.querySelectorAll('thead th .psi')).forEach(function(s) {
    s.innerHTML = '&#9660;';
    s.style.color = 'var(--muted)';
    s.style.opacity = '.35';
  });
  var ind = th.querySelector('.psi');
  if (ind) {
    ind.innerHTML = (dir === 'desc') ? '&#9660;' : '&#9650;';
    ind.style.color = 'var(--accent)';
    ind.style.opacity = '1';
  }
}

/* ── Column color coding (gold leader + red→white→blue gradient) ─── */
/* Rankings always computed from ALL rows (full league), not just visible ones */
function applyFantColors(tblId) {
  var tbl = document.getElementById(tblId);
  if (!tbl) return;
  var allRows = Array.from(tbl.querySelectorAll('tbody tr'));
  if (!allRows.length) return;
  var nCols = allRows[0].cells.length;
  // Gold for leader in each category column
  // (skip rank=0, name=1, team=2, earned$=3, proj$=4 — dollars handled below)
  for (var c = 5; c < nCols; c++) {
    var vals = [];
    allRows.forEach(function(tr) {
      var v = parseFloat(tr.cells[c] && tr.cells[c].dataset.val);
      if (!isNaN(v)) vals.push(v);
    });
    if (!vals.length) continue;
    var best = Math.max.apply(null, vals);
    allRows.forEach(function(tr) {
      var cell = tr.cells[c];
      if (!cell) return;
      var v = parseFloat(cell.dataset.val);
      if (isNaN(v)) return;
      if (Math.abs(v - best) < 0.00001) {
        cell.style.color = '#f0c040';
        cell.style.fontWeight = '700';
      }
    });
  }
  // Gold for top dollar value in the Earned$ (col 3) and Proj$ (col 4) columns
  [3, 4].forEach(function(dc) {
    var dolVals = [];
    allRows.forEach(function(tr) {
      var v = parseFloat(tr.cells[dc] && tr.cells[dc].dataset.val);
      if (!isNaN(v)) dolVals.push(v);
    });
    if (!dolVals.length) return;
    var bestDol = Math.max.apply(null, dolVals);
    allRows.forEach(function(tr) {
      var cell = tr.cells[dc];
      if (!cell) return;
      var v = parseFloat(cell.dataset.val);
      if (!isNaN(v) && Math.abs(v - bestDol) < 0.00001) {
        cell.style.color = '#f0c040';
      }
    });
  });
}
// Colors run from _bootFantasy after the dollar tables are built.

/* ── Compare Players (Fantasy tab) ─────────────────────────────── */
/* Uses TRADE_HITTERS / TRADE_PITCHERS — same data as the Fantasy $ tables.
   Color coding uses applyFantColors (gold leader + red→white→blue gradient)
   computed against the FULL league pool, not just the selected players. */
var _fcmpHNames = new Set();   // selected hitter names
var _fcmpPNames = new Set();   // selected pitcher names
var _fcmpDdIdx = -1;

function fcmpSearch() {
  var q = document.getElementById('fcmp-search').value.toLowerCase().trim();
  var dd = document.getElementById('fcmp-dropdown');
  if (!q) { dd.style.display='none'; _fcmpDdIdx=-1; return; }
  var hMatches = TRADE_HITTERS.filter(function(p) {
    return p.name.toLowerCase().includes(q) || (p.team||'').toLowerCase().includes(q);
  }).slice(0, 12);
  var pMatches = TRADE_PITCHERS.filter(function(p) {
    return p.name.toLowerCase().includes(q) || (p.team||'').toLowerCase().includes(q);
  }).slice(0, 12);
  var all = [];
  hMatches.forEach(function(p) { all.push({player:p, type:'h'}); });
  pMatches.forEach(function(p) { all.push({player:p, type:'p'}); });
  if (!all.length) { dd.style.display='none'; _fcmpDdIdx=-1; return; }
  dd.innerHTML = all.map(function(item) {
    var p = item.player;
    var isH = item.type === 'h';
    var added = isH ? _fcmpHNames.has(p.name) : _fcmpPNames.has(p.name);
    var tag = isH ? '<span style="color:#3d9be9;font-size:.7rem;font-weight:600;margin-right:4px">BAT</span>'
                  : '<span style="color:#e8832a;font-size:.7rem;font-weight:600;margin-right:4px">' + (p.role==='sp'?'SP':'RP') + '</span>';
    var _pl = isH ? _posLabel(p) : '';
    var posStr = _pl ? '<span style="color:#777;font-size:.6rem;font-weight:700;margin-left:4px">' + _pl + '</span>' : '';
    return '<div class="cmp-di" data-name="'+p.name.replace(/"/g,'&amp;quot;')+'" data-type="'+item.type+'" onmousedown="fcmpAdd(this.dataset.name,this.dataset.type)">'
      + tag + ' <span style="color:var(--muted);font-size:.78rem;margin-right:4px">' + (p.team||'') + '</span>'
      + '<span>' + p.name + '</span>' + posStr
      + (added ? '<span style="color:var(--muted);font-size:.72rem;margin-left:auto">Added</span>' : '')
      + '</div>';
  }).join('');
  dd.style.display = '';
  _fcmpDdIdx = -1;
}

function fcmpAdd(name, type) {
  if (type === 'h') _fcmpHNames.add(name); else _fcmpPNames.add(name);
  document.getElementById('fcmp-search').value = '';
  document.getElementById('fcmp-dropdown').style.display = 'none';
  _fcmpDdIdx = -1;
  fcmpRender();
}

function fcmpRemove(name, type) {
  if (type === 'h') _fcmpHNames.delete(name); else _fcmpPNames.delete(name);
  fcmpRender();
}

function fcmpClear() {
  _fcmpHNames.clear();
  _fcmpPNames.clear();
  fcmpRender();
}

/* Color compare-table cells using full-league data from TRADE arrays.
   keys = ordered list of stat keys matching columns starting at col 2 (after Name, Team).
   isHitter: true for hitters (K is negative = lower better), false for pitchers (ERA/WHIP negative). */
function _fcmpColorize(tblId, pool, keys, isHitter) {
  var tbl = document.getElementById(tblId);
  if (!tbl) return;
  var rows = Array.from(tbl.querySelectorAll('tbody tr'));
  if (!rows.length) return;
  keys.forEach(function(key, ki) {
    var colIdx = ki + 2;
    var allVals = [];
    pool.forEach(function(p) {
      var v;
      if (key === 'dollars') v = p.dollars;
      else if (key === 'PA' || key === 'IP') v = (p.proj || {})[key] || (p.cats || {})[key];
      else v = (p.cats || {})[key];
      if (v != null && !isNaN(v)) allVals.push(v);
    });
    if (!allVals.length) return;
    var best = Math.max.apply(null, allVals);
    rows.forEach(function(tr) {
      var cell = tr.cells[colIdx];
      if (!cell) return;
      var v = parseFloat(cell.dataset.val);
      if (isNaN(v)) return;
      if (Math.abs(v - best) < 0.00001) {
        cell.style.color = '#f0c040';
        cell.style.fontWeight = '700';
        return;
      }
      var better = allVals.filter(function(x) { return x > v + 0.00001; }).length;
      var total = allVals.length;
      if (total <= 1) return;
      var t = better / (total - 1);
      var r, g, b;
      if (t < 0.5) {
        var s = t * 2;
        r = Math.round(255 + (235 - 255) * s);
        g = Math.round(60  + (235 - 60)  * s);
        b = Math.round(50  + (235 - 50)  * s);
      } else {
        var s2 = (t - 0.5) * 2;
        r = Math.round(235 + ( 60 - 235) * s2);
        g = Math.round(235 + (140 - 235) * s2);
        b = Math.round(235 + (255 - 235) * s2);
      }
      cell.style.color = 'rgb(' + r + ',' + g + ',' + b + ')';
      cell.style.fontWeight = '600';
    });
  });
}

function _fcmpFmtDol(v) {
  if (v == null) return '—';
  var col = v >= 0 ? '#4CAF50' : '#e74c3c';
  return '<span style="color:'+col+';font-weight:700">$' + v.toFixed(1) + '</span>';
}

function _fcmpStatCell(dolVal, projVal, cat) {
  /* Two-line cell: dollar contribution on top, projected stat below */
  if (dolVal == null) return '<td style="text-align:center;padding:3px 6px;opacity:.5" data-val="0">—</td>';
  var dv = parseFloat(dolVal);
  var dStr = (dv >= 0 ? '$' : '−$') + Math.abs(dv).toFixed(1);
  var proj = '';
  if (projVal != null) {
    var pv = parseFloat(projVal);
    var pStr;
    if (cat === 'OBP') pStr = pv.toFixed(3);
    else if (cat === 'ERA' || cat === 'WHIP') pStr = pv.toFixed(2);
    else pStr = Math.round(pv).toString();
    proj = '<div style="font-size:.68rem;color:#777;font-weight:400;line-height:1.1;margin-top:1px">(' + pStr + ')</div>';
  }
  return '<td style="text-align:center;padding:3px 6px" data-val="' + dv + '">'
    + '<div style="font-size:.9rem;line-height:1.15">' + dStr + '</div>'
    + proj + '</td>';
}

function _fcmpInfoCell(val, decimals) {
  /* Info-only cell (PA, IP) — just the projected value, no dollar */
  if (val == null) return '<td style="text-align:center;padding:3px 6px;opacity:.5" data-val="0">—</td>';
  var v = parseFloat(val);
  var s = decimals > 0 ? v.toFixed(decimals) : Math.round(v).toString();
  return '<td style="text-align:center;padding:3px 6px;color:#999;font-size:.88rem" data-val="' + v.toFixed(1) + '">' + s + '</td>';
}

function fcmpRender() {
  var hNames = Array.from(_fcmpHNames);
  var pNames = Array.from(_fcmpPNames);
  var hasH = hNames.length > 0;
  var hasP = pNames.length > 0;

  document.getElementById('fcmp-empty').style.display = (hasH || hasP) ? 'none' : '';
  document.getElementById('fcmp-h-section').style.display = hasH ? '' : 'none';
  document.getElementById('fcmp-p-section').style.display = hasP ? '' : 'none';

  var total = hNames.length + pNames.length;
  document.getElementById('fcmp-cnt').textContent = total ? total + ' player' + (total === 1 ? '' : 's') : '';

  // Chips
  var chips = [];
  hNames.forEach(function(name) {
    var p = TRADE_HITTERS.find(function(x) { return x.name === name; });
    if (!p) return;
    chips.push('<span style="display:inline-flex;align-items:center;gap:4px;background:#1a2a3a;'
      + 'border:1px solid #3d9be9;border-radius:14px;padding:3px 10px;font-size:.78rem;color:#7bb8e8">'
      + (p.team||'') + ' ' + p.name
      + ' <span data-name="'+p.name.replace(/"/g,'&amp;quot;')+'" data-type="h" onclick="fcmpRemove(this.dataset.name,this.dataset.type)" style="cursor:pointer;color:#888;font-weight:700;margin-left:2px">✕</span></span>');
  });
  pNames.forEach(function(name) {
    var p = TRADE_PITCHERS.find(function(x) { return x.name === name; });
    if (!p) return;
    chips.push('<span style="display:inline-flex;align-items:center;gap:4px;background:#2a1f12;'
      + 'border:1px solid #e8832a;border-radius:14px;padding:3px 10px;font-size:.78rem;color:#e8b87a">'
      + (p.team||'') + ' ' + p.name
      + ' <span data-name="'+p.name.replace(/"/g,'&amp;quot;')+'" data-type="p" onclick="fcmpRemove(this.dataset.name,this.dataset.type)" style="cursor:pointer;color:#888;font-weight:700;margin-left:2px">✕</span></span>');
  });
  document.getElementById('fcmp-chips').innerHTML = chips.join('');

  // Hitter table — uses same columns as Fantasy $ hitter table
  if (hasH) {
    var tb = document.getElementById('fcmp-h-body');
    tb.innerHTML = hNames.map(function(name) {
      var p = TRADE_HITTERS.find(function(x) { return x.name === name; });
      if (!p) return '';
      var c = p.cats || {};
      var pr = p.proj || {};
      return '<tr>'
        + '<td class="nm">' + p.name + '</td>'
        + '<td style="white-space:nowrap">' + (p.team||'') + '</td>'
        + '<td style="text-align:center;padding:3px 6px" data-val="' + (p.dollars||0) + '">' + _fcmpFmtDol(p.dollars) + '</td>'
        + _fcmpInfoCell(pr.PA, 0)
        + _fcmpStatCell(c.R, pr.R, 'R')
        + _fcmpStatCell(c.HR, pr.HR, 'HR')
        + _fcmpStatCell(c.RBI, pr.RBI, 'RBI')
        + _fcmpStatCell(c.SB, pr.SB, 'SB')
        + _fcmpStatCell(c.K_h, pr.K_h, 'K_h')
        + _fcmpStatCell(c.OBP, pr.OBP, 'OBP')
        + '<td style="text-align:center"><button class="cmp-remove" data-name="'+p.name.replace(/"/g,'&amp;quot;')+'" data-type="h" onclick="fcmpRemove(this.dataset.name,this.dataset.type)">✕</button></td>'
        + '</tr>';
    }).join('');
    // Color against full league
    _fcmpColorize('fcmp-h-tbl', TRADE_HITTERS, ['dollars','PA','R','HR','RBI','SB','K_h','OBP'], true);
  }

  // Pitcher table — uses same columns as Fantasy $ pitcher table
  if (hasP) {
    var ptb = document.getElementById('fcmp-p-body');
    ptb.innerHTML = pNames.map(function(name) {
      var p = TRADE_PITCHERS.find(function(x) { return x.name === name; });
      if (!p) return '';
      var c = p.cats || {};
      var pr = p.proj || {};
      return '<tr>'
        + '<td class="nm">' + p.name + '</td>'
        + '<td style="white-space:nowrap">' + (p.team||'') + '</td>'
        + '<td style="text-align:center;padding:3px 6px" data-val="' + (p.dollars||0) + '">' + _fcmpFmtDol(p.dollars) + '</td>'
        + _fcmpInfoCell(pr.IP, 1)
        + _fcmpStatCell(c.W, pr.W, 'W')
        + _fcmpStatCell(c.ERA, pr.ERA, 'ERA')
        + _fcmpStatCell(c.WHIP, pr.WHIP, 'WHIP')
        + _fcmpStatCell(c.K_p, pr.K_p, 'K_p')
        + _fcmpStatCell(c.SV, pr.SV, 'SV')
        + _fcmpStatCell(c.HLD, pr.HLD, 'HLD')
        + '<td style="text-align:center"><button class="cmp-remove" data-name="'+p.name.replace(/"/g,'&amp;quot;')+'" data-type="p" onclick="fcmpRemove(this.dataset.name,this.dataset.type)">✕</button></td>'
        + '</tr>';
    }).join('');
    _fcmpColorize('fcmp-p-tbl', TRADE_PITCHERS, ['dollars','IP','W','ERA','WHIP','K_p','SV','HLD'], false);
  }
}

// Keyboard nav for compare search
(function() {
  var el = document.getElementById('fcmp-search');
  if (!el) return;
  el.addEventListener('keydown', function(e) {
    var dd = document.getElementById('fcmp-dropdown');
    var items = Array.from(dd.querySelectorAll('.cmp-di'));
    if (!items.length) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      _fcmpDdIdx = Math.min(_fcmpDdIdx + 1, items.length - 1);
      items.forEach(function(el, i) { el.classList.toggle('active', i === _fcmpDdIdx); });
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      _fcmpDdIdx = Math.max(_fcmpDdIdx - 1, 0);
      items.forEach(function(el, i) { el.classList.toggle('active', i === _fcmpDdIdx); });
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (_fcmpDdIdx >= 0 && items[_fcmpDdIdx]) {
        var it = items[_fcmpDdIdx];
        fcmpAdd(it.dataset.name, it.dataset.type);
      }
    } else if (e.key === 'Escape') {
      dd.style.display = 'none';
      _fcmpDdIdx = -1;
    }
  });
  el.addEventListener('blur', function() {
    setTimeout(function() { document.getElementById('fcmp-dropdown').style.display = 'none'; }, 200);
  });
})();

/* ── Trade Calculator ────────────────────────────────────────── */
/* Player pool and league state arrive in data/fantasy.json. The shell
   sets window._fantasyPayload before this script runs. */
var TRADE_HITTERS  = [];
var TRADE_PITCHERS = [];
/* Phase 3: full league state (per-team rosters + baseline z-scores).
   Null if no ESPN snapshot is present — the calculator falls back to the
   classic projected-stat-diff verdict only in that case. */
var PHASE3_LEAGUE  = null;
var _tradeRoster   = {
  send: [], recv: [], sendTeamId: null, recvTeamId: null,
  pickups: [], pitcherPickups: [],          /* user-side waiver adds */
  oppPickups: [], oppPitcherPickups: [],    /* opponent-side waiver adds */
  drops: [], oppDrops: []                   /* roster drops when a side receives > sends */
};
/* Active free-agent pickup-picker state. When set, _phase3RenderLineups renders
   an inline free-agent list in place of the "+" button for the target slot.
   Cleared on any add/remove. */
window._phase3PickerSlot     = null;
window._phase3OppPickerSlot  = null;
window._phase3PitcherPicker     = false;
window._phase3OppPitcherPicker  = false;
window._phase3DropPicker        = false;   /* user drop picker visible */
window._phase3OppDropPicker     = false;   /* opponent drop picker visible */

/* Manual lineup overrides — lets the user override the dollar-optimized
   post-trade lineup on either side of the trade. Structure:
     _phase3CustomLineup[team_id] = {slot_id: espn_id, ...}
   A missing or empty entry for a team_id means "use the optimizer".
   Each slot is treated as a *pin*: the named player is locked into that
   slot, and the remaining slots fill via the LAP over the leftover roster.
   An explicit null value pins the slot to "empty". */
window._phase3CustomLineup = {};

/* IL pitcher activations — pitchers have no slot system, so activation is
   a swap PAIR: "bring IL pitcher IN, take starter X OUT". Structure:
     _phase3IlPitcherActivations[team_id] = {in_espn_id: out_espn_id, ...}
   At sim time, _phase3InjectActivatedIlPitchers removes each "out" starter
   from team.pitchers and pushes the corresponding IL record. Deactivating
   an entry restores the starter, mirroring the hitter IL-pin flow. */
window._phase3IlPitcherActivations = {};
/* Mid-activation state: the user clicked Activate on an IL pitcher and
   now needs to pick a current starter to swap out. null when not active.
   {side, in_espn_id} */
window._phase3IlPitcherSwapPicker = null;

/* Active roster-slot picker (click an After cell to open). When set,
   _phase3RenderLineups inserts a sub-row listing every eligible hitter
   on that side's post-trade roster. Cleared on pick / close / trade
   composition change. */
window._phase3RosterSlotUser = null;   /* {slot_id, slot_label} or null */
window._phase3RosterSlotOpp  = null;

function tradeSearch(side, q) {
  var dd = document.getElementById('trade-' + side + '-dd');
  q = (q || '').toLowerCase().trim();
  if (!q) { dd.style.display = 'none'; return; }
  var added = _tradeRoster.send.concat(_tradeRoster.recv).map(function(p) { return p.name; });
  var pool  = TRADE_HITTERS.concat(TRADE_PITCHERS);
  // Phase 3 filter: send side = selected send team, recv side = selected counterparty.
  // (If PHASE3 isn't loaded the pool stays unfiltered — old behavior.)
  if (PHASE3_LEAGUE) {
    var sendTid = _tradeRoster.sendTeamId;
    var recvTid = _tradeRoster.recvTeamId;
    pool = pool.filter(function(p) {
      if (p.team_id == null) return false;        // unrostered → drop
      if (side === 'send') return sendTid != null && p.team_id === sendTid;
      if (side === 'recv') return recvTid != null && p.team_id === recvTid;
      return true;
    });
  }
  var hits  = pool.filter(function(p) {
    return added.indexOf(p.name) === -1 && p.name.toLowerCase().indexOf(q) !== -1;
  }).slice(0, 8);
  if (!hits.length) {
    dd.innerHTML = '<div style="padding:8px 12px;color:var(--muted);font-size:.82rem">No results</div>';
  } else {
    dd.innerHTML = hits.map(function(p) {
      var meta = p.team + (p.role ? ' ' + p.role.toUpperCase() : '') + '  $' + p.dollars.toFixed(1);
      var _plt = !p.is_pitcher ? _posLabel(p) : '';
      var posTag = _plt ? '<span style="color:#777;font-size:.6rem;font-weight:700;margin-left:4px">' + _plt + '</span>' : '';
      return '<div data-side="' + side + '" data-name="' + p.name.replace(/"/g,"&quot;") + '"'
        + ' onmousedown="tradeAdd(this.dataset.side,this.dataset.name)"'
        + ' style="padding:7px 12px;cursor:pointer;border-bottom:1px solid #252525;font-size:.83rem"'
        + '>'
        + '<span style="font-weight:600">' + p.name + '</span>' + posTag
        + '<span style="opacity:.55;font-size:.77rem;margin-left:7px">' + meta + '</span>'
        + '</div>';
    }).join('');
  }
  dd.style.display = '';
}

function tradeAdd(side, name) {
  var pool = TRADE_HITTERS.concat(TRADE_PITCHERS);
  var p = null;
  for (var i=0;i<pool.length;i++) { if (pool[i].name === name) { p=pool[i]; break; } }
  if (!p) return;
  var already = false;
  for (var i=0;i<_tradeRoster[side].length;i++) { if (_tradeRoster[side][i].name===name) { already=true; break; } }
  if (!already) _tradeRoster[side].push(p);
  var inp = document.getElementById('trade-' + side + '-search');
  if (inp) inp.value = '';
  var dd = document.getElementById('trade-' + side + '-dd');
  if (dd) dd.style.display = 'none';
  _tradeRender();
}

function tradeRemove(side, name) {
  _tradeRoster[side] = _tradeRoster[side].filter(function(p) { return p.name !== name; });
  // Clear pickups if the trade is now empty — orphaned waiver adds are
  // confusing and phase3 simulation won't run without both sides anyway.
  if (!_tradeRoster.send.length && !_tradeRoster.recv.length) {
    _tradeRoster.pickups = [];
    _tradeRoster.pitcherPickups = [];
    _tradeRoster.oppPickups = [];
    _tradeRoster.oppPitcherPickups = [];
    _tradeRoster.drops = [];
    _tradeRoster.oppDrops = [];
    window._phase3PickerSlot = null;
    window._phase3OppPickerSlot = null;
    window._phase3PitcherPicker = false;
    window._phase3OppPitcherPicker = false;
    window._phase3DropPicker = false;
    window._phase3OppDropPicker = false;
  }
  _tradeRender();
}

function _tradePlayerRow(side, p) {
  var d = p.dollars;
  var dStr = (d >= 0 ? '$' : '−$') + Math.abs(d).toFixed(1);
  var roleTag = p.role ? '<span style="opacity:.45;font-size:.68rem;margin-left:3px">' + p.role.toUpperCase() + '</span>' : '';
  var posTag = '';
  if (!p.is_pitcher) {
    var lbl = _posLabel(p);
    if (lbl) posTag = '<span style="color:#888;font-size:.62rem;font-weight:700;margin-left:4px">' + lbl + '</span>';
  }
  return '<div style="display:flex;align-items:center;justify-content:space-between;'
    + 'padding:5px 8px;background:#1a1a1a;border-radius:5px;margin-bottom:3px">'
    + '<div><span style="font-size:.83rem;font-weight:600">' + p.name + '</span>' + posTag
    + '<span style="font-size:.73rem;color:var(--muted);margin-left:5px">' + p.team + roleTag + '</span></div>'
    + '<div style="display:flex;align-items:center;gap:7px">'
    + '<span style="color:#ddd;font-weight:700;font-size:.82rem;white-space:nowrap">' + dStr + '</span>'
    + '<button data-side="' + side + '" data-name="' + p.name.replace(/"/g,"&quot;") + '"'
    + ' onmousedown="tradeRemove(this.dataset.side,this.dataset.name)"'
    + ' style="background:none;border:none;color:#888;cursor:pointer;font-size:.75rem;padding:2px 4px;line-height:1;border-radius:3px">&#x2715;</button>'
    + '</div></div>';
}

function _tradeSectionRows(players, isPitcher) {
  return players.filter(function(p) { return !!p.is_pitcher === isPitcher; });
}

function _tradeRender() {
  ['send','recv'].forEach(function(side) {
    var players = _tradeRoster[side];
    var hitters  = _tradeSectionRows(players, false);
    var pitchers = _tradeSectionRows(players, true);
    var html = '';
    if (!players.length) {
      html = '<div style="color:var(--muted);font-size:.79rem;font-style:italic;padding:6px 2px">No players added</div>';
    } else {
      if (hitters.length) {
        html += '<div style="font-size:.7rem;color:var(--muted);font-weight:700;text-transform:uppercase;'
              + 'letter-spacing:.05em;margin:6px 0 4px">&#x1F3CF; Hitters</div>';
        hitters.forEach(function(p) { html += _tradePlayerRow(side, p); });
      }
      if (pitchers.length) {
        html += '<div style="font-size:.7rem;color:var(--muted);font-weight:700;text-transform:uppercase;'
              + 'letter-spacing:.05em;margin:8px 0 4px">&#x26BE; Pitchers</div>';
        pitchers.forEach(function(p) { html += _tradePlayerRow(side, p); });
      }
    }
    document.getElementById('trade-' + side + '-list').innerHTML = html;
    var tot = _tradeRoster[side].reduce(function(s,p){ return s + (p.dollars||0); }, 0);
    var tel = document.getElementById('trade-' + side + '-total');
    if (tel) tel.textContent = (tot >= 0 ? '$' : '−$') + Math.abs(tot).toFixed(1);
  });
  _tradeCalc();
}

/* ── Trade Calc: verdict + per-stat breakdown ────────────────── */
// Counting stats are summed; rate stats (OBP/ERA/WHIP) are averaged across the side.
var TRADE_RATE_STATS = {'OBP':true,'ERA':true,'WHIP':true};
// Lower-is-better categories. Hitter strikeouts (K_h) are lower-better;
// pitcher strikeouts (K_p) are higher-better — they used to share the key
// 'K' which silently collided in mixed trades.
var TRADE_LOWER_BETTER = {'ERA':true,'WHIP':true,'K_h':true};
// Stat order so hitter cats line up with sidebar order, pitcher cats too
var TRADE_STAT_ORDER = ['R','HR','RBI','SB','K_h','OBP','W','SV','HLD','K_p','ERA','WHIP'];
// Display label map — lets us use disambiguated internal keys while showing
// friendlier labels in the UI. Unmapped keys render as-is.
var TRADE_DISPLAY_LABEL = {'K_h':'K (H)','K_p':'K (P)'};

function _aggProj(players) {
  // Returns {stat: aggregated_value} — sums for counting stats, averages for rate stats
  var sums = {}, counts = {};
  players.forEach(function(p) {
    if (!p.proj) return;
    Object.keys(p.proj).forEach(function(k) {
      var v = parseFloat(p.proj[k]);
      if (isNaN(v)) return;
      sums[k] = (sums[k] || 0) + v;
      counts[k] = (counts[k] || 0) + 1;
    });
  });
  var out = {};
  Object.keys(sums).forEach(function(k) {
    out[k] = TRADE_RATE_STATS[k] ? (sums[k] / counts[k]) : sums[k];
  });
  return out;
}

function _fmtStatDiff(k, diff) {
  var sign = diff >= 0 ? '+' : '−';
  var mag  = Math.abs(diff);
  if (k === 'OBP')  return sign + mag.toFixed(3);
  if (k === 'ERA' || k === 'WHIP') return sign + mag.toFixed(2);
  // Counting stats — round to nearest whole number
  return sign + Math.round(mag).toString();
}

function _tradeCalc() {
  var send = _tradeRoster['send'];
  var recv = _tradeRoster['recv'];
  var verdictEl = document.getElementById('trade-verdict');
  var breakEl   = document.getElementById('trade-cat-breakdown');
  if (!send.length && !recv.length) {
    verdictEl.innerHTML = '<span style="color:var(--muted);font-size:.8rem">Add players<br>to both sides</span>';
    breakEl.innerHTML = '';
    return;
  }
  var sendTot = send.reduce(function(s,p){ return s + (p.dollars||0); }, 0);
  var recvTot = recv.reduce(function(s,p){ return s + (p.dollars||0); }, 0);
  var net = recvTot - sendTot;

  // Resolve team names for the verdict
  var sendName = 'Sender', recvName = 'Receiver';
  if (PHASE3_LEAGUE) {
    var _sT = PHASE3_LEAGUE.teams.find(function(t) { return t.team_id === _tradeRoster.sendTeamId; });
    var _rT = PHASE3_LEAGUE.teams.find(function(t) { return t.team_id === _tradeRoster.recvTeamId; });
    if (_sT) sendName = _sT.name;
    if (_rT) recvName = _rT.name;
  }
  var arrowHtml, vc;
  if (net > 0.5) {
    // Sender (left side) receives more value than they give → sender wins
    arrowHtml = '<div style="font-size:3rem;color:#4caf50;line-height:1;margin:4px 0">&#x25B6;</div>'
              + '<div style="font-size:.75rem;color:#4caf50;font-weight:800;letter-spacing:.06em">'
              + sendName.toUpperCase() + ' WINS</div>';
    vc = '#4caf50';
  } else if (net < -0.5) {
    // Sender gives more value than they get → receiver wins
    arrowHtml = '<div style="font-size:3rem;color:#e05555;line-height:1;margin:4px 0;transform:rotate(180deg)">&#x25B6;</div>'
              + '<div style="font-size:.75rem;color:#e05555;font-weight:800;letter-spacing:.06em">'
              + recvName.toUpperCase() + ' WINS</div>';
    vc = '#e05555';
  } else {
    arrowHtml = '<div style="font-size:2.4rem;color:#aaa;line-height:1;margin:4px 0">&#x21C6;</div>'
              + '<div style="font-size:.75rem;color:#aaa;font-weight:800;letter-spacing:.06em">EVEN</div>';
    vc = '#aaa';
  }
  var netStr = (net >= 0 ? '+$' : '−$') + Math.abs(net).toFixed(1);
  verdictEl.innerHTML = arrowHtml
    + '<div style="font-size:1.5rem;font-weight:800;color:' + vc + ';margin:6px 0 2px">' + netStr + '</div>'
    + '<div style="font-size:.69rem;color:var(--muted);margin-top:2px">'
    + '<span style="color:#e05555">&#x25BE; $' + sendTot.toFixed(1) + ' sent</span>'
    + '&nbsp;&nbsp;<span style="color:#4caf50">&#x25B4; $' + recvTot.toFixed(1) + ' recv</span>'
    + '</div>';

  // Use projected season totals (proj) for actual stat differentials
  var sendAgg = _aggProj(send);
  var recvAgg = _aggProj(recv);
  var seen = {}, allKeys = [];
  // Order keys via canonical TRADE_STAT_ORDER first, then anything unexpected
  TRADE_STAT_ORDER.forEach(function(k) {
    if ((k in sendAgg) || (k in recvAgg)) { seen[k]=1; allKeys.push(k); }
  });
  Object.keys(sendAgg).concat(Object.keys(recvAgg)).forEach(function(k) {
    if (!seen[k]) { seen[k]=1; allKeys.push(k); }
  });
  if (!allKeys.length) { breakEl.innerHTML=''; return; }
  var bHtml = '<div style="font-size:.66rem;color:var(--muted);text-transform:uppercase;'
            + 'letter-spacing:.06em;font-weight:700;margin:8px 0 4px;text-align:center">'
            + 'Projected Season Δ</div>'
            + '<div style="display:flex;flex-direction:column;gap:4px">';
  allKeys.forEach(function(k) {
    var sv = sendAgg[k]||0, rv = recvAgg[k]||0;
    var rawDiff = rv - sv;
    var adjDiff = TRADE_LOWER_BETTER[k] ? -rawDiff : rawDiff;
    // Rate stats (ERA/WHIP/OBP) need a finer significance threshold.
    var isRate = (k === 'OBP' || k === 'ERA' || k === 'WHIP');
    var thresh = (k === 'OBP') ? 0.0005 : (isRate ? 0.005 : 0.5);
    var isPos = adjDiff >  thresh;
    var isNeg = adjDiff < -thresh;
    var col   = isPos ? '#4caf50' : isNeg ? '#e05555' : '#888';
    var arrow = isPos ? '&#x25B2;' : isNeg ? '&#x25BC;' : '&#x25A0;';
    var dispDiff = _fmtStatDiff(k, rawDiff);
    var label = TRADE_DISPLAY_LABEL[k] || k;
    bHtml += '<div style="display:flex;justify-content:space-between;align-items:center;'
           + 'padding:5px 9px;background:#1a1a1a;border-radius:5px;border-left:3px solid ' + col + '">'
           + '<span style="font-size:.76rem;font-weight:700;color:#ccc">' + label + '</span>'
           + '<span style="font-size:.8rem;font-weight:700;color:' + col + '">' + arrow + ' ' + dispDiff + '</span>'
           + '</div>';
  });
  bHtml += '</div>';
  breakEl.innerHTML = bHtml;

  // Phase 3: full league recompute (only when ESPN snapshot is loaded
  // and the trade has at least one player on each side)
  if (PHASE3_LEAGUE && send.length && recv.length) {
    _phase3RenderImpact();
  } else {
    var p3 = document.getElementById('phase3-wrap');
    if (p3) p3.style.display = 'none';
  }
}

/* ════════════════════════════════════════════════════════════════════
   ── Phase 3: Trade impact on league standings ─────────────────────
   ════════════════════════════════════════════════════════════════════
   When a trade has both sides populated, swap players between the two
   affected teams, re-optimize each team's hitter lineup with a JS LAP
   solver, re-aggregate stats, and recompute z-scores across all 10
   teams. Then render a compact before/after delta block plus an
   optional collapsible full standings table.
   ──────────────────────────────────────────────────────────────────── */

/* One-time init: populate the counterparty dropdown and default to the
   first non-user team. Hides the whole UI if PHASE3_LEAGUE is unavailable. */
function phase3Init() {
  var sendWrap = document.getElementById('trade-send-wrap');
  var recvWrap = document.getElementById('trade-counter-wrap');
  if (!PHASE3_LEAGUE) {
    if (sendWrap) sendWrap.style.display = 'none';
    if (recvWrap) recvWrap.style.display = 'none';
    return;
  }
  var allTeams = PHASE3_LEAGUE.teams.slice().sort(function(a,b) {
    return (a.name || '').localeCompare(b.name || '');
  });
  var optHtml = '<option value="">— pick a team —</option>';
  allTeams.forEach(function(t) {
    optHtml += '<option value="' + t.team_id + '">' + t.name + '</option>';
  });
  // Send-side dropdown
  var sendSel = document.getElementById('trade-send-sel');
  if (sendSel) { sendSel.innerHTML = optHtml; }
  if (sendWrap) sendWrap.style.display = '';
  // Recv-side dropdown
  var recvSel = document.getElementById('trade-counter-sel');
  if (recvSel) { recvSel.innerHTML = optHtml; }
  if (recvWrap) recvWrap.style.display = '';
}
phase3Init();

/* Send-side team change handler. Wipes the send side because old players
   would belong to a different team. */
function tradeSetSender(val) {
  _tradeRoster.sendTeamId = val ? parseInt(val, 10) : null;
  _tradeRoster.send = [];
  _tradeRoster.pickups = [];
  _tradeRoster.pitcherPickups = [];
  _tradeRoster.drops = [];
  window._phase3PickerSlot = null;
  window._phase3PitcherPicker = false;
  window._phase3DropPicker = false;
  var inp = document.getElementById('trade-send-search');
  if (inp) inp.value = '';
  _tradeRender();
}

/* Counterparty change handler. Wipes the recv side because old players
   would belong to a different team. */
function tradeSetCounter(val) {
  _tradeRoster.recvTeamId = val ? parseInt(val, 10) : null;
  _tradeRoster.recv = [];
  _tradeRoster.oppPickups = [];
  _tradeRoster.oppPitcherPickups = [];
  _tradeRoster.oppDrops = [];
  window._phase3OppPickerSlot = null;
  window._phase3OppPitcherPicker = false;
  window._phase3OppDropPicker = false;
  var inp = document.getElementById('trade-recv-search');
  if (inp) inp.value = '';
  _tradeRender();
}

/* ── Hungarian LAP solver (square matrix, minimizes total cost) ─────
   Pure-JS port of the classic O(n^3) Munkres-Kuhn algorithm. We use it
   the same way scipy does on the Python side: build a cost matrix where
   ineligible (player, slot) pairs get a very large penalty and eligible
   pairs get -dollar (so minimizing cost = maximizing $).               */
function _phase3Hungarian(cost) {
  var n = cost.length;
  if (!n) return [];
  var INF = 1e18;
  var u = new Array(n + 1).fill(0);
  var v = new Array(n + 1).fill(0);
  var p = new Array(n + 1).fill(0);
  var way = new Array(n + 1).fill(0);
  for (var i = 1; i <= n; i++) {
    p[0] = i;
    var j0 = 0;
    var minv = new Array(n + 1).fill(INF);
    var used = new Array(n + 1).fill(false);
    do {
      used[j0] = true;
      var i0 = p[j0], delta = INF, j1 = 0;
      for (var j = 1; j <= n; j++) {
        if (used[j]) continue;
        var cur = cost[i0 - 1][j - 1] - u[i0] - v[j];
        if (cur < minv[j]) { minv[j] = cur; way[j] = j0; }
        if (minv[j] < delta) { delta = minv[j]; j1 = j; }
      }
      for (var j = 0; j <= n; j++) {
        if (used[j]) { u[p[j]] += delta; v[j] -= delta; }
        else { minv[j] -= delta; }
      }
      j0 = j1;
    } while (p[j0] !== 0);
    do { var j2 = way[j0]; p[j0] = p[j2]; j0 = j2; } while (j0 !== 0);
  }
  // p[j] = row assigned to column j → flip to row→col
  var ans = new Array(n).fill(-1);
  for (var jj = 1; jj <= n; jj++) {
    if (p[jj] > 0) ans[p[jj] - 1] = jj - 1;
  }
  return ans;
}

/* Pick the best 11-hitter lineup from the given roster.
   Returns the subset of `hitters` (player records) that fill starting
   slots — same shape as the Python optimize_hitter_lineup output. */
/* LAP-optimize hitters and ALSO return per-slot assignment so callers
   can render the actual lineup the optimizer picked. */
function _phase3OptimizeHittersFull(hitters) {
  var SLOTS = PHASE3_LEAGUE.slots;
  var nSlots = SLOTS.length;
  var emptyAssigns = [];
  for (var k = 0; k < nSlots; k++) {
    emptyAssigns.push({slot_id: SLOTS[k][0], slot_label: SLOTS[k][1], player: null});
  }
  if (!hitters || !hitters.length) return {starters: [], assigns: emptyAssigns};
  var nPlayers = hitters.length;
  var n = Math.max(nPlayers, nSlots);
  var INELIG = 1e9;
  var cost = [];
  for (var i = 0; i < n; i++) {
    var row = new Array(n).fill(INELIG);
    if (i < nPlayers) {
      var rec = hitters[i];
      var elig = rec.elig || [];
      var dollar = rec.dollars || 0;
      for (var j = 0; j < nSlots; j++) {
        var slotId = SLOTS[j][0];
        if (elig.indexOf(slotId) !== -1) {
          // Negate so the LAP minimizer maximizes dollar value.
          // +1 ensures even $0 players strictly beat ineligible cells.
          row[j] = -(dollar + 1.0);
        }
      }
    }
    cost.push(row);
  }
  var assign = _phase3Hungarian(cost);
  var starters = [];
  var slotPlayer = {};   // slot_index -> player record (only for valid fills)
  for (var r = 0; r < n; r++) {
    var c = assign[r];
    if (c < 0 || c >= nSlots) continue;
    if (r >= nPlayers) continue;
    if (cost[r][c] >= INELIG / 2) continue;   // ineligible filler — skip
    starters.push(hitters[r]);
    slotPlayer[c] = hitters[r];
  }
  var assigns = [];
  for (var jj = 0; jj < nSlots; jj++) {
    assigns.push({
      slot_id:    SLOTS[jj][0],
      slot_label: SLOTS[jj][1],
      player:     slotPlayer[jj] || null
    });
  }
  return {starters: starters, assigns: assigns};
}

/* Backwards-compatible wrapper used by the simulation pipeline. */
function _phase3OptimizeHitters(hitters) {
  return _phase3OptimizeHittersFull(hitters).starters;
}

/* Variant of _phase3OptimizeHittersFull that marks specified slot indexes
   as ineligible for every hitter. Used by _phase3ResolveLineup to fill the
   remaining (unpinned) slots around manually-pinned ones. */
function _phase3OptimizeHittersRestricted(hitters, excludedSlotIdxs) {
  var SLOTS = PHASE3_LEAGUE.slots;
  var nSlots = SLOTS.length;
  var emptyAssigns = [];
  for (var k = 0; k < nSlots; k++) {
    emptyAssigns.push({slot_id: SLOTS[k][0], slot_label: SLOTS[k][1], player: null});
  }
  if (!hitters || !hitters.length) return {starters: [], assigns: emptyAssigns};
  var nPlayers = hitters.length;
  var n = Math.max(nPlayers, nSlots);
  var INELIG = 1e9;
  var cost = [];
  for (var i = 0; i < n; i++) {
    var row = new Array(n).fill(INELIG);
    if (i < nPlayers) {
      var rec = hitters[i];
      var elig = rec.elig || [];
      var dollar = rec.dollars || 0;
      for (var j = 0; j < nSlots; j++) {
        if (excludedSlotIdxs && excludedSlotIdxs[j]) continue;
        var slotId = SLOTS[j][0];
        if (elig.indexOf(slotId) !== -1) {
          row[j] = -(dollar + 1.0);
        }
      }
    }
    cost.push(row);
  }
  var assign = _phase3Hungarian(cost);
  var starters = [];
  var slotPlayer = {};
  for (var r = 0; r < n; r++) {
    var c = assign[r];
    if (c < 0 || c >= nSlots) continue;
    if (r >= nPlayers) continue;
    if (cost[r][c] >= INELIG / 2) continue;
    starters.push(hitters[r]);
    slotPlayer[c] = hitters[r];
  }
  var assigns = [];
  for (var jj = 0; jj < nSlots; jj++) {
    assigns.push({
      slot_id:    SLOTS[jj][0],
      slot_label: SLOTS[jj][1],
      player:     slotPlayer[jj] || null
    });
  }
  return {starters: starters, assigns: assigns};
}

/* Convert a TRADE_HITTERS record into a team.hitters-shaped record with the
   direct counting fields the LAP + aggregator use. Reused for both IL
   activations and any other "pull a hitter from the flat trade pool into
   a team's roster" flow. */
function _phase3MakeHitterFromTrade(p) {
  return {
    espn_id: p.espn_id,
    name:    p.name,
    team:    p.team,
    dollars: p.dollars || 0,
    elig:    (p.elig && p.elig.length) ? p.elig.slice() : _fgPosToSlots(p.fg_pos || ''),
    R:       (p.proj && p.proj.R)    || 0,
    HR:      (p.proj && p.proj.HR)   || 0,
    RBI:     (p.proj && p.proj.RBI)  || 0,
    SO_h:    (p.proj && p.proj.K_h)  || 0,
    SB:      (p.proj && p.proj.SB)   || 0,
    OBP:     (p.proj && p.proj.OBP)  || 0,
    PA:      (p.proj && p.proj.PA)   || 600,
    is_il_activation: true
  };
}

/* Same idea as _phase3MakeHitterFromTrade but for a pitcher record pulled
   from TRADE_PITCHERS. Synthesizes the counting-stat fields the aggregator
   (IP-weighted ERA/WHIP, sum W/SO_p/SV/HLD) needs. */
function _phase3MakePitcherFromTrade(p) {
  return {
    espn_id: p.espn_id,
    name:    p.name,
    team:    p.team,
    role:    p.role,
    dollars: p.dollars || 0,
    W:       (p.proj && p.proj.W)    || 0,
    SO_p:    (p.proj && p.proj.K_p)  || 0,
    SV:      (p.proj && p.proj.SV)   || 0,
    HLD:     (p.proj && p.proj.HLD)  || 0,
    ERA:     (p.proj && p.proj.ERA)  || 0,
    WHIP:    (p.proj && p.proj.WHIP) || 0,
    IP:      (p.proj && p.proj.IP)   || 0,
    is_il_activation: true
  };
}

/* Team-owned IL/inactive pitchers — same logic as _phase3IlHittersFor but
   over the pitcher pool and against pre-trade team.pitchers (which phase3
   already filters to actives). */
function _phase3IlPitchersFor(teamId) {
  if (teamId == null) return [];
  var origTeam = PHASE3_LEAGUE && PHASE3_LEAGUE.teams
    ? PHASE3_LEAGUE.teams.find(function(t) { return t.team_id === teamId; })
    : null;
  var origActiveIds = {};
  if (origTeam) {
    (origTeam.pitchers || []).forEach(function(p) { origActiveIds[p.espn_id] = true; });
  }
  var pool = (typeof TRADE_PITCHERS !== 'undefined' ? TRADE_PITCHERS : []);
  var out = [];
  pool.forEach(function(p) {
    if (p.team_id !== teamId) return;
    if (p.espn_id == null) return;
    if (origActiveIds[p.espn_id]) return;
    out.push(p);
  });
  return out;
}

/* Apply every activation swap pair. For each (in_eid → out_eid) entry we
   remove the "out" starter from team.pitchers (so their W/ERA/etc. don't
   count) and inject the "in" IL pitcher's projection-synthesized record.
   Stale entries (either side missing) are skipped silently. */
function _phase3InjectActivatedIlPitchers(team) {
  var act = window._phase3IlPitcherActivations[team.team_id];
  if (!act) return;
  var ilPool = _phase3IlPitchersFor(team.team_id);
  var ilById = {};
  ilPool.forEach(function(p) { ilById[p.espn_id] = p; });

  // Collect swap-out ids first so we can filter them out in one pass.
  var swapOutIds = {};
  var swapIns = [];   // pitcher records to push after the filter
  Object.keys(act).forEach(function(inEidStr) {
    var outEid = act[inEidStr];
    if (outEid == null) return;
    var inEid = isNaN(Number(inEidStr)) ? inEidStr : Number(inEidStr);
    var rec = ilById[inEid];
    if (!rec) {
      for (var i = 0; i < ilPool.length; i++) {
        if (String(ilPool[i].espn_id) === String(inEid)) { rec = ilPool[i]; break; }
      }
    }
    if (!rec) return;                     // IL record gone — skip
    var outKey = isNaN(Number(outEid)) ? outEid : Number(outEid);
    swapOutIds[outKey] = true;
    swapOutIds[String(outKey)] = true;   // handle string-vs-number id mismatch
    swapIns.push(_phase3MakePitcherFromTrade(rec));
  });

  // Remove swapped-out starters, then add the IL pitchers in their place.
  if (Object.keys(swapOutIds).length) {
    team.pitchers = (team.pitchers || []).filter(function(p) {
      return !swapOutIds[p.espn_id] && !swapOutIds[String(p.espn_id)];
    });
  }
  swapIns.forEach(function(pk) { team.pitchers.push(pk); });
}

/* Return the list of team-owned IL/inactive hitters for `teamId`. "IL" is
   defined against the PRE-trade active roster (PHASE3_LEAGUE.teams) rather
   than the post-trade team.hitters — otherwise a player the user trades
   away would look like an IL candidate since they'd be off the post-trade
   roster. True IL players are TRADE_HITTERS rows team-tagged to `teamId`
   that were never on the pre-trade active roster to begin with. */
function _phase3IlHittersFor(teamId, team) {
  if (teamId == null) return [];
  // Baseline = the pre-trade active roster (phase3 excludes inactives from
  // team.hitters at build time, so anyone in the baseline is definitely
  // active, not IL).
  var origTeam = PHASE3_LEAGUE && PHASE3_LEAGUE.teams
    ? PHASE3_LEAGUE.teams.find(function(t) { return t.team_id === teamId; })
    : null;
  var origActiveIds = {};
  if (origTeam) {
    (origTeam.hitters || []).forEach(function(p) { origActiveIds[p.espn_id] = true; });
  }
  var pool = (typeof TRADE_HITTERS !== 'undefined' ? TRADE_HITTERS : []);
  var out = [];
  pool.forEach(function(p) {
    if (p.team_id !== teamId) return;
    if (p.espn_id == null) return;
    if (origActiveIds[p.espn_id]) return;   // was active pre-trade — not IL
    out.push(p);
  });
  return out;
}

/* After _phase3PruneCustomLineup runs, any surviving pins that reference an
   espn_id not on `team.hitters` must be an IL activation. Inject those
   players into `team.hitters` so the resolver/LAP can actually place them. */
function _phase3InjectActivatedIl(team) {
  var custom = window._phase3CustomLineup[team.team_id];
  if (!custom) return;
  var byId = {};
  (team.hitters || []).forEach(function(p) { byId[p.espn_id] = p; });
  var ilPool = _phase3IlHittersFor(team.team_id, team);
  var ilById = {};
  ilPool.forEach(function(p) { ilById[p.espn_id] = p; });
  var seen = {};
  Object.keys(custom).forEach(function(slotId) {
    var eid = custom[slotId];
    if (eid == null) return;
    if (byId[eid]) return;             // already on active roster
    if (seen[eid]) return;
    seen[eid] = true;
    var rec = ilById[eid];
    // Loose-equality fallback (espn_ids may be numeric vs string)
    if (!rec) {
      for (var i = 0; i < ilPool.length; i++) {
        if (String(ilPool[i].espn_id) === String(eid)) { rec = ilPool[i]; break; }
      }
    }
    if (rec) team.hitters.push(_phase3MakeHitterFromTrade(rec));
  });
}

/* Drop any custom-lineup pins whose named player is no longer on the team's
   post-trade active roster OR in the pre-trade IL pool. Keeps still-valid
   pins — including IL pins so the user's activated injured players survive
   a sim refresh. Uses _phase3IlHittersFor for the IL check so traded-away
   players (who look "not on active" post-trade) are correctly pruned and
   don't get re-injected as fake IL activations. */
function _phase3PruneCustomLineup(teamId, team) {
  var custom = window._phase3CustomLineup[teamId];
  if (!custom) return false;
  var activeById = {};
  (team.hitters || []).forEach(function(p) { activeById[p.espn_id] = p; });
  var ilById = {};
  _phase3IlHittersFor(teamId, team).forEach(function(p) { ilById[p.espn_id] = p; });
  var pruned = false;
  Object.keys(custom).forEach(function(key) {
    var eid = custom[key];
    if (eid == null) return;                    // explicit "empty" pin — keep
    if (activeById[eid] || ilById[eid]) return; // active or IL — keep
    // Loose-equality fallback for numeric-vs-string id mismatch
    var found = false;
    for (var k in activeById) { if (String(k) === String(eid)) { found = true; break; } }
    if (!found) for (var k2 in ilById) { if (String(k2) === String(eid)) { found = true; break; } }
    if (!found) { delete custom[key]; pruned = true; }
  });
  if (!Object.keys(custom).length) delete window._phase3CustomLineup[teamId];
  return pruned;
}

/* Return {starters, assigns} for `team`, honoring any manual pins in
   _phase3CustomLineup[team.team_id]. Pinned slots get their named player;
   remaining slots run through the LAP over the leftover (unpinned) roster
   so the "auto-fill the rest" behavior still holds. */
function _phase3ResolveLineup(team) {
  if (!team) return {starters: [], assigns: []};
  var custom = window._phase3CustomLineup[team.team_id];
  if (!custom || !Object.keys(custom).length) {
    return _phase3OptimizeHittersFull(team.hitters || []);
  }

  var SLOTS = PHASE3_LEAGUE.slots;
  var byId = {};
  (team.hitters || []).forEach(function(p) { byId[p.espn_id] = p; });

  // Resolve valid pins. Invalid pins (player not on roster, or not
  // eligible for the slot) are dropped silently.
  // Keys in `custom` are SLOT INDEXES (0..SLOTS.length-1), not slot_ids —
  // this is important because multiple slots share the same slot_id (e.g.
  // OF has three slots, all slot_id=5) and keying by slot_id would duplicate
  // the pinned player into every slot with that slot_id.
  var pinnedSlotIdx = {};   // slot_index → player
  var pinnedIds = {};
  var explicitEmpty = {};   // slot_index → true (pinned to empty)
  for (var i = 0; i < SLOTS.length; i++) {
    if (!(i in custom)) continue;
    var slotId = SLOTS[i][0];
    var eid = custom[i];
    if (eid == null) { explicitEmpty[i] = true; continue; }
    var p = byId[eid];
    if (p && _canFillSlot(p, slotId)) {
      pinnedSlotIdx[i] = p;
      pinnedIds[eid] = true;
    }
  }

  var hasAnyPin = Object.keys(pinnedSlotIdx).length > 0
               || Object.keys(explicitEmpty).length > 0;
  if (!hasAnyPin) return _phase3OptimizeHittersFull(team.hitters || []);

  // Slot indexes the LAP must not use (pinned OR pinned-to-empty)
  var excluded = {};
  Object.keys(pinnedSlotIdx).forEach(function(k) { excluded[k] = true; });
  Object.keys(explicitEmpty).forEach(function(k) { excluded[k] = true; });

  var remaining = (team.hitters || []).filter(function(p) { return !pinnedIds[p.espn_id]; });
  var partial = _phase3OptimizeHittersRestricted(remaining, excluded);

  var assigns = [];
  for (var j = 0; j < SLOTS.length; j++) {
    if (j in pinnedSlotIdx) {
      assigns.push({slot_id: SLOTS[j][0], slot_label: SLOTS[j][1], player: pinnedSlotIdx[j]});
    } else if (j in explicitEmpty) {
      assigns.push({slot_id: SLOTS[j][0], slot_label: SLOTS[j][1], player: null});
    } else {
      var a = partial.assigns[j];
      assigns.push({slot_id: SLOTS[j][0], slot_label: SLOTS[j][1], player: (a && a.player) || null});
    }
  }
  var starters = assigns.map(function(a) { return a.player; }).filter(Boolean);
  return {starters: starters, assigns: assigns};
}

/* Sum counting stats + PA-weighted OBP across the optimized lineup */
function _phase3AggHit(starters) {
  var out = {R:0, HR:0, RBI:0, SO_h:0, SB:0, OBP:0};
  var obpNum = 0, paTotal = 0;
  starters.forEach(function(p) {
    out.R    += p.R    || 0;
    out.HR   += p.HR   || 0;
    out.RBI  += p.RBI  || 0;
    out.SO_h += p.SO_h || 0;
    out.SB   += p.SB   || 0;
    var pa = p.PA || 0, obp = p.OBP || 0;
    if (pa > 0 && obp > 0) { obpNum += obp * pa; paTotal += pa; }
  });
  out.OBP = paTotal > 0 ? (obpNum / paTotal) : 0;
  return out;
}

/* Sum counting stats + IP-weighted ERA/WHIP across all rostered pitchers */
function _phase3AggPit(pitchers) {
  var out = {W:0, SO_p:0, SV:0, HLD:0, ERA:0, WHIP:0};
  var eraNum = 0, whipNum = 0, ipTotal = 0;
  pitchers.forEach(function(p) {
    out.W    += p.W    || 0;
    out.SO_p += p.SO_p || 0;
    out.SV   += p.SV   || 0;
    out.HLD  += p.HLD  || 0;
    var ip = p.IP || 0, era = p.ERA || 0, whip = p.WHIP || 0;
    if (ip > 0) {
      if (era  > 0) eraNum  += era  * ip;
      if (whip > 0) whipNum += whip * ip;
      ipTotal += ip;
    }
  });
  out.ERA  = ipTotal > 0 ? (eraNum  / ipTotal) : 0;
  out.WHIP = ipTotal > 0 ? (whipNum / ipTotal) : 0;
  return out;
}

/* Z-score every team across all 12 cats; return ranks + subtotals.
   Mutates the passed-in `teams` array in place by setting .stats, .z,
   .rank, .z_total, .z_hit, .z_pit, .rank_total, .rank_hit, .rank_pit. */
function _phase3RecomputeZ(teams) {
  var allCats = PHASE3_LEAGUE.hit_cats.concat(PHASE3_LEAGUE.pit_cats);
  var lower = {};
  PHASE3_LEAGUE.lower_better.forEach(function(c) { lower[c] = true; });
  // mean / std per category
  allCats.forEach(function(cat) {
    var vals = teams.map(function(t) { return t.stats[cat] || 0; });
    var mu = vals.reduce(function(a,b) { return a+b; }, 0) / vals.length;
    var ssd = vals.reduce(function(a,b) { return a + (b-mu)*(b-mu); }, 0);
    var sig = Math.sqrt(ssd / vals.length);
    if (sig < 1e-9) sig = 1e-9;
    teams.forEach(function(t) {
      var v = t.stats[cat] || 0;
      var z = (v - mu) / sig;
      if (lower[cat]) z = -z;
      if (!t.z) t.z = {};
      t.z[cat] = Math.round(z * 1000) / 1000;
    });
  });
  // per-cat ranks
  allCats.forEach(function(cat) {
    var sorted = teams.slice().sort(function(a,b) { return b.z[cat] - a.z[cat]; });
    sorted.forEach(function(t, i) {
      if (!t.rank) t.rank = {};
      t.rank[cat] = i + 1;
    });
  });
  // subtotals + total ranks
  teams.forEach(function(t) {
    var zh = 0, zp = 0;
    PHASE3_LEAGUE.hit_cats.forEach(function(c) { zh += t.z[c]; });
    PHASE3_LEAGUE.pit_cats.forEach(function(c) { zp += t.z[c]; });
    t.z_hit = Math.round(zh * 1000) / 1000;
    t.z_pit = Math.round(zp * 1000) / 1000;
    t.z_total = Math.round((zh + zp) * 1000) / 1000;
  });
  ['z_hit','z_pit','z_total'].forEach(function(key) {
    var rk = key === 'z_hit' ? 'rank_hit' : key === 'z_pit' ? 'rank_pit' : 'rank_total';
    var sorted = teams.slice().sort(function(a,b) { return b[key] - a[key]; });
    sorted.forEach(function(t, i) { t[rk] = i + 1; });
  });
  return teams;
}

/* ══ Monte Carlo finish-probability simulation ═════════════════════════
   PLAYER-LEVEL rotisserie sim that mirrors sim_module.py. For every
   rostered player we:
     1. Sample (n_trials × n_stats) correlated shocks using the Cholesky
        factor of the MEASURED within-player residual correlation matrix
        (sim_backtest_cache.json), scaled by the single-year sigma_cv
        (yoy_sigma / sqrt(2)) per stat per role group.
     2. Apply a Beta-distributed injury fraction using the Zimmerman
        expected_loss from sim_data_cache.json.
     3. Apply discrete closer role-change events for projected closers
        on the same MLB team, transferring saves to the next-best RP.
   We then roll up to team totals (optimized 11 hitters + all pitchers)
   and score roto-style (rank each cat, sum points). _mcRunSim's external
   contract is the same as before — called with a `teams` array, returns
   {team_id: {probs, expFinish}} — so the Season Projections and Trade
   Machine buttons keep working unchanged.
   The old team-level sim (_MC_CV, _mcRunSimLegacy) is kept as a fallback
   for when sim_cfg isn't in the payload (caches missing, etc.).
   ───────────────────────────────────────────────────────────────────── */

var _MC_TRIALS = 50000;
var _mcBaselineCache = null;

/* ── Legacy knobs (fallback path only) ── */
var _MC_CV = {
  R: 0.05, HR: 0.08, RBI: 0.06, SB: 0.12, SO_h: 0.06, OBP: 0.015,
  W: 0.10, SO_p: 0.06, SV: 0.15, HLD: 0.15, ERA: 0.05, WHIP: 0.03
};

/* ── New-sim constants (mirror sim_module.py) ── */
var _MC_RATIO_STATS = {OBP: 1, AVG: 1, ERA: 1, WHIP: 1, "K/9": 1, "BB/9": 1};
var _MC_CONF_ROOKIE = 1.15;
var _MC_ROOKIE_PA   = 200;
var _MC_ROOKIE_IP   = 50;
var _MC_CATS        = ['R','HR','RBI','SO_h','SB','OBP',
                       'W','SO_p','SV','HLD','ERA','WHIP'];
var _MC_LOWER       = {SO_h: 1, ERA: 1, WHIP: 1};

/* Box-Muller standard normal. */
function _mcGaussian() {
  var u = 1 - Math.random(), v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

/* Marsaglia-Tsang gamma sampler (shape a, scale 1). Uses the boost trick
   for a < 1. Fast and numerically stable for the 0.05..20 range this sim
   actually hits. */
function _mcSampleGamma(a) {
  if (a < 1) {
    var x = _mcSampleGamma(a + 1);
    var u = Math.random();
    return x * Math.pow(u, 1 / a);
  }
  var d = a - 1 / 3;
  var c = 1 / Math.sqrt(9 * d);
  for (;;) {
    var x, v;
    do {
      x = _mcGaussian();
      v = 1 + c * x;
    } while (v <= 0);
    v = v * v * v;
    var u2 = Math.random();
    if (u2 < 1 - 0.0331 * x * x * x * x) return d * v;
    if (Math.log(u2) < 0.5 * x * x + d * (1 - v + Math.log(v))) return d * v;
  }
}

/* Beta(a, b) via gamma ratio. */
function _mcSampleBeta(a, b) {
  var ga = _mcSampleGamma(a), gb = _mcSampleGamma(b);
  return ga / (ga + gb);
}

/* Build a fallback sim_cfg player entry from a raw team.hitters/pitchers
   record. Used when a player (eg. a free-agent pickup added in the trade
   machine) doesn't have a server-side sim_cfg entry. The sampler still
   runs — just without a Zimmerman injury profile. */
function _mcFallbackPlayerCfg(rec, isPit) {
  if (isPit) {
    var mu = {
      IP:   rec.IP   || 0, W:   rec.W    || 0, SO: rec.SO_p || 0,
      ERA:  rec.ERA  || 0, WHIP: rec.WHIP || 0,
      SV:   rec.SV   || 0, HLD: rec.HLD  || 0
    };
    var role = (mu.IP < 80 || mu.SV >= 5) ? 'RP' : 'SP';
    var out = {
      is_pitcher:    true,
      role:          role,
      mlb_team:      (rec.team || '').toUpperCase(),
      mu:            mu,
      volume_proj:   mu.IP,
      expected_loss: 0
    };
    // Pass through variance_scale for composite/streaming pitchers
    if (rec.variance_scale != null) out.variance_scale = rec.variance_scale;
    return out;
  }
  return {
    is_pitcher:    false,
    role:          'hitter',
    mlb_team:      (rec.team || '').toUpperCase(),
    mu: {
      PA:  rec.PA  || 0, R:  rec.R  || 0, HR:  rec.HR  || 0,
      RBI: rec.RBI || 0, SB: rec.SB || 0, SO:  rec.SO_h || 0,
      OBP: rec.OBP || 0
    },
    volume_proj:   rec.PA || 0,
    expected_loss: 0
  };
}

/* Sample a single player nTrials times. Returns {statName: Float64Array}.
   Uses correlated shocks from the role's Cholesky factor, scales by the
   measured single-year sigma, applies rookie confidence bump, applies a
   Beta-distributed injury trim on counting stats. */
function _mcSamplePlayer(player, roleModel, nTrials) {
  var stats = roleModel.stats;
  var cvs   = roleModel.sigma_cv;
  var chol  = roleModel.chol;
  var k     = stats.length;

  var muVec = new Float64Array(k);
  var have  = new Uint8Array(k);
  for (var i = 0; i < k; i++) {
    var v = (player.mu && player.mu[stats[i]] != null) ? player.mu[stats[i]] : null;
    if (v !== null && v !== undefined) {
      muVec[i] = +v;
      have[i]  = 1;
    }
  }

  // Rookie confidence bump (wider sigma for low-volume samples)
  var conf = 1.0;
  var vol  = player.volume_proj || 0;
  if (player.is_pitcher) {
    if (vol < _MC_ROOKIE_IP) conf = _MC_CONF_ROOKIE;
  } else {
    if (vol < _MC_ROOKIE_PA) conf = _MC_CONF_ROOKIE;
  }

  // sigma[i] = |mu| × cv × conf × variance_scale
  // variance_scale < 1 for composite/streaming pitchers (diversification)
  var vScale = player.variance_scale || 1.0;

  var sig = new Float64Array(k);
  for (var i2 = 0; i2 < k; i2++) {
    if (!have[i2]) continue;
    var m = Math.abs(muVec[i2]);
    if (_MC_RATIO_STATS[stats[i2]]) {
      if (m < 0.01) m = 0.01;
    } else {
      if (m < 1.0) m = 1.0;
    }
    sig[i2] = m * cvs[i2] * conf * vScale;
  }

  // Output per-stat sample arrays
  var samples = {};
  for (var i3 = 0; i3 < k; i3++) {
    if (have[i3]) samples[stats[i3]] = new Float64Array(nTrials);
  }

  // Scratch: iid normals and correlated shocks
  var z = new Float64Array(k);
  for (var t = 0; t < nTrials; t++) {
    for (var i4 = 0; i4 < k; i4++) z[i4] = _mcGaussian();
    // shock[i] = Σ_{j≤i} chol[i][j] × z[j]  (chol is lower triangular)
    for (var i5 = 0; i5 < k; i5++) {
      if (!have[i5]) continue;
      var row = chol[i5];
      var s = 0;
      for (var j = 0; j <= i5; j++) s += row[j] * z[j];
      var val = muVec[i5] + sig[i5] * s;
      var sn = stats[i5];
      var isRatio = _MC_RATIO_STATS[sn];
      if (!isRatio) {
        if (val < 0) val = 0;                 // counting stats floored at 0
      } else if (sn === 'OBP' || sn === 'AVG') {
        if (val < 0) val = 0;
        else if (val > 1) val = 1;
      } else {                              // ERA/WHIP/K9/BB9
        if (val < 0) val = 0;
      }
      samples[sn][t] = val;
    }
  }

  // Injury trimming — Beta(a, b) centred on expected_loss / volume_proj.
  // We record the (1-keep) * vol "missing volume" per trial so the team
  // rollup can fill that gap with replacement-level production (a waiver
  // pickup) instead of leaving the playing time empty.
  var expLoss = player.expected_loss || 0;
  if (vol > 0 && expLoss > 0) {
    var frac = expLoss / vol;
    if (frac < 0) frac = 0;
    if (frac > 0.9) frac = 0.9;
    var conc = 8.0;
    var aa = frac * conc;             if (aa < 0.05) aa = 0.05;
    var bb = (1 - frac) * conc;       if (bb < 0.05) bb = 0.05;
    var missingVol = new Float64Array(nTrials);
    for (var t2 = 0; t2 < nTrials; t2++) {
      var keep = 1.0 - _mcSampleBeta(aa, bb);
      missingVol[t2] = (1.0 - keep) * vol;
      for (var i6 = 0; i6 < k; i6++) {
        if (!have[i6]) continue;
        if (_MC_RATIO_STATS[stats[i6]]) continue;
        samples[stats[i6]][t2] *= keep;
      }
    }
    samples.__missingVol = missingVol;
  }

  return samples;
}

/* Apply closer role-change in-place on pitcher samples. pitcherRecs is a
   flat array of {player, samples} tuples for every rostered pitcher
   across every team. Pitchers are grouped by MLB team — a projected
   closer (mu.SV > threshold) rolls a fire event per trial; when fired,
   a fraction of their saves is transferred to the best-ERA same-team RP. */
function _mcApplyCloserRoleChange(pitcherRecs, cfg, nTrials) {
  // Group by MLB team
  var byTeam = {};
  for (var i = 0; i < pitcherRecs.length; i++) {
    var pr = pitcherRecs[i];
    var tm = (pr.player.mlb_team || '').toUpperCase();
    if (!tm) continue;
    if (!byTeam[tm]) byTeam[tm] = [];
    byTeam[tm].push(pr);
  }

  for (var tm2 in byTeam) {
    var roster = byTeam[tm2];
    for (var ci = 0; ci < roster.length; ci++) {
      var closer = roster[ci];
      var svMu = (closer.player.mu && closer.player.mu.SV) || 0;
      if (svMu <= cfg.sv_threshold) continue;

      // 4-tier ERA-bucketed fire probability. Elite closers (projected
      // sub-3.00 ERA) almost always keep the job; average ones lose it
      // ~20% of seasons; shaky/bad ones are more volatile.
      var era = (closer.player.mu && closer.player.mu.ERA) || 4.0;
      var prob;
      if      (era <= cfg.elite_era)   prob = cfg.p_elite;
      else if (era <= cfg.average_era) prob = cfg.p_average;
      else if (era <= cfg.shaky_era)   prob = cfg.p_shaky;
      else                              prob = cfg.p_bad;
      if (prob > cfg.p_cap) prob = cfg.p_cap;

      // Find successor: lowest-ERA RP on same team (excluding the closer)
      var successor = null;
      var bestEra = Infinity;
      for (var si = 0; si < roster.length; si++) {
        if (si === ci) continue;
        if (roster[si].player.role !== 'RP') continue;
        var e2 = (roster[si].player.mu && roster[si].player.mu.ERA) || 99;
        if (e2 < bestEra) {
          bestEra   = e2;
          successor = roster[si];
        }
      }

      var svArr  = closer.samples.SV;
      var hldArr = closer.samples.HLD;
      var sucSv  = (successor && successor.samples.SV) ? successor.samples.SV : null;
      if (!svArr) continue;
      var keep = 1.0 - cfg.saves_transfer;
      for (var t3 = 0; t3 < nTrials; t3++) {
        if (Math.random() < prob) {
          var orig = svArr[t3];
          svArr[t3] = orig * keep;
          if (sucSv) sucSv[t3] += orig * cfg.saves_transfer;
          if (hldArr) hldArr[t3] += cfg.hld_compensation;
        }
      }
    }
  }
}

/* Player-level _mcRunSim. Returns {team_id: {probs, expFinish}}. */
function _mcRunSim(teams) {
  var N = teams.length;
  if (!N || !PHASE3_LEAGUE) return {};

  var cfg = PHASE3_LEAGUE.sim_cfg;
  if (!cfg || !cfg.ok || !cfg.role_models) {
    // Fallback: caches not available — use the legacy team-level CV sim
    return _mcRunSimLegacy(teams);
  }

  var nTrials    = _MC_TRIALS;
  var roleModels = cfg.role_models;
  var playerCfgs = cfg.players || {};
  var closerCfg  = cfg.closer_cfg;
  var replRates  = cfg.replacement_rates || {};  // per-role injury gap-fill

  // Per-team: optimize hitter lineup (same LAP the dashboard already uses),
  // then sample starters + all pitchers.
  var teamSamples  = new Array(N);
  var pitcherRecs  = [];

  for (var i = 0; i < N; i++) {
    var team = teams[i];
    var starters = _phase3OptimizeHitters(team.hitters || []);
    var pitchers = team.pitchers || [];

    var recs = [];

    for (var h = 0; h < starters.length; h++) {
      var hit = starters[h];
      var key = hit.espn_id != null ? String(hit.espn_id) : null;
      var pc  = (key && playerCfgs[key]) || _mcFallbackPlayerCfg(hit, false);
      var samp = _mcSamplePlayer(pc, roleModels.hitter, nTrials);
      recs.push({player: pc, samples: samp, is_pitcher: false,
                  replacement: replRates.hitter || null});
    }

    for (var pi = 0; pi < pitchers.length; pi++) {
      var pit = pitchers[pi];
      var pkey = pit.espn_id != null ? String(pit.espn_id) : null;
      var ppc  = (pkey && playerCfgs[pkey]) || _mcFallbackPlayerCfg(pit, true);
      var role = ppc.role === 'RP' ? 'RP' : 'SP';
      var psamp = _mcSamplePlayer(ppc, roleModels[role], nTrials);
      var prec = {player: ppc, samples: psamp, is_pitcher: true,
                   replacement: replRates[role] || null};
      recs.push(prec);
      pitcherRecs.push(prec);
    }

    teamSamples[i] = recs;
  }

  // Closer role-change across the full pitcher pool
  _mcApplyCloserRoleChange(pitcherRecs, closerCfg, nTrials);

  // Roll up per-team totals into Float64Arrays (one per cat)
  var teamTotals = new Array(N);
  for (var ti = 0; ti < N; ti++) {
    var tot = {};
    for (var ci2 = 0; ci2 < _MC_CATS.length; ci2++) {
      tot[_MC_CATS[ci2]] = new Float64Array(nTrials);
    }
    var sumPA     = new Float64Array(nTrials);
    var sumOBPxPA = new Float64Array(nTrials);
    var sumIP     = new Float64Array(nTrials);
    var sumERAxIP = new Float64Array(nTrials);
    var sumWHPxIP = new Float64Array(nTrials);
    var recs2 = teamSamples[ti];
    for (var ri = 0; ri < recs2.length; ri++) {
      var rec  = recs2[ri];
      var s    = rec.samples;
      var miss = s.__missingVol || null;      // null if no injury trimming
      var repl = rec.replacement || null;     // {R, HR, ..., OBP} or {W, ERA, ..., WHIP}
      if (rec.is_pitcher) {
        if (s.W)   { var sW = s.W;   var oW = tot.W;    for (var t = 0; t < nTrials; t++) oW[t]   += sW[t]; }
        if (s.SO)  { var sK = s.SO;  var oK = tot.SO_p; for (var t = 0; t < nTrials; t++) oK[t]   += sK[t]; }
        if (s.SV)  { var sV = s.SV;  var oV = tot.SV;   for (var t = 0; t < nTrials; t++) oV[t]   += sV[t]; }
        if (s.HLD) { var sH = s.HLD; var oH = tot.HLD;  for (var t = 0; t < nTrials; t++) oH[t]   += sH[t]; }
        if (s.IP) {
          var sI = s.IP, sE = s.ERA, sWH = s.WHIP;
          for (var t = 0; t < nTrials; t++) {
            var ip = sI[t];
            sumIP[t] += ip;
            if (sE)  sumERAxIP[t] += sE[t]  * ip;
            if (sWH) sumWHPxIP[t] += sWH[t] * ip;
          }
        }
        // Replacement-level injury gap-fill (pitcher)
        if (miss && repl) {
          var rW = repl.W || 0, rSO = repl.SO || 0, rSV = repl.SV || 0,
              rHLD = repl.HLD || 0, rERA = repl.ERA || 0, rWHIP = repl.WHIP || 0;
          var oW2 = tot.W, oK2 = tot.SO_p, oV2 = tot.SV, oH2 = tot.HLD;
          for (var t = 0; t < nTrials; t++) {
            var mi = miss[t];
            if (mi <= 0) continue;
            oW2[t]  += rW   * mi;
            oK2[t]  += rSO  * mi;
            oV2[t]  += rSV  * mi;
            oH2[t]  += rHLD * mi;
            sumIP[t]     += mi;
            sumERAxIP[t] += rERA  * mi;
            sumWHPxIP[t] += rWHIP * mi;
          }
        }
      } else {
        if (s.R)   { var sR = s.R;   var oR = tot.R;    for (var t = 0; t < nTrials; t++) oR[t]   += sR[t]; }
        if (s.HR)  { var sHr = s.HR; var oHr = tot.HR;  for (var t = 0; t < nTrials; t++) oHr[t]  += sHr[t]; }
        if (s.RBI) { var sRb = s.RBI; var oRb = tot.RBI; for (var t = 0; t < nTrials; t++) oRb[t] += sRb[t]; }
        if (s.SO)  { var sSO = s.SO; var oSO = tot.SO_h; for (var t = 0; t < nTrials; t++) oSO[t] += sSO[t]; }
        if (s.SB)  { var sSB = s.SB; var oSB = tot.SB;  for (var t = 0; t < nTrials; t++) oSB[t] += sSB[t]; }
        if (s.PA) {
          var sPA = s.PA, sOBP = s.OBP;
          for (var t = 0; t < nTrials; t++) {
            var pa = sPA[t];
            sumPA[t] += pa;
            if (sOBP) sumOBPxPA[t] += sOBP[t] * pa;
          }
        }
        // Replacement-level injury gap-fill (hitter)
        if (miss && repl) {
          var rR = repl.R || 0, rHR = repl.HR || 0, rRBI = repl.RBI || 0,
              rSBh = repl.SB || 0, rSOh = repl.SO || 0, rOBP = repl.OBP || 0;
          var oR3 = tot.R, oHr3 = tot.HR, oRb3 = tot.RBI, oSO3 = tot.SO_h, oSB3 = tot.SB;
          for (var t = 0; t < nTrials; t++) {
            var mi = miss[t];
            if (mi <= 0) continue;
            oR3[t]  += rR   * mi;
            oHr3[t] += rHR  * mi;
            oRb3[t] += rRBI * mi;
            oSO3[t] += rSOh * mi;
            oSB3[t] += rSBh * mi;
            sumPA[t]     += mi;
            sumOBPxPA[t] += rOBP * mi;
          }
        }
      }
    }
    // Finalize PA- and IP-weighted ratios
    var oOBP = tot.OBP, oERA = tot.ERA, oWHIP = tot.WHIP;
    for (var tx = 0; tx < nTrials; tx++) {
      var pa = sumPA[tx]; if (pa < 1) pa = 1;
      var ip = sumIP[tx]; if (ip < 1) ip = 1;
      oOBP[tx]  = sumOBPxPA[tx] / pa;
      oERA[tx]  = sumERAxIP[tx] / ip;
      oWHIP[tx] = sumWHPxIP[tx] / ip;
    }
    teamTotals[ti] = tot;
  }

  // Rank each cat per trial, sum roto points, accumulate finish counts
  var counts = new Array(N);
  for (var ci3 = 0; ci3 < N; ci3++) {
    counts[ci3] = new Int32Array(N);
  }
  var roto = new Float64Array(N);
  var idx  = new Array(N);
  var nCats = _MC_CATS.length;

  for (var t4 = 0; t4 < nTrials; t4++) {
    for (var ri2 = 0; ri2 < N; ri2++) roto[ri2] = 0;
    for (var k2 = 0; k2 < nCats; k2++) {
      var c = _MC_CATS[k2];
      for (var ii = 0; ii < N; ii++) idx[ii] = ii;
      var isLower = _MC_LOWER[c] ? 1 : 0;
      (function(catName, low, trial) {
        idx.sort(function(a, b) {
          var va = teamTotals[a][catName][trial];
          var vb = teamTotals[b][catName][trial];
          return low ? (va - vb) : (vb - va);
        });
      })(c, isLower, t4);
      for (var jj = 0; jj < N; jj++) roto[idx[jj]] += (N - jj);
    }
    for (var ii2 = 0; ii2 < N; ii2++) idx[ii2] = ii2;
    idx.sort(function(a, b) { return roto[b] - roto[a]; });
    for (var jj2 = 0; jj2 < N; jj2++) counts[idx[jj2]][jj2]++;
  }

  var result = {};
  for (var fi = 0; fi < N; fi++) {
    var probs = new Array(N);
    var exp   = 0;
    for (var fj = 0; fj < N; fj++) {
      probs[fj] = counts[fi][fj] / nTrials;
      exp += probs[fj] * (fj + 1);
    }
    result[teams[fi].team_id] = {probs: probs, expFinish: exp};
  }
  return result;
}

/* Legacy team-level CV sim — kept as a fallback for when PHASE3_LEAGUE.sim_cfg
   is missing (caches not present, etc.). This was the pre-refactor behaviour. */
function _mcRunSimLegacy(teams) {
  var N = teams.length;
  if (!N || !PHASE3_LEAGUE) return {};
  var cats  = PHASE3_LEAGUE.hit_cats.concat(PHASE3_LEAGUE.pit_cats);
  var nCats = cats.length;
  var lower = {};
  PHASE3_LEAGUE.lower_better.forEach(function(c) { lower[c] = true; });

  var mu  = new Array(N);
  var sig = new Array(N);
  for (var i = 0; i < N; i++) {
    var mrow = new Array(nCats);
    var srow = new Array(nCats);
    var st = teams[i].stats || {};
    for (var k = 0; k < nCats; k++) {
      var c = cats[k];
      var v = st[c] || 0;
      mrow[k] = v;
      var s = Math.abs(v) * (_MC_CV[c] || 0.08);
      if (s < 1e-6) s = Math.max(1e-6, Math.abs(v) * 0.02);
      srow[k] = s;
    }
    mu[i] = mrow;
    sig[i] = srow;
  }
  var counts = new Array(N);
  for (var i = 0; i < N; i++) {
    counts[i] = new Array(N);
    for (var j = 0; j < N; j++) counts[i][j] = 0;
  }
  var sampled = new Array(N);
  for (var i = 0; i < N; i++) sampled[i] = new Array(nCats);
  var roto = new Array(N);
  var idx  = new Array(N);
  var nTrials = _MC_TRIALS;
  for (var trial = 0; trial < nTrials; trial++) {
    for (var i = 0; i < N; i++) {
      roto[i] = 0;
      for (var k = 0; k < nCats; k++) {
        sampled[i][k] = mu[i][k] + sig[i][k] * _mcGaussian();
      }
    }
    for (var k = 0; k < nCats; k++) {
      for (var i = 0; i < N; i++) idx[i] = i;
      var isLower = lower[cats[k]];
      var kk = k;
      idx.sort(function(a, b) {
        var va = sampled[a][kk], vb = sampled[b][kk];
        return isLower ? (va - vb) : (vb - va);
      });
      for (var i = 0; i < N; i++) roto[idx[i]] += (N - i);
    }
    for (var i = 0; i < N; i++) idx[i] = i;
    idx.sort(function(a, b) { return roto[b] - roto[a]; });
    for (var i = 0; i < N; i++) counts[idx[i]][i]++;
  }
  var result = {};
  for (var i = 0; i < N; i++) {
    var probs = new Array(N);
    var exp = 0;
    for (var j = 0; j < N; j++) {
      probs[j] = counts[i][j] / nTrials;
      exp += probs[j] * (j + 1);
    }
    result[teams[i].team_id] = {probs: probs, expFinish: exp};
  }
  return result;
}

/* Baseline league sim is expensive and never changes within a session;
   cache it so repeated trade-machine renders don't rerun it. */
function _mcGetBaseline() {
  if (_mcBaselineCache) return _mcBaselineCache;
  _mcBaselineCache = _mcRunSim(PHASE3_LEAGUE.teams);
  return _mcBaselineCache;
}

/* Render a rows=teams, cols=finish-positions heatmap table.
   Teams sorted ASC by expected finish (best on top).
   highlightTids: optional array of team_ids to bold / yellow-edge. */
function _mcRenderHeatmap(teams, sim, highlightTids) {
  var rows = teams.map(function(t) {
    var s = sim[t.team_id] || {probs: [], expFinish: 0};
    return {
      team_id:   t.team_id,
      name:      t.name,
      probs:     s.probs,
      expFinish: s.expFinish
    };
  });
  rows.sort(function(a, b) { return a.expFinish - b.expFinish; });
  var N = rows.length;
  var hl = {};
  (highlightTids || []).forEach(function(tid) { hl[tid] = true; });

  function cellBg(p) {
    if (p < 0.005) return 'transparent';
    var a = Math.min(0.85, 0.08 + p * 2.2);
    return 'rgba(78,180,100,' + a.toFixed(3) + ')';
  }

  var html = '<table style="width:100%;border-collapse:collapse;background:#0e0e0e;'
           + 'border-radius:8px;overflow:hidden;font-size:.68rem">'
           + '<thead><tr style="background:#161616;color:var(--muted);'
           + 'text-transform:uppercase;letter-spacing:.05em">'
           + '<th style="padding:5px 10px;text-align:left;font-size:.62rem">Team</th>'
           + '<th style="padding:5px 6px;text-align:right;font-size:.62rem">Exp.</th>';
  for (var i = 1; i <= N; i++) {
    html += '<th style="padding:5px 4px;text-align:center;width:32px;font-size:.62rem">' + i + '</th>';
  }
  html += '</tr></thead><tbody>';
  rows.forEach(function(r) {
    var hlStyle = hl[r.team_id] ? 'background:#1c1610;border-left:2px solid #f0c040;' : '';
    html += '<tr style="border-bottom:1px solid #1f1f1f;' + hlStyle + '">'
         +  '<td style="padding:4px 10px;font-weight:600;color:#ddd;white-space:nowrap">' + r.name + '</td>'
         +  '<td style="padding:4px 6px;text-align:right;font-weight:700;color:#ddd">' + r.expFinish.toFixed(1) + '</td>';
    for (var i = 0; i < N; i++) {
      var p   = r.probs[i] || 0;
      var pct = p * 100;
      var label = pct < 0.5 ? '' : (pct < 10 ? pct.toFixed(1) : Math.round(pct).toString());
      html += '<td style="padding:3px;text-align:center;background:' + cellBg(p)
           +  ';color:#eee;font-weight:600">' + label + '</td>';
    }
    html += '</tr>';
  });
  html += '</tbody></table>';
  return html;
}

/* Trade machine: render side-by-side before / after heatmaps with a
   compact expected-finish delta summary for the two involved teams. */
function _mcRenderBeforeAfter(baseTeams, newTeams, baseSim, newSim, userTid, oppTid) {
  function fmtD(d) {
    if (Math.abs(d) < 0.05) return '<span style="color:#888;font-weight:700">■ 0.00</span>';
    var col  = d < 0 ? '#4caf50' : '#e05555';
    var sign = d < 0 ? '▲' : '▼';   // up arrow = better (lower exp finish)
    return '<span style="color:' + col + ';font-weight:700">' + sign + ' ' + Math.abs(d).toFixed(2) + '</span>';
  }
  function row(label, tid, color) {
    var b = baseSim[tid], a = newSim[tid];
    if (!b || !a) return '';
    return '<div style="display:grid;grid-template-columns:110px 1fr 1fr 1fr;gap:10px;'
         + 'padding:4px 0;font-size:.78rem;align-items:center">'
         + '<div style="color:' + color + ';font-weight:700;text-transform:uppercase;letter-spacing:.05em;font-size:.68rem">' + label + '</div>'
         + '<div style="color:var(--muted)">Before <span style="color:#fff;font-weight:700;margin-left:6px">' + b.expFinish.toFixed(2) + '</span></div>'
         + '<div style="color:var(--muted)">After  <span style="color:#fff;font-weight:700;margin-left:6px">' + a.expFinish.toFixed(2) + '</span></div>'
         + '<div>Δ ' + fmtD(a.expFinish - b.expFinish) + '</div>'
         + '</div>';
  }
  var summary = '<div style="background:#131313;border-radius:8px;padding:10px 14px;margin-bottom:10px">'
              + '<div style="font-size:.68rem;color:var(--muted);font-weight:700;'
              + 'text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px">'
              + 'Expected finish — 10,000-trial roto sim</div>'
              + row('You',          userTid, '#4caf50')
              + row('Counterparty', oppTid,  '#e05555')
              + '</div>';

  var hl = [userTid, oppTid];
  var grid = '<div style="display:flex;gap:12px;flex-wrap:wrap">'
           + '<div style="flex:1;min-width:320px">'
           + '<div style="font-size:.66rem;color:var(--muted);font-weight:700;'
           + 'text-transform:uppercase;letter-spacing:.05em;padding:0 0 6px 4px">Before trade</div>'
           + _mcRenderHeatmap(baseTeams, baseSim, hl)
           + '</div>'
           + '<div style="flex:1;min-width:320px">'
           + '<div style="font-size:.66rem;color:var(--muted);font-weight:700;'
           + 'text-transform:uppercase;letter-spacing:.05em;padding:0 0 6px 4px">After trade</div>'
           + _mcRenderHeatmap(newTeams, newSim, hl)
           + '</div>'
           + '</div>';
  return summary + grid;
}

function phase3ToggleMc() {
  var mw  = document.getElementById('phase3-mc-wrap');
  var btn = document.getElementById('phase3-mc-btn');
  if (!mw) return;
  var open = mw.style.display !== 'none';
  if (open) {
    mw.style.display = 'none';
    btn.innerHTML = '▼ Show finish-probability sim (before / after)';
  } else {
    if (!window._phase3Last || !window._phase3Last.newLeague) return;
    _phase3RenderMcSim();
    mw.style.display = '';
    btn.innerHTML = '▲ Hide finish-probability sim';
  }
}

function _phase3RenderMcSim() {
  var mw = document.getElementById('phase3-mc-wrap');
  if (!mw || !window._phase3Last || !window._phase3Last.newLeague) return;
  mw.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:.75rem">Running 50,000 trials…</div>';
  setTimeout(function() {
    var L = window._phase3Last;
    var userTid = _tradeRoster.sendTeamId;
    var oppTid  = _tradeRoster.recvTeamId;
    var baseSim = _mcGetBaseline();
    var newSim  = _mcRunSim(L.newLeague);
    mw.innerHTML = _mcRenderBeforeAfter(PHASE3_LEAGUE.teams, L.newLeague, baseSim, newSim, userTid, oppTid);
  }, 20);
}

/* Season Projections tab handler: run baseline sim over the current league
   and render a single heatmap with Team Alex highlighted. */
function mcRunSeasonProjSim() {
  if (!PHASE3_LEAGUE) return;
  var out = document.getElementById('mc-proj-out');
  if (!out) return;
  out.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:.75rem">Running 50,000 trials…</div>';
  setTimeout(function() {
    var sim = _mcGetBaseline();
    var userTid = PHASE3_LEAGUE.user_team_id;
    out.innerHTML = _mcRenderHeatmap(PHASE3_LEAGUE.teams, sim, userTid != null ? [userTid] : []);
  }, 20);
}


/* Build a deep-copied league with the trade applied. The two affected
   teams have their hitter / pitcher lists rewritten (subtract sent
   players, add received players), then their lineups are re-optimized
   and stats re-aggregated. Untouched teams keep their existing stats. */
function _phase3SimulateTrade() {
  var league = JSON.parse(JSON.stringify(PHASE3_LEAGUE.teams));
  var userTid = _tradeRoster.sendTeamId;
  var oppTid  = _tradeRoster.recvTeamId;
  if (userTid == null || oppTid == null) return null;

  var sendIds = {};   // espn_ids leaving the user
  var recvIds = {};   // espn_ids leaving the opponent
  _tradeRoster.send.forEach(function(p) { if (p.espn_id != null) sendIds[p.espn_id] = true; });
  _tradeRoster.recv.forEach(function(p) { if (p.espn_id != null) recvIds[p.espn_id] = true; });

  function findById(teamId, espn_id) {
    var tm = league.find(function(t) { return t.team_id === teamId; });
    if (!tm) return null;
    var hit = tm.hitters .find(function(p) { return p.espn_id === espn_id; });
    if (hit) return hit;
    return tm.pitchers.find(function(p) { return p.espn_id === espn_id; });
  }

  // Pull the actual player records (with full stat fields) from the
  // original league snapshot — the trade-roster entries don't carry the
  // hitter PA / pitcher IP fields needed for re-aggregation.
  var sendPlayers = _tradeRoster.send.map(function(p) {
    return findById(userTid, p.espn_id) || p;
  });
  var recvPlayers = _tradeRoster.recv.map(function(p) {
    return findById(oppTid, p.espn_id) || p;
  });

  league.forEach(function(team) {
    if (team.team_id !== userTid && team.team_id !== oppTid) return;
    var isUserTeam = (team.team_id === userTid);
    var rmIds = isUserTeam ? sendIds : recvIds;
    var addList = isUserTeam ? recvPlayers : sendPlayers;
    var origPitIds = team.pitchers.map(function(p){ return p.espn_id; }).sort().join();
    team.hitters  = team.hitters .filter(function(p) { return !rmIds[p.espn_id]; });
    team.pitchers = team.pitchers.filter(function(p) { return !rmIds[p.espn_id]; });
    addList.forEach(function(p) {
      // Decide bucket: if it has elig list it's a hitter, else pitcher
      // (every hitter record carries elig; pitchers don't)
      if (p.elig && p.elig.length) team.hitters.push(p);
      else team.pitchers.push(p);
    });

    // Apply roster drops for this side. Drops carry espn_id from the
    // original roster so this filters out any player the user chose to drop
    // when the trade's inbound count exceeds outbound. Drops never remove
    // players that are being sent away (already gone above) because the
    // drop picker excludes those from its list.
    var dropList = isUserTeam
      ? (_tradeRoster.drops || [])
      : (_tradeRoster.oppDrops || []);
    if (dropList.length) {
      var dropIds = {};
      dropList.forEach(function(d) { if (d.espn_id != null) dropIds[d.espn_id] = true; });
      team.hitters  = team.hitters .filter(function(p) { return !dropIds[p.espn_id]; });
      team.pitchers = team.pitchers.filter(function(p) { return !dropIds[p.espn_id]; });
    }

    // Apply any free-agent hitter pickups. Each pickup carries elig=[slotId]
    // so the LAP pins it to the target slot.
    var hitPickups = isUserTeam
      ? (_tradeRoster.pickups || [])
      : (_tradeRoster.oppPickups || []);
    hitPickups.forEach(function(pk) { team.hitters.push(pk); });

    // Apply any free-agent pitcher pickups. Pitchers have no slot/LAP —
    // they just add to the aggregate pool.
    var pitPickups = isUserTeam
      ? (_tradeRoster.pitcherPickups || [])
      : (_tradeRoster.oppPitcherPickups || []);
    pitPickups.forEach(function(pk) { team.pitchers.push(pk); });
    // Drop any custom-lineup pins for players the user traded away /
    // dropped before resolving — keeps the manual override valid.
    _phase3PruneCustomLineup(team.team_id, team);
    // Inject any IL / inactive players the user activated via the roster
    // picker. They're not in team.hitters by default (the phase3 payload
    // excludes inactives), so if a custom pin references an IL player we
    // pull them from TRADE_HITTERS into the hitter pool here.
    _phase3InjectActivatedIl(team);
    // Same for pitchers — activated IL pitchers get pushed into
    // team.pitchers so the W/SO/ERA aggregate reflects them.
    _phase3InjectActivatedIlPitchers(team);
    // Re-optimize hitter lineup (honors manual pins in _phase3CustomLineup)
    var resolved = _phase3ResolveLineup(team);
    var starters = resolved.starters;
    var hAgg = _phase3AggHit(starters);
    team.stats.R    = Math.round(hAgg.R    * 10) / 10;
    team.stats.HR   = Math.round(hAgg.HR   * 10) / 10;
    team.stats.RBI  = Math.round(hAgg.RBI  * 10) / 10;
    team.stats.SO_h = Math.round(hAgg.SO_h * 10) / 10;
    team.stats.SB   = Math.round(hAgg.SB   * 10) / 10;
    team.stats.OBP  = Math.round(hAgg.OBP  * 10000) / 10000;
    // Only recompute pitcher stats if pitcher roster actually changed —
    // avoids floating-point drift from JS re-aggregating IP-weighted ERA/WHIP
    var newPitIds = team.pitchers.map(function(p){ return p.espn_id; }).sort().join();
    if (origPitIds !== newPitIds) {
      var pAgg = _phase3AggPit(team.pitchers);
      team.stats.W    = Math.round(pAgg.W    * 10) / 10;
      team.stats.SO_p = Math.round(pAgg.SO_p * 10) / 10;
      team.stats.SV   = Math.round(pAgg.SV   * 10) / 10;
      team.stats.HLD  = Math.round(pAgg.HLD  * 10) / 10;
      team.stats.ERA  = Math.round(pAgg.ERA  * 1000) / 1000;
      team.stats.WHIP = Math.round(pAgg.WHIP * 1000) / 1000;
    }
  });

  _phase3RecomputeZ(league);
  return league;
}

/* Color helper — exact JS port of Python _proj_rank_color in fantasy.py.
   Gold (#f0c040) for #1; then red(255,60,50) → white(235,235,235) → blue(60,140,255)
   for ranks 2..n. Matches the rest of the dashboard's red=best/blue=worst gradient. */
function _phase3RankColor(rank, n) {
  if (n <= 1) return '#fff';
  if (rank === 1) return '#f0c040';
  var t = (rank - 2) / Math.max(1, n - 2);
  var r, g, b;
  if (t < 0.5) {
    var s = t * 2;
    r = Math.round(255 + (235 - 255) * s);
    g = Math.round( 60 + (235 -  60) * s);
    b = Math.round( 50 + (235 -  50) * s);
  } else {
    var s2 = (t - 0.5) * 2;
    r = Math.round(235 + ( 60 - 235) * s2);
    g = Math.round(235 + (140 - 235) * s2);
    b = Math.round(235 + (255 - 235) * s2);
  }
  return 'rgb(' + r + ',' + g + ',' + b + ')';
}

/* Convert an ESPN elig slot-ID array to a compact position label string.
   Filters to real positions (C/1B/2B/3B/SS/OF/DH), skips MI/CI/UTIL. */
var _ELIG_MAP = {0:'C',1:'1B',2:'2B',3:'3B',4:'SS',5:'OF'};
function _eligLabel(elig) {
  if (!elig || !elig.length) return '';
  var parts = elig.map(function(e){ return _ELIG_MAP[e] || ''; })
                  .filter(function(x){ return x; });
  // Show DH only when the player has no real field position (e.g. Schwarber)
  if (!parts.length && elig.indexOf(11) >= 0) parts.push('DH');
  return parts.join('/');
}

/* Map FanGraphs position string → array of ESPN slot IDs.
   FG uses strings like "C", "1B", "2B/SS", "OF", "DH". We parse all
   slash-separated tokens and map to slot IDs. Also add composite slots
   (MI=6 for 2B/SS, CI=7 for 1B/3B, UTIL=12 for any). */
var _FG_POS_TO_SLOT = {'C':0,'1B':1,'2B':2,'3B':3,'SS':4,'OF':5,'DH':19};
function _fgPosToSlots(fgPos) {
  if (!fgPos) return [];
  var parts = fgPos.split('/');
  var slots = [];
  parts.forEach(function(p) {
    var s = _FG_POS_TO_SLOT[p.trim()];
    if (s !== undefined) slots.push(s);
  });
  // Add composite positions
  var has2B = slots.indexOf(2) >= 0, hasSS = slots.indexOf(4) >= 0;
  var has1B = slots.indexOf(1) >= 0, has3B = slots.indexOf(3) >= 0;
  if (has2B || hasSS) slots.push(6);  // MI
  if (has1B || has3B) slots.push(7);  // CI
  slots.push(12); // UTIL — every hitter qualifies
  return slots;
}

/* Check if a player (with elig array or fg_pos string) can fill a given slot ID.
   Returns true if the player is eligible for that slot. */
function _canFillSlot(player, slotId) {
  // ESPN elig takes priority (more accurate)
  if (player.elig && player.elig.length) {
    return player.elig.indexOf(slotId) >= 0;
  }
  // Fall back to FG position
  if (player.fg_pos) {
    var slots = _fgPosToSlots(player.fg_pos);
    return slots.indexOf(slotId) >= 0;
  }
  return true; // no position data → don't filter out
}

/* For FA hitters without ESPN elig, derive a label from fg_pos. */
function _posLabel(player) {
  if (player.elig && player.elig.length) return _eligLabel(player.elig);
  if (player.fg_pos) return player.fg_pos;
  return '';
}

/* Compute the LAP-optimized lineup dollar value + slot assignments for one team.
   Also returns the pitcher staff sorted by dollar value (no slot constraints).
   Used for the diagnostic display so the user can SEE why z changed. */
function _phase3LineupSummary(team, applyCustom) {
  // applyCustom defaults to true — only the pre-trade "Before" snapshots
  // pass `false` so the baseline stays the pure dollar-optimized lineup.
  var full = (applyCustom === false)
    ? _phase3OptimizeHittersFull(team.hitters || [])
    : _phase3ResolveLineup(team);
  var hDollar = 0;
  full.starters.forEach(function(s) { hDollar += (s.dollars || 0); });
  var pitchers = (team.pitchers || []).slice().sort(function(a, b) {
    return (b.dollars || 0) - (a.dollars || 0);
  });
  var pDollar = 0;
  pitchers.forEach(function(p) { pDollar += (p.dollars || 0); });
  return {
    dollar:   hDollar,               // back-compat: 'dollar' = hitter lineup $
    hDollar:  hDollar,
    pDollar:  pDollar,
    count:    full.starters.length,
    assigns:  full.assigns,          // [{slot_id, slot_label, player|null}, ...]
    pitchers: pitchers               // dollar-sorted full staff (no slot)
  };
}

/* Render the compact 2-team delta + (collapsed) full standings table */
function _phase3RenderImpact() {
  var newLeague = _phase3SimulateTrade();
  var wrap = document.getElementById('phase3-wrap');
  if (!newLeague || !wrap) { if (wrap) wrap.style.display = 'none'; return; }
  wrap.style.display = '';
  var n = PHASE3_LEAGUE.teams.length;
  var userTid = _tradeRoster.sendTeamId;
  var oppTid  = _tradeRoster.recvTeamId;
  function findOld(tid) { return PHASE3_LEAGUE.teams.find(function(t) { return t.team_id === tid; }); }
  function findNew(tid) { return newLeague.find(function(t) { return t.team_id === tid; }); }
  var oldUser = findOld(userTid), newUser = findNew(userTid);
  var oldOpp  = findOld(oppTid),  newOpp  = findNew(oppTid);
  if (!oldUser || !newUser || !oldOpp || !newOpp) {
    wrap.style.display = 'none'; return;
  }

  // Compute lineup-$ before/after for both teams (diagnostic)
  var sumOldUser = _phase3LineupSummary(oldUser, false);
  var sumNewUser = _phase3LineupSummary(newUser);
  var sumOldOpp  = _phase3LineupSummary(oldOpp, false);
  var sumNewOpp  = _phase3LineupSummary(newOpp);

  function fmtDelta(before, after, decimals) {
    var d = after - before;
    var sign = d > 0 ? '+' : (d < 0 ? '−' : '');
    var col  = d > 0.005 ? '#4caf50' : (d < -0.005 ? '#e05555' : '#888');
    return '<span style="color:' + col + ';font-weight:700">'
         + sign + Math.abs(d).toFixed(decimals) + '</span>';
  }
  function fmtRankDelta(before, after) {
    var d = before - after;  // positive = improved (rank dropped numerically)
    var col, sign;
    if (d > 0) { col = '#4caf50'; sign = '▲'; }
    else if (d < 0) { col = '#e05555'; sign = '▼'; }
    else { col = '#888'; sign = '■'; }
    return '<span style="color:' + col + ';font-weight:700">'
         + sign + ' ' + Math.abs(d) + '</span>';
  }

  function teamCard(label, oldT, newT, oldSum, newSum, accent) {
    var oC = _phase3RankColor(oldT.rank_total, n);
    var nC = _phase3RankColor(newT.rank_total, n);
    var dollarDelta = newSum.dollar - oldSum.dollar;
    var dCol = dollarDelta > 0.05 ? '#4caf50' : (dollarDelta < -0.05 ? '#e05555' : '#888');
    var dSign = dollarDelta > 0 ? '+' : (dollarDelta < 0 ? '−' : '');
    var dollarStr = '<span style="color:' + dCol + ';font-weight:700">'
                  + dSign + '$' + Math.abs(dollarDelta).toFixed(1) + '</span>';
    return '<div style="flex:1;min-width:230px;background:#1a1a1a;border-radius:8px;'
         + 'padding:10px 13px;border-left:3px solid ' + accent + '">'
         + '<div style="font-size:.7rem;color:var(--muted);font-weight:700;'
         +   'text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">'
         +   label + '</div>'
         + '<div style="font-size:.92rem;font-weight:700;color:#fff;margin-bottom:8px">'
         +   newT.name + '</div>'
         + '<div style="display:grid;grid-template-columns:auto auto auto auto;gap:5px 10px;'
         +   'font-size:.78rem;align-items:center">'
         +   '<div style="color:var(--muted)">Z Total</div>'
         +   '<div>' + oldT.z_total.toFixed(2) + '</div>'
         +   '<div style="color:#666">→</div>'
         +   '<div style="color:' + nC + ';font-weight:700">' + newT.z_total.toFixed(2) + '</div>'
         +   '<div style="color:var(--muted)">Rank</div>'
         +   '<div style="color:' + oC + '">#' + oldT.rank_total + '</div>'
         +   '<div style="color:#666">→</div>'
         +   '<div style="color:' + nC + ';font-weight:700">#' + newT.rank_total + '&nbsp;&nbsp;'
         +     fmtRankDelta(oldT.rank_total, newT.rank_total) + '</div>'
         +   '<div style="color:var(--muted)">H&nbsp;Z</div>'
         +   '<div>' + oldT.z_hit.toFixed(2) + '</div>'
         +   '<div style="color:#666">→</div>'
         +   '<div>' + newT.z_hit.toFixed(2) + '&nbsp;&nbsp;' + fmtDelta(oldT.z_hit, newT.z_hit, 2) + '</div>'
         +   '<div style="color:var(--muted)">P&nbsp;Z</div>'
         +   '<div>' + oldT.z_pit.toFixed(2) + '</div>'
         +   '<div style="color:#666">→</div>'
         +   '<div>' + newT.z_pit.toFixed(2) + '&nbsp;&nbsp;' + fmtDelta(oldT.z_pit, newT.z_pit, 2) + '</div>'
         +   '<div style="color:var(--muted)" title="Sum of $ values of the 11 LAP-optimized starting hitters">Lineup&nbsp;$</div>'
         +   '<div>$' + oldSum.dollar.toFixed(1) + '</div>'
         +   '<div style="color:#666">→</div>'
         +   '<div>$' + newSum.dollar.toFixed(1) + '&nbsp;&nbsp;' + dollarStr + '</div>'
         + '</div>'
         + '</div>';
  }

  document.getElementById('phase3-delta').innerHTML =
    '<div style="display:flex;gap:12px;flex-wrap:wrap">'
    + teamCard('You',          oldUser, newUser, sumOldUser, sumNewUser, '#4caf50')
    + teamCard('Counterparty', oldOpp,  newOpp,  sumOldOpp,  sumNewOpp,  '#e05555')
    + '</div>';

  // Stash summaries + new league so the toggle handlers can re-render
  // their respective views without recomputing the simulation.
  window._phase3Last = {
    sumOldUser: sumOldUser, sumNewUser: sumNewUser,
    sumOldOpp:  sumOldOpp,  sumNewOpp:  sumNewOpp,
    oldUser:    oldUser,    newUser:    newUser,
    oldOpp:     oldOpp,     newOpp:     newOpp,
    newLeague:  newLeague
  };

  // Re-build any open sub-views
  var lw = document.getElementById('phase3-lineup-wrap');
  if (lw && lw.style.display !== 'none') _phase3RenderLineups();
  var tw = document.getElementById('phase3-table-wrap');
  if (tw && tw.style.display !== 'none') _phase3RenderTable(newLeague);
  var mw = document.getElementById('phase3-mc-wrap');
  if (mw && mw.style.display !== 'none') _phase3RenderMcSim();
}

/* Toggle the optimized-lineup table (slot-by-slot before/after for both teams) */
function phase3ToggleLineups() {
  var lw  = document.getElementById('phase3-lineup-wrap');
  var btn = document.getElementById('phase3-lineup-btn');
  if (!lw) return;
  var open = lw.style.display !== 'none';
  if (open) {
    lw.style.display = 'none';
    btn.innerHTML = '▼ Show optimized lineups + staff (before / after)';
  } else {
    if (!window._phase3Last) {
      // No render has happened yet — bail quietly.
      return;
    }
    _phase3RenderLineups();
    lw.style.display = '';
    btn.innerHTML = '▲ Hide optimized lineups';
  }
}

/* Build a phase3-compatible hitter record from a TRADE_HITTERS free-agent row.
   The pickup is pinned to exactly one slot via elig=[slotId] so the LAP puts
   them there and nowhere else — honest "fill the hole" semantics. */
function _phase3MakePickup(p, slotId) {
  return {
    espn_id:  'fa_' + (p.name || '').replace(/[^A-Za-z0-9]+/g, '_'),
    name:     p.name,
    team:     p.team,
    dollars:  p.dollars || 0,
    elig:     [slotId],
    R:        (p.proj && p.proj.R)    || 0,
    HR:       (p.proj && p.proj.HR)   || 0,
    RBI:      (p.proj && p.proj.RBI)  || 0,
    SO_h:     (p.proj && p.proj.K_h)  || 0,
    SB:       (p.proj && p.proj.SB)   || 0,
    OBP:      (p.proj && p.proj.OBP)  || 0,
    PA:       (p.proj && p.proj.PA)   || 600,
    is_pickup:   true,
    pickup_slot: slotId
  };
}

/* Return the top-N unrostered hitters from the global trade pool, sorted by $.
   Excludes any players already pinned as pickups (on either side). */
function _phase3FreeAgentHitters(limit) {
  var inUse = {};
  (_tradeRoster.pickups    || []).forEach(function(pk) { inUse[pk.name] = true; });
  (_tradeRoster.oppPickups || []).forEach(function(pk) { inUse[pk.name] = true; });
  var fas = TRADE_HITTERS.filter(function(p) {
    return p.team_id == null && !inUse[p.name];
  });
  fas.sort(function(a, b) { return (b.dollars || 0) - (a.dollars || 0); });
  return fas.slice(0, limit || 15);
}

/* Open the picker menu for a given empty slot. Stores state on the window
   so the next _phase3RenderLineups() call renders the inline list.
   `side` is 'user' or 'opp'. */
function phase3OpenPickupMenu(slotId, slotLabel) {
  window._phase3PickerSlot = {slot_id: slotId, slot_label: slotLabel};
  window._phase3OppPickerSlot = null;
  _phase3RenderLineups();
}
function phase3ClosePickupMenu() {
  window._phase3PickerSlot = null;
  _phase3RenderLineups();
}
function phase3OpenOppPickupMenu(slotId, slotLabel) {
  window._phase3OppPickerSlot = {slot_id: slotId, slot_label: slotLabel};
  window._phase3PickerSlot = null;
  _phase3RenderLineups();
}
function phase3CloseOppPickupMenu() {
  window._phase3OppPickerSlot = null;
  _phase3RenderLineups();
}

/* Add a free-agent pickup to the user's team pinned to the current picker slot,
   then re-run the full phase3 simulation so z-scores, ranks, and the lineup
   display all reflect the pickup. */
function phase3AddPickup(playerName) {
  if (!window._phase3PickerSlot) return;
  var slotId = window._phase3PickerSlot.slot_id;
  var p = TRADE_HITTERS.find(function(x) { return x.name === playerName; });
  if (!p) return;
  _tradeRoster.pickups.push(_phase3MakePickup(p, slotId));
  window._phase3PickerSlot = null;
  _tradeCalc();          // triggers _phase3RenderImpact → re-renders lineups too
}

/* Add a free-agent pickup to the OPPONENT's team. Used when a trade opens a
   hole on the counterparty side and we want a realistic sim instead of one
   where the opposing team is playing short a starter. */
function phase3AddOppPickup(playerName) {
  if (!window._phase3OppPickerSlot) return;
  var slotId = window._phase3OppPickerSlot.slot_id;
  var p = TRADE_HITTERS.find(function(x) { return x.name === playerName; });
  if (!p) return;
  if (!_tradeRoster.oppPickups) _tradeRoster.oppPickups = [];
  _tradeRoster.oppPickups.push(_phase3MakePickup(p, slotId));
  window._phase3OppPickerSlot = null;
  _tradeCalc();
}

/* Remove an active pickup by name. */
function phase3RemovePickup(name) {
  _tradeRoster.pickups = (_tradeRoster.pickups || []).filter(function(pk) {
    return pk.name !== name;
  });
  _tradeCalc();
}
function phase3RemoveOppPickup(name) {
  _tradeRoster.oppPickups = (_tradeRoster.oppPickups || []).filter(function(pk) {
    return pk.name !== name;
  });
  _tradeCalc();
}

/* ── Manual-lineup roster-slot picker handlers ─────────────────────────
   Let the user override the dollar-optimized After lineup by pinning a
   specific player to a specific slot. All handlers pair with a mirror
   variant for the counterparty side. _tradeCalc() re-runs simulation so
   stats, z-scores, and the lineup table all reflect the change. */
function _phase3EnsureCustom(teamId) {
  if (!window._phase3CustomLineup[teamId]) window._phase3CustomLineup[teamId] = {};
  return window._phase3CustomLineup[teamId];
}
/* All picker handlers key by SLOT INDEX (position in PHASE3_LEAGUE.slots)
   rather than slot_id, because multiple slots can share the same slot_id
   (OF has three). slotId is still passed along so the picker can filter
   eligible players; the INDEX is what uniquely identifies a lineup spot. */
function phase3OpenUserRosterPicker(slotIdx, slotId, slotLabel) {
  window._phase3RosterSlotUser = {slot_idx: slotIdx, slot_id: slotId, slot_label: slotLabel};
  window._phase3RosterSlotOpp  = null;
  window._phase3PickerSlot     = null;
  window._phase3OppPickerSlot  = null;
  _phase3RenderLineups();
}
function phase3CloseUserRosterPicker() {
  window._phase3RosterSlotUser = null;
  _phase3RenderLineups();
}
function phase3OpenOppRosterPicker(slotIdx, slotId, slotLabel) {
  window._phase3RosterSlotOpp  = {slot_idx: slotIdx, slot_id: slotId, slot_label: slotLabel};
  window._phase3RosterSlotUser = null;
  window._phase3PickerSlot     = null;
  window._phase3OppPickerSlot  = null;
  _phase3RenderLineups();
}
function phase3CloseOppRosterPicker() {
  window._phase3RosterSlotOpp = null;
  _phase3RenderLineups();
}
/* Pin a rostered player (by espn_id) to a slot INDEX. If that player was
   pinned elsewhere we clear the old pin so they only occupy one slot. */
function _phase3ApplyRosterPick(teamId, slotIdx, espnIdStr) {
  var custom = _phase3EnsureCustom(teamId);
  // Clear any other slot pinning this same player
  Object.keys(custom).forEach(function(sidx) {
    if (String(custom[sidx]) === String(espnIdStr)) delete custom[sidx];
  });
  // ESPN IDs may be strings (FA synthetic ids start "fa_"); coerce to number
  // where possible to match the hitter records.
  var eid = isNaN(Number(espnIdStr)) ? espnIdStr : Number(espnIdStr);
  custom[slotIdx] = eid;
}
function phase3PickUserRosterPlayer(slotIdx, espnIdStr) {
  var tid = _tradeRoster.sendTeamId;
  if (tid == null) return;
  _phase3ApplyRosterPick(tid, slotIdx, espnIdStr);
  window._phase3RosterSlotUser = null;
  _tradeCalc();
}
function phase3PickOppRosterPlayer(slotIdx, espnIdStr) {
  var tid = _tradeRoster.recvTeamId;
  if (tid == null) return;
  _phase3ApplyRosterPick(tid, slotIdx, espnIdStr);
  window._phase3RosterSlotOpp = null;
  _tradeCalc();
}
/* Pin a slot (by INDEX) to empty — overrides the optimizer for that one
   lineup spot but leaves the rest alone. */
function phase3ClearUserRosterSlot(slotIdx) {
  var tid = _tradeRoster.sendTeamId;
  if (tid == null) return;
  var custom = _phase3EnsureCustom(tid);
  custom[slotIdx] = null;
  window._phase3RosterSlotUser = null;
  _tradeCalc();
}
function phase3ClearOppRosterSlot(slotIdx) {
  var tid = _tradeRoster.recvTeamId;
  if (tid == null) return;
  var custom = _phase3EnsureCustom(tid);
  custom[slotIdx] = null;
  window._phase3RosterSlotOpp = null;
  _tradeCalc();
}
/* Wipe all manual pins for a team → falls back to the dollar-optimized lineup. */
function phase3ResetUserLineup() {
  var tid = _tradeRoster.sendTeamId;
  if (tid != null) delete window._phase3CustomLineup[tid];
  window._phase3RosterSlotUser = null;
  _tradeCalc();
}
function phase3ResetOppLineup() {
  var tid = _tradeRoster.recvTeamId;
  if (tid != null) delete window._phase3CustomLineup[tid];
  window._phase3RosterSlotOpp = null;
  _tradeCalc();
}

/* IL-pitcher activation handlers. Activation is a PAIR (in→out): the
   swapped-out starter is removed from the pitching aggregate and the
   activated IL pitcher replaces them. Clicking Activate opens a picker
   showing current starters to swap out; confirming records the pair and
   re-runs the sim. Deactivating restores the swapped-out starter. */
function phase3StartActivateUserIlPitcher(espnIdStr) {
  window._phase3IlPitcherSwapPicker = {side: 'user', in_espn_id: espnIdStr};
  _phase3RenderLineups();
}
function phase3StartActivateOppIlPitcher(espnIdStr) {
  window._phase3IlPitcherSwapPicker = {side: 'opp', in_espn_id: espnIdStr};
  _phase3RenderLineups();
}
function phase3CancelActivateIlPitcher() {
  window._phase3IlPitcherSwapPicker = null;
  _phase3RenderLineups();
}
function _phase3SetIlPitcherPair(teamId, inEidStr, outEidStr) {
  if (teamId == null) return;
  if (!window._phase3IlPitcherActivations[teamId]) {
    window._phase3IlPitcherActivations[teamId] = {};
  }
  var inEid  = isNaN(Number(inEidStr))  ? inEidStr  : Number(inEidStr);
  var outEid = isNaN(Number(outEidStr)) ? outEidStr : Number(outEidStr);
  window._phase3IlPitcherActivations[teamId][inEid] = outEid;
}
function _phase3RemoveIlPitcherPair(teamId, inEidStr) {
  if (teamId == null) return;
  var map = window._phase3IlPitcherActivations[teamId];
  if (!map) return;
  var inEid = isNaN(Number(inEidStr)) ? inEidStr : Number(inEidStr);
  delete map[inEid];
  if (!Object.keys(map).length) delete window._phase3IlPitcherActivations[teamId];
}
function phase3ConfirmActivateUserIlPitcher(outEidStr) {
  var pick = window._phase3IlPitcherSwapPicker;
  if (!pick || pick.side !== 'user') return;
  _phase3SetIlPitcherPair(_tradeRoster.sendTeamId, pick.in_espn_id, outEidStr);
  window._phase3IlPitcherSwapPicker = null;
  _tradeCalc();
}
function phase3ConfirmActivateOppIlPitcher(outEidStr) {
  var pick = window._phase3IlPitcherSwapPicker;
  if (!pick || pick.side !== 'opp') return;
  _phase3SetIlPitcherPair(_tradeRoster.recvTeamId, pick.in_espn_id, outEidStr);
  window._phase3IlPitcherSwapPicker = null;
  _tradeCalc();
}
function phase3DeactivateUserIlPitcher(espnIdStr) {
  _phase3RemoveIlPitcherPair(_tradeRoster.sendTeamId, espnIdStr);
  _tradeCalc();
}
function phase3DeactivateOppIlPitcher(espnIdStr) {
  _phase3RemoveIlPitcherPair(_tradeRoster.recvTeamId, espnIdStr);
  _tradeCalc();
}

/* ── Pitcher free-agent pickups ────────────────────────────────────────
   Mirrors the hitter pickup pipeline but simpler: pitchers have no slot
   positions, so a pickup is just another entry in the team.pitchers list.
   Triggered when a trade leaves the user with more pitchers going out than
   coming in — each open pitcher spot gets a "+" button that opens an
   inline picker of top-10 SP + top-3 RP available from free agency. */

function _phase3MakePickupPitcher(p) {
  return {
    espn_id:  'fa_' + (p.name || '').replace(/[^A-Za-z0-9]+/g, '_'),
    name:     p.name,
    team:     p.team,
    dollars:  p.dollars || 0,
    role:     p.role || 'sp',
    W:        (p.proj && p.proj.W)    || 0,
    SO_p:     (p.proj && p.proj.K_p)  || 0,
    SV:       (p.proj && p.proj.SV)   || 0,
    HLD:      (p.proj && p.proj.HLD)  || 0,
    ERA:      (p.proj && p.proj.ERA)  || 0,
    WHIP:     (p.proj && p.proj.WHIP) || 0,
    // IP fallback: SP ~ 160, RP ~ 65. Rough but good enough for IP-weighted
    // ERA/WHIP aggregation — FA pickups don't hugely move a team's rates.
    IP:       (p.role === 'sp' ? 160 : 65),
    is_pickup:   true,
    is_pitcher:  true
  };
}

/* Top N unrostered pitchers sorted by $, split out by role.
   Returns top 10 SPs followed by top 3 RPs, excluding anyone already
   picked up. team_id == null catches both true FAs and IL/NA players since
   IL players now carry a team_id (see _all_rostered in _build_phase3_payload). */
function _phase3FreeAgentPitchers() {
  var inUse = {};
  (_tradeRoster.pitcherPickups    || []).forEach(function(pk) { inUse[pk.name] = true; });
  (_tradeRoster.oppPitcherPickups || []).forEach(function(pk) { inUse[pk.name] = true; });
  var pool = TRADE_PITCHERS.filter(function(p) {
    return p.team_id == null && !inUse[p.name];
  });
  var sps = pool.filter(function(p) { return (p.role || 'sp') === 'sp'; });
  var rps = pool.filter(function(p) { return (p.role || 'sp') === 'rp'; });
  sps.sort(function(a, b) { return (b.dollars || 0) - (a.dollars || 0); });
  rps.sort(function(a, b) { return (b.dollars || 0) - (a.dollars || 0); });
  return sps.slice(0, 10).concat(rps.slice(0, 3));
}

function phase3OpenPitcherPicker() {
  window._phase3PitcherPicker = true;
  window._phase3OppPitcherPicker = false;
  _phase3RenderLineups();
}
function phase3ClosePitcherPicker() {
  window._phase3PitcherPicker = false;
  _phase3RenderLineups();
}
function phase3OpenOppPitcherPicker() {
  window._phase3OppPitcherPicker = true;
  window._phase3PitcherPicker = false;
  _phase3RenderLineups();
}
function phase3CloseOppPitcherPicker() {
  window._phase3OppPitcherPicker = false;
  _phase3RenderLineups();
}

function phase3AddPitcherPickup(playerName) {
  var p = TRADE_PITCHERS.find(function(x) { return x.name === playerName; });
  if (!p) return;
  if (!_tradeRoster.pitcherPickups) _tradeRoster.pitcherPickups = [];
  _tradeRoster.pitcherPickups.push(_phase3MakePickupPitcher(p));
  window._phase3PitcherPicker = false;
  _tradeCalc();          // triggers _phase3RenderImpact → re-renders lineups too
}
function phase3AddOppPitcherPickup(playerName) {
  var p = TRADE_PITCHERS.find(function(x) { return x.name === playerName; });
  if (!p) return;
  if (!_tradeRoster.oppPitcherPickups) _tradeRoster.oppPitcherPickups = [];
  _tradeRoster.oppPitcherPickups.push(_phase3MakePickupPitcher(p));
  window._phase3OppPitcherPicker = false;
  _tradeCalc();
}

function phase3RemovePitcherPickup(name) {
  _tradeRoster.pitcherPickups = (_tradeRoster.pitcherPickups || []).filter(function(pk) {
    return pk.name !== name;
  });
  _tradeCalc();
}
function phase3RemoveOppPitcherPickup(name) {
  _tradeRoster.oppPitcherPickups = (_tradeRoster.oppPitcherPickups || []).filter(function(pk) {
    return pk.name !== name;
  });
  _tradeCalc();
}

/* ── Roster drops (for trades where inbound > outbound on one side) ────
   When a side receives more players than they send, they need to drop
   someone from their roster to keep the sim honest. Drops can be ANY
   rostered player (hitter or pitcher). Players being sent away are
   excluded from the drop-picker list so you can't double-count them. */
function phase3OpenDropPicker() {
  window._phase3DropPicker = true;
  window._phase3OppDropPicker = false;
  _phase3RenderLineups();
}
function phase3CloseDropPicker() {
  window._phase3DropPicker = false;
  _phase3RenderLineups();
}
function phase3OpenOppDropPicker() {
  window._phase3OppDropPicker = true;
  window._phase3DropPicker = false;
  _phase3RenderLineups();
}
function phase3CloseOppDropPicker() {
  window._phase3OppDropPicker = false;
  _phase3RenderLineups();
}

/* Find a rostered player's record (hitter or pitcher) on a given team.
   Returns { rec, is_pitcher } or null. */
function _phase3FindRostered(teamId, espnId) {
  if (!PHASE3_LEAGUE) return null;
  var tm = PHASE3_LEAGUE.teams.find(function(t) { return t.team_id === teamId; });
  if (!tm) return null;
  for (var i = 0; i < (tm.hitters || []).length; i++) {
    if (tm.hitters[i].espn_id === espnId) {
      return { rec: tm.hitters[i], is_pitcher: false };
    }
  }
  for (var j = 0; j < (tm.pitchers || []).length; j++) {
    if (tm.pitchers[j].espn_id === espnId) {
      return { rec: tm.pitchers[j], is_pitcher: true };
    }
  }
  return null;
}

function phase3AddDrop(espnIdStr) {
  if (!PHASE3_LEAGUE) return;
  var espnId = isNaN(parseInt(espnIdStr, 10)) ? espnIdStr : parseInt(espnIdStr, 10);
  var userTid = _tradeRoster.sendTeamId;
  var found = _phase3FindRostered(userTid, espnId);
  if (!found) return;
  if (!_tradeRoster.drops) _tradeRoster.drops = [];
  // Prevent duplicates
  for (var i = 0; i < _tradeRoster.drops.length; i++) {
    if (_tradeRoster.drops[i].espn_id === espnId) return;
  }
  _tradeRoster.drops.push({
    espn_id:    espnId,
    name:       found.rec.name || '',
    team:       found.rec.team || '',
    dollars:    found.rec.dollars || 0,
    is_pitcher: found.is_pitcher
  });
  window._phase3DropPicker = false;
  _tradeCalc();
}

function phase3AddOppDrop(espnIdStr) {
  if (!PHASE3_LEAGUE) return;
  var espnId = isNaN(parseInt(espnIdStr, 10)) ? espnIdStr : parseInt(espnIdStr, 10);
  var oppTid = _tradeRoster.recvTeamId;
  if (oppTid == null) return;
  var found = _phase3FindRostered(oppTid, espnId);
  if (!found) return;
  if (!_tradeRoster.oppDrops) _tradeRoster.oppDrops = [];
  for (var i = 0; i < _tradeRoster.oppDrops.length; i++) {
    if (_tradeRoster.oppDrops[i].espn_id === espnId) return;
  }
  _tradeRoster.oppDrops.push({
    espn_id:    espnId,
    name:       found.rec.name || '',
    team:       found.rec.team || '',
    dollars:    found.rec.dollars || 0,
    is_pitcher: found.is_pitcher
  });
  window._phase3OppDropPicker = false;
  _tradeCalc();
}

function phase3RemoveDrop(espnIdStr) {
  var espnId = isNaN(parseInt(espnIdStr, 10)) ? espnIdStr : parseInt(espnIdStr, 10);
  _tradeRoster.drops = (_tradeRoster.drops || []).filter(function(d) {
    return d.espn_id !== espnId;
  });
  _tradeCalc();
}
function phase3RemoveOppDrop(espnIdStr) {
  var espnId = isNaN(parseInt(espnIdStr, 10)) ? espnIdStr : parseInt(espnIdStr, 10);
  _tradeRoster.oppDrops = (_tradeRoster.oppDrops || []).filter(function(d) {
    return d.espn_id !== espnId;
  });
  _tradeCalc();
}

/* How many drops does a given side need? = max(0, recv - send - existing pickups).
   We treat hitter/pitcher pickups as already filling incoming spots, so only
   the remaining surplus needs a drop. */
function _phase3DropsNeeded(side) {
  // side: 'user' | 'opp'
  if (side === 'user') {
    var inc = _tradeRoster.recv.length;
    var out = _tradeRoster.send.length;
    var pk  = (_tradeRoster.pickups || []).length + (_tradeRoster.pitcherPickups || []).length;
    return Math.max(0, (inc + pk) - out);
  } else {
    var incO = _tradeRoster.send.length;
    var outO = _tradeRoster.recv.length;
    var pkO  = (_tradeRoster.oppPickups || []).length + (_tradeRoster.oppPitcherPickups || []).length;
    return Math.max(0, (incO + pkO) - outO);
  }
}

/* Render a 2-column (You / Counterparty) slot-by-slot before/after lineup table.
   Highlights any slot whose occupant changed. Includes pitcher staff and
   "+" buttons on empty hitter slots so the user can simulate a waiver pickup.
   Empty slots on the opponent side now also get "+ FA" buttons so the sim
   doesn't treat the counterparty as playing short a starter. */
function _phase3RenderLineups() {
  var lw = document.getElementById('phase3-lineup-wrap');
  if (!lw || !window._phase3Last) return;
  var L = window._phase3Last;
  var userTid = _tradeRoster.sendTeamId;
  var userPicker = window._phase3PickerSlot;
  var oppPicker  = window._phase3OppPickerSlot;

  function fmtCell(p) {
    if (!p) return '<span style="color:#e05555;font-weight:700">(empty)</span>';
    var tag = p.is_pickup
      ? '<span style="color:#f0c040;font-size:.62rem;font-weight:700;margin-left:4px">PICKUP</span>'
      : p.is_il_activation
      ? '<span style="color:#6ab4ff;font-size:.62rem;font-weight:700;margin-left:4px">IL&#x2192;ACTIVE</span>'
      : '';
    var posTag = (p.elig && !p.is_pitcher) ? '<span style="color:#777;font-size:.58rem;font-weight:700;margin-left:3px">' + _eligLabel(p.elig) + '</span>' : '';
    return '<span style="color:#ddd">' + (p.name || '?') + '</span>' + posTag
         + '<span style="color:#888;margin-left:4px">$' + (p.dollars || 0).toFixed(1) + '</span>'
         + tag;
  }

  // Picker sub-row: list of top FA hitters. `side` is 'user' or 'opp' and
  // determines which add/close handlers get wired.
  function renderPicker(side, slotLabel, slotId) {
    var fas = _phase3FreeAgentHitters(50);
    // Filter to position-eligible players for the target slot
    if (slotId != null) {
      fas = fas.filter(function(p) { return _canFillSlot(p, slotId); });
    }
    fas = fas.slice(0, 15);
    var closeFn = (side === 'user') ? 'phase3ClosePickupMenu' : 'phase3CloseOppPickupMenu';
    var addFn   = (side === 'user') ? 'phase3AddPickup'       : 'phase3AddOppPickup';
    var headerTxt = (side === 'user' ? 'Pick up a FA hitter for ' : 'Opponent picks up a FA hitter for ') + slotLabel;
    var html = '<div style="background:#0f0f0f;border:1px solid #333;border-radius:6px;'
             + 'padding:7px 9px;margin:4px 0">'
             + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">'
             + '<span style="font-size:.68rem;color:var(--muted);font-weight:700;letter-spacing:.05em;text-transform:uppercase">'
             + headerTxt + '</span>'
             + '<button onmousedown="' + closeFn + '()" '
             + 'style="background:none;border:none;color:#888;cursor:pointer;font-size:.9rem;padding:0 4px">&#x2715;</button>'
             + '</div>';
    if (!fas.length) {
      html += '<div style="color:var(--muted);font-size:.75rem;padding:4px">No unrostered hitters available.</div>';
    } else {
      html += '<div style="display:flex;flex-wrap:wrap;gap:3px">';
      fas.forEach(function(p) {
        var dCol = p.dollars >= 10 ? '#f0c040' : p.dollars >= 0 ? '#7ec87e' : '#888';
        var pl = _posLabel(p);
        var posLbl = pl ? '<span style="color:#777;font-size:.56rem;font-weight:700">' + pl + '</span>' : '';
        html += '<button onmousedown="' + addFn + '(this.dataset.name)" '
             +  'data-name="' + (p.name || '').replace(/"/g,'&quot;') + '" '
             +  'style="background:#1a1a1a;border:1px solid #2a2a2a;color:#ddd;'
             +  'padding:4px 7px;border-radius:4px;cursor:pointer;font-size:.73rem;'
             +  'display:inline-flex;align-items:center;gap:4px">'
             +  '<span style="font-weight:600">' + p.name + '</span>' + posLbl
             +  '<span style="color:#777;font-size:.68rem">' + (p.team||'') + '</span>'
             +  '<span style="color:' + dCol + ';font-weight:700">$' + (p.dollars||0).toFixed(1) + '</span>'
             +  '</button>';
      });
      html += '</div>';
    }
    html += '</div>';
    return html;
  }

  // Roster picker — surfaces every eligible player on the team's post-trade
  // roster for a given slot INDEX, grouped so the user can see where each
  // candidate is currently coming from (other starters, bench, or IL).
  function renderRosterPicker(side, slotIdx, slotLabel, slotId, newTeam) {
    var closeFn = (side === 'user') ? 'phase3CloseUserRosterPicker'
                                    : 'phase3CloseOppRosterPicker';
    var pickFn  = (side === 'user') ? 'phase3PickUserRosterPlayer'
                                    : 'phase3PickOppRosterPlayer';
    var clearFn = (side === 'user') ? 'phase3ClearUserRosterSlot'
                                    : 'phase3ClearOppRosterSlot';
    var allHitters = (newTeam && newTeam.hitters) ? newTeam.hitters : [];
    var curAssigns = (newTeam && newTeam.__assigns) ? newTeam.__assigns : [];

    // Map every starter's espn_id to the slot LABEL they're currently in,
    // keyed by slot INDEX (so the three OF slots stay distinct).
    var starterAtIdx = {};
    for (var q = 0; q < curAssigns.length; q++) {
      var a = curAssigns[q];
      if (a.player) starterAtIdx[q] = a;
    }
    // Current occupant of *this* slot (by index — unique).
    var currentOccupant = (starterAtIdx[slotIdx] && starterAtIdx[slotIdx].player) || null;
    var currentEid = currentOccupant ? currentOccupant.espn_id : null;

    // Build an espn_id → {starter: true, slot_label: X} map so we can show
    // each candidate's current home in the chip.
    var otherStarterInfo = {};
    Object.keys(starterAtIdx).forEach(function(idxStr) {
      var idxNum = Number(idxStr);
      if (idxNum === slotIdx) return;   // skip THIS slot's occupant
      var a = starterAtIdx[idxStr];
      if (a && a.player) otherStarterInfo[a.player.espn_id] = a.slot_label;
    });

    // Split all eligible hitters into Other-Starters, Bench.
    var otherStarters = [];
    var bench = [];
    allHitters.forEach(function(p) {
      if (!_canFillSlot(p, slotId)) return;
      if (p.espn_id === currentEid) return;
      if (otherStarterInfo[p.espn_id]) otherStarters.push(p);
      else bench.push(p);
    });
    var sortByDollars = function(a, b) { return (b.dollars || 0) - (a.dollars || 0); };
    otherStarters.sort(sortByDollars);
    bench.sort(sortByDollars);

    // Shared chip styling — muted $ color, optional "(currently: X)" tag
    // so the user knows where each candidate is coming from.
    function chipBtn(p, extraStyle, currentSlotLbl) {
      var pl = _posLabel(p);
      var posLbl = pl ? '<span style="color:#888;font-size:.58rem;font-weight:700;margin-left:3px">' + pl + '</span>' : '';
      var locTag = currentSlotLbl
        ? '<span style="color:#f0c040;font-size:.55rem;font-weight:700;margin-left:3px">in ' + currentSlotLbl + '</span>'
        : '';
      return '<button onmousedown="' + pickFn + '(' + slotIdx + ',this.dataset.eid)" '
           +  'data-eid="' + p.espn_id + '" '
           +  'style="background:#1a1a1a;border:1px solid #2a2a2a;color:#ddd;'
           +  'padding:5px 9px;border-radius:4px;cursor:pointer;font-size:.76rem;'
           +  'display:inline-flex;align-items:center;gap:5px;' + (extraStyle || '') + '">'
           +  '<span style="font-weight:600">' + p.name + '</span>' + posLbl + locTag
           +  '<span style="color:#aaa;font-weight:700;font-variant-numeric:tabular-nums">$'
           +    (p.dollars || 0).toFixed(1) + '</span>'
           +  '</button>';
    }

    var html = '<div style="background:#0f0f0f;border:1px solid #333;border-radius:6px;'
             + 'padding:9px 11px;margin:4px 0">'
             + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
             + '<span style="font-size:.72rem;color:var(--muted);font-weight:700;letter-spacing:.05em;text-transform:uppercase">'
             + 'Set ' + slotLabel + '</span>'
             + '<button onmousedown="' + closeFn + '()" '
             + 'style="background:none;border:none;color:#888;cursor:pointer;font-size:.95rem;padding:0 4px">&#x2715;</button>'
             + '</div>';

    // Row 1: "Leave empty" + current occupant (keeps it as an option).
    html += '<div style="display:flex;flex-wrap:wrap;gap:5px;margin-bottom:6px">'
         +  '<button onmousedown="' + clearFn + '(' + slotIdx + ')" '
         +  'style="background:#2a1a1a;border:1px solid #6b2e2e;color:#e0a0a0;'
         +  'padding:5px 10px;border-radius:4px;cursor:pointer;font-size:.76rem;font-weight:700">'
         +  '(empty)</button>';
    if (currentOccupant) {
      html += chipBtn(currentOccupant, 'background:#1d2a1d;border-color:#3e6b3e;');
      html += '<span style="color:#888;font-size:.66rem;font-weight:600;align-self:center">'
            + '&nbsp;current</span>';
    }
    html += '</div>';

    // Row 2a: eligible starters currently in other slots. Picking one
    // moves them here and frees their old slot for the LAP to fill.
    if (otherStarters.length) {
      html += '<div style="font-size:.64rem;color:#888;font-weight:600;'
            + 'text-transform:uppercase;letter-spacing:.04em;margin:6px 0 3px">'
            + 'Other Starters <span style="color:#777;font-weight:500;text-transform:none;'
            + 'letter-spacing:0">(moving here frees their current slot)</span></div>'
            + '<div style="display:flex;flex-wrap:wrap;gap:5px">';
      otherStarters.forEach(function(p) {
        html += chipBtn(p, '', otherStarterInfo[p.espn_id]);
      });
      html += '</div>';
    }

    // Row 2b: eligible bench players.
    if (bench.length) {
      html += '<div style="font-size:.64rem;color:#888;font-weight:600;'
            + 'text-transform:uppercase;letter-spacing:.04em;margin:6px 0 3px">Bench</div>'
            + '<div style="display:flex;flex-wrap:wrap;gap:5px">';
      bench.forEach(function(p) { html += chipBtn(p); });
      html += '</div>';
    }
    if (!otherStarters.length && !bench.length) {
      html += '<div style="color:var(--muted);font-size:.76rem;padding:3px 0">'
            + 'No eligible hitters for this slot.</div>';
    }

    // Row 3: IL / inactive players on this team — lets the user pin an
    // injured player into the slot to project standings if they return.
    // The sim layer detects the IL pin and injects them into team.hitters.
    var ilPool = _phase3IlHittersFor(newTeam && newTeam.team_id, newTeam);
    // Filter out anyone currently being sent/dropped in the trade — those
    // players aren't IL, they're leaving the roster.
    var goneIds = {};
    if (side === 'user') {
      (_tradeRoster.send  || []).forEach(function(p) { if (p.espn_id != null) goneIds[p.espn_id] = true; });
      (_tradeRoster.drops || []).forEach(function(p) { if (p.espn_id != null) goneIds[p.espn_id] = true; });
    } else if (side === 'opp') {
      (_tradeRoster.recv     || []).forEach(function(p) { if (p.espn_id != null) goneIds[p.espn_id] = true; });
      (_tradeRoster.oppDrops || []).forEach(function(p) { if (p.espn_id != null) goneIds[p.espn_id] = true; });
    }
    var ilEligible = ilPool.filter(function(p) {
      return !goneIds[p.espn_id] && _canFillSlot(p, slotId);
    });
    ilEligible.sort(sortByDollars);
    if (ilEligible.length) {
      html += '<div style="font-size:.64rem;color:#6ab4ff;font-weight:600;'
            + 'text-transform:uppercase;letter-spacing:.04em;margin:8px 0 3px">'
            + 'IL / Inactive '
            + '<span style="color:#777;font-weight:500;text-transform:none;letter-spacing:0">'
            + '(activate to project if healthy)</span></div>'
            + '<div style="display:flex;flex-wrap:wrap;gap:5px">';
      ilEligible.forEach(function(p) {
        html += chipBtn(p, 'border-color:#2e4a6b;color:#cde1f4;background:#152334;');
      });
      html += '</div>';
    }

    html += '</div>';
    return html;
  }

  function hitterTable(label, oldSum, newSum, accent, side, newTeam) {
    // side: 'user' | 'opp' | null — non-null sides get "+ FA" buttons on empty slots
    // newTeam: the post-trade team object (hitters+team_id) used to power the
    //   manual roster-swap picker. null for the non-interactive case.
    var rows = '';
    var oldA = oldSum.assigns || [];
    var newA = newSum.assigns || [];
    // Stash assigns on newTeam so renderRosterPicker can surface "currently
    // in slot X" hints without another lookup pass.
    if (newTeam) newTeam.__assigns = newA;
    var n = Math.max(oldA.length, newA.length);
    var picker = (side === 'user') ? userPicker : (side === 'opp') ? oppPicker : null;
    var rosterPicker = (side === 'user') ? window._phase3RosterSlotUser
                      : (side === 'opp')  ? window._phase3RosterSlotOpp
                      : null;
    var openFn = (side === 'user') ? 'phase3OpenPickupMenu' : 'phase3OpenOppPickupMenu';
    var editFn = (side === 'user') ? 'phase3OpenUserRosterPicker'
                                    : 'phase3OpenOppRosterPicker';
    for (var i = 0; i < n; i++) {
      var oa = oldA[i] || {slot_label: '?', player: null};
      var na = newA[i] || {slot_label: '?', player: null};
      var oid = oa.player ? oa.player.espn_id : null;
      var nid = na.player ? na.player.espn_id : null;
      var changed = (oid !== nid);
      var bg = changed ? '#241a1a' : 'transparent';
      var slotCol = changed ? '#f0c040' : 'var(--muted)';
      var afterCell;
      var slotLbl = (na.slot_label || '').replace(/"/g, '&quot;');
      // Pass BOTH the slot index (unique, used for pinning) and slot_id
      // (used for eligibility filtering) to the picker handlers.
      // Wrap the string arg in HTML-entity &quot;s so the attribute stays
      // syntactically closed; the browser unescapes them before JS parses.
      var editArgs = i + ',' + na.slot_id + ',&quot;' + slotLbl + '&quot;';
      if (na.player) {
        // Filled slot — click the player name to swap via the roster picker
        if (side) {
          afterCell = '<span '
                    + 'onmousedown="' + editFn + '(' + editArgs + ')" '
                    + 'style="cursor:pointer;border-bottom:1px dotted #555" '
                    + 'title="Click to swap in a different player">'
                    + fmtCell(na.player) + '</span>';
        } else {
          afterCell = fmtCell(na.player);
        }
      } else if (side) {
        // Empty AFTER slot — both "+ FA" (pick up a free agent) and
        // "From roster" (move an existing rostered hitter into this slot).
        // NOTE: the FA pickup menu still keys by slot_id because the FA
        // pickup pipeline is separate from the slot-index-based roster pins.
        afterCell = '<span style="color:#e05555;font-weight:700">(empty)</span>'
                  + '&nbsp;&nbsp;<button '
                  + 'onmousedown="' + openFn + '(' + na.slot_id + ',&quot;' + slotLbl + '&quot;)" '
                  + 'style="background:#1e3a1e;border:1px solid #2e6b2e;color:#9cd39c;'
                  + 'cursor:pointer;font-size:.7rem;padding:1px 7px;border-radius:4px;font-weight:700">'
                  + '+ FA</button>'
                  + '&nbsp;<button '
                  + 'onmousedown="' + editFn + '(' + editArgs + ')" '
                  + 'style="background:#1a2a3a;border:1px solid #2e4a6b;color:#9cb8d9;'
                  + 'cursor:pointer;font-size:.7rem;padding:1px 7px;border-radius:4px;font-weight:700">'
                  + 'From roster</button>';
      } else {
        afterCell = fmtCell(na.player);
      }
      rows += '<tr style="background:' + bg + ';border-bottom:1px solid #1d1d1d">'
            +   '<td style="padding:4px 8px;color:' + slotCol + ';font-weight:700;width:46px">'
            +     (na.slot_label || oa.slot_label || '?') + '</td>'
            +   '<td style="padding:4px 8px">' + fmtCell(oa.player) + '</td>'
            +   '<td style="padding:4px 8px;color:#666;text-align:center;width:18px">→</td>'
            +   '<td style="padding:4px 8px">' + afterCell + '</td>'
            + '</tr>';
      // If the FA picker is open on this side/slot, render the inline FA list.
      if (side && picker && picker.slot_id === na.slot_id && !na.player) {
        rows += '<tr><td colspan="4" style="padding:0 8px 6px">'
              + renderPicker(side, picker.slot_label, picker.slot_id)
              + '</td></tr>';
      }
      // Roster picker: display under THIS row only — keyed by slot INDEX
      // so the three OF slots don't all light up when the user clicks one.
      if (side && rosterPicker && rosterPicker.slot_idx === i) {
        rows += '<tr><td colspan="4" style="padding:0 8px 6px">'
              + renderRosterPicker(side, rosterPicker.slot_idx, rosterPicker.slot_label,
                                   rosterPicker.slot_id, newTeam)
              + '</td></tr>';
      }
    }

    // Bench row — any hitter on the post-trade roster who isn't in a
    // starting slot. Display-only (they don't contribute to the projected
    // stats shown above, which are summed from `starters` only). Clicking
    // a starting slot lets the user pin one of these bench hitters into it.
    if (newTeam && newTeam.hitters && newTeam.hitters.length) {
      var starterIds = {};
      newA.forEach(function(a) { if (a.player) starterIds[a.player.espn_id] = true; });
      var benchList = newTeam.hitters.filter(function(p) { return !starterIds[p.espn_id]; });
      benchList.sort(function(a, b) { return (b.dollars || 0) - (a.dollars || 0); });
      if (benchList.length) {
        var benchHtml = benchList.map(function(p) {
          var pl = _posLabel(p);
          var posLbl = pl ? '<span style="color:#777;font-size:.56rem;font-weight:700;margin-left:3px">'
                          + pl + '</span>' : '';
          return '<span style="display:inline-flex;align-items:center;gap:4px;'
               + 'background:#181818;border:1px solid #262626;border-radius:4px;'
               + 'padding:2px 7px;margin:2px 3px 2px 0;font-size:.74rem">'
               + '<span style="color:#ddd;font-weight:600">' + p.name + '</span>' + posLbl
               + '<span style="color:#aaa;font-weight:700;font-variant-numeric:tabular-nums">$'
               +   (p.dollars || 0).toFixed(1) + '</span>'
               + '</span>';
        }).join('');
        rows += '<tr><td colspan="4" style="padding:8px 8px 4px;border-top:1px solid #262626">'
              + '<div style="font-size:.62rem;color:#888;font-weight:700;'
              + 'text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">'
              + 'Bench <span style="color:#555;font-weight:500;text-transform:none;'
              + 'letter-spacing:0;margin-left:4px">(not counted in projections)</span></div>'
              + '<div>' + benchHtml + '</div>'
              + '</td></tr>';
      }
    }

    var oldD = (oldSum.hDollar || 0).toFixed(1);
    var newD = (newSum.hDollar || 0).toFixed(1);
    var dDelta = (newSum.hDollar || 0) - (oldSum.hDollar || 0);
    var dCol = dDelta > 0.05 ? '#4caf50' : (dDelta < -0.05 ? '#e05555' : '#888');
    var dSign = dDelta > 0 ? '+' : (dDelta < 0 ? '−' : '');

    // Header: "optimized hitter lineup" + (Reset) button when the team has
    // a manual override active.
    var hasCustom = !!(newTeam && window._phase3CustomLineup[newTeam.team_id]
                                && Object.keys(window._phase3CustomLineup[newTeam.team_id]).length);
    var resetBtn = '';
    if (hasCustom && side) {
      var resetFn = (side === 'user') ? 'phase3ResetUserLineup' : 'phase3ResetOppLineup';
      resetBtn = '&nbsp;<button onmousedown="' + resetFn + '()" '
               + 'style="background:#2a1a1a;border:1px solid #6b2e2e;color:#e0a0a0;'
               + 'cursor:pointer;font-size:.6rem;padding:1px 6px;border-radius:4px;'
               + 'font-weight:700;letter-spacing:.04em">&#x21BA; RESET</button>';
    }
    var customNote = hasCustom
      ? '<span style="color:#f0c040;font-size:.6rem;font-weight:700;'
        + 'letter-spacing:.04em;margin-left:6px">&#x270E; MANUAL</span>'
      : '';

    return '<div style="font-size:.7rem;color:var(--muted);font-weight:700;'
         +   'text-transform:uppercase;letter-spacing:.05em;margin:0 0 6px">'
         +   label + ' &nbsp;—&nbsp; hitter lineup'
         +   customNote + resetBtn + '</div>'
         + '<table style="width:100%;border-collapse:collapse;font-size:.78rem">'
         +   '<thead><tr style="border-bottom:1px solid #333">'
         +     '<th style="text-align:left;padding:4px 8px;color:var(--muted)">Slot</th>'
         +     '<th style="text-align:left;padding:4px 8px;color:var(--muted)">Before</th>'
         +     '<th></th>'
         +     '<th style="text-align:left;padding:4px 8px;color:var(--muted)">After</th>'
         +   '</tr></thead><tbody>'
         + rows
         + '<tr style="border-top:1px solid #333">'
         +   '<td style="padding:6px 8px;color:var(--muted);font-weight:700">Hit&nbsp;$</td>'
         +   '<td style="padding:6px 8px;color:#bbb;font-weight:700">$' + oldD + '</td>'
         +   '<td style="padding:6px 8px;color:#666;text-align:center">→</td>'
         +   '<td style="padding:6px 8px;color:#bbb;font-weight:700">$' + newD
         +     '&nbsp;&nbsp;<span style="color:' + dCol + '">' + dSign
         +     '$' + Math.abs(dDelta).toFixed(1) + '</span></td>'
         + '</tr>'
         + '</tbody></table>';
  }

  // Picker sub-row for pitcher FA pickups — top 10 SPs + top 3 RPs.
  // `side` is 'user' or 'opp'.
  function renderPitcherPicker(side) {
    var fas = _phase3FreeAgentPitchers();
    var closeFn = (side === 'user') ? 'phase3ClosePitcherPicker' : 'phase3CloseOppPitcherPicker';
    var addFn   = (side === 'user') ? 'phase3AddPitcherPickup'   : 'phase3AddOppPitcherPickup';
    var headerTxt = (side === 'user' ? 'Pick up a FA pitcher' : 'Opponent picks up a FA pitcher')
                  + ' (top 10 SP &bull; top 3 RP)';
    var html = '<div style="background:#0f0f0f;border:1px solid #333;border-radius:6px;'
             + 'padding:7px 9px;margin:4px 0">'
             + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">'
             + '<span style="font-size:.68rem;color:var(--muted);font-weight:700;letter-spacing:.05em;text-transform:uppercase">'
             + headerTxt + '</span>'
             + '<button onmousedown="' + closeFn + '()" '
             + 'style="background:none;border:none;color:#888;cursor:pointer;font-size:.9rem;padding:0 4px">&#x2715;</button>'
             + '</div>';
    if (!fas.length) {
      html += '<div style="color:var(--muted);font-size:.75rem;padding:4px">No unrostered pitchers available.</div>';
    } else {
      html += '<div style="display:flex;flex-wrap:wrap;gap:3px">';
      fas.forEach(function(p) {
        var dCol = p.dollars >= 10 ? '#f0c040' : p.dollars >= 0 ? '#7ec87e' : '#888';
        var roleTag = '<span style="color:#777;font-size:.66rem;margin-left:2px">'
                    + (p.role || 'sp').toUpperCase() + '</span>';
        html += '<button onmousedown="' + addFn + '(this.dataset.name)" '
             +  'data-name="' + (p.name || '').replace(/"/g,'&quot;') + '" '
             +  'style="background:#1a1a1a;border:1px solid #2a2a2a;color:#ddd;'
             +  'padding:4px 7px;border-radius:4px;cursor:pointer;font-size:.73rem;'
             +  'display:inline-flex;align-items:center;gap:4px">'
             +  '<span style="font-weight:600">' + p.name + '</span>'
             +  '<span style="color:#777;font-size:.68rem">' + (p.team||'') + '</span>'
             +  roleTag
             +  '<span style="color:' + dCol + ';font-weight:700">$' + (p.dollars||0).toFixed(1) + '</span>'
             +  '</button>';
      });
      html += '</div>';
    }
    html += '</div>';
    return html;
  }

  // Pitcher staff: no slots, so show a union of before/after sorted by $.
  // Added pitchers tinted green, removed tinted red, kept neutral.
  // Activated IL pitchers get a blue tint + inline Deactivate button.
  // `side` ('user'|'opp'|null): non-null sides render "+ FA" buttons on any
  // open pitcher spots created by the trade.
  function pitcherTable(oldSum, newSum, side, newTeam) {
    var oldP = oldSum.pitchers || [];
    var newP = newSum.pitchers || [];
    var oldIds = {}; oldP.forEach(function(p) { oldIds[p.espn_id] = p; });
    var newIds = {}; newP.forEach(function(p) { newIds[p.espn_id] = p; });
    // Union, preserving order: new first (so kept pitchers stay in the
    // dollar-sorted order you'll have after the trade), then any removed.
    var union = newP.slice();
    oldP.forEach(function(p) { if (!newIds[p.espn_id]) union.push(p); });
    // Open pitcher spots = how many more pitchers the side had before the
    // trade than after (post-pickup). newSum already includes active pitcher
    // pickups from _phase3SimulateTrade, so this auto-calibrates.
    var openSlots = side ? Math.max(0, oldP.length - newP.length) : 0;
    var pickerActive = (side === 'user') ? !!window._phase3PitcherPicker
                      : (side === 'opp')  ? !!window._phase3OppPitcherPicker
                      : false;
    var openFn = (side === 'user') ? 'phase3OpenPitcherPicker' : 'phase3OpenOppPitcherPicker';
    var deactFn  = (side === 'user') ? 'phase3DeactivateUserIlPitcher'
                  : (side === 'opp')  ? 'phase3DeactivateOppIlPitcher' : null;
    var startFn  = (side === 'user') ? 'phase3StartActivateUserIlPitcher'
                  : (side === 'opp')  ? 'phase3StartActivateOppIlPitcher'  : null;
    var confirmFn = (side === 'user') ? 'phase3ConfirmActivateUserIlPitcher'
                  : (side === 'opp')  ? 'phase3ConfirmActivateOppIlPitcher' : null;
    var swapPick = window._phase3IlPitcherSwapPicker;
    var swapActive = (swapPick && swapPick.side === side);
    var rows = '';
    union.forEach(function(p) {
      var inNew = !!newIds[p.espn_id];
      var inOld = !!oldIds[p.espn_id];
      var isIl  = !!p.is_il_activation;
      var bg, beforeStr, afterStr;
      if (isIl && inNew) {
        // Activated IL pitcher — wasn't in the baseline, surfaced now.
        bg = '#152334';
        beforeStr = '<span style="color:#666">IL</span>';
        afterStr  = fmtCell(p);
        if (deactFn) {
          afterStr += '&nbsp;<button data-eid="' + p.espn_id + '" '
                   + 'onclick="' + deactFn + '(this.dataset.eid)" '
                   + 'style="background:#2a1a1a;border:1px solid #6b2e2e;color:#e0a0a0;'
                   + 'cursor:pointer;font-size:.62rem;padding:1px 7px;border-radius:4px;'
                   + 'font-weight:700;letter-spacing:.04em">&#x2715; DEACTIVATE</button>';
        }
      } else if (inNew && inOld) {
        bg = 'transparent';
        beforeStr = fmtCell(p); afterStr = fmtCell(p);
      } else if (inNew && !inOld) {
        bg = '#1a2a1a';
        beforeStr = '<span style="color:#666">—</span>'; afterStr = fmtCell(p);
      } else {
        bg = '#2a1a1a';
        beforeStr = fmtCell(p); afterStr = '<span style="color:#666">—</span>';
      }
      var role = (p.role ? (String(p.role).toLowerCase() === 'sp' ? 'SP' : 'RP')
                          : ((p.IP || 0) >= 100 ? 'SP' : 'RP'));
      rows += '<tr style="background:' + bg + ';border-bottom:1px solid #1d1d1d">'
            +   '<td style="padding:4px 8px;color:var(--muted);font-weight:700;width:46px">'
            +     role + '</td>'
            +   '<td style="padding:4px 8px">' + beforeStr + '</td>'
            +   '<td style="padding:4px 8px;color:#666;text-align:center;width:18px">→</td>'
            +   '<td style="padding:4px 8px">' + afterStr + '</td>'
            + '</tr>';
    });
    // Render one "(empty)" row per open pitcher spot with a "+ FA" button.
    for (var os = 0; os < openSlots; os++) {
      var emptyCell = '<span style="color:#e05555;font-weight:700">(empty)</span>'
                    + '&nbsp;&nbsp;<button '
                    + 'onmousedown="' + openFn + '()" '
                    + 'style="background:#1e3a1e;border:1px solid #2e6b2e;color:#9cd39c;'
                    + 'cursor:pointer;font-size:.7rem;padding:1px 7px;border-radius:4px;font-weight:700">'
                    + '+ FA</button>';
      rows += '<tr style="background:#241a1a;border-bottom:1px solid #1d1d1d">'
            +   '<td style="padding:4px 8px;color:#f0c040;font-weight:700;width:46px">P</td>'
            +   '<td style="padding:4px 8px"><span style="color:#666">—</span></td>'
            +   '<td style="padding:4px 8px;color:#666;text-align:center;width:18px">→</td>'
            +   '<td style="padding:4px 8px">' + emptyCell + '</td>'
            + '</tr>';
      if (os === 0 && pickerActive) {
        rows += '<tr><td colspan="4" style="padding:0 8px 6px">'
              + renderPitcherPicker(side)
              + '</td></tr>';
      }
    }

    // ── IL / Inactive Pitchers section ─────────────────────────────────
    // Activation is a swap: user clicks Activate, then picks which current
    // starter to take out. Team-owned IL pitchers whose espn_id isn't
    // already acting as the "in" side of an active pair are listed here.
    var ilRows = '';
    if (side && startFn && newTeam && newTeam.team_id != null) {
      var activePairs = window._phase3IlPitcherActivations[newTeam.team_id] || {};
      var pairedInIds = {};
      Object.keys(activePairs).forEach(function(k) { pairedInIds[k] = true; pairedInIds[String(k)] = true; });
      var ilPitchers = _phase3IlPitchersFor(newTeam.team_id)
        .filter(function(p) {
          return !pairedInIds[p.espn_id] && !pairedInIds[String(p.espn_id)];
        });
      ilPitchers.sort(function(a,b) { return (b.dollars||0) - (a.dollars||0); });
      if (ilPitchers.length) {
        var chips = ilPitchers.map(function(p) {
          var rl = (p.role && String(p.role).toLowerCase() === 'sp') ? 'SP' : 'RP';
          return '<button data-eid="' + p.espn_id + '" '
               + 'onclick="' + startFn + '(this.dataset.eid)" '
               + 'style="background:#152334;border:1px solid #2e4a6b;color:#cde1f4;'
               + 'padding:5px 9px;margin:2px 3px 2px 0;border-radius:4px;cursor:pointer;'
               + 'font-size:.74rem;display:inline-flex;align-items:center;gap:5px">'
               + '<span style="font-weight:600">' + p.name + '</span>'
               + '<span style="color:#888;font-size:.58rem;font-weight:700">' + rl + '</span>'
               + '<span style="color:#aaa;font-weight:700;font-variant-numeric:tabular-nums">$'
               + (p.dollars || 0).toFixed(1) + '</span>'
               + '<span style="color:#7ec87e;font-size:.6rem;font-weight:700;'
               + 'letter-spacing:.04em">+ ACTIVATE</span></button>';
        }).join('');
        ilRows = '<tr><td colspan="4" style="padding:8px 8px 4px;border-top:1px solid #262626">'
               + '<div style="font-size:.62rem;color:#6ab4ff;font-weight:700;'
               + 'text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">'
               + 'IL / Inactive Pitchers '
               + '<span style="color:#555;font-weight:500;text-transform:none;letter-spacing:0;'
               + 'margin-left:4px">(activate to swap in for a current starter)</span></div>'
               + '<div>' + chips + '</div>'
               + '</td></tr>';
      }

      // If the user just clicked Activate on an IL pitcher, show a sub-row
      // where they pick which current starter to swap OUT. The incoming IL
      // pitcher is named in the header; each current (non-activated) roster
      // pitcher becomes a clickable chip that finalizes the pair.
      if (swapActive && confirmFn) {
        var inEid = swapPick.in_espn_id;
        var ilLookup = _phase3IlPitchersFor(newTeam.team_id);
        var inRec = null;
        for (var ii = 0; ii < ilLookup.length; ii++) {
          if (String(ilLookup[ii].espn_id) === String(inEid)) { inRec = ilLookup[ii]; break; }
        }
        var inName = inRec ? inRec.name : '(IL pitcher)';
        // Current roster pitchers minus any that are already "in" via swaps.
        var alreadyIn = {};
        Object.keys(activePairs).forEach(function(k) { alreadyIn[k] = true; alreadyIn[String(k)] = true; });
        var candidates = (newTeam.pitchers || []).filter(function(p) {
          return !p.is_il_activation
              && !alreadyIn[p.espn_id] && !alreadyIn[String(p.espn_id)];
        });
        candidates.sort(function(a,b) { return (a.dollars||0) - (b.dollars||0); });
        var swapChips = candidates.map(function(p) {
          var rl = (p.role && String(p.role).toLowerCase() === 'sp') ? 'SP'
                  : ((p.IP || 0) >= 100 ? 'SP' : 'RP');
          return '<button data-eid="' + p.espn_id + '" '
               + 'onclick="' + confirmFn + '(this.dataset.eid)" '
               + 'style="background:#1a1a1a;border:1px solid #2a2a2a;color:#ddd;'
               + 'padding:5px 9px;margin:2px 3px 2px 0;border-radius:4px;cursor:pointer;'
               + 'font-size:.74rem;display:inline-flex;align-items:center;gap:5px">'
               + '<span style="font-weight:600">' + p.name + '</span>'
               + '<span style="color:#888;font-size:.58rem;font-weight:700">' + rl + '</span>'
               + '<span style="color:#aaa;font-weight:700;font-variant-numeric:tabular-nums">$'
               + (p.dollars || 0).toFixed(1) + '</span></button>';
        }).join('');
        var swapHtml = '<div style="background:#0f0f0f;border:1px solid #6ab4ff;border-radius:6px;'
                     + 'padding:9px 11px;margin:4px 0">'
                     + '<div style="display:flex;justify-content:space-between;'
                     + 'align-items:center;margin-bottom:6px">'
                     + '<span style="font-size:.72rem;color:#cde1f4;font-weight:700;'
                     + 'letter-spacing:.05em;text-transform:uppercase">'
                     + 'Activate ' + inName + ' &mdash; who do they replace?</span>'
                     + '<button onclick="phase3CancelActivateIlPitcher()" '
                     + 'style="background:none;border:none;color:#888;cursor:pointer;'
                     + 'font-size:.95rem;padding:0 4px">&#x2715;</button></div>'
                     + (swapChips.length
                        ? '<div>' + swapChips + '</div>'
                        : '<div style="color:var(--muted);font-size:.76rem">'
                          + 'No pitchers available to swap out.</div>')
                     + '</div>';
        ilRows += '<tr><td colspan="4" style="padding:0 8px 6px">' + swapHtml + '</td></tr>';
      }
    }
    rows += ilRows;
    if (!union.length && !openSlots && !ilRows) {
      return '<div style="color:var(--muted);font-size:.75rem;padding:4px">No pitchers.</div>';
    }
    var oldD = (oldSum.pDollar || 0).toFixed(1);
    var newD = (newSum.pDollar || 0).toFixed(1);
    var dDelta = (newSum.pDollar || 0) - (oldSum.pDollar || 0);
    var dCol = dDelta > 0.05 ? '#4caf50' : (dDelta < -0.05 ? '#e05555' : '#888');
    var dSign = dDelta > 0 ? '+' : (dDelta < 0 ? '−' : '');
    return '<div style="font-size:.7rem;color:var(--muted);font-weight:700;'
         +   'text-transform:uppercase;letter-spacing:.05em;margin:12px 0 6px">'
         +   'Pitching staff</div>'
         + '<table style="width:100%;border-collapse:collapse;font-size:.78rem">'
         +   '<thead><tr style="border-bottom:1px solid #333">'
         +     '<th style="text-align:left;padding:4px 8px;color:var(--muted)">Role</th>'
         +     '<th style="text-align:left;padding:4px 8px;color:var(--muted)">Before</th>'
         +     '<th></th>'
         +     '<th style="text-align:left;padding:4px 8px;color:var(--muted)">After</th>'
         +   '</tr></thead><tbody>'
         + rows
         + '<tr style="border-top:1px solid #333">'
         +   '<td style="padding:6px 8px;color:var(--muted);font-weight:700">Pit&nbsp;$</td>'
         +   '<td style="padding:6px 8px;color:#bbb;font-weight:700">$' + oldD + '</td>'
         +   '<td style="padding:6px 8px;color:#666;text-align:center">→</td>'
         +   '<td style="padding:6px 8px;color:#bbb;font-weight:700">$' + newD
         +     '&nbsp;&nbsp;<span style="color:' + dCol + '">' + dSign
         +     '$' + Math.abs(dDelta).toFixed(1) + '</span></td>'
         + '</tr>'
         + '</tbody></table>';
  }

  function teamLineupCard(label, oldSum, newSum, accent, side, newTeam) {
    return '<div style="flex:1;min-width:340px;background:#141414;border-radius:8px;'
         + 'padding:10px 12px;border-left:3px solid ' + accent + '">'
         +   hitterTable(label, oldSum, newSum, accent, side, newTeam)
         +   pitcherTable(oldSum, newSum, side, newTeam)
         + '</div>';
  }

  // ── Waiver-adds banner ─────────────────────────────────────────────
  // One chip row per side. Each chip shows position/role, name, $,
  // and a × button that removes that pickup and re-sims.
  function _slotLabelFor(slotId) {
    var slots = PHASE3_LEAGUE ? PHASE3_LEAGUE.slots : [];
    for (var i = 0; i < slots.length; i++) {
      if (slots[i][0] === slotId) return slots[i][1];
    }
    return '?';
  }
  function _hitterChip(pk, rmFn) {
    var slotLabel = _slotLabelFor(pk.pickup_slot);
    return '<span style="display:inline-flex;align-items:center;gap:5px;'
         + 'background:#1e2a1e;border:1px solid #2e6b2e;border-radius:12px;'
         + 'padding:3px 9px;margin-right:5px;font-size:.73rem">'
         + '<span style="color:#f0c040;font-weight:700">+' + slotLabel + '</span>'
         + '<span style="color:#ddd">' + pk.name + '</span>'
         + '<span style="color:#888">$' + (pk.dollars||0).toFixed(1) + '</span>'
         + '<button onmousedown="' + rmFn + '(this.dataset.name)" '
         + 'data-name="' + (pk.name||'').replace(/"/g,'&quot;') + '" '
         + 'style="background:none;border:none;color:#888;cursor:pointer;font-size:.85rem;padding:0 2px">&#x2715;</button>'
         + '</span>';
  }
  function _pitcherChip(pk, rmFn) {
    var roleLbl = (pk.role || 'sp').toUpperCase();
    return '<span style="display:inline-flex;align-items:center;gap:5px;'
         + 'background:#1e2a1e;border:1px solid #2e6b2e;border-radius:12px;'
         + 'padding:3px 9px;margin-right:5px;font-size:.73rem">'
         + '<span style="color:#f0c040;font-weight:700">+' + roleLbl + '</span>'
         + '<span style="color:#ddd">' + pk.name + '</span>'
         + '<span style="color:#888">$' + (pk.dollars||0).toFixed(1) + '</span>'
         + '<button onmousedown="' + rmFn + '(this.dataset.name)" '
         + 'data-name="' + (pk.name||'').replace(/"/g,'&quot;') + '" '
         + 'style="background:none;border:none;color:#888;cursor:pointer;font-size:.85rem;padding:0 2px">&#x2715;</button>'
         + '</span>';
  }
  function _dropChip(d, rmFn) {
    var roleLbl = d.is_pitcher ? (((d.IP || 0) >= 100) ? 'SP' : 'P') : 'H';
    return '<span style="display:inline-flex;align-items:center;gap:5px;'
         + 'background:#2a1a1a;border:1px solid #6b2e2e;border-radius:12px;'
         + 'padding:3px 9px;margin-right:5px;font-size:.73rem">'
         + '<span style="color:#e05555;font-weight:700">−' + roleLbl + '</span>'
         + '<span style="color:#ddd">' + d.name + '</span>'
         + '<span style="color:#888">$' + (d.dollars||0).toFixed(1) + '</span>'
         + '<button onmousedown="' + rmFn + '(this.dataset.eid)" '
         + 'data-eid="' + d.espn_id + '" '
         + 'style="background:none;border:none;color:#888;cursor:pointer;font-size:.85rem;padding:0 2px">&#x2715;</button>'
         + '</span>';
  }

  function _buildAddsBanner(label, hPks, pPks, rmHit, rmPit) {
    var chips = '';
    if (hPks && hPks.length) chips += hPks.map(function(pk) { return _hitterChip(pk, rmHit); }).join('');
    if (pPks && pPks.length) chips += pPks.map(function(pk) { return _pitcherChip(pk, rmPit); }).join('');
    if (!chips) return '';
    return '<div style="margin-bottom:6px;padding:6px 9px;'
         + 'background:#0f1a0f;border-radius:6px;border-left:3px solid #4caf50">'
         + '<span style="font-size:.66rem;color:var(--muted);font-weight:700;'
         + 'text-transform:uppercase;letter-spacing:.05em;margin-right:6px">' + label + '</span>'
         + chips + '</div>';
  }

  var bannerHtml = '';
  bannerHtml += _buildAddsBanner('Your waiver adds',
                                  _tradeRoster.pickups, _tradeRoster.pitcherPickups,
                                  'phase3RemovePickup', 'phase3RemovePitcherPickup');
  bannerHtml += _buildAddsBanner('Counterparty waiver adds',
                                  _tradeRoster.oppPickups, _tradeRoster.oppPitcherPickups,
                                  'phase3RemoveOppPickup', 'phase3RemoveOppPitcherPickup');

  // ── Drop banner ────────────────────────────────────────────────────
  // Shown whenever this side is "receiving" more players than sending
  // (accounting for any pickups). Lets the user pick any rostered player
  // — hitter or pitcher — to drop. The button is always rendered even
  // when drops are zero, as long as there's a roster imbalance, so the
  // user can still choose to preemptively drop someone.
  function _buildDropBanner(side) {
    // side: 'user' | 'opp'
    var needed = _phase3DropsNeeded(side);
    var drops  = (side === 'user' ? _tradeRoster.drops : _tradeRoster.oppDrops) || [];
    if (!needed && !drops.length) return '';
    var rmFn   = (side === 'user') ? 'phase3RemoveDrop'   : 'phase3RemoveOppDrop';
    var openFn = (side === 'user') ? 'phase3OpenDropPicker' : 'phase3OpenOppDropPicker';
    var label  = (side === 'user') ? 'Your drops'          : 'Counterparty drops';
    var remaining = needed - drops.length;
    var status;
    if (remaining > 0) {
      status = '<span style="color:#e05555;font-weight:700">'
             + remaining + ' drop' + (remaining === 1 ? '' : 's')
             + ' required</span>';
    } else if (remaining < 0) {
      status = '<span style="color:#e05555;font-weight:700">over-dropped by '
             + Math.abs(remaining) + '</span>';
    } else {
      status = '<span style="color:#7ec87e;font-weight:700">balanced</span>';
    }
    var chips = drops.map(function(d) { return _dropChip(d, rmFn); }).join('');
    var addBtn = '<button onmousedown="' + openFn + '()" '
               + 'style="background:#2a1a1a;border:1px solid #6b2e2e;color:#e08484;'
               + 'cursor:pointer;font-size:.72rem;padding:3px 9px;border-radius:12px;'
               + 'font-weight:700;margin-left:4px">+ pick drop</button>';
    return '<div style="margin-bottom:6px;padding:6px 9px;'
         + 'background:#1a0f0f;border-radius:6px;border-left:3px solid #e05555">'
         + '<span style="font-size:.66rem;color:var(--muted);font-weight:700;'
         + 'text-transform:uppercase;letter-spacing:.05em;margin-right:6px">' + label + '</span>'
         + status + '&nbsp;&nbsp;' + chips + addBtn + '</div>';
  }
  bannerHtml += _buildDropBanner('user');
  bannerHtml += _buildDropBanner('opp');

  // ── Drop picker sub-view ───────────────────────────────────────────
  // Renders the full list of that side's rostered players (hitters +
  // pitchers) sorted by $ descending. Players being sent in the trade
  // and already-dropped players are excluded.
  function _renderDropPicker(side) {
    if (!PHASE3_LEAGUE) return '';
    var tid = (side === 'user') ? _tradeRoster.sendTeamId : _tradeRoster.recvTeamId;
    if (tid == null) return '';
    var tm = PHASE3_LEAGUE.teams.find(function(t) { return t.team_id === tid; });
    if (!tm) return '';
    var excluded = {};
    var sendList = (side === 'user') ? _tradeRoster.send : _tradeRoster.recv;
    sendList.forEach(function(p) { if (p.espn_id != null) excluded[p.espn_id] = true; });
    var dropList = (side === 'user' ? _tradeRoster.drops : _tradeRoster.oppDrops) || [];
    dropList.forEach(function(d) { if (d.espn_id != null) excluded[d.espn_id] = true; });
    var pool = [];
    (tm.hitters  || []).forEach(function(p) {
      if (!excluded[p.espn_id]) pool.push({rec: p, is_pitcher: false});
    });
    (tm.pitchers || []).forEach(function(p) {
      if (!excluded[p.espn_id]) pool.push({rec: p, is_pitcher: true});
    });
    pool.sort(function(a, b) {
      return (a.rec.dollars || 0) - (b.rec.dollars || 0);   // lowest $ first (likely drop)
    });
    var closeFn = (side === 'user') ? 'phase3CloseDropPicker' : 'phase3CloseOppDropPicker';
    var addFn   = (side === 'user') ? 'phase3AddDrop'         : 'phase3AddOppDrop';
    var heading = (side === 'user') ? 'Drop a player from your roster'
                                     : 'Drop a player from the counterparty roster';
    var html = '<div style="margin-bottom:8px;background:#0f0f0f;border:1px solid #333;'
             + 'border-radius:6px;padding:7px 9px">'
             + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">'
             + '<span style="font-size:.68rem;color:var(--muted);font-weight:700;letter-spacing:.05em;text-transform:uppercase">'
             + heading + ' (sorted by $ asc)</span>'
             + '<button onmousedown="' + closeFn + '()" '
             + 'style="background:none;border:none;color:#888;cursor:pointer;font-size:.9rem;padding:0 4px">&#x2715;</button>'
             + '</div>';
    if (!pool.length) {
      html += '<div style="color:var(--muted);font-size:.75rem;padding:4px">No droppable players.</div>';
    } else {
      html += '<div style="display:flex;flex-wrap:wrap;gap:3px;max-height:220px;overflow-y:auto">';
      pool.forEach(function(item) {
        var p = item.rec;
        var dCol = p.dollars >= 10 ? '#f0c040' : p.dollars >= 0 ? '#7ec87e' : '#888';
        var roleTag = item.is_pitcher
          ? '<span style="color:#e08484;font-size:.66rem;margin-left:2px">'
            + (((p.IP || 0) >= 100) ? 'SP' : 'P') + '</span>'
          : '';
        var posLbl = (!item.is_pitcher && p.elig) ? '<span style="color:#777;font-size:.56rem;font-weight:700;margin-left:2px">' + _eligLabel(p.elig) + '</span>' : '';
        html += '<button onmousedown="' + addFn + '(this.dataset.eid)" '
             +  'data-eid="' + p.espn_id + '" '
             +  'style="background:#1a1a1a;border:1px solid #2a2a2a;color:#ddd;'
             +  'padding:4px 7px;border-radius:4px;cursor:pointer;font-size:.73rem;'
             +  'display:inline-flex;align-items:center;gap:4px">'
             +  '<span style="font-weight:600">' + (p.name || '?') + '</span>' + posLbl
             +  '<span style="color:#777;font-size:.68rem">' + (p.team || '') + '</span>'
             +  roleTag
             +  '<span style="color:' + dCol + ';font-weight:700">$' + (p.dollars||0).toFixed(1) + '</span>'
             +  '</button>';
      });
      html += '</div>';
    }
    html += '</div>';
    return html;
  }
  if (window._phase3DropPicker)    bannerHtml += _renderDropPicker('user');
  if (window._phase3OppDropPicker) bannerHtml += _renderDropPicker('opp');

  lw.innerHTML = bannerHtml
               + '<div style="display:flex;gap:12px;flex-wrap:wrap">'
               + teamLineupCard('You',          L.sumOldUser, L.sumNewUser, '#4caf50', 'user', L.newUser)
               + teamLineupCard('Counterparty', L.sumOldOpp,  L.sumNewOpp,  '#e05555', 'opp',  L.newOpp)
               + '</div>';
}

function phase3ToggleTable() {
  var tw  = document.getElementById('phase3-table-wrap');
  var btn = document.getElementById('phase3-toggle-btn');
  if (!tw) return;
  var open = tw.style.display !== 'none';
  if (open) {
    tw.style.display = 'none';
    btn.innerHTML = '▼ Show full updated standings';
  } else {
    var newLeague = _phase3SimulateTrade();
    if (!newLeague) return;
    _phase3RenderTable(newLeague);
    tw.style.display = '';
    btn.innerHTML = '▲ Hide full updated standings';
  }
}

function _phase3RenderTable(newLeague) {
  var tw = document.getElementById('phase3-table-wrap');
  if (!tw) return;
  var n = newLeague.length;
  function findOld(tid) { return PHASE3_LEAGUE.teams.find(function(t) { return t.team_id === tid; }); }
  var sorted = newLeague.slice().sort(function(a,b) { return a.rank_total - b.rank_total; });
  var lower = {};
  PHASE3_LEAGUE.lower_better.forEach(function(c) { lower[c] = true; });

  /* Category display labels */
  var hCats = PHASE3_LEAGUE.hit_cats || [];
  var pCats = PHASE3_LEAGUE.pit_cats || [];
  var allCats = hCats.concat(pCats);
  var catLabel = {'R':'R','HR':'HR','RBI':'RBI','SO_h':'K','SB':'SB','OBP':'OBP',
                  'W':'W','SO_p':'K','SV':'SV','HLD':'HLD','ERA':'ERA','WHIP':'WHIP'};
  var thSt = 'padding:5px 8px;text-align:center;color:var(--muted);font-size:.78rem;white-space:nowrap';

  /* Header row */
  var html = '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:.85rem">'
           + '<thead><tr style="border-bottom:1px solid #333">'
           + '<th style="text-align:left;padding:6px 10px;color:var(--muted)">#</th>'
           + '<th style="text-align:left;padding:6px 10px;color:var(--muted)">Team</th>'
           + '<th style="text-align:center;padding:6px 10px;color:var(--muted)">Z Tot</th>'
           + '<th style="text-align:center;padding:6px 10px;color:var(--muted)">ΔZ</th>'
           + '<th style="text-align:center;padding:6px 10px;color:var(--muted)">ΔRk</th>'
           + '<th style="border-left:2px solid #444;' + thSt + '">H Z</th>';
  hCats.forEach(function(c) { html += '<th style="' + thSt + '">' + (catLabel[c]||c) + '</th>'; });
  html += '<th style="border-left:2px solid #444;' + thSt + '">P Z</th>';
  pCats.forEach(function(c) { html += '<th style="' + thSt + '">' + (catLabel[c]||c) + '</th>'; });
  html += '</tr></thead><tbody>';

  /* Compute per-cat ranks for color-coding */
  var allCats = hCats.concat(pCats);
  var catRanks = {};
  allCats.forEach(function(cat) {
    var isLow = lower[cat];
    var pairs = newLeague.map(function(t) { return {tid: t.team_id, v: t.stats[cat]||0}; });
    pairs.sort(function(a,b) { return isLow ? a.v - b.v : b.v - a.v; });
    var rk = {};
    pairs.forEach(function(p, i) { rk[p.tid] = i + 1; });
    catRanks[cat] = rk;
  });

  sorted.forEach(function(t) {
    var old = findOld(t.team_id);
    var rkC = _phase3RankColor(t.rank_total, n);
    var zd  = t.z_total - old.z_total;
    var rd  = old.rank_total - t.rank_total;
    var zCol = zd > 0.005 ? '#4caf50' : zd < -0.005 ? '#e05555' : '#888';
    var rCol = rd > 0     ? '#4caf50' : rd < 0      ? '#e05555' : '#888';
    var zStr = (zd >= 0 ? '+' : '−') + Math.abs(zd).toFixed(2);
    var rStr = rd === 0 ? '■ 0'
             : (rd > 0 ? '▲ ' : '▼ ') + Math.abs(rd);
    var isUser = t.team_id === _tradeRoster.sendTeamId;
    var isOpp  = t.team_id === _tradeRoster.recvTeamId;
    var nameAccent = isUser ? '#4caf50' : isOpp ? '#e05555' : '#ddd';
    var bg = (isUser || isOpp) ? '#1a1a1a' : 'transparent';

    /* Format a raw stat value for display */
    function fmtRaw(cat, v) {
      if (cat === 'OBP') return v.toFixed(3);
      if (cat === 'ERA' || cat === 'WHIP') return v.toFixed(2);
      return Math.round(v).toString();
    }

    /* Build per-category cell: raw stat on top, z-score + raw delta below */
    function zCell(cat, borderLeft) {
      var nz = t.z[cat] || 0;
      var oz = old.z[cat] || 0;
      var nv = t.stats[cat] || 0;
      var ov = old.stats[cat] || 0;
      var rawD = nv - ov;
      var zd = nz - oz;

      /* Raw stat change — color by whether the change is GOOD for this cat */
      var isLower = lower[cat];
      var rawGood = isLower ? (rawD < -0.0005) : (rawD > 0.0005);
      var rawBad  = isLower ? (rawD > 0.0005)  : (rawD < -0.0005);
      var rawChanged = Math.abs(rawD) > 0.0005;

      var rawDStr = '';
      if (rawChanged) {
        var rc = rawGood ? '#4caf50' : rawBad ? '#e05555' : '#888';
        var rawSign = rawD >= 0 ? '+' : '−';
        rawDStr = '<div style="font-size:.66rem;color:' + rc + ';line-height:1;margin-top:2px">'
                + rawSign + fmtRaw(cat, Math.abs(rawD)) + '</div>';
      }

      /* Z-score line */
      var zStr = '<div style="font-size:.68rem;color:#666;line-height:1;margin-top:2px">z ' + nz.toFixed(2) + '</div>';
      /* Rank number line */
      var rkNum = catRanks[cat][t.team_id];
      var rkStr = '<div style="font-size:.55rem;color:#555;line-height:1;margin-top:1px">#' + rkNum + '</div>';

      var bl = borderLeft ? 'border-left:2px solid #444;' : '';
      return '<td style="' + bl + 'text-align:center;padding:4px 6px">'
           + '<div style="font-size:.92rem;line-height:1.15;font-weight:600;color:' + _phase3RankColor(catRanks[cat][t.team_id], n) + '">' + fmtRaw(cat, nv) + '</div>'
           + zStr + rkStr
           + rawDStr + '</td>';
    }

    html += '<tr style="background:' + bg + ';border-bottom:1px solid #222">'
          + '<td style="padding:5px 10px;color:' + rkC + ';font-weight:700">#' + t.rank_total + '</td>'
          + '<td style="padding:5px 10px;color:' + nameAccent + ';font-weight:600;white-space:nowrap">' + t.name + '</td>'
          + '<td style="padding:5px 10px;text-align:center;color:' + rkC + ';font-weight:700">'
          +   t.z_total.toFixed(2) + '</td>'
          + '<td style="padding:5px 10px;text-align:center;color:' + zCol + '">' + zStr + '</td>'
          + '<td style="padding:5px 10px;text-align:center;color:' + rCol + '">' + rStr + '</td>';

    /* Hitter subtotal + per-cat */
    var hzd = (t.z_hit||0) - (old.z_hit||0);
    var hzCol = hzd > 0.005 ? '#4caf50' : hzd < -0.005 ? '#e05555' : '#888';
    var hzDStr = Math.abs(hzd) > 0.005
               ? '<div style="font-size:.68rem;color:' + hzCol + ';line-height:1">'
                 + (hzd > 0 ? '+' : '−') + Math.abs(hzd).toFixed(2) + '</div>'
               : '';
    html += '<td style="border-left:2px solid #444;text-align:center;padding:4px 6px;font-weight:600">'
          + '<div style="font-size:.92rem;line-height:1.15;color:' + _phase3RankColor(t.rank_hit, n) + '">' + (t.z_hit||0).toFixed(2) + '</div>' + hzDStr + '</td>';
    hCats.forEach(function(c) { html += zCell(c, false); });

    /* Pitcher subtotal + per-cat */
    var pzd = (t.z_pit||0) - (old.z_pit||0);
    var pzCol = pzd > 0.005 ? '#4caf50' : pzd < -0.005 ? '#e05555' : '#888';
    var pzDStr = Math.abs(pzd) > 0.005
               ? '<div style="font-size:.68rem;color:' + pzCol + ';line-height:1">'
                 + (pzd > 0 ? '+' : '−') + Math.abs(pzd).toFixed(2) + '</div>'
               : '';
    html += '<td style="border-left:2px solid #444;text-align:center;padding:4px 6px;font-weight:600">'
          + '<div style="font-size:.92rem;line-height:1.15;color:' + _phase3RankColor(t.rank_pit, n) + '">' + (t.z_pit||0).toFixed(2) + '</div>' + pzDStr + '</td>';
    pCats.forEach(function(c) { html += zCell(c, false); });

    html += '</tr>';
  });
  html += '</tbody></table></div>';
  tw.innerHTML = html;
}

/* ════════════════════════════════════════════════════════════════════
   ── Waiver Wire ──────────────────────────────────────────────────
   ════════════════════════════════════════════════════════════════════ */

/* ── State ── */
var _wwState = {
  teamId: null,
  drops: [],      // array of player objects dropped
  adds: [],       // array of player objects added from FA
  mode: 'adddrop' // 'adddrop' or 'stream'
};
var _wwStreamState = {
  teamId: null,
  drop: null,      // single player dropped (or null if open spot)
  streamerProfile: null  // computed avg stats of top 8 SP (100+ IP)
};

/* ── Sub-tab toggle ── */
function wwSwitch(which) {
  document.getElementById('ww-adddrop-wrap').style.display = which==='adddrop' ? '' : 'none';
  document.getElementById('ww-stream-wrap').style.display  = which==='stream'  ? '' : 'none';
  var topFaWrap = document.getElementById('ww-topfa-wrap');
  if (topFaWrap) topFaWrap.style.display = which==='topfa' ? '' : 'none';
  ['adddrop','stream','topfa'].forEach(function(w) {
    var btn = document.getElementById('ww-' + w + '-btn');
    if (!btn) return;
    var on = (w === which);
    btn.style.borderBottom = on ? '3px solid var(--accent)' : 'none';
    btn.style.color = on ? '#fff' : '';
  });
  _wwState.mode = which;
  // Belt-and-suspenders: re-render Top FA on activation so it's populated
  // even if wwInit ran before the template was hydrated.
  if (which === 'topfa') _wwRenderTopFa();
}

/* ── Init: populate team dropdowns when waiver tab is first shown ── */
function wwInit() {
  if (!PHASE3_LEAGUE) return;
  var allTeams = PHASE3_LEAGUE.teams.slice().sort(function(a,b) {
    return (a.name || '').localeCompare(b.name || '');
  });
  // Add/Drop + Stream Pitchers dropdowns DEFAULT to the user's team.
  var optHtmlWithUserSel = '<option value="">— pick a team —</option>';
  allTeams.forEach(function(t) {
    var sel = (t.team_id === PHASE3_LEAGUE.user_team_id) ? ' selected' : '';
    optHtmlWithUserSel += '<option value="' + t.team_id + '"' + sel + '>' + t.name + '</option>';
  });
  // Top FA dropdown stays NEUTRAL — no team pre-selected.
  var optHtmlNeutral = '<option value="">— pick a team —</option>';
  allTeams.forEach(function(t) {
    optHtmlNeutral += '<option value="' + t.team_id + '">' + t.name + '</option>';
  });
  var s1 = document.getElementById('ww-team-sel');
  if (s1) { s1.innerHTML = optHtmlWithUserSel; }
  var s2 = document.getElementById('ww-stream-team-sel');
  if (s2) { s2.innerHTML = optHtmlWithUserSel; }
  var s3 = document.getElementById('ww-topfa-team-sel');
  if (s3) { s3.innerHTML = optHtmlNeutral; }
  // Auto-select user team for Add/Drop and Stream (but NOT Top FA —
  // that one stays neutral until the user picks a team).
  if (PHASE3_LEAGUE.user_team_id != null) {
    wwSetTeam(PHASE3_LEAGUE.user_team_id);
    wwStreamSetTeam(PHASE3_LEAGUE.user_team_id);
  }
  // Top FA lists render immediately (ungreened) so the user sees the
  // top 30 FAs even without picking a team.
  _wwRenderTopFa();
}
// wwInit runs from _bootFantasy once PHASE3_LEAGUE is loaded.

/* ════════════════════════════════════════════════════════════════════
   ── ADD / DROP MODE ──────────────────────────────────────────────
   ════════════════════════════════════════════════════════════════════ */

function wwSetTeam(val) {
  var tid = val === '' ? null : parseInt(val, 10);
  _wwState.teamId = tid;
  _wwState.drops = [];
  _wwState.adds = [];
  var rw = document.getElementById('ww-roster-wrap');
  if (!tid) { if (rw) rw.style.display = 'none'; return; }
  if (rw) rw.style.display = '';
  _wwRenderRoster();
}

function _wwGetTeam() {
  if (!PHASE3_LEAGUE || _wwState.teamId == null) return null;
  return PHASE3_LEAGUE.teams.find(function(t) { return t.team_id === _wwState.teamId; });
}

function _wwRosterSize(team) {
  return (team.hitters || []).length + (team.pitchers || []).length;
}

/* Max active roster = hitter slots (11) + bench hitters (variable) + pitchers
   We compute it from the current team size since it varies. */
function _wwMaxRoster(team) {
  return _wwRosterSize(team);
}

function _wwRenderRoster() {
  var team = _wwGetTeam();
  if (!team) return;
  var rList = document.getElementById('ww-roster-list');
  var addSec = document.getElementById('ww-add-section');
  var impWrap = document.getElementById('ww-impact-wrap');
  if (!rList) return;

  var hitters = (team.hitters || []).slice().sort(function(a,b) { return (b.dollars||0)-(a.dollars||0); });
  var pitchers = (team.pitchers || []).slice().sort(function(a,b) { return (b.dollars||0)-(a.dollars||0); });

  // Build drop set
  var dropIds = {};
  _wwState.drops.forEach(function(d) { dropIds[d.espn_id] = true; });

  function playerRow(p, isPitcher) {
    var dropped = !!dropIds[p.espn_id];
    var dc = _dollarRankColor(p.dollars || 0);
    var opacity = dropped ? 'opacity:0.35;' : '';
    var role = isPitcher ? ((p.IP||0) >= 100 ? 'SP' : 'RP') : '';
    var posLabel = isPitcher ? role : _eligLabel(p.elig);
    var posBadge = posLabel ? '<span style="color:#777;font-size:.58rem;font-weight:700;margin-left:4px">' + posLabel + '</span>' : '';
    var html = '<div style="display:flex;align-items:center;gap:8px;padding:5px 8px;'
             + 'background:#1a1a1a;border-radius:5px;margin-bottom:3px;' + opacity + '">'
             + '<span style="flex:1;font-size:.82rem;font-weight:600;color:#ddd">'
             + (p.name || 'Unknown') + posBadge + '</span>'
             + '<span style="font-size:.68rem;color:#888">' + (p.team || '') + '</span>'
             + '<span style="font-size:.82rem;font-weight:700;color:' + dc + ';width:52px;text-align:right">'
             + '$' + (p.dollars||0).toFixed(1) + '</span>';
    if (!dropped) {
      html += '<button data-eid="' + (p.espn_id+'').replace(/"/g,'&quot;') + '"'
            + ' onclick="wwDrop(this.dataset.eid)"'
            + ' style="background:#3a1a1a;border:1px solid #6b2e2e;color:#e05555;cursor:pointer;'
            + 'font-size:.68rem;padding:2px 8px;border-radius:4px;font-weight:700">Drop</button>';
    } else {
      html += '<button data-eid="' + (p.espn_id+'').replace(/"/g,'&quot;') + '"'
            + ' onclick="wwUndrop(this.dataset.eid)"'
            + ' style="background:#1a3a1a;border:1px solid #2e6b2e;color:#9cd39c;cursor:pointer;'
            + 'font-size:.68rem;padding:2px 8px;border-radius:4px;font-weight:700">Undo</button>';
    }
    html += '</div>';
    return html;
  }

  var html = '<div style="font-size:.7rem;color:#4caf50;font-weight:700;margin-bottom:4px">Hitters</div>';
  hitters.forEach(function(p) { html += playerRow(p, false); });
  html += '<div style="font-size:.7rem;color:#4caf50;font-weight:700;margin:8px 0 4px">Pitchers</div>';
  pitchers.forEach(function(p) { html += playerRow(p, true); });

  // Show added players
  if (_wwState.adds.length) {
    html += '<div style="font-size:.7rem;color:#4caf50;font-weight:700;margin:8px 0 4px">&#x2795; Added</div>';
    _wwState.adds.forEach(function(p) {
      var dc = _dollarColor(p.dollars || 0);
      var role = p.is_pitcher ? ((p.proj && p.proj.IP || 0) >= 100 ? 'SP' : 'RP') : 'H';
      html += '<div style="display:flex;align-items:center;gap:8px;padding:5px 8px;'
            + 'background:#1a2a1a;border-radius:5px;margin-bottom:3px;border-left:3px solid #4caf50">'
            + '<span style="color:#4caf50;font-size:.68rem;font-weight:700;width:36px">' + role + '</span>'
            + '<span style="flex:1;font-size:.82rem;font-weight:600;color:#ddd">'
            + (p.name || 'Unknown') + '</span>'
            + '<span style="font-size:.68rem;color:#888">' + (p.team || '') + '</span>'
            + '<span style="font-size:.82rem;font-weight:700;color:' + dc + ';width:52px;text-align:right">'
            + '$' + (p.dollars||0).toFixed(1) + '</span>'
            + '<button data-pname="' + (p.name||'').replace(/"/g,'&quot;') + '"'
            + ' onclick="wwRemoveAdd(this.dataset.pname)"'
            + ' style="background:#3a1a1a;border:1px solid #6b2e2e;color:#e05555;cursor:pointer;'
            + 'font-size:.68rem;padding:2px 8px;border-radius:4px;font-weight:700">✕</button>'
            + '</div>';
    });
  }

  var countEl = document.getElementById('ww-roster-count');
  var netChange = _wwState.adds.length - _wwState.drops.length;
  if (countEl) countEl.textContent = '(' + _wwRosterSize(team) + ' active' + (netChange !== 0 ? ', net ' + (netChange > 0 ? '+' : '') + netChange : '') + ')';

  rList.innerHTML = html;

  // Show add section when drops > adds (open spots)
  var openSpots = _wwState.drops.length - _wwState.adds.length;
  if (addSec) addSec.style.display = openSpots > 0 ? '' : 'none';

  // Show impact when at least one add OR one drop
  if (_wwState.adds.length > 0 || _wwState.drops.length > 0) {
    _wwRenderImpact();
    if (impWrap) impWrap.style.display = '';
  } else {
    if (impWrap) impWrap.style.display = 'none';
  }
}

function wwDrop(espnId) {
  var team = _wwGetTeam();
  if (!team) return;
  var all = (team.hitters || []).concat(team.pitchers || []);
  var p = all.find(function(x) { return String(x.espn_id) === String(espnId); });
  if (!p) return;
  // Check if already dropped
  if (_wwState.drops.some(function(d) { return String(d.espn_id) === String(espnId); })) return;
  _wwState.drops.push(p);
  _wwRenderRoster();
}

function wwUndrop(espnId) {
  _wwState.drops = _wwState.drops.filter(function(d) { return String(d.espn_id) !== String(espnId); });
  // If we now have more adds than drops, remove the last add
  while (_wwState.adds.length > _wwState.drops.length) {
    _wwState.adds.pop();
  }
  _wwRenderRoster();
}

/* ── Free Agent Search ── */
function wwSearchFA(q) {
  var dd = document.getElementById('ww-add-dd');
  if (!dd) return;
  q = (q || '').trim().toLowerCase();
  if (q.length < 2) { dd.style.display = 'none'; return; }

  // Build exclusion set: rostered + already added
  var addedNames = {};
  _wwState.adds.forEach(function(p) { addedNames[p.name] = true; });

  var pool = TRADE_HITTERS.concat(TRADE_PITCHERS);
  var matches = pool.filter(function(p) {
    if (p.team_id != null) return false;  // rostered
    if (addedNames[p.name]) return false;
    return (p.name || '').toLowerCase().includes(q);
  });
  matches.sort(function(a,b) { return (b.dollars||0) - (a.dollars||0); });
  matches = matches.slice(0, 10);

  if (!matches.length) {
    dd.innerHTML = '<div style="padding:10px;color:var(--muted);font-size:.8rem">No free agents found</div>';
    dd.style.display = '';
    return;
  }

  var html = '';
  matches.forEach(function(p) {
    var dc = _dollarRankColor(p.dollars || 0);
    var role = p.is_pitcher ? ((p.proj && p.proj.IP || 0) >= 100 ? 'SP' : 'RP') : 'H';
    html += '<div data-pname="' + (p.name||'').replace(/"/g,'&quot;') + '"'
          + ' onmousedown="wwAddFA(this.dataset.pname)"'
          + ' style="display:flex;align-items:center;gap:8px;padding:6px 10px;cursor:pointer;'
          + 'border-bottom:1px solid #2a2a2a;font-size:.82rem" '
          + 'onmouseover="this.style.background=\\x27#2a2a2a\\x27" onmouseout="this.style.background=\\x27\\x27">'
          + '<span style="color:#888;font-size:.68rem;font-weight:700;width:28px">' + role + '</span>'
          + '<span style="flex:1;color:#ddd;font-weight:600">' + p.name
          + (function(){ var pl = !p.is_pitcher ? _posLabel(p) : ''; return pl ? '<span style="color:#777;font-size:.58rem;font-weight:700;margin-left:4px">' + pl + '</span>' : ''; })()
          + '</span>'
          + '<span style="color:#888;font-size:.75rem">' + (p.team || '') + '</span>'
          + '<span style="color:' + dc + ';font-weight:700;width:50px;text-align:right">$' + (p.dollars||0).toFixed(1) + '</span>'
          + '</div>';
  });
  dd.innerHTML = html;
  dd.style.display = '';
}

function wwAddFA(name) {
  var pool = TRADE_HITTERS.concat(TRADE_PITCHERS);
  var p = pool.find(function(x) { return x.name === name && x.team_id == null; });
  if (!p) return;
  // Don't add more than we have open spots
  if (_wwState.adds.length >= _wwState.drops.length) return;
  _wwState.adds.push(p);
  var dd = document.getElementById('ww-add-dd');
  if (dd) dd.style.display = 'none';
  var search = document.getElementById('ww-add-search');
  if (search) search.value = '';
  _wwRenderRoster();
}

function wwRemoveAdd(name) {
  _wwState.adds = _wwState.adds.filter(function(p) { return p.name !== name; });
  _wwRenderRoster();
}

/* ── Dollar color helper (reuse from existing) ── */
function _dollarColor(d) {
  if (d >= 30) return '#4CAF50';
  if (d >= 20) return '#8BC34A';
  if (d >= 10) return '#CDDC39';
  if (d >= 5)  return '#FFC107';
  if (d >= 1)  return '#FF9800';
  return '#ef5350';
}

/* Rank-based dollar color: sort all players by $ desc, then apply the
   gold/red→blue gradient. Cached per-call for performance. */
var _dollarRankCache = null;
function _dollarRankColor(d) {
  if (!_dollarRankCache) {
    var all = (typeof TRADE_HITTERS !== 'undefined' ? TRADE_HITTERS : [])
              .concat(typeof TRADE_PITCHERS !== 'undefined' ? TRADE_PITCHERS : []);
    var vals = all.map(function(p) { return p.dollars || 0; });
    vals.sort(function(a,b) { return b - a; });
    // remove dupes for ranking
    var unique = [];
    vals.forEach(function(v) { if (!unique.length || unique[unique.length-1] !== v) unique.push(v); });
    _dollarRankCache = unique;
  }
  var idx = 0;
  for (var i = 0; i < _dollarRankCache.length; i++) {
    if (d >= _dollarRankCache[i]) { idx = i; break; }
  }
  var n = _dollarRankCache.length;
  var rank = idx + 1;
  return _phase3RankColor(rank, n);
}

/* ── Simulate waiver move: deep-copy league, apply drops + adds to team ── */
function _wwSimulate() {
  if (!PHASE3_LEAGUE || _wwState.teamId == null) return null;
  if (!_wwState.drops.length && !_wwState.adds.length) return null;

  var league = JSON.parse(JSON.stringify(PHASE3_LEAGUE.teams));
  var tid = _wwState.teamId;

  league.forEach(function(team) {
    if (team.team_id !== tid) return;

    // Remove dropped players
    var dropIds = {};
    _wwState.drops.forEach(function(d) { dropIds[d.espn_id] = true; });
    var origPitIds = team.pitchers.map(function(p){ return p.espn_id; }).sort().join();
    team.hitters  = team.hitters.filter(function(p)  { return !dropIds[p.espn_id]; });
    team.pitchers = team.pitchers.filter(function(p) { return !dropIds[p.espn_id]; });

    // Add FA pickups
    _wwState.adds.forEach(function(fa) {
      if (fa.is_pitcher) {
        team.pitchers.push({
          espn_id:  'fa_' + (fa.name || '').replace(/[^A-Za-z0-9]+/g, '_'),
          name:     fa.name,
          team:     fa.team,
          dollars:  fa.dollars || 0,
          W:        (fa.proj && fa.proj.W)    || 0,
          SO_p:     (fa.proj && fa.proj.K_p)  || 0,
          SV:       (fa.proj && fa.proj.SV)   || 0,
          HLD:      (fa.proj && fa.proj.HLD)  || 0,
          ERA:      (fa.proj && fa.proj.ERA)  || 0,
          WHIP:     (fa.proj && fa.proj.WHIP) || 0,
          IP:       (fa.proj && fa.proj.IP)   || ((fa.role === 'sp' || ((fa.proj && fa.proj.IP)||0) >= 100) ? 160 : 65)
        });
      } else {
        team.hitters.push({
          espn_id:  'fa_' + (fa.name || '').replace(/[^A-Za-z0-9]+/g, '_'),
          name:     fa.name,
          team:     fa.team,
          dollars:  fa.dollars || 0,
          elig:     fa.elig || (fa.fg_pos ? _fgPosToSlots(fa.fg_pos) : [12]),
          R:        (fa.proj && fa.proj.R)    || 0,
          HR:       (fa.proj && fa.proj.HR)   || 0,
          RBI:      (fa.proj && fa.proj.RBI)  || 0,
          SO_h:     (fa.proj && fa.proj.K_h)  || 0,
          SB:       (fa.proj && fa.proj.SB)   || 0,
          OBP:      (fa.proj && fa.proj.OBP)  || 0,
          PA:       (fa.proj && fa.proj.PA)   || 600
        });
      }
    });

    // Re-optimize hitter lineup
    var starters = _phase3OptimizeHitters(team.hitters);
    var hAgg = _phase3AggHit(starters);
    team.stats.R    = Math.round(hAgg.R    * 10) / 10;
    team.stats.HR   = Math.round(hAgg.HR   * 10) / 10;
    team.stats.RBI  = Math.round(hAgg.RBI  * 10) / 10;
    team.stats.SO_h = Math.round(hAgg.SO_h * 10) / 10;
    team.stats.SB   = Math.round(hAgg.SB   * 10) / 10;
    team.stats.OBP  = Math.round(hAgg.OBP  * 10000) / 10000;
    // Only recompute pitcher stats if pitcher roster actually changed
    var newPitIds = team.pitchers.map(function(p){ return p.espn_id; }).sort().join();
    if (origPitIds !== newPitIds) {
      var pAgg = _phase3AggPit(team.pitchers);
      team.stats.W    = Math.round(pAgg.W    * 10) / 10;
      team.stats.SO_p = Math.round(pAgg.SO_p * 10) / 10;
      team.stats.SV   = Math.round(pAgg.SV   * 10) / 10;
      team.stats.HLD  = Math.round(pAgg.HLD  * 10) / 10;
      team.stats.ERA  = Math.round(pAgg.ERA  * 1000) / 1000;
      team.stats.WHIP = Math.round(pAgg.WHIP * 1000) / 1000;
    }
  });

  _phase3RecomputeZ(league);
  return league;
}

/* ── Render Impact (compact delta for the user's team) ── */
function _wwRenderImpact() {
  var newLeague = _wwSimulate();
  var delta = document.getElementById('ww-delta');
  if (!newLeague || !delta) return;

  var tid = _wwState.teamId;
  var oldT = PHASE3_LEAGUE.teams.find(function(t) { return t.team_id === tid; });
  var newT = newLeague.find(function(t) { return t.team_id === tid; });
  if (!oldT || !newT) return;

  window._wwLastLeague = newLeague;

  var n = newLeague.length;
  var rkC = _phase3RankColor(newT.rank_total, n);
  var zd = newT.z_total - oldT.z_total;
  var rd = oldT.rank_total - newT.rank_total;
  var zCol = zd > 0.005 ? '#4caf50' : zd < -0.005 ? '#e05555' : '#888';
  var rCol = rd > 0     ? '#4caf50' : rd < 0      ? '#e05555' : '#888';
  var zStr = (zd >= 0 ? '+' : '−') + Math.abs(zd).toFixed(2);
  var rStr = rd === 0 ? '■ 0'
           : (rd > 0 ? '▲ ' : '▼ ') + Math.abs(rd);

  var html = '<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;padding:10px 14px;'
           + 'background:#1a1a1a;border-radius:8px;border-left:3px solid ' + rkC + '">'
           + '<div><span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;'
           + 'letter-spacing:.05em">Rank</span><br>'
           + '<span style="font-size:1.3rem;font-weight:700;color:' + rkC + '">#' + newT.rank_total + '</span></div>'
           + '<div><span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;'
           + 'letter-spacing:.05em">Z Total</span><br>'
           + '<span style="font-size:1.1rem;font-weight:700;color:#ddd">' + newT.z_total.toFixed(2) + '</span></div>'
           + '<div><span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;'
           + 'letter-spacing:.05em">Δ Z</span><br>'
           + '<span style="font-size:1.1rem;font-weight:700;color:' + zCol + '">' + zStr + '</span></div>'
           + '<div><span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;'
           + 'letter-spacing:.05em">Δ Rank</span><br>'
           + '<span style="font-size:1.1rem;font-weight:700;color:' + rCol + '">' + rStr + '</span></div>'
           + '</div>';
  delta.innerHTML = html;

  // Reset toggle states
  document.getElementById('ww-table-wrap').style.display = 'none';
  document.getElementById('ww-table-wrap').innerHTML = '';
  document.getElementById('ww-mc-wrap').style.display = 'none';
  document.getElementById('ww-mc-wrap').innerHTML = '';
  var tbtn = document.getElementById('ww-standings-btn');
  if (tbtn) tbtn.innerHTML = '▼ Show full updated standings';
  var mbtn = document.getElementById('ww-mc-btn');
  if (mbtn) mbtn.innerHTML = '▼ Show finish-probability sim (before / after)';
}

/* ── Toggle standings table (reuses _phase3RenderTable) ── */
function wwToggleTable() {
  var tw  = document.getElementById('ww-table-wrap');
  var btn = document.getElementById('ww-standings-btn');
  if (!tw) return;
  var open = tw.style.display !== 'none';
  if (open) {
    tw.style.display = 'none';
    btn.innerHTML = '▼ Show full updated standings';
  } else {
    var newLeague = window._wwLastLeague || _wwSimulate();
    if (!newLeague) return;
    // Reuse the existing _phase3RenderTable by temporarily swapping the target element
    _wwRenderStandingsTable(newLeague, 'ww-table-wrap');
    tw.style.display = '';
    btn.innerHTML = '▲ Hide full updated standings';
  }
}

/* Render standings table into a given container (shared by add/drop and stream) */
function _wwRenderStandingsTable(newLeague, containerId, highlightTeamId) {
  var tw = document.getElementById(containerId);
  if (!tw) return;
  var n = newLeague.length;
  function findOld(tid) { return PHASE3_LEAGUE.teams.find(function(t) { return t.team_id === tid; }); }
  var sorted = newLeague.slice().sort(function(a,b) { return a.rank_total - b.rank_total; });
  var lower = {};
  PHASE3_LEAGUE.lower_better.forEach(function(c) { lower[c] = true; });
  var hCats = PHASE3_LEAGUE.hit_cats || [];
  var pCats = PHASE3_LEAGUE.pit_cats || [];
  var catLabel = {'R':'R','HR':'HR','RBI':'RBI','SO_h':'K','SB':'SB','OBP':'OBP',
                  'W':'W','SO_p':'K','SV':'SV','HLD':'HLD','ERA':'ERA','WHIP':'WHIP'};
  var thSt = 'padding:5px 8px;text-align:center;color:var(--muted);font-size:.78rem;white-space:nowrap';

  var html = '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:.85rem">'
           + '<thead><tr style="border-bottom:1px solid #333">'
           + '<th style="text-align:left;padding:6px 10px;color:var(--muted)">#</th>'
           + '<th style="text-align:left;padding:6px 10px;color:var(--muted)">Team</th>'
           + '<th style="text-align:center;padding:6px 10px;color:var(--muted)">Z Tot</th>'
           + '<th style="text-align:center;padding:6px 10px;color:var(--muted)">ΔZ</th>'
           + '<th style="text-align:center;padding:6px 10px;color:var(--muted)">ΔRk</th>'
           + '<th style="border-left:2px solid #444;' + thSt + '">H Z</th>';
  hCats.forEach(function(c) { html += '<th style="' + thSt + '">' + (catLabel[c]||c) + '</th>'; });
  html += '<th style="border-left:2px solid #444;' + thSt + '">P Z</th>';
  pCats.forEach(function(c) { html += '<th style="' + thSt + '">' + (catLabel[c]||c) + '</th>'; });
  html += '</tr></thead><tbody>';

  /* Compute per-cat ranks for color-coding */
  var allCats = hCats.concat(pCats);
  var catRanks = {};
  allCats.forEach(function(cat) {
    var isLow = lower[cat];
    var pairs = newLeague.map(function(t) { return {tid: t.team_id, v: t.stats[cat]||0}; });
    pairs.sort(function(a,b) { return isLow ? a.v - b.v : b.v - a.v; });
    var rk = {};
    pairs.forEach(function(p, i) { rk[p.tid] = i + 1; });
    catRanks[cat] = rk;
  });

  sorted.forEach(function(t) {
    var old = findOld(t.team_id);
    var rkC = _phase3RankColor(t.rank_total, n);
    var zd  = t.z_total - old.z_total;
    var rd  = old.rank_total - t.rank_total;
    var zCol = zd > 0.005 ? '#4caf50' : zd < -0.005 ? '#e05555' : '#888';
    var rCol = rd > 0     ? '#4caf50' : rd < 0      ? '#e05555' : '#888';
    var zStr = (zd >= 0 ? '+' : '−') + Math.abs(zd).toFixed(2);
    var rStr = rd === 0 ? '■ 0'
             : (rd > 0 ? '▲ ' : '▼ ') + Math.abs(rd);
    var isUser = t.team_id === (highlightTeamId != null ? highlightTeamId : _wwState.teamId);
    var nameAccent = isUser ? '#4caf50' : '#ddd';
    var bg = isUser ? '#1a1a1a' : 'transparent';

    function fmtRaw(cat, v) {
      if (cat === 'OBP') return v.toFixed(3);
      if (cat === 'ERA' || cat === 'WHIP') return v.toFixed(2);
      return Math.round(v).toString();
    }

    function zCell(cat, borderLeft) {
      var nz = t.z[cat] || 0;
      var oz = old.z[cat] || 0;
      var nv = t.stats[cat] || 0;
      var ov = old.stats[cat] || 0;
      var rawD = nv - ov;
      var zd2 = nz - oz;
      var isLower = lower[cat];
      var rawGood = isLower ? (rawD < -0.0005) : (rawD > 0.0005);
      var rawBad  = isLower ? (rawD > 0.0005)  : (rawD < -0.0005);
      var rawChanged = Math.abs(rawD) > 0.0005;
      var rawDStr = '';
      if (rawChanged) {
        var rc = rawGood ? '#4caf50' : rawBad ? '#e05555' : '#888';
        var rawSign = rawD >= 0 ? '+' : '−';
        rawDStr = '<div style="font-size:.66rem;color:' + rc + ';line-height:1;margin-top:2px">'
                + rawSign + fmtRaw(cat, Math.abs(rawD)) + '</div>';
      }
      var zStr2 = '<div style="font-size:.68rem;color:#666;line-height:1;margin-top:2px">z ' + nz.toFixed(2) + '</div>';
      var rkNum = catRanks[cat][t.team_id];
      var rkStr2 = '<div style="font-size:.55rem;color:#555;line-height:1;margin-top:1px">#' + rkNum + '</div>';
      var bl = borderLeft ? 'border-left:2px solid #444;' : '';
      return '<td style="' + bl + 'text-align:center;padding:4px 6px">'
           + '<div style="font-size:.92rem;line-height:1.15;font-weight:600;color:' + _phase3RankColor(catRanks[cat][t.team_id], n) + '">' + fmtRaw(cat, nv) + '</div>'
           + zStr2 + rkStr2
           + rawDStr + '</td>';
    }

    html += '<tr style="background:' + bg + ';border-bottom:1px solid #222">'
          + '<td style="padding:5px 10px;color:' + rkC + ';font-weight:700">#' + t.rank_total + '</td>'
          + '<td style="padding:5px 10px;color:' + nameAccent + ';font-weight:600;white-space:nowrap">' + t.name + '</td>'
          + '<td style="padding:5px 10px;text-align:center;color:' + rkC + ';font-weight:700">'
          +   t.z_total.toFixed(2) + '</td>'
          + '<td style="padding:5px 10px;text-align:center;color:' + zCol + '">' + zStr + '</td>'
          + '<td style="padding:5px 10px;text-align:center;color:' + rCol + '">' + rStr + '</td>';
    var hzd = (t.z_hit||0) - (old.z_hit||0);
    var hzCol = hzd > 0.005 ? '#4caf50' : hzd < -0.005 ? '#e05555' : '#888';
    var hzDStr = Math.abs(hzd) > 0.005
               ? '<div style="font-size:.68rem;color:' + hzCol + ';line-height:1">'
                 + (hzd > 0 ? '+' : '−') + Math.abs(hzd).toFixed(2) + '</div>'
               : '';
    html += '<td style="border-left:2px solid #444;text-align:center;padding:4px 6px;font-weight:600">'
          + '<div style="font-size:.92rem;line-height:1.15;color:' + _phase3RankColor(t.rank_hit, n) + '">' + (t.z_hit||0).toFixed(2) + '</div>' + hzDStr + '</td>';
    hCats.forEach(function(c) { html += zCell(c, false); });
    var pzd = (t.z_pit||0) - (old.z_pit||0);
    var pzCol = pzd > 0.005 ? '#4caf50' : pzd < -0.005 ? '#e05555' : '#888';
    var pzDStr = Math.abs(pzd) > 0.005
               ? '<div style="font-size:.68rem;color:' + pzCol + ';line-height:1">'
                 + (pzd > 0 ? '+' : '−') + Math.abs(pzd).toFixed(2) + '</div>'
               : '';
    html += '<td style="border-left:2px solid #444;text-align:center;padding:4px 6px;font-weight:600">'
          + '<div style="font-size:.92rem;line-height:1.15;color:' + _phase3RankColor(t.rank_pit, n) + '">' + (t.z_pit||0).toFixed(2) + '</div>' + pzDStr + '</td>';
    pCats.forEach(function(c) { html += zCell(c, false); });
    html += '</tr>';
  });
  html += '</tbody></table></div>';
  tw.innerHTML = html;
}

/* ── Toggle MC sim (before/after) ── */
function wwToggleMc() {
  var mw  = document.getElementById('ww-mc-wrap');
  var btn = document.getElementById('ww-mc-btn');
  if (!mw) return;
  var open = mw.style.display !== 'none';
  if (open) {
    mw.style.display = 'none';
    btn.innerHTML = '▼ Show finish-probability sim (before / after)';
  } else {
    _wwRenderMc('ww-mc-wrap', window._wwLastLeague, _wwState.teamId);
    mw.style.display = '';
    btn.innerHTML = '▲ Hide finish-probability sim';
  }
}

/* Shared MC renderer for both add/drop and stream modes */
function _wwRenderMc(containerId, newLeague, highlightTid) {
  var mw = document.getElementById(containerId);
  if (!mw || !newLeague) return;
  mw.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:.75rem">Running 50,000 trials…</div>';
  setTimeout(function() {
    var baseSim = _mcGetBaseline();
    var newSim  = _mcRunSim(newLeague);
    var hl = highlightTid != null ? [highlightTid] : [];

    // Build before/after side by side
    var baseExp = baseSim[highlightTid] ? baseSim[highlightTid].expFinish : 0;
    var newExp  = newSim[highlightTid]  ? newSim[highlightTid].expFinish  : 0;
    var delta   = newExp - baseExp;
    var dCol    = delta < -0.05 ? '#4caf50' : delta > 0.05 ? '#e05555' : '#888';
    var dSign   = delta < 0 ? '▲' : (delta > 0 ? '▼' : '■');

    var teamName = '';
    PHASE3_LEAGUE.teams.forEach(function(t) { if (t.team_id === highlightTid) teamName = t.name; });

    var summary = '<div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;'
                + 'padding:10px 14px;background:#1a1a1a;border-radius:8px;margin-bottom:12px">'
                + '<div style="font-weight:700;color:#ddd;font-size:.85rem">' + teamName + '</div>'
                + '<div><span style="color:var(--muted);font-size:.68rem">Before: </span>'
                + '<span style="font-weight:700;color:#ddd">' + baseExp.toFixed(2) + '</span></div>'
                + '<div><span style="color:var(--muted);font-size:.68rem">After: </span>'
                + '<span style="font-weight:700;color:#ddd">' + newExp.toFixed(2) + '</span></div>'
                + '<div><span style="color:var(--muted);font-size:.68rem">Δ: </span>'
                + '<span style="font-weight:700;color:' + dCol + '">' + dSign + ' '
                + Math.abs(delta).toFixed(2) + '</span></div>'
                + '</div>';

    var grid = '<div style="display:flex;gap:14px;flex-wrap:wrap">'
             + '<div style="flex:1;min-width:320px">'
             + '<div style="font-size:.66rem;color:var(--muted);font-weight:700;'
             + 'text-transform:uppercase;letter-spacing:.05em;padding:0 0 6px 4px">Before</div>'
             + _mcRenderHeatmap(PHASE3_LEAGUE.teams, baseSim, hl)
             + '</div>'
             + '<div style="flex:1;min-width:320px">'
             + '<div style="font-size:.66rem;color:var(--muted);font-weight:700;'
             + 'text-transform:uppercase;letter-spacing:.05em;padding:0 0 6px 4px">After waiver move</div>'
             + _mcRenderHeatmap(newLeague, newSim, hl)
             + '</div>'
             + '</div>';
    mw.innerHTML = summary + grid;
  }, 20);
}

/* ════════════════════════════════════════════════════════════════════
   ── STREAM PITCHERS MODE ─────────────────────────────────────────
   ════════════════════════════════════════════════════════════════════ */

function wwStreamSetTeam(val) {
  var tid = val === '' ? null : parseInt(val, 10);
  _wwStreamState.teamId = tid;
  _wwStreamState.drop = null;
  _wwStreamState.streamerProfile = null;
  var body = document.getElementById('ww-stream-body');
  if (!tid) { if (body) body.style.display = 'none'; return; }
  if (body) body.style.display = '';
  _wwStreamComputeProfile();
  _wwStreamRender();
}

function _wwStreamGetTeam() {
  if (!PHASE3_LEAGUE || _wwStreamState.teamId == null) return null;
  return PHASE3_LEAGUE.teams.find(function(t) { return t.team_id === _wwStreamState.teamId; });
}

/* Compute the "average streamer" from top 8 unrostered SP by $ value.
   Only includes pitchers with SP role AND 100+ projected IP (filters out
   relievers who happen to have SP eligibility). ALSO excludes anyone on
   the MLB 60-day IL (p.il) — high-$ injured arms like Shane Bieber would
   otherwise inflate the streamer baseline even though they aren't realistic
   pickups right now.
   Returns per-start stats scaled to 4 starts/week for the remaining season. */
function _wwStreamComputeProfile() {
  // Find top 8 unrostered, healthy SP by dollar value, requiring 100+ proj IP
  var sps = TRADE_PITCHERS.filter(function(p) {
    return p.team_id == null && p.role === 'sp'
        && !p.il
        && p.proj && p.proj.IP >= 100;
  });
  sps.sort(function(a,b) { return (b.dollars||0) - (a.dollars||0); });
  var top5 = sps.slice(0, 8);

  if (!top5.length) {
    _wwStreamState.streamerProfile = null;
    return;
  }

  // Average their RoS projections
  var avg = { W:0, K_p:0, ERA:0, WHIP:0, IP:0, dollars:0 };
  top5.forEach(function(p) {
    avg.W    += (p.proj && p.proj.W)    || 0;
    avg.K_p  += (p.proj && p.proj.K_p)  || 0;
    avg.ERA  += (p.proj && p.proj.ERA)  || 0;
    avg.WHIP += (p.proj && p.proj.WHIP) || 0;
    avg.IP   += (p.proj && p.proj.IP)   || 0;
    avg.dollars += (p.dollars || 0);
  });
  var cnt = top5.length;
  avg.W /= cnt; avg.K_p /= cnt;
  avg.ERA /= cnt; avg.WHIP /= cnt; avg.IP /= cnt; avg.dollars /= cnt;

  // Per-start decomposition: compute per-start rates from the top-8 average,
  // then multiply by total streamer starts (4/week * weeksLeft).
  // Use ESPN matchup schedule data when available; fall back to date calc.
  var weeksLeft;
  if (PHASE3_LEAGUE && PHASE3_LEAGUE.matchup_total && PHASE3_LEAGUE.matchup_current) {
    // ESPN H2H: weeks remaining AFTER the current week finishes
    // (i.e. "if I start streaming next week, how many weeks do I get?")
    weeksLeft = Math.max(1, PHASE3_LEAGUE.matchup_total - PHASE3_LEAGUE.matchup_current);
  } else {
    var today = new Date();
    var seasonEnd = new Date(today.getFullYear(), 8, 28); // Sept 28 fallback
    var msPerWeek = 7 * 24 * 3600 * 1000;
    weeksLeft = Math.max(1, Math.round((seasonEnd - today) / msPerWeek));
  }
  var streamerStarts = 4 * weeksLeft;

  // Derive per-start rates from the average top-8 SP full-season projection
  var IP_PER_START = 5.3;
  var avgStartsRoS = Math.max(1, avg.IP / IP_PER_START);
  var wPerStart  = avg.W   / avgStartsRoS;
  var kPerStart  = avg.K_p / avgStartsRoS;
  var ipPerStart = IP_PER_START;

  // Scale counting stats and IP by total streamer starts;
  // rate stats (ERA, WHIP) stay as the top-8 average (they're per-inning rates).
  // SV and HLD are forced to 0: streaming SPs never earn saves or holds.
  var profile = {
    W:    wPerStart  * streamerStarts,
    SO_p: kPerStart  * streamerStarts,
    SV:   0,
    HLD:  0,
    ERA:  avg.ERA,
    WHIP: avg.WHIP,
    IP:   ipPerStart * streamerStarts,
    dollars: avg.dollars,
    top5Names: top5.map(function(p) { return p.name; }),
    weeksLeft: weeksLeft,
    startsPerWeek: 4,
    totalStarts: streamerStarts
  };
  _wwStreamState.streamerProfile = profile;
}

function _wwStreamRender() {
  var team = _wwStreamGetTeam();
  if (!team) return;
  var rosterEl = document.getElementById('ww-stream-roster');
  var previewEl = document.getElementById('ww-stream-preview');
  var impactEl = document.getElementById('ww-stream-impact');
  var statsEl = document.getElementById('ww-stream-stats');

  if (!rosterEl) return;

  // Show roster with drop buttons
  var hitters = (team.hitters || []).slice().sort(function(a,b) { return (b.dollars||0)-(a.dollars||0); });
  var pitchers = (team.pitchers || []).slice().sort(function(a,b) { return (b.dollars||0)-(a.dollars||0); });

  var html = '';
  var hasDropped = !!_wwStreamState.drop;

  function pRow(p, isPitcher) {
    var isDropped = hasDropped && String(p.espn_id) === String(_wwStreamState.drop.espn_id);
    var dc = _dollarRankColor(p.dollars || 0);
    var opacity = isDropped ? 'opacity:0.35;' : '';
    var role = isPitcher ? ((p.IP||0) >= 100 ? 'SP' : 'RP') : '';
    var posLabel = isPitcher ? role : _eligLabel(p.elig);
    var posBadge = posLabel ? '<span style="color:#777;font-size:.58rem;font-weight:700;margin-left:4px">' + posLabel + '</span>' : '';
    var r = '<div style="display:flex;align-items:center;gap:8px;padding:5px 8px;'
          + 'background:#1a1a1a;border-radius:5px;margin-bottom:3px;' + opacity + '">'
          + '<span style="flex:1;font-size:.82rem;font-weight:600;color:#ddd">'
          + (p.name || '') + posBadge + '</span>'
          + '<span style="font-size:.68rem;color:#888">' + (p.team || '') + '</span>'
          + '<span style="font-size:.82rem;font-weight:700;color:' + dc + ';width:52px;text-align:right">'
          + '$' + (p.dollars||0).toFixed(1) + '</span>';
    if (!isDropped && !hasDropped) {
      r += '<button data-eid="' + (p.espn_id+'').replace(/"/g,'&quot;') + '"'
         + ' onclick="wwStreamDrop(this.dataset.eid)"'
         + ' style="background:#3a1a1a;border:1px solid #6b2e2e;color:#e05555;cursor:pointer;'
         + 'font-size:.68rem;padding:2px 8px;border-radius:4px;font-weight:700">Drop</button>';
    } else if (isDropped) {
      r += '<button onclick="wwStreamUndrop()"'
         + ' style="background:#1a3a1a;border:1px solid #2e6b2e;color:#9cd39c;cursor:pointer;'
         + 'font-size:.68rem;padding:2px 8px;border-radius:4px;font-weight:700">Undo</button>';
    }
    r += '</div>';
    return r;
  }

  html += '<div style="font-size:.7rem;color:#4caf50;font-weight:700;margin-bottom:4px">Hitters</div>';
  hitters.forEach(function(p) { html += pRow(p, false); });
  html += '<div style="font-size:.7rem;color:#4caf50;font-weight:700;margin:8px 0 4px">Pitchers</div>';
  pitchers.forEach(function(p) { html += pRow(p, true); });
  rosterEl.innerHTML = html;

  // Show streamer profile preview
  var prof = _wwStreamState.streamerProfile;
  if (prof && previewEl && statsEl) {
    previewEl.style.display = '';
    var nameList = prof.top5Names.join(', ');
    var dropP = _wwStreamState.drop;
    // Net gains = streamer totals minus dropped pitcher's projections
    var netW  = dropP ? prof.W    - (dropP.W    || 0) : prof.W;
    var netK  = dropP ? prof.SO_p - (dropP.SO_p || 0) : prof.SO_p;
    var netIP = dropP ? prof.IP   - (dropP.IP   || 0) : prof.IP;
    var netLabel = dropP ? 'Net gain (vs dropping ' + (dropP.name||'player') + ')' : 'Streamer RoS totals';
    statsEl.innerHTML = '<div style="font-size:.78rem;color:#ddd;margin-bottom:6px">'
      + 'Based on: <span style="color:#4caf50">' + nameList + '</span></div>'
      + '<div style="font-size:.75rem;color:#bbb;margin-bottom:8px">'
      + prof.weeksLeft + ' weeks left &times; ' + prof.startsPerWeek + ' starts/wk = '
      + '<strong style="color:#fff">' + prof.totalStarts + ' total starts</strong></div>'
      + '<div style="font-size:.65rem;color:#999;letter-spacing:.03em;margin-bottom:3px">' + netLabel + '</div>'
      + '<div style="display:flex;gap:12px;flex-wrap:wrap;font-size:.78rem">'
      + '<span><span style="color:var(--muted)">W:</span> <strong>' + (netW>=0?'+':'') + netW.toFixed(1) + '</strong></span>'
      + '<span><span style="color:var(--muted)">K:</span> <strong>' + (netK>=0?'+':'') + netK.toFixed(1) + '</strong></span>'
      + '<span><span style="color:var(--muted)">ERA:</span> <strong>' + prof.ERA.toFixed(2) + '</strong></span>'
      + '<span><span style="color:var(--muted)">WHIP:</span> <strong>' + prof.WHIP.toFixed(2) + '</strong></span>'
      + '<span><span style="color:var(--muted)">IP:</span> <strong>' + (netIP>=0?'+':'') + netIP.toFixed(1) + '</strong></span>'
      + '</div>';
  }

  // Show impact if player dropped (or simulate without drop for preview)
  if (hasDropped) {
    _wwStreamRenderImpact();
    if (impactEl) impactEl.style.display = '';
  } else {
    if (impactEl) impactEl.style.display = 'none';
  }
}

function wwStreamDrop(espnId) {
  var team = _wwStreamGetTeam();
  if (!team) return;
  var all = (team.hitters || []).concat(team.pitchers || []);
  var p = all.find(function(x) { return String(x.espn_id) === String(espnId); });
  if (!p) return;
  _wwStreamState.drop = p;
  _wwStreamRender();
}

function wwStreamUndrop() {
  _wwStreamState.drop = null;
  _wwStreamRender();
}

/* Simulate streaming: drop one player, add the streamer profile as a pitcher */
function _wwStreamSimulate() {
  if (!PHASE3_LEAGUE || _wwStreamState.teamId == null) return null;
  var prof = _wwStreamState.streamerProfile;
  if (!prof) return null;
  if (!_wwStreamState.drop) return null;

  var league = JSON.parse(JSON.stringify(PHASE3_LEAGUE.teams));
  var tid = _wwStreamState.teamId;

  league.forEach(function(team) {
    if (team.team_id !== tid) return;

    // Remove dropped player
    var dropId = _wwStreamState.drop.espn_id;
    team.hitters  = team.hitters.filter(function(p)  { return p.espn_id !== dropId; });
    team.pitchers = team.pitchers.filter(function(p) { return p.espn_id !== dropId; });

    // Add streamer pitcher with the profile stats.
    // variance_scale reduces MC sim variance to reflect diversification:
    // streaming 96 independent starts (vs ~28 for one SP) means the
    // aggregate outcome has lower variance by ~1/sqrt(96/28) ≈ 0.54.
    var typicalSPStarts = 28;
    var vScale = Math.sqrt(typicalSPStarts / Math.max(1, prof.totalStarts));
    team.pitchers.push({
      espn_id:  'streamer_composite',
      name:     'Streaming SP (4/wk)',
      team:     'FA',
      dollars:  prof.dollars || 0,
      W:        prof.W,
      SO_p:     prof.SO_p,
      SV:       prof.SV,
      HLD:      prof.HLD,
      ERA:      prof.ERA,
      WHIP:     prof.WHIP,
      IP:       prof.IP,
      variance_scale: vScale
    });

    // Re-optimize + re-aggregate
    var starters = _phase3OptimizeHitters(team.hitters);
    var hAgg = _phase3AggHit(starters);
    var pAgg = _phase3AggPit(team.pitchers);
    team.stats = {
      R:    Math.round(hAgg.R    * 10) / 10,
      HR:   Math.round(hAgg.HR   * 10) / 10,
      RBI:  Math.round(hAgg.RBI  * 10) / 10,
      SO_h: Math.round(hAgg.SO_h * 10) / 10,
      SB:   Math.round(hAgg.SB   * 10) / 10,
      OBP:  Math.round(hAgg.OBP  * 10000) / 10000,
      W:    Math.round(pAgg.W    * 10) / 10,
      SO_p: Math.round(pAgg.SO_p * 10) / 10,
      SV:   Math.round(pAgg.SV   * 10) / 10,
      HLD:  Math.round(pAgg.HLD  * 10) / 10,
      ERA:  Math.round(pAgg.ERA  * 1000) / 1000,
      WHIP: Math.round(pAgg.WHIP * 1000) / 1000,
    };
  });

  _phase3RecomputeZ(league);
  return league;
}

function _wwStreamRenderImpact() {
  var newLeague = _wwStreamSimulate();
  var delta = document.getElementById('ww-stream-delta');
  if (!newLeague || !delta) return;

  var tid = _wwStreamState.teamId;
  var oldT = PHASE3_LEAGUE.teams.find(function(t) { return t.team_id === tid; });
  var newT = newLeague.find(function(t) { return t.team_id === tid; });
  if (!oldT || !newT) return;

  window._wwStreamLastLeague = newLeague;

  var n = newLeague.length;
  var rkC = _phase3RankColor(newT.rank_total, n);
  var zd = newT.z_total - oldT.z_total;
  var rd = oldT.rank_total - newT.rank_total;
  var zCol = zd > 0.005 ? '#4caf50' : zd < -0.005 ? '#e05555' : '#888';
  var rCol = rd > 0     ? '#4caf50' : rd < 0      ? '#e05555' : '#888';
  var zStr = (zd >= 0 ? '+' : '−') + Math.abs(zd).toFixed(2);
  var rStr = rd === 0 ? '■ 0'
           : (rd > 0 ? '▲ ' : '▼ ') + Math.abs(rd);

  delta.innerHTML = '<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;padding:10px 14px;'
    + 'background:#1a1a1a;border-radius:8px;border-left:3px solid ' + rkC + '">'
    + '<div><span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;'
    + 'letter-spacing:.05em">Rank</span><br>'
    + '<span style="font-size:1.3rem;font-weight:700;color:' + rkC + '">#' + newT.rank_total + '</span></div>'
    + '<div><span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;'
    + 'letter-spacing:.05em">Z Total</span><br>'
    + '<span style="font-size:1.1rem;font-weight:700;color:#ddd">' + newT.z_total.toFixed(2) + '</span></div>'
    + '<div><span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;'
    + 'letter-spacing:.05em">Δ Z</span><br>'
    + '<span style="font-size:1.1rem;font-weight:700;color:' + zCol + '">' + zStr + '</span></div>'
    + '<div><span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;'
    + 'letter-spacing:.05em">Δ Rank</span><br>'
    + '<span style="font-size:1.1rem;font-weight:700;color:' + rCol + '">' + rStr + '</span></div>'
    + '</div>';

  // Reset toggle states
  document.getElementById('ww-stream-table-wrap').style.display = 'none';
  document.getElementById('ww-stream-table-wrap').innerHTML = '';
  document.getElementById('ww-stream-mc-wrap').style.display = 'none';
  document.getElementById('ww-stream-mc-wrap').innerHTML = '';
  var tbtn = document.getElementById('ww-stream-standings-btn');
  if (tbtn) tbtn.innerHTML = '▼ Show full updated standings';
  var mbtn = document.getElementById('ww-stream-mc-btn');
  if (mbtn) mbtn.innerHTML = '▼ Show finish-probability sim (before / after)';
}

function wwStreamToggleTable() {
  var tw  = document.getElementById('ww-stream-table-wrap');
  var btn = document.getElementById('ww-stream-standings-btn');
  if (!tw) return;
  var open = tw.style.display !== 'none';
  if (open) {
    tw.style.display = 'none';
    btn.innerHTML = '▼ Show full updated standings';
  } else {
    var newLeague = window._wwStreamLastLeague || _wwStreamSimulate();
    if (!newLeague) return;
    _wwRenderStandingsTable(newLeague, 'ww-stream-table-wrap', _wwStreamState.teamId);
    tw.style.display = '';
    btn.innerHTML = '▲ Hide full updated standings';
  }
}

function wwStreamToggleMc() {
  var mw  = document.getElementById('ww-stream-mc-wrap');
  var btn = document.getElementById('ww-stream-mc-btn');
  if (!mw) return;
  var open = mw.style.display !== 'none';
  if (open) {
    mw.style.display = 'none';
    btn.innerHTML = '▼ Show finish-probability sim (before / after)';
  } else {
    _wwRenderMc('ww-stream-mc-wrap', window._wwStreamLastLeague, _wwStreamState.teamId);
    mw.style.display = '';
    btn.innerHTML = '▲ Hide finish-probability sim';
  }
}

/* ════════════════════════════════════════════════════════════════════
   ── TOP FA MODE ──────────────────────────────────────────────────
   Shows top 30 unrostered hitters + SPs by RoS dollar value. When a
   team is selected, highlights any FA who would improve that team:
   — Hitters: runs the LAP with the FA added; if they claim a starting
     slot they're highlighted green.
   — SPs: compares the FA's $ to the team's lowest-$ rostered SP
     (role='sp' / IP >= 100); green if the FA outearns them.
   ════════════════════════════════════════════════════════════════════ */
var _wwTopFaState = { teamId: null };

function wwTopFaSetTeam(val) {
  var tid = val === '' ? null : parseInt(val, 10);
  _wwTopFaState.teamId = tid;
  _wwRenderTopFa();
}

/* Grab the currently selected team from PHASE3_LEAGUE (or null). */
function _wwTopFaGetTeam() {
  if (!PHASE3_LEAGUE || _wwTopFaState.teamId == null) return null;
  return PHASE3_LEAGUE.teams.find(function(t) { return t.team_id === _wwTopFaState.teamId; });
}

/* If adding this FA hitter would displace someone in the LAP starting
   lineup, return the baseline starter who loses their slot. Otherwise
   null. `baselineStarters` is the team's pre-FA LAP-optimized lineup —
   we pass it in so it only gets computed once per team select. */
function _wwTopFaHitterHelps(team, fa, baselineStarters) {
  if (!team || !fa) return null;
  var faClone = {
    espn_id: '__topfa_' + (fa.name || '').replace(/[^A-Za-z0-9]+/g, '_'),
    name:    fa.name,
    team:    fa.team,
    dollars: fa.dollars || 0,
    elig:    (fa.elig && fa.elig.length) ? fa.elig.slice() : _fgPosToSlots(fa.fg_pos || '')
  };
  if (!faClone.elig || !faClone.elig.length) return null;
  var hitters = (team.hitters || []).concat([faClone]);
  var full = _phase3OptimizeHittersFull(hitters);
  var faIn = false;
  var newIds = {};
  for (var i = 0; i < full.assigns.length; i++) {
    var p = full.assigns[i].player;
    if (!p) continue;
    if (p.espn_id === faClone.espn_id) faIn = true;
    newIds[p.espn_id] = true;
  }
  if (!faIn) return null;
  // Someone in the baseline starting 11 isn't in the new 11 → displaced.
  for (var j = 0; j < baselineStarters.length; j++) {
    var bp = baselineStarters[j];
    if (bp && !newIds[bp.espn_id]) return bp;
  }
  return {name: 'a starter', dollars: 0};
}

/* If this FA SP outearns the team's lowest-$ rostered SP, return that SP
   (so the caller can show who they'd replace). Otherwise null. "SP" is
   inferred like elsewhere in the dashboard: explicit role tag, else IP
   >= 100. If the team has no rostered SP, any FA qualifies. */
function _wwTopFaSpHelps(team, fa) {
  if (!team || !fa) return null;
  var spList = (team.pitchers || []).filter(function(p) {
    if (p.role) return String(p.role).toLowerCase() === 'sp';
    return (p.IP || 0) >= 100;
  });
  if (!spList.length) return {name: '(open SP slot)', dollars: 0};
  var lowest = spList[0];
  for (var i = 1; i < spList.length; i++) {
    if ((spList[i].dollars || 0) < (lowest.dollars || 0)) lowest = spList[i];
  }
  return (fa.dollars || 0) > (lowest.dollars || 0) ? lowest : null;
}

function _wwFmtTopFaRow(p, displaces, isPitcher) {
  var helps   = !!displaces;
  var nameCol = helps ? '#7ec87e' : '#ddd';
  var nameBg  = helps ? 'background:#1a2a1a;' : '';
  var dCol    = (p.dollars || 0) >= 10 ? '#f0c040'
              : (p.dollars || 0) >= 0  ? '#bbb' : '#888';
  var posStr = '';
  if (isPitcher) {
    posStr = 'SP';
  } else {
    if (p.elig && p.elig.length) posStr = _eligLabel(p.elig);
    if (!posStr && p.fg_pos) posStr = p.fg_pos;
  }
  // "UPGRADE over <player> ($X)" tag when the FA would displace a starter.
  var helpsTag = '';
  if (helps) {
    var overName = displaces && displaces.name ? displaces.name : 'a starter';
    var overDol  = (displaces && displaces.dollars != null)
      ? ' $' + displaces.dollars.toFixed(1)
      : '';
    helpsTag = '<span style="color:#7ec87e;font-size:.58rem;font-weight:700;'
             + 'margin-left:5px;letter-spacing:.04em">&#x25B2; over ' + overName
             + '<span style="color:#5a9a5a;font-weight:600">' + overDol + '</span>'
             + '</span>';
  }
  var teamStr = p.team ? '<span style="color:#777;font-size:.68rem;margin-left:6px">' + p.team + '</span>' : '';
  var posTag = posStr ? '<span style="color:#888;font-size:.58rem;font-weight:700;margin-left:6px">' + posStr + '</span>' : '';
  return '<div style="display:flex;align-items:center;justify-content:space-between;'
       + 'padding:5px 10px;border-bottom:1px solid #1f1f1f;' + nameBg + '">'
       + '<div style="display:flex;align-items:center;min-width:0;flex:1;flex-wrap:wrap">'
       +   '<span style="color:' + nameCol + ';font-weight:600;white-space:nowrap">'
       +     (p.name || '?') + '</span>' + teamStr + posTag + helpsTag
       + '</div>'
       + '<span style="color:' + dCol + ';font-weight:700;margin-left:10px;'
       + 'font-variant-numeric:tabular-nums">$' + (p.dollars || 0).toFixed(1) + '</span>'
       + '</div>';
}

function _wwRenderTopFa() {
  var hEl = document.getElementById('ww-topfa-h-list');
  var pEl = document.getElementById('ww-topfa-p-list');
  if (!hEl || !pEl) return;   // template not yet hydrated — wwSwitch will retry

  try {
    var team = _wwTopFaGetTeam();

    // Top 30 FA hitters — unrostered (team_id null OR undefined when no
    // ESPN snapshot was loaded), sorted by $ desc.
    var fas_h = (typeof TRADE_HITTERS !== 'undefined' ? TRADE_HITTERS : [])
      .filter(function(p) { return p.team_id == null; });
    fas_h.sort(function(a, b) { return (b.dollars || 0) - (a.dollars || 0); });
    fas_h = fas_h.slice(0, 30);

    // Top 30 FA SPs — unrostered, role == sp. Include when role tag is
    // missing as a safety fallback.
    var fas_p = (typeof TRADE_PITCHERS !== 'undefined' ? TRADE_PITCHERS : [])
      .filter(function(p) {
        if (p.team_id != null) return false;
        if (p.role) return String(p.role).toLowerCase() === 'sp';
        return true;
      });
    fas_p.sort(function(a, b) { return (b.dollars || 0) - (a.dollars || 0); });
    fas_p = fas_p.slice(0, 30);

    // Compute the team's baseline LAP starters ONCE — needed to identify
    // who each upgrading FA would displace.
    var baselineStarters = [];
    if (team) {
      try {
        baselineStarters = _phase3OptimizeHittersFull(team.hitters || []).starters || [];
      } catch (e) {
        console.warn('[TopFA] baseline LAP failed', e);
      }
    }

    // Greening is best-effort — if the LAP throws on one FA, it shouldn't
    // nuke the whole render. Catch per-FA and treat as "doesn't help".
    function safeHelpsHitter(p) {
      if (!team) return null;
      try { return _wwTopFaHitterHelps(team, p, baselineStarters); }
      catch (e) { console.warn('[TopFA] hitter-helps failed', p && p.name, e); return null; }
    }
    function safeHelpsSp(p) {
      if (!team) return null;
      try { return _wwTopFaSpHelps(team, p); }
      catch (e) { console.warn('[TopFA] sp-helps failed', p && p.name, e); return null; }
    }

    var hHtml = fas_h.map(function(p) {
      return _wwFmtTopFaRow(p, safeHelpsHitter(p), false);
    }).join('') || '<div style="color:var(--muted);font-size:.8rem;padding:8px">No free-agent hitters.</div>';

    var pHtml = fas_p.map(function(p) {
      return _wwFmtTopFaRow(p, safeHelpsSp(p), true);
    }).join('') || '<div style="color:var(--muted);font-size:.8rem;padding:8px">No free-agent SPs.</div>';

    hEl.innerHTML = hHtml;
    pEl.innerHTML = pHtml;
  } catch (e) {
    console.error('[TopFA] render failed:', e);
    var msg = '<div style="color:#e05555;font-size:.8rem;padding:8px">'
            + 'Top FA render failed: ' + ((e && e.message) || e) + '</div>';
    hEl.innerHTML = msg;
    pEl.innerHTML = '';
  }
}
function _activateEmbeddedScripts(root) {
  if (!root) return;
  root.querySelectorAll('script').forEach(function(oldScript) {
    var script = document.createElement('script');
    script.textContent = oldScript.textContent;
    oldScript.parentNode.replaceChild(script, oldScript);
  });
}
function _fantEsc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
    .replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function _fantMval(value) {
  var n = Number(value);
  if (!isFinite(n)) return '–';
  return n >= 0 ? ('$' + n.toFixed(1)) : ('−$' + Math.abs(n).toFixed(1));
}
function _fantEarned(value) {
  if (value == null || value === '') return {text:'–', color:'#666', val:-999};
  var n = Number(value);
  if (!isFinite(n)) return {text:'–', color:'#666', val:-999};
  return {
    text: _fantMval(n),
    color: n > 1 ? '#d8a13a' : '#8a8a8a',
    val: n
  };
}
function _fantProjDol(value) {
  if (value == null || value === '') return {text:'–', color:'#888', val:-999};
  var n = Number(value);
  if (!isFinite(n)) return {text:'–', color:'#888', val:-999};
  return {text:_fantMval(n), color:'#4CAF50', val:n};
}
function _fantProjStat(value, label) {
  if (value == null || value === '') return null;
  var n = Number(value);
  if (!isFinite(n)) return null;
  if (label === 'OBP') return n.toFixed(3);
  if (label === 'ERA' || label === 'WHIP') return n.toFixed(2);
  return String(Math.round(n));
}
function _fantTh(label, col, tableId) {
  return '<th onclick="fantSort(\'' + tableId + '\',this)" data-col="' + col + '" '
    + 'style="cursor:pointer;user-select:none;white-space:nowrap">'
    + label + '&nbsp;<span class="fsi" style="color:var(--muted);font-size:.65rem;opacity:.35">&#9660;</span></th>';
}
function _renderFantTable(tableId, players, infoLabel, infoKey, cats) {
  var slot = document.getElementById(tableId + '-slot');
  if (!slot) return;
  var col = 0;
  var hdr = '<thead><tr>' + _fantTh('#', col, tableId)
    + _fantTh('Name', ++col, tableId)
    + _fantTh('Team', ++col, tableId)
    + _fantTh('Earned&nbsp;$', ++col, tableId)
    + _fantTh('Proj&nbsp;$', ++col, tableId);
  if (infoLabel) hdr += _fantTh(infoLabel, ++col, tableId);
  cats.forEach(function(cat) { hdr += _fantTh(cat.label, ++col, tableId); });
  hdr += '</tr></thead>';
  var rows = [];
  var rank = 0;
  (players || []).forEach(function(player) {
    if (!player || !player.table) return;
    rank += 1;
    var name = player.name || '–';
    var team = (player.team || '').toUpperCase();
    var search = (name + ' ' + team).toLowerCase()
      .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
      .replace(/</g, '&lt;').replace(/>/g, '&gt;');
    var pos = player.pos || '';
    var posHtml = pos
      ? '<span style="color:#777;font-size:.58rem;font-weight:700;margin-left:4px">' + _fantEsc(pos) + '</span>'
      : '';
    var teamCell = (team && typeof tm === 'function') ? tm(team) : _fantEsc(team);
    var earned = _fantEarned(player.earned);
    var projected = _fantProjDol(player.dollars);
    var info = '';
    if (infoKey) {
      var raw = player.proj ? player.proj[infoKey] : null;
      var num = raw == null ? null : Number(raw);
      if (num != null && isFinite(num)) {
        info = '<td style="text-align:center;padding:3px 6px;color:#999;font-size:.88rem" data-val="'
          + num.toFixed(1) + '">' + Math.round(num) + '</td>';
      } else {
        info = '<td style="text-align:center;opacity:.5" data-val="0">—</td>';
      }
    }
    var stats = '';
    cats.forEach(function(cat) {
      var dollar = player.cats ? player.cats[cat.key] : null;
      var proj = player.proj ? player.proj[cat.key] : null;
      if (dollar == null || dollar === '') {
        stats += '<td style="text-align:center;opacity:.5" data-val="0">—</td>';
        return;
      }
      var projTxt = _fantProjStat(proj, cat.label);
      var projHtml = projTxt != null
        ? '<div style="font-size:.68rem;color:#777;font-weight:400;line-height:1.1;margin-top:1px">(' + projTxt + ')</div>'
        : '';
      stats += '<td style="text-align:center;padding:3px 6px" data-val="' + Number(dollar) + '">'
        + '<div style="font-size:.9rem;line-height:1.15">' + _fantMval(dollar) + '</div>'
        + projHtml + '</td>';
    });
    rows.push(
      '<tr data-role="' + _fantEsc(player.role || '') + '" data-pos="' + _fantEsc(pos) + '" data-search="' + search + '">'
      + '<td class="rank-col" data-val="' + rank + '">' + rank + '</td>'
      + '<td class="name-col">' + _fantEsc(name) + posHtml + '</td>'
      + '<td style="white-space:nowrap">' + teamCell + '</td>'
      + '<td style="color:' + earned.color + ';font-weight:700;font-size:.95rem" data-val="' + earned.val + '">' + earned.text + '</td>'
      + '<td style="color:' + projected.color + ';font-weight:700;font-size:.95rem" data-val="' + projected.val + '">' + projected.text + '</td>'
      + info + stats + '</tr>'
    );
  });
  slot.innerHTML = '<div class="table-wrap" id="' + tableId + '"><table class="stats-table"><colgroup></colgroup>'
    + hdr + '<tbody>' + rows.join('') + '</tbody></table></div>';
  var table = document.getElementById(tableId);
  if (table) table._fantRowCache = null;
}
function _renderFantDollarTables() {
  var hitCats = [
    {label:'R', key:'R'}, {label:'HR', key:'HR'}, {label:'RBI', key:'RBI'},
    {label:'SB', key:'SB'}, {label:'K', key:'K_h'}, {label:'OBP', key:'OBP'}
  ];
  var pitCats = [
    {label:'W', key:'W'}, {label:'ERA', key:'ERA'}, {label:'WHIP', key:'WHIP'},
    {label:'K', key:'K_p'}, {label:'SV', key:'SV'}, {label:'HLD', key:'HLD'}
  ];
  _renderFantTable('fant-h-tbl', TRADE_HITTERS, 'PA', 'PA', hitCats);
  _renderFantTable('fant-p-tbl', TRADE_PITCHERS, 'IP', 'IP', pitCats);
}
window._bootFantasy = function() {
  var payload = window._fantasyPayload || {};
  TRADE_HITTERS = payload.hitters || [];
  TRADE_PITCHERS = payload.pitchers || [];
  PHASE3_LEAGUE = payload.phase3 || null;
  var standings = document.getElementById('fant-standings-slot');
  if (standings) {
    standings.innerHTML = payload.standings_html || '';
    _activateEmbeddedScripts(standings);
  }
  var projections = document.getElementById('fant-proj-slot');
  if (projections) {
    projections.innerHTML = payload.projections_html || '';
    _activateEmbeddedScripts(projections);
  }
  _renderFantDollarTables();
  applyFantColors('fant-h-tbl');
  applyFantColors('fant-p-tbl');
  if (typeof phase3Init === 'function') phase3Init();
  wwInit();
};
if (window._fantasyPayload) window._bootFantasy();
