"""
patch_fantasy.py
Run from the repo root:  python patch_fantasy.py
Adds a Fantasy Dollar-Value tab to fetch_mlb_stats.py and the generated dashboard.
"""
import sys, os, re

TARGET = "fetch_mlb_stats.py"

# ─────────────────────────────────────────────────────────────────────────────
# NEW PYTHON CODE — inserted into fetch_mlb_stats.py before the
# `if __name__ == "__main__":` block.
# ─────────────────────────────────────────────────────────────────────────────
NEW_FUNCTIONS = r'''
# ═══════════════════════════════════════════════════════════════════════════════
#  Fantasy Dollar-Value Engine
#  League: 10-team H2H, $260/team, 6x6
#  Hitting:  R / HR / RBI / SB / K / OBP
#  Pitching: W / ERA / WHIP / K / SV / HLD
# ═══════════════════════════════════════════════════════════════════════════════

_FANT = {
    "n_teams":      10,
    "budget":       260,
    "h_slots":      14,   # hitter roster spots per team
    "p_slots":      9,    # pitcher roster spots per team
    "h_split":      0.67, # fraction of total budget for hitters
    "h_cats":       ["R", "HR", "RBI", "SB", "K", "OBP"],
    "p_cats":       ["W", "ERA", "WHIP", "K", "SV", "HLD"],
    "neg_cats":     {"ERA", "WHIP"},  # lower = better
}


def fetch_fg_projections(year: int, proj_type: str, stats_type: str) -> list:
    """Fetch FanGraphs projections.  proj_type: 'oopsy' | 'batx'  stats_type: 'bat' | 'pit'"""
    url = (f"https://www.fangraphs.com/api/projections"
           f"?type={proj_type}&stats={stats_type}&pos=all&team=0&players=0&lg=all")
    try:
        cookie_str = _load_fg_cookie()
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer":    "https://www.fangraphs.com/",
            "Accept":     "application/json",
        }
        if cookie_str:
            if isinstance(cookie_str, dict):
                hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookie_str.items())
            else:
                hdrs["Cookie"] = str(cookie_str)
        resp = requests.get(url, headers=hdrs, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            print(f"    [{proj_type}/{stats_type}] {len(data)} rows")
            return data
        print(f"    [{proj_type}/{stats_type}] HTTP {resp.status_code}")
    except Exception as exc:
        print(f"    [{proj_type}/{stats_type}] error: {exc}")
    return []


def _avg_proj_sets(a: list, b: list, id_key: str = "playerid") -> list:
    """Average two projection lists element-wise, matched by player ID."""
    if not b:
        return list(a or [])
    if not a:
        return list(b)
    b_map = {str(p.get(id_key, "")): p for p in b if p.get(id_key)}
    result = []
    for pa in a:
        pid = str(pa.get(id_key, ""))
        pb  = b_map.get(pid)
        if not pb:
            result.append(pa)
            continue
        merged = dict(pa)
        for k, va in pa.items():
            vb = pb.get(k)
            try:
                merged[k] = (float(va) + float(vb)) / 2.0
            except (TypeError, ValueError):
                pass
        result.append(merged)
    return result


def _fant_stat(row: dict, cat: str) -> float:
    """Pull a fantasy category value from a player row, handling key variants."""
    try:
        if cat == "K":
            return float(row.get("SO") or row.get("K") or row.get("k") or 0)
        if cat == "HLD":
            return float(row.get("HLD") or row.get("holds") or row.get("hld") or 0)
        return float(row.get(cat) or row.get(cat.lower()) or 0)
    except (TypeError, ValueError):
        return 0.0


def _z_to_dollars(players: list, cats: list, neg_cats: set,
                  n_roster: int, usable: float, is_pitcher: bool) -> list:
    """Core z-score → dollar-value calculation."""
    if not players:
        return []

    # Rank by playing time to define the rostered pool
    pt_key = ((lambda p: float(p.get("IP") or p.get("ip") or 0))
              if is_pitcher
              else (lambda p: float(p.get("PA") or p.get("pa") or
                                    float(p.get("G") or 0) * 3.8)))

    pool = sorted(players, key=pt_key, reverse=True)[:n_roster]

    # Per-category mean / std within the pool
    cat_params: dict = {}
    for cat in cats:
        vals = [_fant_stat(p, cat) for p in pool]
        vals = [v for v in vals if v != 0]
        mu  = float(np.mean(vals)) if vals else 0.0
        sig = float(np.std(vals))  if vals else 1e-9
        cat_params[cat] = (mu, max(sig, 1e-9))

    # Compute z-scores for every player
    out = []
    for p in players:
        zc: dict = {}
        z_sum = 0.0
        for cat in cats:
            mu, sig = cat_params[cat]
            v = _fant_stat(p, cat)
            z = (v - mu) / sig
            if cat in neg_cats:
                z = -z
            zc[cat]  = round(z, 2)
            z_sum   += z
        out.append({"player": p, "z": round(z_sum, 2), "zc": zc})

    pos_z_total = sum(max(0.0, r["z"]) for r in out) or 1.0
    for r in out:
        r["dollar"] = round(max(1.0, (r["z"] / pos_z_total) * usable + 1.0), 1)

    out.sort(key=lambda x: x["dollar"], reverse=True)
    return out


def compute_fantasy_dollar_values(lb_data: list, lb_pitch_data: dict, year: int) -> dict:
    """
    Compute YTD and projected fantasy dollar values for hitters and pitchers.
    Returns dict with keys: ytd_h, ytd_p, fut_h, fut_p.
    """
    cfg    = _FANT
    total  = cfg["n_teams"] * cfg["budget"]           # 2 600
    h_bud  = total * cfg["h_split"]                   # 1 742
    p_bud  = total * (1 - cfg["h_split"])             #   858
    n_h    = cfg["n_teams"] * cfg["h_slots"]          #   140
    n_p    = cfg["n_teams"] * cfg["p_slots"]          #    90
    h_use  = h_bud - n_h                              # 1 602
    p_use  = p_bud - n_p                              #   768

    # ── Present (YTD season leaderboards) ─────────────────────────────────
    all_pit_lb = ((list(lb_pitch_data.get("starters",  []))
                 + list(lb_pitch_data.get("relievers", [])))
                 if lb_pitch_data else [])

    ytd_h = _z_to_dollars(lb_data or [], cfg["h_cats"], cfg["neg_cats"],
                           n_h, h_use, False)
    ytd_p = _z_to_dollars(all_pit_lb,   cfg["p_cats"], cfg["neg_cats"],
                           n_p, p_use, True)

    # ── Future (OOPSY + Bat X projected average) ──────────────────────────
    print("  [FANTASY] Fetching OOPSY projections…")
    ob = fetch_fg_projections(year, "oopsy", "bat")
    op = fetch_fg_projections(year, "oopsy", "pit")
    print("  [FANTASY] Fetching Bat X projections…")
    bb = fetch_fg_projections(year, "batx",  "bat")
    bp = fetch_fg_projections(year, "batx",  "pit")

    avg_b = _avg_proj_sets(ob, bb)
    avg_p = _avg_proj_sets(op, bp)

    def _nb(r):
        return {
            "name": r.get("PlayerName", ""), "team": r.get("Team", ""),
            "fg_id": r.get("playerid"),      "mlbam": r.get("xMLBAMID"),
            "R":   r.get("R",   0), "HR":  r.get("HR",  0),
            "RBI": r.get("RBI", 0), "SB":  r.get("SB",  0),
            "SO":  r.get("SO",  0), "OBP": r.get("OBP", 0),
            "PA":  r.get("PA",  0), "G":   r.get("G",   0),
        }

    def _np(r):
        return {
            "name": r.get("PlayerName", ""), "team": r.get("Team", ""),
            "fg_id": r.get("playerid"),      "mlbam": r.get("xMLBAMID"),
            "W":    r.get("W",    0), "ERA":  r.get("ERA",  0),
            "WHIP": r.get("WHIP", 0), "SO":   r.get("SO",   0),
            "SV":   r.get("SV",   0), "HLD":  r.get("HLD",  0),
            "IP":   r.get("IP",   0), "G":    r.get("G",    0),
            "GS":   r.get("GS",   0),
        }

    proj_b = [_nb(r) for r in avg_b] if avg_b else []
    proj_p = [_np(r) for r in avg_p] if avg_p else []

    fut_h = _z_to_dollars(proj_b, cfg["h_cats"], cfg["neg_cats"], n_h, h_use, False)
    fut_p = _z_to_dollars(proj_p, cfg["p_cats"], cfg["neg_cats"], n_p, p_use, True)

    print(f"  [FANTASY] YTD  — {len(ytd_h)} hitters, {len(ytd_p)} pitchers")
    print(f"  [FANTASY] Proj — {len(fut_h)} hitters, {len(fut_p)} pitchers")

    return {"ytd_h": ytd_h, "ytd_p": ytd_p, "fut_h": fut_h, "fut_p": fut_p}


# ─── HTML helpers ─────────────────────────────────────────────────────────────

def _fmt_dollar(v):
    if v is None:
        return "–"
    return f"${v:.1f}" if v >= 0 else f"−${abs(v):.1f}"


def _dollar_color(v):
    if v is None:
        return "#888"
    if v >= 30:  return "#4CAF50"
    if v >= 20:  return "#8BC34A"
    if v >= 10:  return "#CDDC39"
    if v >= 5:   return "#FFC107"
    if v >= 1:   return "#FF9800"
    return "#ef5350"


def _z_color(z):
    if z >=  1.5: return "#4CAF50"
    if z >=  0.5: return "#8BC34A"
    if z >= -0.5: return "#aaa"
    if z >= -1.5: return "#FF9800"
    return "#ef5350"


def _merge_players(ytd_list: list, fut_list: list, is_pitcher: bool) -> list:
    """
    Combine YTD and future dollar lists into one merged list.
    Matched by fg_id first, then by lower-cased name.
    Sorted by projected $, then YTD $.
    """
    # Build lookup maps for future data
    fut_by_fgid: dict = {}
    fut_by_name: dict = {}
    for fr in fut_list:
        p    = fr["player"]
        fgid = str(p.get("fg_id") or p.get("playerid") or "")
        nm   = (p.get("name") or p.get("PlayerName") or "").strip().lower()
        if fgid:
            fut_by_fgid[fgid] = fr
        if nm:
            fut_by_name[nm] = fr

    seen: set = set()
    rows: list = []

    for yr in ytd_list:
        p    = yr["player"]
        fgid = str(p.get("fg_id") or p.get("playerid") or "")
        nm   = (p.get("name") or p.get("PlayerName") or "").strip().lower()
        fr   = fut_by_fgid.get(fgid) or fut_by_name.get(nm)
        key  = fgid or nm
        seen.add(key)
        rows.append({
            "ytd": yr, "fut": fr,
            "name": p.get("name") or p.get("PlayerName") or "–",
            "team": p.get("team") or p.get("Team") or "",
            "sort": (fr["dollar"] if fr else 0, yr["dollar"]),
        })

    for fr in fut_list:
        p    = fr["player"]
        fgid = str(p.get("fg_id") or p.get("playerid") or "")
        nm   = (p.get("name") or p.get("PlayerName") or "").strip().lower()
        key  = fgid or nm
        if key in seen:
            continue
        rows.append({
            "ytd": None, "fut": fr,
            "name": p.get("name") or p.get("PlayerName") or "–",
            "team": p.get("team") or p.get("Team") or "",
            "sort": (fr["dollar"], 0),
        })

    rows.sort(key=lambda x: x["sort"], reverse=True)
    return rows


def render_fantasy_tab(fdata: dict) -> str:
    """Generate the full HTML for the Fantasy tab panel."""
    if not fdata:
        return '<div id="fantasy-panel" class="tab-panel"></div>'

    cfg    = _FANT
    h_cats = cfg["h_cats"]
    p_cats = cfg["p_cats"]

    # ── stat display helpers ───────────────────────────────────────────────
    def _stat_fmt(v, cat):
        if v is None:
            return "–"
        if cat == "OBP":
            return f"{float(v):.3f}"
        if cat in ("ERA", "WHIP"):
            return f"{float(v):.2f}"
        return str(int(round(float(v))))

    # ── build one HTML table ───────────────────────────────────────────────
    def _build_table(merged: list, cats: list, is_pitcher: bool, table_id: str) -> str:
        cat_hdrs = "".join(f'<th title="{c}">{c}</th>' for c in cats)
        hdr = (
            f'<thead><tr>'
            f'<th class="rank-col">#</th>'
            f'<th class="name-col">Name</th>'
            f'<th>Team</th>'
            f'<th>Proj&nbsp;$</th>'
            f'<th>YTD&nbsp;$</th>'
            f'{cat_hdrs}'
            f'</tr></thead>'
        )
        rows_html = []
        for rank, row in enumerate(merged[:200], 1):
            nm   = row["name"]
            tm   = row["team"]
            yr   = row["ytd"]
            fr   = row["fut"]
            ydol = yr["dollar"] if yr else None
            fdol = fr["dollar"] if fr else None
            # Use future stats for display if available, else YTD
            src  = (fr["player"] if fr else (yr["player"] if yr else {}))
            zc   = (fr["zc"] if fr else (yr["zc"] if yr else {}))

            stat_cells = ""
            for cat in cats:
                v    = _fant_stat(src, cat)
                zval = zc.get(cat, 0)
                stat_cells += (
                    f'<td title="{cat} z={zval:+.2f}" '
                    f'style="color:{_z_color(zval)}">'
                    f'{_stat_fmt(v, cat)}</td>'
                )

            ydol_str = _fmt_dollar(ydol)
            fdol_str = _fmt_dollar(fdol)
            ydol_col = _dollar_color(ydol)
            fdol_col = _dollar_color(fdol)

            rows_html.append(
                f'<tr>'
                f'<td class="rank-col">{rank}</td>'
                f'<td class="name-col">{nm}</td>'
                f'<td style="color:#aaa;font-size:.8rem">{tm}</td>'
                f'<td style="color:{fdol_col};font-weight:700;font-size:.95rem">{fdol_str}</td>'
                f'<td style="color:{ydol_col}">{ydol_str}</td>'
                f'{stat_cells}'
                f'</tr>'
            )

        return (
            f'<div class="table-wrap" id="{table_id}">'
            f'<table class="stats-table"><colgroup></colgroup>'
            f'{hdr}'
            f'<tbody>{"".join(rows_html)}</tbody>'
            f'</table></div>'
        )

    # ── merge YTD + future ─────────────────────────────────────────────────
    merged_h = _merge_players(fdata["ytd_h"], fdata["fut_h"], False)
    merged_p = _merge_players(fdata["ytd_p"], fdata["fut_p"], True)

    tbl_h = _build_table(merged_h, h_cats, False, "fant-h-tbl")
    tbl_p = _build_table(merged_p, p_cats, True,  "fant-p-tbl")

    inner = f"""
<div id="fantasy-panel" class="tab-panel">
  <div style="padding:18px 20px 6px">
    <h2 style="color:var(--accent);margin:0 0 6px">💰 Fantasy Dollar Values</h2>
    <p style="color:var(--muted);font-size:.82rem;margin:0 0 14px">
      10-team H2H &nbsp;•&nbsp; $260/team &nbsp;•&nbsp; 6×6
      &nbsp;|&nbsp; <strong>Proj $</strong>: OOPSY + Bat X avg (full-season)
      &nbsp;•&nbsp; <strong>YTD $</strong>: season-to-date
      &nbsp;•&nbsp; Hover stats for z-score
    </p>
    <div style="display:flex;gap:10px;margin-bottom:14px">
      <button id="fant-h-btn" class="tab-btn active"
              onclick="fantSwitch('h')"
              style="border-bottom:3px solid var(--accent);color:#fff;padding:8px 18px">
        Hitters
      </button>
      <button id="fant-p-btn" class="tab-btn"
              onclick="fantSwitch('p')"
              style="padding:8px 18px">
        Pitchers
      </button>
    </div>
  </div>
  <div id="fant-h-wrap">{tbl_h}</div>
  <div id="fant-p-wrap" style="display:none">{tbl_p}</div>
</div>
<script>
function fantSwitch(which) {{
  document.getElementById('fant-h-wrap').style.display = which==='h' ? '' : 'none';
  document.getElementById('fant-p-wrap').style.display = which==='p' ? '' : 'none';
  var hb = document.getElementById('fant-h-btn');
  var pb = document.getElementById('fant-p-btn');
  hb.classList.toggle('active', which==='h');
  pb.classList.toggle('active', which==='p');
  hb.style.borderBottom = which==='h' ? '3px solid var(--accent)' : '3px solid transparent';
  pb.style.borderBottom = which==='p' ? '3px solid var(--accent)' : '3px solid transparent';
  hb.style.color = which==='h' ? '#fff' : '';
  pb.style.color = which==='p' ? '#fff' : '';
}}
</script>
"""
    return inner


def inject_fantasy_tab(html: str, fdata: dict) -> str:
    """
    Inject Fantasy tab button + panel into the rendered dashboard HTML.
    Looks for the compare-button anchor to place the tab button,
    and appends the panel just before </body>.
    """
    if not fdata:
        return html

    # 1. Tab button — insert after the compare button's closing </button>
    btn_html = "\n  <button class=\"tab-btn\" onclick=\"showTab('fantasy',this)\">💰 Fantasy</button>"
    anchor   = "showTab('compare'"
    if anchor in html:
        idx     = html.index(anchor)
        end_btn = html.index("</button>", idx) + len("</button>")
        html    = html[:end_btn] + btn_html + html[end_btn:]
    else:
        # Fallback: append inside tab-bar div
        html = html.replace('</div>', btn_html + '\n</div>', 1)

    # 2. Tab panel — insert before </body>
    panel_html = render_fantasy_tab(fdata)
    html       = html.replace("</body>", panel_html + "\n</body>")

    return html
'''

# ─────────────────────────────────────────────────────────────────────────────
# PATCH LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def patch():
    if not os.path.exists(TARGET):
        print(f"ERROR: {TARGET} not found. Run from the repo root.")
        sys.exit(1)

    with open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()

    changed = False

    # ── 1. Insert new functions before `if __name__ == "__main__":` ──────────
    MAIN_GUARD = 'if __name__ == "__main__":'
    if "compute_fantasy_dollar_values" in src:
        print("⚠️  Fantasy functions already present — skipping function insertion.")
    elif MAIN_GUARD not in src:
        print(f"ERROR: Could not find '{MAIN_GUARD}' in {TARGET}")
        sys.exit(1)
    else:
        src = src.replace(MAIN_GUARD, NEW_FUNCTIONS + "\n" + MAIN_GUARD)
        print("✅ Inserted fantasy functions.")
        changed = True

    # ── 2. Add fantasy computation step in main() ────────────────────────────
    LB_MARKER   = "    lb_pitch_data = fetch_season_pitching_leaderboard(year)"
    FANT_MARKER = "    fantasy_data = compute_fantasy_dollar_values("

    if FANT_MARKER in src:
        print("⚠️  Fantasy computation already present in main() — skipping.")
    elif LB_MARKER not in src:
        print(f"ERROR: Could not find lb_pitch_data line in main()")
        sys.exit(1)
    else:
        fant_call = (
            LB_MARKER + "\n"
            "    print(\"\\n[ 6b/6 ] Fantasy dollar values\")\n"
            "    fantasy_data = compute_fantasy_dollar_values(lb_data, lb_pitch_data, year)"
        )
        src     = src.replace(LB_MARKER, fant_call)
        print("✅ Added fantasy computation to main().")
        changed = True

    # ── 3. Inject fantasy tab into rendered HTML ─────────────────────────────
    INJECT_MARKER = "    html = inject_fantasy_tab(html, fantasy_data)"

    if INJECT_MARKER in src:
        print("⚠️  inject_fantasy_tab already present — skipping.")
    else:
        # Find the render_html call ending and insert after it
        # The call ends with:  lb_data=lb_data, lb_pitch_data=lb_pitch_data)
        RENDER_END = "lb_data=lb_data, lb_pitch_data=lb_pitch_data)"
        if RENDER_END not in src:
            print("ERROR: Could not find render_html call ending.")
            sys.exit(1)
        src = src.replace(
            RENDER_END,
            RENDER_END + "\n    html = inject_fantasy_tab(html, fantasy_data)"
        )
        print("✅ Added inject_fantasy_tab call.")
        changed = True

    if changed:
        with open(TARGET, "w", encoding="utf-8") as f:
            f.write(src)
        print(f"\n✅ Patch applied to {TARGET}")
    else:
        print("\nNo changes needed — already fully patched.")


if __name__ == "__main__":
    patch()
