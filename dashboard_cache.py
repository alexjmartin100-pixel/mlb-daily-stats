"""Saved dashboard stats for days with no MLB games.

A game-day run of fetch_mlb_stats.py writes dashboard_stats_cache.json
(full-fidelity inputs to the HTML renderer). A no-game run prefers that
file. If it is missing or unreadable, the previous mlb_daily_stats.html
is parsed for the same inputs. If neither source can be read, the caller
rebuilds from ESPN rosters alone.
"""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "dashboard_stats_cache.json")
HTML_PATH = os.path.join(HERE, "mlb_daily_stats.html")
CACHE_VERSION = 1

_ASSIGN_RE_TMPL = r"(?:const|let|var)\s+%s\s*=\s*"
_TITLE_RE = re.compile(r"<title>MLB Daily Stats · ([^<]+)</title>")
_GAMES_RE = re.compile(r"(\d+)\s+game\(s\)")


def empty_saved_stats(date_display: str) -> dict:
    """Stats payload that still lets the renderer emit roster/standings HTML."""
    return {
        "version": CACHE_VERSION,
        "source": "empty",
        "game_date": "",
        "date_display": date_display,
        "n_games": 0,
        "hitters": [],
        "all_pitchers": [],
        "ta_hitters": [],
        "ta_starters": [],
        "ta_relievers": [],
        "lb_data": [],
        "lb_pitch_data": {"starters": [], "relievers": []},
        "fantasy_data": {"fut_h": [], "fut_p": []},
        "all_mlb_players": [],
    }


def save_dashboard_cache(payload: dict, path: str | None = None) -> str:
    """Atomically write a game-day stats snapshot. Raises on I/O failure."""
    path = path or CACHE_PATH
    body = {
        "version": CACHE_VERSION,
        "source": "build",
        "game_date": payload.get("game_date") or "",
        "date_display": payload.get("date_display") or "",
        "n_games": int(payload.get("n_games") or 0),
        "hitters": payload.get("hitters") or [],
        "all_pitchers": payload.get("all_pitchers") or [],
        "ta_hitters": payload.get("ta_hitters") or [],
        "ta_starters": payload.get("ta_starters") or [],
        "ta_relievers": payload.get("ta_relievers") or [],
        "lb_data": payload.get("lb_data") or [],
        "lb_pitch_data": payload.get("lb_pitch_data") or {},
        "fantasy_data": payload.get("fantasy_data") or {},
        "all_mlb_players": payload.get("all_mlb_players") or [],
    }
    cleaned = _json_clean(body)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cleaned, fh, separators=(",", ":"), allow_nan=False)
    os.replace(tmp, path)
    return path


def resolve_saved_stats(cache_path: str | None = None,
                        html_path: str | None = None) -> dict | None:
    """Return the best previously saved stats, or None when nothing is usable.

    Preference: a game-day cache file, then JSON embedded in the last
    dashboard HTML, then any other readable cache file. Never raises.
    """
    cache_path = cache_path or CACHE_PATH
    html_path = html_path or HTML_PATH

    cache = _read_cache_file(cache_path)
    if cache and cache.get("source") == "build" and _usable(cache):
        return cache

    extracted = _extract_html_cache(html_path)
    if not (extracted and _usable(extracted)):
        extracted = _extract_split_json()
    if extracted and _usable(extracted):
        return extracted

    if cache and _usable(cache):
        return cache
    return None


def _read_cache_file(path: str) -> dict | None:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _usable(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    if not isinstance(data.get("hitters"), list):
        return False
    if not isinstance(data.get("all_pitchers"), list):
        return False
    fantasy = data.get("fantasy_data")
    if fantasy is not None and not isinstance(fantasy, dict):
        return False
    return True


def _read_json_file(path: str):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def _extract_split_json() -> dict | None:
    """Read tab JSON written by a previous build.

    data/saved-stats.json is the full renderer input. The public tab files
    are the fallback when that snapshot is missing: hitters and leaderboards
    are complete, and fantasy rows are rebuilt from the trade pool.
    """
    data_dir = os.path.join(HERE, "data")
    saved = _read_json_file(os.path.join(data_dir, "saved-stats.json"))
    if isinstance(saved, dict) and _usable(saved):
        saved = dict(saved)
        saved["source"] = "json"
        saved.setdefault("version", CACHE_VERSION)
        return saved

    gamelog = _read_json_file(os.path.join(data_dir, "gamelog.json")) or {}
    board = _read_json_file(os.path.join(data_dir, "leaderboard.json")) or {}
    meta = _read_json_file(os.path.join(data_dir, "meta.json")) or {}
    fantasy_file = _read_json_file(os.path.join(data_dir, "fantasy.json")) or {}
    if not isinstance(gamelog, dict):
        gamelog = {}
    if not isinstance(board, dict):
        board = {}
    if not isinstance(meta, dict):
        meta = {}
    if not isinstance(fantasy_file, dict):
        fantasy_file = {}

    hitters = gamelog.get("hitters") if isinstance(gamelog.get("hitters"), list) else []
    all_pitchers = gamelog.get("pitchers") if isinstance(gamelog.get("pitchers"), list) else []
    lb_data = board.get("hitters") if isinstance(board.get("hitters"), list) else []
    lb_sp = board.get("starters") if isinstance(board.get("starters"), list) else []
    lb_rp = board.get("relievers") if isinstance(board.get("relievers"), list) else []
    all_mlb = meta.get("all_mlb_players") if isinstance(meta.get("all_mlb_players"), list) else []
    trade_h = fantasy_file.get("hitters") if isinstance(fantasy_file.get("hitters"), list) else []
    trade_p = fantasy_file.get("pitchers") if isinstance(fantasy_file.get("pitchers"), list) else []
    if not hitters and not lb_data and not trade_h and not trade_p:
        return None

    try:
        n_games = int(meta.get("n_games") or 0)
    except (TypeError, ValueError):
        n_games = 0
    return {
        "version": CACHE_VERSION,
        "source": "json",
        "game_date": "",
        "date_display": meta.get("date_display") or "",
        "n_games": n_games,
        "hitters": hitters,
        "all_pitchers": all_pitchers,
        "ta_hitters": meta.get("ta_hitters") if isinstance(meta.get("ta_hitters"), list) else [],
        "ta_starters": meta.get("ta_starters") if isinstance(meta.get("ta_starters"), list) else [],
        "ta_relievers": meta.get("ta_relievers") if isinstance(meta.get("ta_relievers"), list) else [],
        "lb_data": lb_data,
        "lb_pitch_data": {"starters": lb_sp, "relievers": lb_rp},
        "fantasy_data": _fantasy_from_trade_pool(trade_h, trade_p, lb_data, lb_sp, lb_rp, all_mlb),
        "all_mlb_players": all_mlb,
    }


def _extract_html_cache(path: str) -> dict | None:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            html = fh.read()
    except Exception:
        return None
    if "const HITTERS" not in html and "var TRADE_HITTERS" not in html:
        return None

    hitters = _js_json(html, "HITTERS") or []
    all_pitchers = _js_json(html, "ALL_PITCHERS") or []
    ta_hitters = _js_json(html, "TA_HITTERS") or []
    ta_starters = _js_json(html, "TA_STARTERS") or []
    ta_relievers = _js_json(html, "TA_RELIEVERS") or []
    lb_data = _js_json(html, "LB_ALL") or []
    lb_sp = _js_json(html, "LB_SP_ALL") or []
    lb_rp = _js_json(html, "LB_RP_ALL") or []
    all_mlb_players = _js_json(html, "ALL_MLB_PLAYERS") or []
    trade_h = _js_json(html, "TRADE_HITTERS")
    trade_p = _js_json(html, "TRADE_PITCHERS")

    if not isinstance(hitters, list):
        hitters = []
    if not isinstance(all_pitchers, list):
        all_pitchers = []
    if not isinstance(lb_data, list):
        lb_data = []
    if not isinstance(lb_sp, list):
        lb_sp = []
    if not isinstance(lb_rp, list):
        lb_rp = []
    if not isinstance(all_mlb_players, list):
        all_mlb_players = []
    if not isinstance(ta_hitters, list):
        ta_hitters = []
    if not isinstance(ta_starters, list):
        ta_starters = []
    if not isinstance(ta_relievers, list):
        ta_relievers = []

    fantasy = {"fut_h": [], "fut_p": []}
    if isinstance(trade_h, list) or isinstance(trade_p, list):
        fantasy = _fantasy_from_trade_pool(
            trade_h if isinstance(trade_h, list) else [],
            trade_p if isinstance(trade_p, list) else [],
            lb_data, lb_sp, lb_rp, all_mlb_players,
        )

    title = _TITLE_RE.search(html)
    date_display = title.group(1).strip() if title else ""
    games = _GAMES_RE.search(html)
    try:
        n_games = int(games.group(1)) if games else 0
    except (TypeError, ValueError):
        n_games = 0

    if not hitters and not lb_data and not fantasy.get("fut_h") and not fantasy.get("fut_p"):
        return None

    return {
        "version": CACHE_VERSION,
        "source": "html",
        "game_date": _parse_display_date(date_display) or "",
        "date_display": date_display,
        "n_games": n_games,
        "hitters": hitters,
        "all_pitchers": all_pitchers,
        "ta_hitters": ta_hitters,
        "ta_starters": ta_starters,
        "ta_relievers": ta_relievers,
        "lb_data": lb_data,
        "lb_pitch_data": {"starters": lb_sp, "relievers": lb_rp},
        "fantasy_data": fantasy,
        "all_mlb_players": all_mlb_players,
    }


def _js_json(html: str, name: str):
    match = re.search(_ASSIGN_RE_TMPL % re.escape(name), html)
    if not match:
        return None
    try:
        value, _end = json.JSONDecoder().raw_decode(html, match.end())
    except Exception:
        return None
    return value


def _parse_display_date(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%A, %B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _name_keys(name: str) -> set:
    raw = (name or "").strip()
    if not raw:
        return set()
    ascii_name = unicodedata.normalize("NFKD", raw)
    ascii_name = "".join(c for c in ascii_name if not unicodedata.combining(c))
    keys = set()
    for variant in (raw, ascii_name):
        keys.add(variant.lower())
        keys.add(variant.lower().replace(".", "").replace("-", " ").strip())
    return {k for k in keys if k}


def _index_by_name(rows: list, value_fn) -> dict:
    out = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        value = value_fn(row)
        if value is None:
            continue
        for key in _name_keys(row.get("name") or ""):
            out.setdefault(key, value)
    return out


def _lookup_name(index: dict, name: str):
    for key in _name_keys(name):
        if key in index:
            return index[key]
    return None


def _fantasy_from_trade_pool(trade_h: list, trade_p: list,
                             lb_data: list, lb_sp: list, lb_rp: list,
                             all_mlb_players: list) -> dict:
    """Rebuild fut_h / fut_p from the trade-pool JSON baked into the HTML.

    Counting-stat projections and marginal dollars are the rounded values
    the previous page shipped. Earned dollars and MLBAM ids are joined back
    from the season leaderboards embedded alongside them. A later game-day
    cache replaces this with the unrounded objects.
    """
    id_rows = []
    for row in (lb_data or []) + (lb_sp or []) + (lb_rp or []) + (all_mlb_players or []):
        if isinstance(row, dict) and row.get("id") is not None:
            id_rows.append(row)
    id_by_name = _index_by_name(id_rows, lambda row: row.get("id"))
    earned_by_name = _index_by_name(
        [r for r in (lb_data or []) + (lb_sp or []) + (lb_rp or [])
         if isinstance(r, dict) and r.get("earned") is not None],
        lambda row: row.get("earned"),
    )
    act_by_name = _index_by_name(
        [r for r in (lb_sp or []) + (lb_rp or []) if isinstance(r, dict)],
        lambda row: {
            "gs": row.get("gs"),
            "g": row.get("g"),
            "ip": row.get("ip_f"),
            "is_sp": row.get("is_sp"),
        },
    )

    def _one(rec: dict, pitcher: bool) -> dict | None:
        if not isinstance(rec, dict):
            return None
        name = rec.get("name") or ""
        if not name:
            return None
        cats = rec.get("cats") if isinstance(rec.get("cats"), dict) else {}
        proj = rec.get("proj") if isinstance(rec.get("proj"), dict) else {}
        mlbam = _lookup_name(id_by_name, name)
        if pitcher:
            role = (rec.get("role") or "").strip().lower()
            fg_pos = rec.get("fg_pos") or ({"sp": "SP", "rp": "RP"}.get(role, ""))
            player = {
                "name": name,
                "team": rec.get("team") or "",
                "fg_id": rec.get("fg_id"),
                "mlbam": mlbam,
                "fg_pos": fg_pos,
                "W": cats.get("W"),
                "ERA": cats.get("ERA"),
                "WHIP": cats.get("WHIP"),
                "SO": cats.get("K_p"),
                "SV": cats.get("SV"),
                "HLD": cats.get("HLD"),
                "W_p": proj.get("W"),
                "ERA_p": proj.get("ERA"),
                "WHIP_p": proj.get("WHIP"),
                "SO_p": proj.get("K_p"),
                "SV_p": proj.get("SV"),
                "HLD_p": proj.get("HLD"),
                "IP_p": proj.get("IP"),
                "_ip": proj.get("IP"),
            }
        else:
            player = {
                "name": name,
                "team": rec.get("team") or "",
                "fg_id": rec.get("fg_id"),
                "mlbam": mlbam,
                "fg_pos": rec.get("fg_pos") or "",
                "R": cats.get("R"),
                "HR": cats.get("HR"),
                "RBI": cats.get("RBI"),
                "SB": cats.get("SB"),
                "SO": cats.get("K_h"),
                "OBP": cats.get("OBP"),
                "R_p": proj.get("R"),
                "HR_p": proj.get("HR"),
                "RBI_p": proj.get("RBI"),
                "SB_p": proj.get("SB"),
                "SO_p": proj.get("K_h"),
                "OBP_p": proj.get("OBP"),
                "PA_p": proj.get("PA"),
            }
        try:
            dollar = float(rec.get("dollars") if rec.get("dollars") is not None else 0)
        except (TypeError, ValueError):
            dollar = 0.0
        entry = {"player": player, "dollar": dollar, "z": 0.0, "zc": {}}
        earned = _lookup_name(earned_by_name, name)
        if earned is not None:
            entry["earned"] = earned
        if pitcher:
            act = _lookup_name(act_by_name, name)
            if act:
                entry["_act"] = act
        return entry

    fut_h = [e for e in (_one(r, False) for r in trade_h) if e]
    fut_p = [e for e in (_one(r, True) for r in trade_p) if e]
    return {"fut_h": fut_h, "fut_p": fut_p}


def _json_clean(o):
    """Convert numpy/pandas leftovers into strict JSON values."""
    if o is None or isinstance(o, str):
        return o
    if isinstance(o, bool):
        return bool(o)
    if isinstance(o, int):
        return int(o)
    if isinstance(o, float):
        if math.isnan(o) or math.isinf(o):
            return None
        return o
    if isinstance(o, dict):
        return {str(k): _json_clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_clean(v) for v in o]
    tname = type(o).__name__
    if tname in ("NAType", "NaTType"):
        return None
    if tname == "Decimal":
        try:
            return _json_clean(float(o))
        except Exception:
            return None
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    tolist = getattr(o, "tolist", None)
    if callable(tolist):
        try:
            converted = tolist()
        except Exception:
            converted = None
        else:
            if converted is not o:
                return _json_clean(converted)
    return str(o)
