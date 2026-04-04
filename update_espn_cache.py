#!/usr/bin/env python3
"""
update_espn_cache.py  –  Run this on YOUR LOCAL MACHINE to sync ESPN rosters.

FIRST RUN (one-time setup):
  A browser window opens. Log in to ESPN normally and complete any email
  verification. Once you're on the ESPN home page hit Enter in this window
  and the script takes over.

EVERY RUN AFTER THAT (automated via Task Scheduler):
  No browser window — loads your saved session silently in the background.
  Only re-opens the browser if ESPN has logged you out.

SETUP: just run  python update_espn_cache.py
"""

import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────────────
ESPN_LEAGUE_IDS = os.environ.get("ESPN_LEAGUE_IDS", "2081322885")
MY_TEAM_NAME    = os.environ.get("MY_TEAM_NAME",    "team alex")
YEAR            = 2026
AUTO_PUSH       = True
# ─────────────────────────────────────────────────────────────────────────────

REPO_DIR      = Path(__file__).parent.resolve()
CACHE_FILE    = REPO_DIR / "espn_rosters_cache.json"
SESSION_FILE  = REPO_DIR / "espn_session.json"   # gitignored — stays local


def _norm(name: str) -> str:
    """Match ta_norm() in fetch_mlb_stats.py."""
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return name.lower().replace(".", "").strip()


def do_login_and_save_session() -> None:
    """Opens a headed browser, waits for the user to log in, saves the session."""
    from playwright.sync_api import sync_playwright

    print("\n" + "=" * 60)
    print("  A browser window will open.")
    print("  Log in to ESPN and complete any email verification.")
    print("  Then come back here and press Enter.")
    print("=" * 60 + "\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = ctx.new_page()
        page.goto("https://www.espn.com/login",
                  wait_until="domcontentloaded", timeout=30_000)

        input("  Press Enter once you are logged in to ESPN… ")

        # Navigate to fantasy home to pick up all fantasy cookies
        print("  Loading fantasy page to collect session cookies…")
        page.goto("https://fantasy.espn.com/baseball/",
                  wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(3_000)

        ctx.storage_state(path=str(SESSION_FILE))
        browser.close()

    print(f"  ✓ Session saved.\n")


def fetch_rosters(league_ids: list, year: int) -> tuple:
    """
    Loads the saved session (headless), navigates to each league's teams page,
    and INTERCEPTS the API responses that the ESPN app makes automatically.
    No manual fetch/XHR — the real browser makes the real requests.
    Returns (leagues_data, my_team_norms).
    """
    from playwright.sync_api import sync_playwright

    leagues_data: dict = {}
    my_team_norms: set = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            storage_state=str(SESSION_FILE),
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = ctx.new_page()

        # Read SWID from session cookies
        swid_inner = ""
        for c in ctx.cookies():
            if c["name"] == "SWID":
                swid_inner = c["value"].strip("{}").upper()
                break

        for lid in league_ids:
            captured: dict = {}

            # ── Set up response interceptor BEFORE navigating ─────────────
            def on_response(response, _lid=lid):
                url = response.url
                if (f"/leagues/{_lid}" in url and
                        "flb" in url and
                        response.status == 200):
                    try:
                        body = response.json()
                        if not isinstance(body, dict):
                            return
                        if "teams" not in body:
                            return
                        # Prefer a response that includes roster entries
                        has_roster = any(
                            bool(t.get("roster", {}).get("entries"))
                            for t in body.get("teams", [])
                        )
                        if has_roster or "data" not in captured:
                            captured["data"] = body
                    except Exception:
                        pass

            page.on("response", on_response)

            try:
                # The "Teams" page loads all rosters — triggers mRoster API calls
                teams_url = (f"https://fantasy.espn.com/baseball/teams"
                             f"?leagueId={lid}")
                page.goto(teams_url, wait_until="networkidle", timeout=60_000)
                page.wait_for_timeout(3_000)

                # If teams page didn't give us roster data, try the league page
                if "data" not in captured or not any(
                    bool(t.get("roster", {}).get("entries"))
                    for t in captured.get("data", {}).get("teams", [])
                ):
                    league_url = (f"https://fantasy.espn.com/baseball/league"
                                  f"?leagueId={lid}")
                    page.goto(league_url, wait_until="networkidle", timeout=60_000)
                    page.wait_for_timeout(3_000)

                if "data" not in captured:
                    # Might be a session expiry — check if we got redirected to login
                    if "login" in page.url:
                        raise ValueError("SESSION_EXPIRED")
                    raise ValueError(
                        f"No API response captured for league {lid}. "
                        "The ESPN app may not have loaded roster data on these pages."
                    )

                data        = captured["data"]
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
                total_players = sum(len(t["players"]) for t in teams_out.values())
                print(f"  ✓ '{league_name}' (id={lid}): "
                      f"{len(teams_out)} teams, {total_players} players, "
                      f"my team found={n_mine > 0}")

            except ValueError as e:
                if str(e) == "SESSION_EXPIRED":
                    browser.close()
                    raise   # bubble up so main() can re-login
                print(f"  ✗ League {lid} failed: {e}")
            except Exception as e:
                print(f"  ✗ League {lid} failed: {e}")
            finally:
                page.remove_listener("response", on_response)

        browser.close()

    return leagues_data, my_team_norms


def main():
    league_ids = [s.strip() for s in ESPN_LEAGUE_IDS.split(",") if s.strip()]

    for attempt in range(2):
        if not SESSION_FILE.exists():
            do_login_and_save_session()

        print(f"Fetching rosters — {len(league_ids)} league(s), year={YEAR}…")
        try:
            leagues_data, my_team_norms = fetch_rosters(league_ids, YEAR)
            break
        except ValueError:
            SESSION_FILE.unlink(missing_ok=True)
            if attempt == 0:
                print("  Session expired — re-opening browser for fresh login…")
            else:
                sys.exit("Login failed twice. Please try again.")
    else:
        sys.exit("\nNo leagues fetched — cache NOT updated.")

    if not leagues_data:
        sys.exit("\nNo leagues fetched — cache NOT updated.")

    cache = {
        "year":          YEAR,
        "leagues":       leagues_data,
        "my_team_norms": sorted(my_team_norms),
    }
    CACHE_FILE.write_text(json.dumps(cache, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {CACHE_FILE.name}  ({len(my_team_norms)} players on my team)")

    if not AUTO_PUSH:
        print("AUTO_PUSH=False — skipping git commit.")
        return

    print("\nCommitting and pushing…")
    try:
        subprocess.run(["git", "-C", str(REPO_DIR), "add", "espn_rosters_cache.json"],
                       check=True)
        diff = subprocess.run(
            ["git", "-C", str(REPO_DIR), "diff", "--cached", "--quiet"]
        )
        if diff.returncode == 0:
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
        print("✓  Pushed — next daily build will use updated rosters.")
    except subprocess.CalledProcessError as e:
        print(f"Git error: {e}\nCache written locally but not pushed.")


if __name__ == "__main__":
    main()
