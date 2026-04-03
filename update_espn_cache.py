#!/usr/bin/env python3
"""
update_espn_cache.py  –  Run this on YOUR LOCAL MACHINE to sync ESPN rosters.

ESPN blocks API requests from GitHub Actions (data-centre IPs).  This script
runs from your home computer where ESPN has no issue, fetches every team's
roster for each of your leagues, writes espn_rosters_cache.json, then commits
and pushes it so the daily GitHub Actions build can read it automatically.

HOW TO AUTOMATE:
  Set it up once in Windows Task Scheduler pointing at run_espn_update.bat.
  Weekly is plenty — only re-run sooner if you make a big roster move.

CREDENTIALS:
  Set three Windows environment variables once (search "Edit environment
  variables for your account" in the Start menu):
    ESPN_SWID          {B4B0FE87-6CCD-4057-8229-0AA0B70FE311}
    ESPN_S2            <your espn_s2 cookie value>
    ESPN_LEAGUE_IDS    2081322885
  Or just hard-code them in the CONFIG block below — this file is gitignored.
"""

import json
import os
import subprocess
import sys
import unicodedata
import urllib.parse
from pathlib import Path

# ── CONFIG (override with env vars, or fill in directly) ──────────────────────
ESPN_SWID       = os.environ.get("ESPN_SWID",       "{B4B0FE87-6CCD-4057-8229-0AA0B70FE311}")
ESPN_S2         = os.environ.get("ESPN_S2",         "")          # paste your espn_s2 here
ESPN_LEAGUE_IDS = os.environ.get("ESPN_LEAGUE_IDS", "2081322885")
MY_TEAM_NAME    = os.environ.get("MY_TEAM_NAME",    "team alex") # case-insensitive
YEAR            = 2026
AUTO_PUSH       = True   # set False to write the file but skip git commit/push
# ─────────────────────────────────────────────────────────────────────────────

REPO_DIR   = Path(__file__).parent.resolve()
CACHE_FILE = REPO_DIR / "espn_rosters_cache.json"


# ── name normalisation (must match ta_norm() in fetch_mlb_stats.py) ──────────
def _norm(name: str) -> str:
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return name.lower().replace(".", "").strip()


# ── fetch one league ──────────────────────────────────────────────────────────
def fetch_league(session, league_id: str, year: int, swid_inner: str) -> tuple:
    """Returns (league_name, teams_dict, my_team_norms_set)."""
    import requests

    url = (f"https://fantasy.espn.com/apis/v3/games/flb/seasons/{year}"
           f"/segments/0/leagues/{league_id}?view=mRoster&view=mTeam")
    resp = session.get(url, timeout=30)
    resp.raise_for_status()

    # Sanity-check: if ESPN returned HTML instead of JSON we're being blocked
    ct = resp.headers.get("Content-Type", "")
    if "html" in ct or resp.text.lstrip().startswith("<!"):
        raise ValueError(
            f"ESPN returned HTML (status {resp.status_code}). "
            "Are you running this on your home computer? "
            "Check that ESPN_S2 / ESPN_SWID are correct and not expired."
        )

    data       = resp.json()
    league_name = (data.get("settings") or {}).get("name") or f"League {league_id}"
    teams_out: dict = {}
    my_norms: set   = set()

    for team in data.get("teams", []):
        team_id   = team.get("id", 0)
        loc       = (team.get("location") or "").strip()
        nick      = (team.get("nickname") or "").strip()
        abbrev    = (team.get("abbrev") or "").strip()
        team_name = f"{loc} {nick}".strip() or abbrev or f"Team {team_id}"

        owners  = team.get("owners", [])
        is_mine = (
            any(o.strip("{}").upper() == swid_inner for o in owners)
            or team_name.lower() == MY_TEAM_NAME.lower()
        )

        players: list = []
        for entry in team.get("roster", {}).get("entries", []):
            try:
                full_name = (entry.get("playerPoolEntry", {})
                             .get("player", {})
                             .get("fullName", ""))
                if full_name:
                    players.append(_norm(full_name))
            except Exception:
                pass

        team_key = f"{league_id}_{team_id}"
        teams_out[team_key] = {
            "name":       team_name,
            "is_my_team": is_mine,
            "players":    players,
        }
        if is_mine and players:
            my_norms = set(players)

    return league_name, teams_out, my_norms


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    import requests

    swid    = ESPN_SWID.strip()
    s2_raw  = ESPN_S2.strip()
    ids_raw = ESPN_LEAGUE_IDS.strip()

    if not s2_raw:
        sys.exit(
            "ERROR: ESPN_S2 is empty.\n"
            "Set the ESPN_S2 environment variable or paste it into the CONFIG "
            "block at the top of this script."
        )

    # URL-decode the s2 if it arrived encoded (ESPN sometimes sends it that way)
    s2 = urllib.parse.unquote(s2_raw)
    swid_inner = swid.strip("{}").upper()
    league_ids = [s.strip() for s in ids_raw.split(",") if s.strip()]

    print(f"Fetching rosters for {len(league_ids)} league(s)…")

    session = requests.Session()
    session.cookies.set("espn_s2", s2,   domain=".espn.com", path="/")
    session.cookies.set("SWID",    swid, domain=".espn.com", path="/")
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":           "application/json",
        "X-Fantasy-Source": "kona",
        "X-Fantasy-Platform": "kona-PROD-m.5533.fantasy.x.011478067.0",
        "Referer": "https://fantasy.espn.com/",
    })

    leagues_data: dict = {}
    all_my_norms: set  = set()

    for lid in league_ids:
        try:
            league_name, teams, my_norms = fetch_league(session, lid, YEAR, swid_inner)
            leagues_data[lid] = {"league_name": league_name, "teams": teams}
            if my_norms:
                all_my_norms = my_norms
            n_mine = sum(1 for t in teams.values() if t["is_my_team"])
            print(f"  ✓  '{league_name}' (id={lid}): "
                  f"{len(teams)} teams, my team found={n_mine > 0}")
        except Exception as e:
            print(f"  ✗  League {lid} failed: {e}")

    if not leagues_data:
        sys.exit("No leagues fetched — cache NOT updated.")

    cache = {
        "year":          YEAR,
        "leagues":       leagues_data,
        "my_team_norms": sorted(all_my_norms),
    }
    CACHE_FILE.write_text(json.dumps(cache, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {CACHE_FILE.name}  "
          f"({len(all_my_norms)} players on my team)")

    if not AUTO_PUSH:
        print("AUTO_PUSH=False — skipping git commit.")
        return

    # ── git commit + push ─────────────────────────────────────────────────────
    print("\nCommitting and pushing…")
    try:
        subprocess.run(
            ["git", "-C", str(REPO_DIR), "add", "espn_rosters_cache.json"],
            check=True
        )
        result = subprocess.run(
            ["git", "-C", str(REPO_DIR), "diff", "--cached", "--quiet"],
        )
        if result.returncode == 0:
            print("Nothing to commit — roster unchanged since last update.")
            return
        subprocess.run(
            ["git", "-C", str(REPO_DIR), "commit",
             "-m", f"Update ESPN roster cache ({YEAR})"],
            check=True
        )
        subprocess.run(
            ["git", "-C", str(REPO_DIR), "push", "origin", "main"],
            check=True
        )
        print("✓  Pushed to GitHub — next daily build will use updated rosters.")
    except subprocess.CalledProcessError as e:
        print(f"Git error: {e}\nThe cache file was written locally but not pushed.")


if __name__ == "__main__":
    main()
