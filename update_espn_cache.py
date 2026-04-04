#!/usr/bin/env python3
"""
update_espn_cache.py  –  Run this on YOUR LOCAL MACHINE to sync ESPN rosters.

FIRST RUN (one-time setup):
  A Chrome window opens. Log in to ESPN normally — complete any email
  verification ESPN asks for. Once you're logged in the script takes over,
  saves your session, fetches rosters, and pushes the cache to GitHub.
  You won't need to log in again unless ESPN logs you out.

EVERY RUN AFTER THAT (automated via Task Scheduler):
  No browser window, no login — loads the saved session silently and runs
  in the background.

SETUP:
  Set two Windows environment variables once
  (search "Edit environment variables for your account" in Start menu):
    ESPN_LEAGUE_IDS   2081322885
    MY_TEAM_NAME      team alex
  Then run:  python update_espn_cache.py
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


def fetch_rosters(league_ids: list, year: int) -> tuple:
    """
    Opens Chromium with a saved session (headless) or prompts for first-time
    login (headed).  Returns (leagues_data dict, my_team_norms set).
    """
    from playwright.sync_api import sync_playwright

    first_time = not SESSION_FILE.exists()

    if first_time:
        print("\n" + "=" * 60)
        print("  FIRST-TIME SETUP")
        print("  A browser window will open — log in to ESPN as normal.")
        print("  Complete any email/2FA verification ESPN asks for.")
        print("  Once you're on the ESPN home page the script continues.")
        print("=" * 60 + "\n")

    leagues_data: dict = {}
    my_team_norms: set = set()

    with sync_playwright() as pw:
        if first_time:
            # Headed so the user can see the login page and interact with it
            browser = pw.chromium.launch(headless=False)
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
        else:
            # Headless — load the saved session, no login needed
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

        # ── First-time: navigate to login and wait for user to finish ─────
        if first_time:
            page.goto("https://www.espn.com/login", wait_until="domcontentloaded",
                      timeout=30_000)
            print("Waiting for you to log in… (the script continues once you")
            print("are on the ESPN home or fantasy page)\n")
            # Wait until the URL no longer contains "login" — i.e. login succeeded
            page.wait_for_url(
                lambda url: "login" not in url and "espn.com" in url,
                timeout=180_000   # 3 minutes to complete login + email verification
            )
            print("  ✓ Login detected — saving session…")
            # Give the page a moment to settle and set all auth cookies
            page.wait_for_timeout(2_000)

        # ── Navigate to fantasy home so ESPN sets all session cookies ────────
        page.goto("https://fantasy.espn.com/baseball/",
                  wait_until="networkidle", timeout=45_000)
        page.wait_for_timeout(2_000)

        # ── Save / refresh the session state ──────────────────────────────
        ctx.storage_state(path=str(SESSION_FILE))
        if first_time:
            print(f"  ✓ Session saved to {SESSION_FILE.name}")

        # ── Read SWID from the live cookie jar ────────────────────────────
        swid_inner = ""
        for cookie in ctx.cookies():
            if cookie["name"] == "SWID":
                swid_inner = cookie["value"].strip("{}").upper()
                break

        # ── Fetch each league via fetch() inside the authenticated page ───
        # page.goto() to an API endpoint looks like a browser navigation and
        # ESPN redirects it to the web app. fetch() from inside the page looks
        # like the ESPN web app's own XHR calls, which ESPN answers with JSON.
        for lid in league_ids:
            api_url = (
                f"https://fantasy.espn.com/apis/v3/games/flb"
                f"/seasons/{year}/segments/0/leagues/{lid}"
                f"?view=mRoster&view=mTeam"
            )
            try:
                js = f"""
async () => {{
    const r = await fetch({json.dumps(api_url)}, {{
        credentials: 'include',
        headers: {{
            'Accept': 'application/json',
            'X-Fantasy-Source': 'kona',
            'X-Fantasy-Platform': 'kona-PROD-m.5533.fantasy.x.011478067.0'
        }}
    }});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
}}
"""
                data = page.evaluate(js)

                # Detect if ESPN returned an error object instead of league data
                if not isinstance(data, dict) or "teams" not in data:
                    SESSION_FILE.unlink(missing_ok=True)
                    raise ValueError(
                        f"Unexpected response (no 'teams' key): {str(data)[:200]}\n"
                        f"  Deleted {SESSION_FILE.name} — run again to re-login."
                    )

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
    league_ids = [s.strip() for s in ESPN_LEAGUE_IDS.split(",") if s.strip()]
    mode = "first-time login" if not SESSION_FILE.exists() else "saved session"
    print(f"ESPN roster sync — {len(league_ids)} league(s), mode={mode}")

    leagues_data, my_team_norms = fetch_rosters(league_ids, YEAR)

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
        diff = subprocess.run(["git", "-C", str(REPO_DIR),
                                "diff", "--cached", "--quiet"])
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
