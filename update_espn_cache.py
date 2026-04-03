#!/usr/bin/env python3
"""
update_espn_cache.py  –  Run this on YOUR LOCAL MACHINE to sync ESPN rosters.

Logs into ESPN with your username/password (via a real Chromium browser),
fetches every team's roster for each of your leagues, writes
espn_rosters_cache.json, then commits and pushes it so the daily GitHub
Actions build can read it without touching ESPN's API at all.

HOW TO AUTOMATE:
  Set it up once in Windows Task Scheduler pointing at run_espn_update.bat.
  Weekly is plenty — only re-run sooner if you make a big roster move.

CREDENTIALS (set once as Windows environment variables):
  ESPN_USERNAME     your ESPN login email
  ESPN_PASSWORD     your ESPN password
  ESPN_LEAGUE_IDS   2081322885  (comma-separated if you have multiple)
  MY_TEAM_NAME      team alex   (optional, used as fallback match)
"""

import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────────────
ESPN_USERNAME   = os.environ.get("ESPN_USERNAME",   "")
ESPN_PASSWORD   = os.environ.get("ESPN_PASSWORD",   "")
ESPN_LEAGUE_IDS = os.environ.get("ESPN_LEAGUE_IDS", "2081322885")
MY_TEAM_NAME    = os.environ.get("MY_TEAM_NAME",    "team alex")
YEAR            = 2026
AUTO_PUSH       = True
# ─────────────────────────────────────────────────────────────────────────────

REPO_DIR   = Path(__file__).parent.resolve()
CACHE_FILE = REPO_DIR / "espn_rosters_cache.json"


def _norm(name: str) -> str:
    """Match ta_norm() in fetch_mlb_stats.py."""
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return name.lower().replace(".", "").strip()


def login_and_fetch(league_ids: list, year: int) -> tuple:
    """
    Opens a real Chromium browser, logs into ESPN, then navigates directly
    to each league's API endpoint and reads the JSON from the page body.
    Returns (leagues_data, my_team_norms).
    """
    from playwright.sync_api import sync_playwright

    leagues_data: dict = {}
    my_team_norms: set = set()

    with sync_playwright() as pw:
        # Run headed (visible) so ESPN's login page renders properly.
        # Change headless=False → True once you've confirmed it works.
        browser = pw.chromium.launch(headless=False)
        ctx     = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = ctx.new_page()

        # ── Step 1: log in ────────────────────────────────────────────────
        print("  Navigating to ESPN login…")
        page.goto("https://www.espn.com/login", wait_until="domcontentloaded",
                  timeout=30_000)

        # ESPN login is inside an iframe
        try:
            frame = page.frame_locator('iframe[name="disneyid-iframe"]')
            frame.locator('input[type="email"], input[name="loginValue"]').fill(
                ESPN_USERNAME, timeout=10_000
            )
            frame.locator('button[type="submit"], button:has-text("Continue")').click(
                timeout=10_000
            )
            frame.locator('input[type="password"]').fill(
                ESPN_PASSWORD, timeout=10_000
            )
            frame.locator('button[type="submit"], button:has-text("Log In")').click(
                timeout=10_000
            )
            # Wait for redirect back to espn.com after successful login
            page.wait_for_url("**/espn.com/**", timeout=20_000)
            print("  ✓ Logged in")
        except Exception as e:
            print(f"  Login step error: {e}")
            print("  Attempting to continue anyway (may already be logged in)…")

        # ── Step 2: fetch each league ─────────────────────────────────────
        swid_raw = ""
        for cookie in ctx.cookies():
            if cookie["name"] == "SWID":
                swid_raw = cookie["value"]
                break
        swid_inner = swid_raw.strip("{}").upper()

        for lid in league_ids:
            api_url = (
                f"https://fantasy.espn.com/apis/v3/games/flb"
                f"/seasons/{year}/segments/0/leagues/{lid}"
                f"?view=mRoster&view=mTeam"
            )
            try:
                page.goto(api_url, wait_until="domcontentloaded", timeout=30_000)
                raw = page.evaluate("document.body.innerText")

                if raw.lstrip().startswith("<") or "Skip to main content" in raw:
                    raise ValueError(
                        "Got HTML instead of JSON — login may have failed. "
                        "Try running with headless=False to watch what happens."
                    )

                data        = json.loads(raw)
                league_name = (data.get("settings") or {}).get("name") or f"League {lid}"
                teams_out: dict = {}

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
                            full_name = (
                                entry.get("playerPoolEntry", {})
                                .get("player", {})
                                .get("fullName", "")
                            )
                            if full_name:
                                players.append(_norm(full_name))
                        except Exception:
                            pass

                    team_key = f"{lid}_{team_id}"
                    teams_out[team_key] = {
                        "name":       team_name,
                        "is_my_team": is_mine,
                        "players":    players,
                    }
                    if is_mine and players:
                        my_team_norms = set(players)

                leagues_data[lid] = {"league_name": league_name, "teams": teams_out}
                n_mine = sum(1 for t in teams_out.values() if t["is_my_team"])
                print(f"  ✓ '{league_name}' (id={lid}): "
                      f"{len(teams_out)} teams, my team found={n_mine > 0}")

            except Exception as e:
                print(f"  ✗ League {lid} failed: {e}")

        browser.close()

    return leagues_data, my_team_norms


def main():
    if not ESPN_USERNAME or not ESPN_PASSWORD:
        sys.exit(
            "ERROR: ESPN_USERNAME or ESPN_PASSWORD is empty.\n\n"
            "Set them as Windows environment variables:\n"
            "  Search 'Edit environment variables for your account' in Start\n"
            "  Add ESPN_USERNAME = your ESPN email\n"
            "  Add ESPN_PASSWORD = your ESPN password\n\n"
            "Then close and reopen PowerShell and run again."
        )

    league_ids = [s.strip() for s in ESPN_LEAGUE_IDS.split(",") if s.strip()]
    print(f"Fetching rosters for {len(league_ids)} league(s) (year={YEAR})…")

    leagues_data, my_team_norms = login_and_fetch(league_ids, YEAR)

    if not leagues_data:
        sys.exit("No leagues fetched — cache NOT updated.")

    cache = {
        "year":          YEAR,
        "leagues":       leagues_data,
        "my_team_norms": sorted(my_team_norms),
    }
    CACHE_FILE.write_text(json.dumps(cache, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {CACHE_FILE.name}  "
          f"({len(my_team_norms)} players on my team)")

    if not AUTO_PUSH:
        print("AUTO_PUSH=False — skipping git commit.")
        return

    print("\nCommitting and pushing…")
    try:
        subprocess.run(["git", "-C", str(REPO_DIR), "add", "espn_rosters_cache.json"],
                       check=True)
        diff = subprocess.run(["git", "-C", str(REPO_DIR), "diff", "--cached", "--quiet"])
        if diff.returncode == 0:
            print("Nothing to commit — roster unchanged since last update.")
            return
        subprocess.run(
            ["git", "-C", str(REPO_DIR), "commit",
             "-m", f"Update ESPN roster cache ({YEAR})"],
            check=True
        )
        subprocess.run(["git", "-C", str(REPO_DIR), "push", "origin", "main"],
                       check=True)
        print("✓  Pushed — next daily build will use updated rosters.")
    except subprocess.CalledProcessError as e:
        print(f"Git error: {e}\nCache written locally but not pushed.")


if __name__ == "__main__":
    main()
