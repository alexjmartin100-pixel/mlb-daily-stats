#!/usr/bin/env python3
"""
update_espn_cache.py  –  Run this on YOUR LOCAL MACHINE to sync ESPN rosters.

FIRST RUN (one-time setup):
  A browser window opens. Log in to ESPN normally and complete any email
  verification. Once you land on the ESPN home page the script takes over,
  saves your cookies, fetches rosters, and pushes the cache to GitHub.

EVERY RUN AFTER THAT (automated via Task Scheduler):
  No browser window — loads the saved cookies silently and runs in the
  background. Only re-runs the browser if ESPN has logged you out.

SETUP:
  No environment variables needed beyond what's already in the CONFIG block.
  Just run:  python update_espn_cache.py
"""

import json
import os
import subprocess
import sys
import unicodedata
import urllib.parse
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────────────
ESPN_LEAGUE_IDS = os.environ.get("ESPN_LEAGUE_IDS", "2081322885")
MY_TEAM_NAME    = os.environ.get("MY_TEAM_NAME",    "team alex")
YEAR            = 2026
AUTO_PUSH       = True
# ─────────────────────────────────────────────────────────────────────────────

REPO_DIR     = Path(__file__).parent.resolve()
CACHE_FILE   = REPO_DIR / "espn_rosters_cache.json"
COOKIES_FILE = REPO_DIR / "espn_cookies.json"   # gitignored — stays local


def _norm(name: str) -> str:
    """Match ta_norm() in fetch_mlb_stats.py."""
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return name.lower().replace(".", "").strip()


def do_browser_login() -> list:
    """
    Opens a headed Chromium browser, waits for the user to log in to ESPN
    (including any email verification), navigates to the fantasy home page
    to trigger all auth cookies, then returns the cookie list.
    """
    from playwright.sync_api import sync_playwright

    print("\n" + "=" * 60)
    print("  FIRST-TIME SETUP")
    print("  A browser window will open — log in to ESPN as normal.")
    print("  Complete any email/2FA verification ESPN asks for.")
    print("  Once you are on the ESPN home page the script continues.")
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

        print("Waiting for you to log in…\n")
        page.wait_for_url(
            lambda url: "login" not in url and "espn.com" in url,
            timeout=300_000   # 5 minutes to log in + verify email
        )
        print("  ✓ Login detected — loading fantasy page to collect cookies…")
        page.wait_for_timeout(2_000)

        # Navigate to fantasy home so ESPN sets the fantasy-specific cookies
        page.goto("https://fantasy.espn.com/baseball/",
                  wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(3_000)

        cookies = ctx.cookies()
        browser.close()

    return cookies


def get_cookies() -> list:
    """
    Returns a fresh cookie list: loads from file if it exists,
    otherwise triggers the browser login flow and saves the result.
    """
    if COOKIES_FILE.exists():
        return json.loads(COOKIES_FILE.read_text(encoding="utf-8"))

    cookies = do_browser_login()
    COOKIES_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
    print(f"  ✓ Cookies saved to {COOKIES_FILE.name}")
    return cookies


def build_requests_session(cookies: list):
    """Builds a requests.Session pre-loaded with the ESPN cookies."""
    import requests

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":             "application/json",
        "X-Fantasy-Source":   "kona",
        "X-Fantasy-Platform": "kona-PROD-m.5533.fantasy.x.011478067.0",
        "Referer":            "https://fantasy.espn.com/",
        "Origin":             "https://fantasy.espn.com",
    })
    for c in cookies:
        # URL-decode the value in case it arrived encoded
        value = urllib.parse.unquote(c["value"])
        session.cookies.set(c["name"], value, domain=c.get("domain", ".espn.com"))
    return session


def fetch_leagues(session, league_ids: list, year: int, cookies: list) -> tuple:
    """
    Calls the ESPN API with a plain requests.Session (no JS, no CSP issues).
    Returns (leagues_data, my_team_norms).  If cookies are expired, raises
    ValueError so the caller can delete them and re-login.
    """
    swid_inner = ""
    for c in cookies:
        if c["name"] == "SWID":
            swid_inner = c["value"].strip("{}").upper()
            break

    leagues_data: dict = {}
    my_team_norms: set = set()

    for lid in league_ids:
        url = (
            f"https://fantasy.espn.com/apis/v3/games/flb"
            f"/seasons/{year}/segments/0/leagues/{lid}"
            f"?view=mRoster&view=mTeam"
        )
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()

            ct = resp.headers.get("Content-Type", "")
            if "html" in ct or resp.text.lstrip().startswith("<!"):
                raise ValueError("EXPIRED")

            data        = resp.json()
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

        except ValueError as e:
            if str(e) == "EXPIRED":
                raise   # let main() catch this and re-login
            print(f"  ✗ League {lid} failed: {e}")
        except Exception as e:
            print(f"  ✗ League {lid} failed: {e}")

    return leagues_data, my_team_norms


def main():
    league_ids = [s.strip() for s in ESPN_LEAGUE_IDS.split(",") if s.strip()]
    print(f"ESPN roster sync — {len(league_ids)} league(s), year={YEAR}")

    # Try with saved cookies first; if they've expired, re-login and try once more
    for attempt in range(2):
        cookies = get_cookies()
        session = build_requests_session(cookies)
        try:
            leagues_data, my_team_norms = fetch_leagues(
                session, league_ids, YEAR, cookies
            )
            break   # success
        except ValueError:
            # Cookies expired — delete them and loop to trigger browser login
            COOKIES_FILE.unlink(missing_ok=True)
            if attempt == 0:
                print("  Cookies expired — re-opening browser for fresh login…")
            else:
                sys.exit("Login failed twice in a row. Please try again.")
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
