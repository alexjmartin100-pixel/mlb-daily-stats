#!/usr/bin/env python3
"""
Split fetch_mlb_stats.py into manageable modules.

Reads the monolith file and creates:
  config.py              – constants, pitch colors, team names, Firebase config
  utils.py               – small helper functions
  fangraphs.py           – Playwright, Cloudflare bypass, FG API, game stuff/velo
  data_fetch.py          – Statcast, MLB API, player info, pitcher box data
  batting_leaderboard.py – fetch_season_batting_leaderboard + percentiles
  pitching_leaderboard.py– fetch_season_pitching_leaderboard (code only)
  player_cards.py        – player cards tab, inject helpers
  html_template.py       – HTML_TEMPLATE string + render_html()
  fantasy.py             – projections, dollar values, fantasy tab
  fetch_mlb_stats.py     – main() entry point (GHA workflow unchanged)

Run:  python split_modules.py
Then: push_split.bat
"""

import os, re, shutil, sys

REPO = os.path.dirname(os.path.abspath(__file__))
SRC  = os.path.join(REPO, "fetch_mlb_stats.py")
BAK  = os.path.join(REPO, "fetch_mlb_stats_BACKUP.py")

# ─── Standard-library imports shared by most modules ─────────────────────────
COMMON_IMPORTS = """\
import os, json, time, sys
from datetime import date, timedelta, datetime
"""

# ─── Module definitions: each maps to (anchor_start, anchor_end, extra_imports)
# anchor_start / anchor_end are regex patterns matched against FULL lines.
# Everything from anchor_start up to (but not including) anchor_end goes in that file.

def find_line(lines, pattern, start=0):
    """Return 0-based index of first line matching pattern at or after start."""
    for i in range(start, len(lines)):
        if re.search(pattern, lines[i]):
            return i
    return None

def find_line_exact(lines, text, start=0):
    """Return 0-based index of first line starting with exact text."""
    for i in range(start, len(lines)):
        if lines[i].startswith(text):
            return i
    return None


def split():
    with open(SRC, "r", encoding="utf-8") as f:
        lines = f.readlines()

    total = len(lines)
    print(f"Read {total} lines from fetch_mlb_stats.py")

    # ── Find key anchors ────────────────────────────────────────────────────
    def fl(text, start=0):
        idx = find_line_exact(lines, text, start)
        if idx is None:
            print(f"  WARNING: anchor not found: {text!r} (from line {start})")
        return idx

    # Module boundaries (0-based line indices)
    # config.py: start of file → first def
    first_def = fl("def ta_norm")  # line ~116

    # utils.py: ta_norm → _PW globals
    pw_globals = fl("_PW_INSTANCE")  # line ~225

    # fangraphs.py: PW globals → fetch_mlb_sb
    fetch_mlb_sb = fl("def fetch_mlb_sb")  # line ~907

    # data_fetch.py: fetch_mlb_sb → fetch_season_batting_leaderboard
    batting_lb = fl("def fetch_season_batting_leaderboard")  # line ~1309

    # batting_leaderboard.py: → compute_hitter_percentiles end / _TEAM_ID_MAP
    team_id_map = fl("_TEAM_ID_MAP")  # line ~1825

    # player_cards.py: _TEAM_ID_MAP → fetch_season_pitching_leaderboard
    pitching_lb = fl("def fetch_season_pitching_leaderboard")  # line ~2279

    # pitching_leaderboard.py: → HTML_TEMPLATE
    html_template = fl("HTML_TEMPLATE")  # line ~2676

    # html_template.py: HTML_TEMPLATE → def main
    main_func = fl("def main")  # line ~5103

    # main / fetch_mlb_stats.py: main → fetch_fg_projections
    fg_projections = fl("def fetch_fg_projections")  # line ~5247

    # fantasy.py: fetch_fg_projections → end of file

    anchors = {
        "first_def": first_def,
        "pw_globals": pw_globals,
        "fetch_mlb_sb": fetch_mlb_sb,
        "batting_lb": batting_lb,
        "team_id_map": team_id_map,
        "pitching_lb": pitching_lb,
        "html_template": html_template,
        "main_func": main_func,
        "fg_projections": fg_projections,
    }
    print("\nAnchors found:")
    for k, v in anchors.items():
        print(f"  {k}: line {v+1 if v is not None else '???'}")

    if any(v is None for v in anchors.values()):
        print("\nERROR: Some anchors not found. Cannot split safely.")
        return False

    # ── Extract original imports (lines before first_def that are import/from) ──
    original_imports = []
    original_other_top = []  # non-import top-level code (try/except blocks etc)
    in_try_block = False
    for i in range(first_def):
        line = lines[i]
        stripped = line.rstrip('\n')
        if stripped.startswith('import ') or stripped.startswith('from '):
            original_imports.append(stripped)
        elif stripped.startswith('try:') or stripped.startswith('except') or (in_try_block and stripped.startswith('    ')):
            original_other_top.append(stripped)
            in_try_block = stripped.startswith('try:') or (in_try_block and not stripped.startswith('except'))
        elif stripped.startswith('    import') or stripped.startswith('    from'):
            original_other_top.append(stripped)

    # Build import block
    import_block = "\n".join(original_imports) + "\n"
    if original_other_top:
        import_block += "\n" + "\n".join(original_other_top) + "\n"

    # ── Define modules ──────────────────────────────────────────────────────
    # Each: (filename, start_idx, end_idx, header_imports, footer)
    modules = []

    # 1. config.py — constants only (imports handled separately)
    modules.append((
        "config.py",
        0, first_def,
        "",  # gets the original imports directly
        ""
    ))

    # 2. utils.py
    modules.append((
        "utils.py",
        first_def, pw_globals,
        import_block + "\nfrom config import *\n",
        ""
    ))

    # 3. fangraphs.py
    modules.append((
        "fangraphs.py",
        pw_globals, fetch_mlb_sb,
        import_block + "\nfrom config import *\nfrom utils import *\n",
        ""
    ))

    # 4. data_fetch.py
    modules.append((
        "data_fetch.py",
        fetch_mlb_sb, batting_lb,
        import_block + "\nfrom config import *\nfrom utils import *\nfrom fangraphs import *\n",
        ""
    ))

    # 5. batting_leaderboard.py
    modules.append((
        "batting_leaderboard.py",
        batting_lb, team_id_map,
        import_block + "\nfrom config import *\nfrom utils import *\nfrom fangraphs import *\nfrom data_fetch import *\n",
        ""
    ))

    # 6. player_cards.py
    modules.append((
        "player_cards.py",
        team_id_map, pitching_lb,
        import_block + "\nfrom config import *\nfrom utils import *\nfrom fangraphs import *\n",
        ""
    ))

    # 7. pitching_leaderboard.py
    modules.append((
        "pitching_leaderboard.py",
        pitching_lb, html_template,
        import_block + "\nfrom config import *\nfrom utils import *\nfrom fangraphs import *\nfrom data_fetch import *\n",
        ""
    ))

    # 8. html_template.py  (HTML_TEMPLATE + render_html)
    modules.append((
        "html_template.py",
        html_template, main_func,
        import_block + "\nfrom config import *\n",
        ""
    ))

    # 9. fetch_mlb_stats.py (main entry point) — lines from main_func to fg_projections
    # This will be written specially at the end

    # 10. fantasy.py
    modules.append((
        "fantasy.py",
        fg_projections, total,
        import_block + "\nfrom config import *\nfrom utils import *\nfrom fangraphs import *\n",
        ""
    ))

    # ── Write module files ──────────────────────────────────────────────────
    written = []
    for filename, start, end, header, footer in modules:
        filepath = os.path.join(REPO, filename)
        chunk = "".join(lines[start:end])

        # config.py keeps its original content as-is (already has imports)
        if filename == "config.py":
            content = chunk
        else:
            content = header + "\n" + chunk
            if footer:
                content += footer

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        line_count = content.count('\n')
        written.append((filename, line_count))
        print(f"  Wrote {filename} ({line_count} lines)")

    # ── Write new fetch_mlb_stats.py (main entry point) ─────────────────────
    main_chunk = "".join(lines[main_func:fg_projections])

    main_content = f'''{import_block}

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

{main_chunk}

if __name__ == "__main__":
    main()
'''
    main_path = os.path.join(REPO, "fetch_mlb_stats.py")
    with open(main_path, "w", encoding="utf-8") as f:
        f.write(main_content)
    main_lines = main_content.count('\n')
    written.append(("fetch_mlb_stats.py", main_lines))
    print(f"  Wrote fetch_mlb_stats.py ({main_lines} lines)")

    # ── Summary ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SPLIT COMPLETE")
    print("=" * 60)
    for fn, lc in sorted(written, key=lambda x: -x[1]):
        bar = "█" * min(lc // 50, 40)
        print(f"  {fn:30s} {lc:5d} lines  {bar}")
    print(f"\n  Total: {sum(lc for _, lc in written)} lines across {len(written)} files")
    print("\nNext: run  push_split.bat")

    return True


def main():
    print("=" * 60)
    print("Splitting fetch_mlb_stats.py into modules")
    print("=" * 60)
    print()

    if not os.path.exists(SRC):
        print(f"ERROR: {SRC} not found")
        return

    # Backup
    print(f"Backing up to {os.path.basename(BAK)}...")
    shutil.copy2(SRC, BAK)
    print("  Done\n")

    ok = split()
    if not ok:
        print("\nRestoring backup...")
        shutil.copy2(BAK, SRC)
        print("Restored. No changes made.")


if __name__ == "__main__":
    main()
