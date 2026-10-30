#!/usr/bin/env python3
"""Refresh espn_rosters.json from the ESPN fantasy baseball API.

Writes the same wrapper the bookmarklet produces:

    {"fetchedAt": ISO-8601, "leagueId": str, "seasonId": str, "raw": <API JSON>}

`parse_espn_rosters.py` and the standings tab both read that file. Standings
come from each team's `record` (overall and division) and `valuesByStat`,
which the mTeam / mStandings views return alongside mRoster and mSettings.

The request goes to lm-api-reads.fantasy.espn.com. That host returns league
JSON. The fantasy.espn.com website host does not: a plain client gets the
HTML app shell, and a browser-like client gets HTTP 202 with
`x-amzn-waf-action: challenge` (AWS WAF on CloudFront).

On any failure the previous espn_rosters.json is left untouched and the
process exits 0, so the daily dashboard build can continue.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

# Reads API. Do not switch this to https://fantasy.espn.com — that host is
# the website and answers with HTML or an AWS WAF challenge.
DEFAULT_API_BASE = "https://lm-api-reads.fantasy.espn.com"

# mRoster: players and lineup slots. mTeam: names plus record / valuesByStat.
# mSettings: scoring categories and divisions. mStandings: standings view
# (team records). Together these are what the roster parser and the
# standings tab consume.
VIEWS = ("mRoster", "mTeam", "mSettings", "mStandings")

# Browser-like headers. On lm-api-reads these still get a normal JSON
# response (probed 2026-10-30). x-fantasy-source / x-fantasy-platform match
# the headers ESPN's own web client and the write API use.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MAX_ATTEMPTS_DEFAULT = 4
REQUEST_TIMEOUT_SECONDS = 90

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "espn_rosters.json")
CONFIG_PATH = os.path.join(HERE, "config.py")


class FetchError(Exception):
    """A refresh failed. The output file must not be replaced."""


class AuthDenied(FetchError):
    """ESPN returned a JSON login / visibility error. Retrying will not help."""


class SeasonNotFound(Exception):
    """This season id is not published for the league."""


def warn(message: str) -> None:
    text = " ".join(str(message).split())
    print(f"WARNING: {text}", file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning::{text}", file=sys.stderr)


def _league_id_from_config() -> Optional[str]:
    """Read ESPN_LEAGUE_ID from config.py without importing it.

    Importing config.py installs packages and loads pybaseball.
    """
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=CONFIG_PATH)
    except OSError:
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "ESPN_LEAGUE_ID":
                value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, (str, int)):
                    return str(value.value).strip()
    return None


def _league_id_from_snapshot(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            snap = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    league_id = snap.get("leagueId")
    if league_id is None and isinstance(snap.get("raw"), dict):
        league_id = snap["raw"].get("id")
    if league_id is None:
        return None
    return str(league_id).strip() or None


def load_league_id(snapshot_path: str) -> str:
    env = os.environ.get("ESPN_LEAGUE_ID", "").strip()
    if env:
        return env
    from_config = _league_id_from_config()
    if from_config:
        return from_config
    from_file = _league_id_from_snapshot(snapshot_path)
    if from_file:
        return from_file
    raise FetchError(
        "No ESPN league id. Set ESPN_LEAGUE_ID in config.py or the environment."
    )


def candidate_seasons(today: date, explicit: Optional[str]) -> List[int]:
    """Season years to try, most sensible first.

    Current calendar year is the MLB / ESPN fantasy year. In Oct–Dec the
    current season is finishing and ESPN may already have opened next year.
    In Jan–Feb the new season is sometimes not published yet, so the
    previous year is the fallback. An explicit ESPN_SEASON is used alone.
    """
    if explicit and explicit.strip():
        return [int(explicit.strip())]
    year = today.year
    if today.month >= 10:
        return [year, year + 1, year - 1]
    if today.month <= 2:
        return [year, year - 1]
    return [year, year - 1]


def load_cookies() -> Dict[str, str]:
    """espn_s2 + SWID when both env vars are set. Otherwise unauthenticated."""
    s2 = os.environ.get("ESPN_S2", "").strip()
    swid = os.environ.get("ESPN_SWID", "").strip().strip('"')
    if bool(s2) ^ bool(swid):
        present = "ESPN_S2" if s2 else "ESPN_SWID"
        missing = "ESPN_SWID" if s2 else "ESPN_S2"
        warn(
            f"{present} is set but {missing} is empty. "
            "Requesting without cookies. Set both ESPN_S2 and ESPN_SWID."
        )
        return {}
    if not s2:
        return {}
    if swid.upper().startswith("%7B"):
        from urllib.parse import unquote
        swid = unquote(swid)
    if len(swid) == 36 and swid.count("-") == 4:
        swid = "{" + swid + "}"
    return {"espn_s2": s2, "SWID": swid}


def build_url(base: str, league_id: str, season: int) -> str:
    query = "&".join(f"view={view}" for view in VIEWS)
    return (
        f"{base.rstrip('/')}/apis/v3/games/flb/seasons/{season}"
        f"/segments/0/leagues/{league_id}?{query}"
    )


def request_headers(league_id: str, cookies: Dict[str, str]) -> Dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://fantasy.espn.com",
        "Referer": f"https://fantasy.espn.com/baseball/league?leagueId={league_id}",
        "x-fantasy-source": "kona",
        "x-fantasy-platform": "espn-fantasy-web",
    }
    if cookies:
        headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in cookies.items())
    return headers


def _header(headers, name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return ""


def _espn_error_detail(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    details = data.get("details") or []
    err_type = ""
    message = ""
    if details and isinstance(details[0], dict):
        err_type = str(details[0].get("type") or "")
        message = str(details[0].get("message") or "")
    if not message:
        messages = data.get("messages") or []
        if messages:
            message = str(messages[0])
    if err_type and message:
        return f"{err_type}: {message}"
    return err_type or message


def _is_html(headers, body: bytes) -> bool:
    ctype = _header(headers, "Content-Type").lower()
    if "text/html" in ctype or "application/xhtml" in ctype:
        return True
    sniff = body.lstrip()[:80].lower()
    return sniff.startswith(b"<!doctype") or sniff.startswith(b"<html")


def _is_waf_or_bot(status: int, headers, body: bytes) -> bool:
    if _header(headers, "x-amzn-waf-action"):
        return True
    if _is_html(headers, body):
        return True
    low = body[:800].lower()
    if b"cloudflare" in low or b"cf-browser-verification" in low or b"akamai" in low:
        return True
    # 202 with an empty body is the AWS WAF challenge seen on fantasy.espn.com.
    if status == 202:
        return True
    return False


def _is_auth_json(status: int, body: bytes) -> bool:
    if status not in (401, 403):
        return False
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeError):
        return False
    if not isinstance(data, dict):
        return False
    details = data.get("details") or []
    err_type = ""
    if details and isinstance(details[0], dict):
        err_type = str(details[0].get("type") or "")
    if err_type.startswith("AUTH_") or err_type in {"NOT_AUTHORIZED"}:
        return True
    messages = " ".join(str(m) for m in (data.get("messages") or [])).lower()
    return "not authorized" in messages or "unauthorized" in messages


def _is_not_found(status: int, body: bytes) -> bool:
    if status != 404:
        return False
    detail = _espn_error_detail(body)
    if not detail:
        return True
    return "NOT_FOUND" in detail or "Not Found" in detail


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_once(url: str, headers: Dict[str, str]) -> Tuple[int, dict, bytes]:
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp is not None else b""
        return exc.code, dict(exc.headers or {}), body


def _max_attempts() -> int:
    raw = os.environ.get("ESPN_MAX_ATTEMPTS", "").strip()
    if not raw:
        return MAX_ATTEMPTS_DEFAULT
    try:
        return max(1, int(raw))
    except ValueError:
        return MAX_ATTEMPTS_DEFAULT


def _backoff_seconds(retry_index: int) -> float:
    """Sleep before the next attempt. retry_index 0 is the first retry."""
    raw = os.environ.get("ESPN_RETRY_BASE_SECONDS", "").strip()
    try:
        base = float(raw) if raw else 2.0
    except ValueError:
        base = 2.0
    return base * (2 ** retry_index)


def fetch_season(base: str, league_id: str, season: int, cookies: Dict[str, str]) -> dict:
    """GET one season, retrying bot-block / network / 5xx responses."""
    url = build_url(base, league_id, season)
    headers = request_headers(league_id, cookies)
    attempts = _max_attempts()
    last_detail = "no response"
    for attempt in range(1, attempts + 1):
        status: Optional[int] = None
        resp_headers: dict = {}
        body = b""
        retryable = False
        try:
            status, resp_headers, body = _http_once(url, headers)
        except Exception as exc:  # URLError, timeout, socket errors
            last_detail = f"{type(exc).__name__}: {exc}"
            retryable = True
        else:
            detail = _espn_error_detail(body)
            if status == 200 and not _is_waf_or_bot(status, resp_headers, body):
                try:
                    data = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError as exc:
                    last_detail = f"HTTP 200 was not valid JSON ({exc})"
                    retryable = True
                else:
                    if not isinstance(data, dict):
                        raise FetchError(
                            f"Season {season}: ESPN returned {type(data).__name__}, not a league object."
                        )
                    return data
            elif _is_auth_json(status or 0, body):
                label = (detail or f"HTTP {status}").rstrip(".")
                raise AuthDenied(
                    f"Season {season}: HTTP {status} {label}. "
                    "This is a login requirement (JSON from the reads API), "
                    "not an AWS WAF / Cloudflare / Akamai challenge."
                )
            elif _is_not_found(status or 0, body):
                raise SeasonNotFound(f"Season {season} was not found (HTTP 404).")
            elif _is_waf_or_bot(status or 0, resp_headers, body):
                waf = _header(resp_headers, "x-amzn-waf-action")
                kind = f"AWS WAF challenge ({waf})" if waf else "HTML/bot response"
                last_detail = f"HTTP {status} {kind}"
                retryable = True
            elif status in (408, 429) or (status is not None and 500 <= status <= 599):
                last_detail = f"HTTP {status}"
                retryable = True
            elif status in (401, 403):
                # JSON auth errors already raised above. Anything else with
                # these statuses is treated as a challenge and retried.
                last_detail = f"HTTP {status} non-JSON response (possible bot block)"
                retryable = True
            elif status is not None and 300 <= status < 400:
                last_detail = f"HTTP {status} redirect"
                retryable = True
            else:
                extra = f" {detail}" if detail else ""
                raise FetchError(
                    f"Season {season}: HTTP {status}{extra}. Not retrying."
                )

        if attempt >= attempts or not retryable:
            break
        delay = _backoff_seconds(attempt - 1)
        print(
            f"  [ESPN] season {season} attempt {attempt}/{attempts}: {last_detail}. "
            f"Retrying in {delay:g}s."
        )
        time.sleep(delay)

    raise FetchError(
        f"Season {season}: gave up after {attempts} attempt(s): {last_detail}."
    )


def validate_league(data: dict, league_id: str) -> Optional[str]:
    """Return a problem string when this payload should not replace the file."""
    got = data.get("id")
    if got is not None and str(got) != str(league_id):
        return f"response league id {got} does not match {league_id}"
    teams = data.get("teams")
    if not isinstance(teams, list) or not teams:
        return "response has no teams"
    rostered = 0
    with_record = 0
    for team in teams:
        if not isinstance(team, dict):
            continue
        entries = (team.get("roster") or {}).get("entries") or []
        if entries:
            rostered += 1
        overall = (team.get("record") or {}).get("overall")
        if isinstance(overall, dict):
            with_record += 1
    if rostered == 0:
        return "response has teams but no roster entries"
    if with_record == 0:
        return "response has rosters but no team records, so standings would be blank"
    return None


def snapshot_document(league_id: str, season: int, raw: dict) -> dict:
    fetched = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {
        "fetchedAt": fetched,
        "leagueId": str(league_id),
        "seasonId": str(season),
        "raw": raw,
    }


def write_snapshot(path: str, document: dict) -> None:
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".espn_rosters.", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(document, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _summarize(document: dict) -> str:
    raw = document["raw"]
    teams = raw.get("teams") or []
    players = 0
    with_record = 0
    with_stats = 0
    for team in teams:
        players += len((team.get("roster") or {}).get("entries") or [])
        if isinstance((team.get("record") or {}).get("overall"), dict):
            with_record += 1
        if team.get("valuesByStat"):
            with_stats += 1
    return (
        f"league {document['leagueId']} season {document['seasonId']}: "
        f"{len(teams)} teams, {players} roster entries, "
        f"{with_record} team records, {with_stats} teams with category stats. "
        f"fetchedAt {document['fetchedAt']}"
    )


def refresh(out_path: str) -> str:
    league_id = load_league_id(out_path)
    explicit = os.environ.get("ESPN_SEASON")
    seasons = candidate_seasons(date.today(), explicit)
    cookies = load_cookies()
    base = os.environ.get("ESPN_API_BASE", DEFAULT_API_BASE).strip() or DEFAULT_API_BASE
    if "fantasy.espn.com" in base and "lm-api-reads.fantasy.espn.com" not in base:
        warn(
            "ESPN_API_BASE points at fantasy.espn.com. That website host returns "
            "HTML or an AWS WAF challenge (HTTP 202, x-amzn-waf-action: challenge) "
            "instead of league JSON. League reads belong on "
            "lm-api-reads.fantasy.espn.com."
        )
    auth_note = "with espn_s2 and SWID cookies" if cookies else "without cookies"
    print(
        f"  [ESPN] league {league_id}, seasons {seasons}, {auth_note}, "
        f"views {', '.join(VIEWS)}"
    )

    skipped: List[str] = []
    for season in seasons:
        try:
            data = fetch_season(base, league_id, season, cookies)
        except SeasonNotFound as exc:
            print(f"  [ESPN] {exc} Trying the next season.")
            skipped.append(str(exc))
            continue
        except AuthDenied as exc:
            cookie_help = (
                "ESPN_S2 and ESPN_SWID were sent and ESPN still refused the league. "
                "Refresh both cookies from a logged-in fantasy.espn.com session."
                if cookies
                else
                "No cookies were sent. This league is private. Add GitHub Actions "
                "secrets ESPN_S2 and ESPN_SWID (the espn_s2 and SWID cookie values "
                "from a logged-in ESPN session) and the next run will retry."
            )
            raise FetchError(f"{exc} {cookie_help}") from exc
        problem = validate_league(data, league_id)
        if problem:
            print(f"  [ESPN] season {season} not used: {problem}.")
            skipped.append(f"season {season}: {problem}")
            continue
        document = snapshot_document(league_id, season, data)
        write_snapshot(out_path, document)
        return _summarize(document)

    joined = " ".join(skipped) if skipped else "no season returned a league."
    raise FetchError(f"No usable ESPN snapshot. {joined}")


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    out_path = os.environ.get("ESPN_ROSTER_OUT", "").strip() or DEFAULT_OUT
    try:
        summary = refresh(out_path)
    except FetchError as exc:
        warn(
            f"{exc} Leaving {os.path.basename(out_path)} unchanged so the "
            "dashboard build can continue from the last good file."
        )
        return 0
    except Exception as exc:
        warn(
            f"Unexpected ESPN refresh error ({type(exc).__name__}: {exc}). "
            f"Leaving {os.path.basename(out_path)} unchanged so the dashboard "
            "build can continue from the last good file."
        )
        return 0
    print(f"  [ESPN] Wrote {out_path} — {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
