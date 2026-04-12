"""
sim_data_fetch.py
─────────────────
Standalone data-preservation layer for the upcoming player-level Monte
Carlo sim. Fetches OOPSY DC, THE BAT X, and Steamer (RoS) projections
from FanGraphs for both hitters and pitchers, KEEPS each system's values
separate instead of averaging them, and writes a structured JSON cache
plus a small set of sanity-check stats to stdout.

This file does NOT touch fantasy.py, the dashboard pipeline, or any
existing data file. It is strictly a standalone exploration tool:

    python sim_data_fetch.py

Outputs:
    sim_data_cache.json     per-player per-system projections + spread
    (prints to stdout)      sanity check on known elite/uncertain players

Once we've confirmed the data looks right, this is the foundation for
the sim refactor: per-system values become the source of truth for
inter-system-spread sigmas, and the BATX-vs-Steamer PA/IP delta becomes
the source for per-player injury expectations.

Nothing here is imported by fantasy.py or any dashboard code, so
running it repeatedly is safe and has no side effects beyond the
sim_data_cache.json file it writes.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from datetime import datetime
from typing import Any

import requests

# ══════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════

PROJ_SYSTEMS = [
    ("oopsy",   "roopsydc"),   # OOPSY DC (RoS)
    ("batx",    "rthebatx"),   # THE BAT X (RoS)
    ("steamer", "steamerr"),   # Steamer (RoS)
]

# Stats we care about for each player type. These map to keys in the
# FG projections API response. OBP is "OBP", K is "SO" (FG convention).
HITTER_STATS  = ["PA", "R", "HR", "RBI", "SB", "SO", "OBP", "AVG"]
PITCHER_STATS = ["IP", "W", "SO", "ERA", "WHIP", "SV", "HLD", "K/9", "BB/9"]

# Ratio stats are bounded / fractional — they get a different minimum-mu
# threshold when we roll up aggregate CVs (otherwise OBP ~ 0.33 gets
# filtered out by a counting-stat floor).
RATIO_STATS = {"OBP", "AVG", "SLG", "ERA", "WHIP", "K/9", "BB/9"}

# "Sparse" stats where a system returning 0 usually means "I don't
# report this in this API endpoint" rather than "I truly project 0."
# BATX specifically returns 0 for SV and (often) HLD in the projections
# endpoint even for elite closers — the real BATX save projections live
# in the auction-calculator API we don't hit here. Our fix: when at
# least one system reports a nonzero value for these stats, we exclude
# any zero-valued systems from the per-player spread computation so
# BATX's structural 0 doesn't corrupt closers' save sigma.
SPARSE_STATS = {"SV", "HLD"}

# Minimum playing time thresholds for aggregate-CV rollups. Below these,
# per-player CVs are dominated by tiny-sample noise (a reliever projected
# for 2 IP looks like he has 90% CV on K when the real disagreement is
# just 1.5 vs 2.1 strikeouts). These thresholds only affect the aggregate
# rollup table; every individual player still appears in the cache file.
MIN_PA_HITTER      = 200
MIN_IP_STARTER     = 80
MIN_IP_RELIEVER    = 25
STARTER_IP_CUTOFF  = 80    # IP >= this → classified as starter
STARTER_MAX_SV     = 5     # and SV < this (pens-of-starters aren't SP)

# ══════════════════════════════════════════════════════════════════════
# Injury calibration — Zimmerman-grounded
# ══════════════════════════════════════════════════════════════════════
# Jeff Zimmerman has published multi-year rolling injury base rates at
# FanGraphs / The Process. The figures below are ROUND APPROXIMATIONS
# drawn from his published aggregate tables — they are meant to be
# close-enough defaults that anchor the calibration, not exact values.
# All three numbers can be tuned after we see real downstream behavior.
#
#   ~27-30% of MLB hitters have at least one IL stint each season;
#     population mean of PAs missed to injury is roughly 40-55 per
#     qualifying hitter. We use 45 PA as the hitter baseline.
#
#   ~50% of starting pitchers hit the IL each season; population mean
#     IP missed is in the 18-28 range per qualifying starter. We use
#     22 IP as the SP baseline.
#
#   ~35-40% of relievers hit the IL; population mean IP missed per
#     qualifying reliever is in the 5-10 range. We use 6 IP as the
#     RP baseline. (Relievers have lower absolute IP, so the fraction
#     missed is comparable to SP.)
#
# On top of these baselines, we use the per-player normalized delta
# (Steamer − BATX − role_median) as a "BATX thinks this guy is at
# extra risk" signal. Each unit of normalized delta adds roughly
# 0.7 units of extra expected loss (INJURY_DELTA_GAIN), so a p90
# flagged hitter (~+34 normalized PA) gets ~+24 PA of extra loss on
# top of the 45 PA baseline = ~69 PA expected loss. Total loss is
# capped at 40% of projected volume so we never zero-out a player.
#
# Role baselines for zero-centering the raw (Steamer − BATX) delta are
# taken directly from the last real run of this script:
#   hitter PA delta median    -26.6
#   SP     IP delta median     +6.2
#   RP     IP delta median    +13.6
# If a future run moves these meaningfully, update ROLE_DELTA_BASELINE.
# ══════════════════════════════════════════════════════════════════════

INJURY_BASELINE_HITTER_PA = 45.0
INJURY_BASELINE_SP_IP     = 22.0
INJURY_BASELINE_RP_IP     = 6.0
INJURY_DELTA_GAIN         = 0.7
INJURY_MAX_LOSS_FRAC      = 0.40

ROLE_DELTA_BASELINE = {
    "hitter": -26.6,
    "SP":      +6.2,
    "RP":     +13.6,
}

OUTPUT_FILE = "sim_data_cache.json"

# Where to find the FG cookie. Reuses the same file fantasy.py / fangraphs.py
# already read from, so the existing auth flow works unchanged.
FG_COOKIE_FILE = "fg_cookie.txt"


# ══════════════════════════════════════════════════════════════════════
# FG cookie loader (duplicated from fangraphs.py so this script has
# zero imports from the dashboard pipeline)
# ══════════════════════════════════════════════════════════════════════

def _load_fg_cookie() -> str:
    """Read the FanGraphs cookie string from fg_cookie.txt. Returns "" if
    missing — the FG endpoints mostly work without auth for projection
    data, but including it avoids rate-limits and matches how fantasy.py
    calls them."""
    try:
        with open(FG_COOKIE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    except Exception as exc:
        print(f"  [sim_data] fg_cookie.txt read error: {exc}")
        return ""


# ══════════════════════════════════════════════════════════════════════
# FG projections fetcher (duplicated from fantasy.py for isolation)
# ══════════════════════════════════════════════════════════════════════

def fetch_fg_projections(year: int, proj_code: str, stats_type: str) -> list:
    """Fetch one (system, player_type) projection set from FG.

    proj_code:  one of 'roopsydc', 'rthebatx', 'steamerr' (RoS variants)
    stats_type: 'bat' or 'pit'
    """
    url = (f"https://www.fangraphs.com/api/projections"
           f"?type={proj_code}&stats={stats_type}&pos=all&team=0&players=0&lg=all")
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer":    "https://www.fangraphs.com/",
        "Accept":     "application/json",
    }
    cookie_str = _load_fg_cookie()
    if cookie_str:
        hdrs["Cookie"] = cookie_str
    try:
        resp = requests.get(url, headers=hdrs, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            print(f"    [{proj_code}/{stats_type}] {len(data)} rows")
            return data
        print(f"    [{proj_code}/{stats_type}] HTTP {resp.status_code}")
    except Exception as exc:
        print(f"    [{proj_code}/{stats_type}] error: {exc}")
    return []


# ══════════════════════════════════════════════════════════════════════
# Per-player merge: keep every system's line separately
# ══════════════════════════════════════════════════════════════════════

def _pid(r: dict) -> str | None:
    """Normalize FG playerid to a string-int key."""
    v = r.get("playerid")
    if v is None:
        return None
    try:
        return str(int(float(v)))
    except (ValueError, TypeError):
        return str(v)


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_per_system_table(
    per_system_rows: dict[str, list],
    stats: list[str],
    is_pitcher: bool,
) -> dict[str, dict]:
    """
    per_system_rows: {system_name: [row, row, ...]}  from fetch_fg_projections

    Returns a dict keyed by playerid, where each record is:
        {
            "name":     "Aaron Judge",
            "team":     "NYY",
            "fg_id":    "15640",
            "mlbam":    592450,
            "is_pitcher": False,
            "by_system": {
                "oopsy":   {"R": 110, "HR": 48, "RBI": 120, ...},
                "batx":    {"R": 115, "HR": 52, "RBI": 125, ...},
                "steamer": {"R": 108, "HR": 45, "RBI": 118, ...},
            },
            "n_systems": 3,
        }

    Players missing from a given system just omit that key from by_system —
    downstream sigma computation uses `statistics.stdev` over whatever
    systems are present, with an n>=2 guard.
    """
    out: dict[str, dict] = {}

    for sys_name, rows in per_system_rows.items():
        for r in rows or []:
            pid = _pid(r)
            if not pid:
                continue
            rec = out.get(pid)
            if rec is None:
                # Position is best-effort — FG's projections endpoint uses
                # different field names for different systems. We try
                # several and default to "UNK" if none match.
                pos = (r.get("minpos") or r.get("Pos") or r.get("position")
                       or r.get("team_position") or "UNK")
                rec = {
                    "name":       r.get("PlayerName") or r.get("Name") or "",
                    "team":       r.get("Team") or r.get("team") or "",
                    "fg_id":      pid,
                    "mlbam":      r.get("xMLBAMID") or r.get("MLBAMID"),
                    "pos":        pos,
                    "is_pitcher": is_pitcher,
                    "by_system":  {},
                }
                out[pid] = rec
            else:
                # Fill in position from a later system if the earlier one lacked it
                if rec.get("pos") in (None, "", "UNK"):
                    p2 = (r.get("minpos") or r.get("Pos") or r.get("position")
                          or r.get("team_position"))
                    if p2:
                        rec["pos"] = p2
            # Extract just the stats we care about as floats
            stat_line: dict[str, float] = {}
            for s in stats:
                v = _to_float(r.get(s))
                if v is not None:
                    stat_line[s] = v
            rec["by_system"][sys_name] = stat_line

    # Fill in n_systems
    for rec in out.values():
        rec["n_systems"] = len(rec["by_system"])

    return out


# ══════════════════════════════════════════════════════════════════════
# Inter-system aggregates (avg + stdev across systems)
# ══════════════════════════════════════════════════════════════════════

def _system_values(rec: dict, stat: str) -> list[float]:
    """Collect this stat's value across every system that has it."""
    vals = []
    for sys_line in rec["by_system"].values():
        v = sys_line.get(stat)
        if v is not None:
            vals.append(v)
    return vals


def compute_aggregates(rec: dict, stats: list[str]) -> None:
    """
    Attach four new fields to each player record:

        avg:                {stat: mean across systems that have it}
        inter_system_sigma: {stat: stdev across systems that have it}
        inter_system_cv:    {stat: sigma/|mu| — the relative spread}
        sigma_sources:      {stat: list of system names used for spread}

    For stats in SPARSE_STATS (SV, HLD), we drop any system reporting
    exactly 0 *if* at least one other system reports a nonzero value —
    this excludes BATX's structural "no saves data" zero from Mason
    Miller's save sigma without breaking the aggregates for a true
    middle reliever whose three systems all correctly say 0 saves.

    Players with only 1 system contributing to a stat get sigma=0 and
    cv=0 for that stat — downstream code must floor these before use.
    """
    avg: dict[str, float] = {}
    sig: dict[str, float] = {}
    cv:  dict[str, float] = {}
    src: dict[str, list[str]] = {}
    for s in stats:
        # Collect (system_name, value) pairs so we can track which
        # systems contributed to each stat after filtering.
        pairs: list[tuple[str, float]] = []
        for sys_name, sys_line in rec["by_system"].items():
            v = sys_line.get(s)
            if v is not None:
                pairs.append((sys_name, v))

        if s in SPARSE_STATS:
            nonzero = [(n, v) for (n, v) in pairs if v > 0]
            if nonzero:
                # At least one system has a real value → drop zeros
                pairs = nonzero
            # else: all systems genuinely report 0 → keep as-is

        if not pairs:
            continue

        vals = [v for (_, v) in pairs]
        mu = sum(vals) / len(vals)
        avg[s] = mu
        src[s] = [n for (n, _) in pairs]
        if len(vals) >= 2:
            stdev = statistics.stdev(vals)
            sig[s] = stdev
            cv[s]  = (stdev / abs(mu)) if mu != 0 else 0.0
        else:
            sig[s] = 0.0
            cv[s]  = 0.0
    rec["avg"] = avg
    rec["inter_system_sigma"] = sig
    rec["inter_system_cv"]    = cv
    rec["sigma_sources"]      = src


# ══════════════════════════════════════════════════════════════════════
# Sanity check — print spread for known players
# ══════════════════════════════════════════════════════════════════════

# Name substrings to match. Case-insensitive. These are selected to cover
# both "projections should agree" (elite, healthy, established) and
# "projections should disagree" (rookies, injury returns, role uncertainty)
# scenarios. Feel free to edit.
SANITY_HITTERS = [
    "Aaron Judge",          # elite, healthy — expect tight spread
    "Shohei Ohtani",        # elite hitter (two-way noise on pitching side)
    "Ronald Acu",           # elite returning from injury — may have wider spread
    "Bobby Witt",           # elite, established
    "Jackson Holliday",     # prospect / rookie — expect wider spread
    "Gunnar Henderson",     # young star
    "Wyatt Langford",       # sophomore
]
SANITY_PITCHERS = [
    "Tarik Skubal",         # ace starter, healthy
    "Paul Skenes",          # young phenom
    "Mason Miller",         # elite closer — sigma test for closer tier
    "Emmanuel Clase",       # elite closer
    "Josh Hader",           # elite closer
    "Felix Bautista",       # closer returning from injury — expect wide spread
    "Spencer Strider",      # injury-return starter
]


def _fmt(v: Any) -> str:
    if v is None:
        return "  -  "
    if isinstance(v, float):
        if abs(v) >= 100:
            return f"{v:6.0f}"
        return f"{v:6.2f}"
    return f"{v!s:>6}"


def print_sanity(table: dict[str, dict], stats: list[str], label: str,
                 name_filters: list[str]) -> None:
    print(f"\n════════ {label} ════════")
    hits = []
    for rec in table.values():
        nm_lower = (rec["name"] or "").lower()
        for f in name_filters:
            if f.lower() in nm_lower:
                hits.append((f, rec))
                break
    # Preserve the order of the filter list
    hits.sort(key=lambda t: name_filters.index(t[0]))

    if not hits:
        print("  (no matches)")
        return

    for _, rec in hits:
        print(f"\n  {rec['name']:<22s} ({rec['team']:<3s})  fg_id={rec['fg_id']}  "
              f"n_systems={rec['n_systems']}")
        # Header
        sys_order = list(rec["by_system"].keys())
        hdr = "    " + f"{'stat':<6s}"
        for s in sys_order:
            hdr += f"  {s:>8s}"
        hdr += f"  {'avg':>8s}  {'stdev':>8s}  {'cv%':>6s}"
        print(hdr)
        # Rows
        for st in stats:
            line = f"    {st:<6s}"
            for sn in sys_order:
                v = rec["by_system"][sn].get(st)
                line += f"  {_fmt(v):>8s}"
            mu  = rec.get("avg", {}).get(st)
            sig = rec.get("inter_system_sigma", {}).get(st)
            cv  = rec.get("inter_system_cv", {}).get(st)
            line += f"  {_fmt(mu):>8s}  {_fmt(sig):>8s}"
            line += f"  {(f'{cv*100:5.1f}' if cv is not None else '  -  '):>6s}"
            print(line)


def _qualifies_hitter(rec: dict) -> bool:
    """PA threshold filter for the hitter aggregate rollup."""
    pa = rec.get("avg", {}).get("PA", 0.0)
    return pa >= MIN_PA_HITTER


def _classify_pitcher(rec: dict) -> str:
    """Return 'SP', 'RP', or 'NONE' based on projected IP and SV.

    A starter is any pitcher with IP >= STARTER_IP_CUTOFF AND projected
    saves below STARTER_MAX_SV (so we don't accidentally lump the very
    rare "closer who starts a few games" case into SP).
    """
    avg = rec.get("avg", {})
    ip  = avg.get("IP", 0.0)
    sv  = avg.get("SV", 0.0)
    if ip >= STARTER_IP_CUTOFF and sv < STARTER_MAX_SV:
        if ip >= MIN_IP_STARTER:
            return "SP"
        return "NONE"
    # Reliever or two-way: require a minimum IP as a noise floor
    if ip >= MIN_IP_RELIEVER:
        return "RP"
    return "NONE"


# ══════════════════════════════════════════════════════════════════════
# Position classification (for delta distribution rollup by position)
# ══════════════════════════════════════════════════════════════════════

# Loose groupings used only for the delta-distribution position breakouts.
# FG's minpos field gives a single string like "2B" or "OF" (or for multi-
# position players the first one listed). We bucket into rough groups so
# per-position injury signals come out of the noise.
HITTER_POS_GROUPS = {
    "C":    ["C"],
    "MI":   ["2B", "SS"],
    "CI":   ["1B", "3B"],
    "OF":   ["OF", "LF", "CF", "RF"],
    "DH":   ["DH"],
}


def _hitter_pos_group(pos: str) -> str:
    """Map a raw position string to one of our five hitter groups."""
    if not pos:
        return "UNK"
    # Positions often come as "OF/1B" or "SS-2B" — check each token
    tokens = []
    for sep in ("/", "-", ","):
        pos = pos.replace(sep, " ")
    for tok in pos.strip().split():
        tokens.append(tok.upper())
    for group, members in HITTER_POS_GROUPS.items():
        for tok in tokens:
            if tok in members:
                return group
    return "UNK"


# ══════════════════════════════════════════════════════════════════════
# Delta-distribution analysis (fix #4)
# ══════════════════════════════════════════════════════════════════════
# Rationale:
#   THE BAT X has a known in-season injury-aware discount built into its
#   projections — it's consistently lower on PA/IP for guys with recent
#   injury history or red flags than Steamer, which is closer to a
#   "talent-level" rate × expected playing time estimate. Subtracting
#   steamer - batx gives a per-player "injury expectation" signal in
#   plate appearances or innings pitched.
#
#   This section computes that delta, rolls it up into a distribution
#   (for calibration of "what's a normal delta" vs "this guy is flagged"),
#   splits it by position so we can see if catchers / starters show
#   different patterns, and prints the top-N most-flagged players so we
#   can eyeball whether the signal matches what we know.
# ══════════════════════════════════════════════════════════════════════

def _delta_for(rec: dict, stat: str) -> float | None:
    """Return steamer_value - batx_value for this stat, or None if either
    system is missing that stat for this player."""
    by = rec.get("by_system", {})
    s_line = by.get("steamer", {}) or {}
    b_line = by.get("batx",    {}) or {}
    sv = s_line.get(stat)
    bv = b_line.get(stat)
    if sv is None or bv is None:
        return None
    return float(sv) - float(bv)


def _percentiles(vals: list[float]) -> dict[str, float]:
    """Compute a small percentile grid. `vals` must be nonempty."""
    xs = sorted(vals)
    n = len(xs)

    def _pick(pct: float) -> float:
        if n == 1:
            return xs[0]
        idx = int(round(pct * (n - 1)))
        return xs[max(0, min(n - 1, idx))]

    return {
        "min":  xs[0],
        "p10":  _pick(0.10),
        "p25":  _pick(0.25),
        "med":  xs[n // 2],
        "mean": sum(xs) / n,
        "p75":  _pick(0.75),
        "p90":  _pick(0.90),
        "p95":  _pick(0.95),
        "max":  xs[-1],
    }


def print_delta_distribution(
    table: dict[str, dict],
    stat: str,
    label: str,
    qualifier,  # callable(rec) -> bool
) -> None:
    """Print a percentile grid for steamer-batx delta over qualified players."""
    vals: list[float] = []
    for rec in table.values():
        if not qualifier(rec):
            continue
        d = _delta_for(rec, stat)
        if d is None:
            continue
        vals.append(d)
    if not vals:
        print(f"\n  [{label}] no qualifying players for delta distribution")
        return
    pc = _percentiles(vals)
    print(f"\n════════ {label}: Steamer − BATX {stat} delta "
          f"(n={len(vals)}) ════════")
    print(f"  min    {pc['min']:+7.1f}")
    print(f"  p10    {pc['p10']:+7.1f}")
    print(f"  p25    {pc['p25']:+7.1f}")
    print(f"  median {pc['med']:+7.1f}")
    print(f"  mean   {pc['mean']:+7.1f}")
    print(f"  p75    {pc['p75']:+7.1f}")
    print(f"  p90    {pc['p90']:+7.1f}")
    print(f"  p95    {pc['p95']:+7.1f}")
    print(f"  max    {pc['max']:+7.1f}")


def print_delta_by_hitter_position(
    table: dict[str, dict],
    stat: str = "PA",
) -> None:
    """Break out the hitter PA delta distribution by position group."""
    print(f"\n════════ Hitter Steamer − BATX {stat} delta BY POSITION "
          f"(qualified hitters only) ════════")
    print(f"  {'group':<6s}  {'n':>5s}  "
          f"{'median':>8s}  {'mean':>8s}  {'p75':>8s}  {'p90':>8s}")
    groups: dict[str, list[float]] = {g: [] for g in HITTER_POS_GROUPS}
    groups["UNK"] = []
    for rec in table.values():
        if not _qualifies_hitter(rec):
            continue
        d = _delta_for(rec, stat)
        if d is None:
            continue
        g = _hitter_pos_group(rec.get("pos", ""))
        groups.setdefault(g, []).append(d)
    for g in ["C", "MI", "CI", "OF", "DH", "UNK"]:
        vals = groups.get(g, [])
        if not vals:
            print(f"  {g:<6s}  {0:>5d}")
            continue
        pc = _percentiles(vals)
        print(f"  {g:<6s}  {len(vals):>5d}  "
              f"{pc['med']:>+7.1f}  {pc['mean']:>+7.1f}  "
              f"{pc['p75']:>+7.1f}  {pc['p90']:>+7.1f}")


def print_delta_by_pitcher_role(
    table: dict[str, dict],
    stat: str = "IP",
) -> None:
    """Break out the pitcher IP delta distribution by SP vs RP."""
    print(f"\n════════ Pitcher Steamer − BATX {stat} delta BY ROLE "
          f"(qualified pitchers only) ════════")
    print(f"  {'role':<6s}  {'n':>5s}  "
          f"{'median':>8s}  {'mean':>8s}  {'p75':>8s}  {'p90':>8s}")
    for role in ("SP", "RP"):
        qualifier = (lambda r, _role=role:
                     _classify_pitcher(r) == _role)
        vals: list[float] = []
        for rec in table.values():
            if not qualifier(rec):
                continue
            d = _delta_for(rec, stat)
            if d is None:
                continue
            vals.append(d)
        if not vals:
            print(f"  {role:<6s}  {0:>5d}")
            continue
        pc = _percentiles(vals)
        print(f"  {role:<6s}  {len(vals):>5d}  "
              f"{pc['med']:>+7.1f}  {pc['mean']:>+7.1f}  "
              f"{pc['p75']:>+7.1f}  {pc['p90']:>+7.1f}")


def print_top_flagged(
    table: dict[str, dict],
    stat: str,
    label: str,
    qualifier,
    n: int = 20,
) -> None:
    """
    Print the top-N players by steamer-batx delta. A positive delta
    means Steamer projects more volume than BATX — BATX is discounting
    for injury risk — so the top of the list is "BATX is most worried
    about these guys."
    """
    rows: list[tuple[float, dict]] = []
    for rec in table.values():
        if not qualifier(rec):
            continue
        d = _delta_for(rec, stat)
        if d is None:
            continue
        rows.append((d, rec))
    rows.sort(key=lambda x: x[0], reverse=True)
    print(f"\n════════ {label}: top {n} by Steamer − BATX {stat} "
          f"(biggest BATX discount) ════════")
    print(f"  {'rank':>4s}  {'player':<24s}  {'team':<4s}  "
          f"{'pos':<5s}  {'steamer':>8s}  {'batx':>8s}  {'delta':>8s}")
    for i, (d, rec) in enumerate(rows[:n], start=1):
        by = rec["by_system"]
        sv = (by.get("steamer") or {}).get(stat)
        bv = (by.get("batx")    or {}).get(stat)
        pos = rec.get("pos") or "-"
        print(f"  {i:>4d}  {rec['name']:<24s}  {rec['team']:<4s}  "
              f"{pos:<5s}  "
              f"{(f'{sv:8.1f}' if sv is not None else '     -  '):>8s}  "
              f"{(f'{bv:8.1f}' if bv is not None else '     -  '):>8s}  "
              f"{d:+8.1f}")


def _role_for(rec: dict) -> str:
    """Return 'hitter', 'SP', 'RP', or 'NONE' for a record."""
    if not rec.get("is_pitcher"):
        return "hitter"
    return _classify_pitcher(rec)


def calibrate_injury(rec: dict) -> None:
    """
    Attach injury-loss calibration fields to a player record.

    Writes the following new keys onto rec:
        role                'hitter' / 'SP' / 'RP' / 'NONE'
        raw_delta           steamer − batx for PA (hit) or IP (pit)
        normalized_delta    raw_delta minus role baseline median
        baseline_loss       Zimmerman population-level expected loss
        extra_loss          delta-driven loss above baseline (>= 0)
        cap_loss            40% of projected volume (hard ceiling)
        expected_loss       baseline_loss + extra_loss, capped
        injury_flag_tier    'none' / 'mild' / 'moderate' / 'severe'

    Must be called AFTER compute_aggregates (needs rec['avg']).

    Players that don't qualify for a role (too few PA / IP) get
    role='NONE' and no injury fields — they're still written to
    the cache but downstream sim code should skip them.
    """
    role = _role_for(rec)
    rec["role"] = role

    # Baselines keyed by role
    vol_stat = "PA" if role == "hitter" else "IP"
    vol = rec.get("avg", {}).get(vol_stat, 0.0)
    raw = _delta_for(rec, vol_stat) if role != "NONE" else None

    if role == "NONE" or raw is None or vol <= 0:
        rec["raw_delta"]        = None
        rec["normalized_delta"] = None
        rec["baseline_loss"]    = None
        rec["extra_loss"]       = None
        rec["cap_loss"]         = None
        rec["expected_loss"]    = None
        rec["injury_flag_tier"] = "none"
        return

    norm = raw - ROLE_DELTA_BASELINE.get(role, 0.0)
    rec["raw_delta"]        = raw
    rec["normalized_delta"] = norm

    baseline_map = {
        "hitter": INJURY_BASELINE_HITTER_PA,
        "SP":     INJURY_BASELINE_SP_IP,
        "RP":     INJURY_BASELINE_RP_IP,
    }
    baseline = baseline_map[role]
    extra = max(0.0, norm) * INJURY_DELTA_GAIN
    cap   = vol * INJURY_MAX_LOSS_FRAC
    exp_loss = min(baseline + extra, cap)

    rec["baseline_loss"] = baseline
    rec["extra_loss"]    = extra
    rec["cap_loss"]      = cap
    rec["expected_loss"] = exp_loss

    # Tier relative to the role baseline — a "none"-tier player is
    # projected to lose about as much as an average player, "severe"
    # is more than 2.5× the role average loss.
    ratio = exp_loss / baseline if baseline > 0 else 0
    if ratio < 1.3:
        tier = "none"
    elif ratio < 1.8:
        tier = "mild"
    elif ratio < 2.5:
        tier = "moderate"
    else:
        tier = "severe"
    rec["injury_flag_tier"] = tier


def print_injury_calibration(
    table: dict[str, dict],
    label: str,
    qualifier,  # callable(rec) -> bool
) -> None:
    """Print a summary of expected-loss distribution + tier counts."""
    losses: list[float] = []
    tiers = {"none": 0, "mild": 0, "moderate": 0, "severe": 0}
    for rec in table.values():
        if not qualifier(rec):
            continue
        el = rec.get("expected_loss")
        if el is None:
            continue
        losses.append(el)
        t = rec.get("injury_flag_tier", "none")
        tiers[t] = tiers.get(t, 0) + 1

    if not losses:
        print(f"\n  [{label}] no qualifying players for injury summary")
        return
    losses.sort()
    n = len(losses)
    print(f"\n════════ {label}: injury calibration (n={n}) ════════")
    print(f"  mean expected loss:   {sum(losses) / n:7.1f}")
    print(f"  median expected loss: {losses[n // 2]:7.1f}")
    print(f"  p75:                  {losses[(3 * n) // 4]:7.1f}")
    print(f"  p90:                  {losses[min(n - 1, (9 * n) // 10)]:7.1f}")
    print(f"  p95:                  {losses[min(n - 1, (95 * n) // 100)]:7.1f}")
    print(f"  max:                  {losses[-1]:7.1f}")
    print(f"  tiers: none={tiers['none']}  mild={tiers['mild']}  "
          f"moderate={tiers['moderate']}  severe={tiers['severe']}")


def print_top_injury_flags(
    table: dict[str, dict],
    label: str,
    qualifier,
    n: int = 15,
) -> None:
    """Print the top-N players by expected injury loss."""
    rows: list[tuple[float, dict]] = []
    for rec in table.values():
        if not qualifier(rec):
            continue
        el = rec.get("expected_loss")
        if el is None:
            continue
        rows.append((el, rec))
    rows.sort(key=lambda r: r[0], reverse=True)
    print(f"\n════════ {label}: top {n} by expected injury loss ════════")
    print(f"  {'rank':>4}  {'player':<24}  {'team':<4}  "
          f"{'role':<6}  {'vol':>6}  {'nΔ':>7}  {'loss':>6}  tier")
    for i, (el, rec) in enumerate(rows[:n], start=1):
        role = rec.get("role", "")
        vs = "PA" if role == "hitter" else "IP"
        vol = rec.get("avg", {}).get(vs, 0.0)
        norm = rec.get("normalized_delta", 0.0) or 0.0
        tier = rec.get("injury_flag_tier", "none")
        print(f"  {i:>4}  {rec['name']:<24}  {rec['team']:<4}  "
              f"{role:<6}  {vol:>6.0f}  {norm:>+7.1f}  "
              f"{el:>6.1f}  {tier}")


def print_aggregate_stats(
    table: dict[str, dict],
    stats: list[str],
    label: str,
    qualifier,  # callable(rec) -> bool
) -> None:
    """
    Roll up inter-system CVs across the qualified player pool for each
    stat. This is our empirical answer to "what should BASE_CV be?" —
    the median CV across players in each stat IS the answer, directly
    from projection-system disagreement.

    Uses a different minimum-mu threshold for ratio stats (OBP, AVG,
    ERA, WHIP, K/9, BB/9) versus counting stats, so ratio stats aren't
    accidentally filtered out by a counting-stat volume floor.
    """
    print(f"\n════════ {label}: aggregate inter-system CV (qualified players only) ════════")
    print(f"  {'stat':<6s}  {'n':>5s}  {'median CV':>10s}  "
          f"{'p25':>8s}  {'p75':>8s}  {'p90':>8s}")
    for st in stats:
        cvs = []
        for rec in table.values():
            if rec["n_systems"] < 2:
                continue
            if not qualifier(rec):
                continue
            cv = rec.get("inter_system_cv", {}).get(st)
            if cv is None:
                continue
            mu = rec.get("avg", {}).get(st, 0.0)
            # Ratio stats use a tiny floor (just filter "no projection"),
            # counting stats use a volume floor to exclude noise.
            if st in RATIO_STATS:
                if abs(mu) < 0.01:
                    continue
            else:
                if abs(mu) < 1.0:
                    continue
            # cv == 0 is only meaningful when at least 2 systems agreed
            # exactly — keep it. Everything else is real spread.
            cvs.append(cv)
        if not cvs:
            print(f"  {st:<6s}  {0:>5d}")
            continue
        cvs.sort()
        n = len(cvs)
        med = cvs[n // 2]
        p25 = cvs[n // 4]
        p75 = cvs[(3 * n) // 4]
        p90 = cvs[min(n - 1, (9 * n) // 10)]
        print(f"  {st:<6s}  {n:>5d}  {med*100:>9.1f}%  "
              f"{p25*100:>7.1f}%  {p75*100:>7.1f}%  {p90*100:>7.1f}%")


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    year = datetime.now().year
    print(f"[sim_data] Fetching projections for {year} from 3 systems × 2 types…")
    t0 = time.time()

    # ── Pull all six (system, type) combinations ───────────────────────
    per_sys_bat: dict[str, list] = {}
    per_sys_pit: dict[str, list] = {}
    for sys_name, code in PROJ_SYSTEMS:
        per_sys_bat[sys_name] = fetch_fg_projections(year, code, "bat")
        per_sys_pit[sys_name] = fetch_fg_projections(year, code, "pit")

    # ── Build per-player tables ────────────────────────────────────────
    hit_table = build_per_system_table(per_sys_bat, HITTER_STATS,  is_pitcher=False)
    pit_table = build_per_system_table(per_sys_pit, PITCHER_STATS, is_pitcher=True)

    print(f"\n[sim_data] Built {len(hit_table)} hitter records, "
          f"{len(pit_table)} pitcher records in {time.time() - t0:.1f}s")

    # ── Compute inter-system averages + spreads per player ────────────
    for rec in hit_table.values():
        compute_aggregates(rec, HITTER_STATS)
    for rec in pit_table.values():
        compute_aggregates(rec, PITCHER_STATS)

    # ── Zimmerman-grounded injury calibration per player ──────────────
    for rec in hit_table.values():
        calibrate_injury(rec)
    for rec in pit_table.values():
        calibrate_injury(rec)

    # ── Write the cache ────────────────────────────────────────────────
    cache = {
        "generated_at":  datetime.now().isoformat(),
        "year":          year,
        "systems":       [s for s, _ in PROJ_SYSTEMS],
        "hitter_stats":  HITTER_STATS,
        "pitcher_stats": PITCHER_STATS,
        "hitters":       hit_table,
        "pitchers":      pit_table,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=1)
    size_kb = os.path.getsize(OUTPUT_FILE) / 1024
    print(f"[sim_data] Wrote {OUTPUT_FILE} ({size_kb:.0f} KB)")

    # ── Coverage summary ───────────────────────────────────────────────
    def _coverage(table: dict) -> tuple[int, int, int]:
        one  = sum(1 for r in table.values() if r["n_systems"] == 1)
        two  = sum(1 for r in table.values() if r["n_systems"] == 2)
        thr  = sum(1 for r in table.values() if r["n_systems"] == 3)
        return one, two, thr
    h1, h2, h3 = _coverage(hit_table)
    p1, p2, p3 = _coverage(pit_table)
    print(f"\n[sim_data] Hitter coverage:   {h3} in all 3, {h2} in 2, {h1} in only 1")
    print(f"[sim_data] Pitcher coverage:  {p3} in all 3, {p2} in 2, {p1} in only 1")

    # ── Sanity: spot-check known elite / uncertain players ─────────────
    print_sanity(hit_table, HITTER_STATS,  "Hitter sanity check",  SANITY_HITTERS)
    print_sanity(pit_table, PITCHER_STATS, "Pitcher sanity check", SANITY_PITCHERS)

    # ── Aggregate CVs across the player pool — this is the real payoff.
    # The median column here is basically the empirical answer to "what
    # should the per-stat base sigma be" — no hand-tuning needed.
    print_aggregate_stats(
        hit_table, HITTER_STATS, "Hitters (PA ≥ 200)",
        _qualifies_hitter,
    )
    print_aggregate_stats(
        pit_table, PITCHER_STATS, f"Starters (IP ≥ {MIN_IP_STARTER})",
        lambda r: _classify_pitcher(r) == "SP",
    )
    print_aggregate_stats(
        pit_table, PITCHER_STATS, f"Relievers (IP ≥ {MIN_IP_RELIEVER})",
        lambda r: _classify_pitcher(r) == "RP",
    )

    # ── Delta distribution analysis (fix #4) ───────────────────────────
    # Steamer vs BATX delta on volume stats is our per-player "injury
    # expectation" signal. Print it three ways:
    #   1. overall distribution for hitter PA, SP IP, RP IP
    #   2. broken out by position group (hitters) / role (pitchers)
    #   3. top-20 most flagged in each group
    print_delta_distribution(
        hit_table, "PA", "Hitters (PA ≥ 200)",
        _qualifies_hitter,
    )
    print_delta_distribution(
        pit_table, "IP", f"Starters (IP ≥ {MIN_IP_STARTER})",
        lambda r: _classify_pitcher(r) == "SP",
    )
    print_delta_distribution(
        pit_table, "IP", f"Relievers (IP ≥ {MIN_IP_RELIEVER})",
        lambda r: _classify_pitcher(r) == "RP",
    )

    print_delta_by_hitter_position(hit_table, stat="PA")
    print_delta_by_pitcher_role(pit_table,   stat="IP")

    print_top_flagged(
        hit_table, "PA", "Hitters",
        _qualifies_hitter, n=20,
    )
    print_top_flagged(
        pit_table, "IP", "Starters",
        lambda r: _classify_pitcher(r) == "SP", n=20,
    )
    print_top_flagged(
        pit_table, "IP", "Relievers",
        lambda r: _classify_pitcher(r) == "RP", n=20,
    )

    # ── Injury calibration summary (Zimmerman-grounded baseline + delta)
    print_injury_calibration(
        hit_table, "Hitters (PA ≥ 200)",
        _qualifies_hitter,
    )
    print_injury_calibration(
        pit_table, f"Starters (IP ≥ {MIN_IP_STARTER})",
        lambda r: _classify_pitcher(r) == "SP",
    )
    print_injury_calibration(
        pit_table, f"Relievers (IP ≥ {MIN_IP_RELIEVER})",
        lambda r: _classify_pitcher(r) == "RP",
    )

    print_top_injury_flags(
        hit_table, "Hitters",
        _qualifies_hitter, n=15,
    )
    print_top_injury_flags(
        pit_table, "Starters",
        lambda r: _classify_pitcher(r) == "SP", n=15,
    )
    print_top_injury_flags(
        pit_table, "Relievers",
        lambda r: _classify_pitcher(r) == "RP", n=15,
    )

    print("\n[sim_data] Done. This script does not modify any dashboard "
          "data or code — it only wrote sim_data_cache.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
