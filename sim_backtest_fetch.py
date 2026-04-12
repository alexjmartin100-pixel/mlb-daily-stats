"""
sim_backtest_fetch.py
─────────────────────
Step 2 of the sim refactor: historical projection-vs-actual backtest.

Pulls pre-season Steamer projections and actual end-of-season stats from
FanGraphs for the 2023 and 2024 seasons, matches players by playerid,
and computes:

    1. Per-stat actual residual sigma across the league pool.
       ratio = actual_residual_sigma / inter_system_sigma
       is the multiplier we apply on top of sim_data_cache's base CVs
       to convert "projection-system disagreement" into "real outcome
       variance." Without this step, the sim UNDERESTIMATES variance.

    2. Within-player residual correlation matrix across stats.
       This is the cross-category correlation that makes HR/RBI/R move
       together (same hitter mashing) and ERA/WHIP move together (same
       pitcher blowing up). In the sim, we use either this matrix
       directly (Cholesky) or its leading eigenvector as the single
       latent "boom/bust" factor loading.

Writes sim_backtest_cache.json and prints a sanity summary.

Standalone — does NOT import from fantasy.py, sim_data_fetch.py, or any
dashboard file. Safe to run repeatedly.

Usage:
    python sim_backtest_fetch.py
"""

from __future__ import annotations

import json
import math
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

BACKTEST_YEARS = [2023, 2024]

HITTER_STATS  = ["PA", "R", "HR", "RBI", "SB", "SO", "OBP", "AVG"]
PITCHER_STATS = ["IP", "W", "SO", "ERA", "WHIP", "SV", "HLD", "K/9", "BB/9"]

RATIO_STATS = {"OBP", "AVG", "SLG", "ERA", "WHIP", "K/9", "BB/9"}

# Volume floors for including a player in the residual/correlation
# rollup. Too-small samples dominate any variance signal with noise.
MIN_PA_HITTER   = 200
MIN_IP_STARTER  = 80
MIN_IP_RELIEVER = 25

OUTPUT_FILE    = "sim_backtest_cache.json"
FG_COOKIE_FILE = "fg_cookie.txt"


# ══════════════════════════════════════════════════════════════════════
# FG cookie + HTTP plumbing
# ══════════════════════════════════════════════════════════════════════

def _load_fg_cookie() -> str:
    try:
        with open(FG_COOKIE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    except Exception as exc:
        print(f"  [backtest] fg_cookie.txt read error: {exc}")
        return ""


def _get_json(url: str, label: str) -> Any:
    """GET a FG URL and return parsed JSON, or None on any failure."""
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer":    "https://www.fangraphs.com/",
        "Accept":     "application/json",
    }
    cookie = _load_fg_cookie()
    if cookie:
        hdrs["Cookie"] = cookie
    try:
        resp = requests.get(url, headers=hdrs, timeout=30)
        if resp.status_code != 200:
            print(f"    [{label}] HTTP {resp.status_code}")
            return None
        return resp.json()
    except Exception as exc:
        print(f"    [{label}] error: {exc}")
        return None


# ══════════════════════════════════════════════════════════════════════
# Historical pre-season projections
# ══════════════════════════════════════════════════════════════════════
# Pre-season projections from past years aren't trivially fetchable via
# a single canonical URL — FG rotates parameters over time. This function
# probes several patterns and returns whatever comes back first.
#
# If NONE of the patterns work for a given (year, stats) pair, the
# function returns [] and prints the failures. In that case, the backtest
# falls back to a much weaker approach: we compute residuals only for
# the players that happen to be in BOTH years' leaderboards (repeat
# sampling), which gives us per-stat actual variance but no projection
# baseline. Better than nothing for calibrating correlation; not
# meaningful for sigma scaling.
# ══════════════════════════════════════════════════════════════════════

def fetch_preseason_projections(year: int, stats_type: str) -> list:
    """Try multiple URL patterns for historical pre-season Steamer.

    Returns the first non-empty list of rows.
    """
    base = "https://www.fangraphs.com/api/projections"
    # Each pattern is (description, url-tail) so we can log what worked.
    patterns = [
        (
            f"steamer&year={year}",
            f"?type=steamer&stats={stats_type}&pos=all&team=0"
            f"&players=0&lg=all&year={year}",
        ),
        (
            f"steamer&season={year}",
            f"?type=steamer&stats={stats_type}&pos=all&team=0"
            f"&players=0&lg=all&season={year}",
        ),
        (
            f"szips&year={year}",
            f"?type=szips&stats={stats_type}&pos=all&team=0"
            f"&players=0&lg=all&year={year}",
        ),
        (
            f"szips&season={year}",
            f"?type=szips&stats={stats_type}&pos=all&team=0"
            f"&players=0&lg=all&season={year}",
        ),
    ]
    for desc, tail in patterns:
        url = base + tail
        data = _get_json(url, f"{stats_type}/{desc}")
        if isinstance(data, list) and data:
            # Heuristic: we want PRE-season (season-start snapshot). If the
            # endpoint silently returned current-year data with a different
            # player pool, we'll still accept it and let the user eyeball.
            print(f"    [{stats_type}/{desc}] {len(data)} rows ✓")
            return data
    print(f"    [{stats_type}/{year}] NO pattern returned data")
    return []


# ══════════════════════════════════════════════════════════════════════
# Actual season-end stats from FG leaderboards
# ══════════════════════════════════════════════════════════════════════

def fetch_actuals(year: int, stats_type: str) -> list:
    """Fetch actual end-of-season leaderboard for a given year + side.

    Uses the same parameter set that the dashboard's batting_leaderboard
    and pitching_leaderboard modules already use successfully:
        qual=1, month=0, team=0, ind=0, pageitems=2000, pagenum=1, type=8

    WITHOUT pageitems FG defaults to 30 rows — a silent failure that
    returns the TOP-30 by WAR and shreds any statistical analysis with
    selection bias. Always pass pageitems explicitly.
    """
    url = (
        f"https://www.fangraphs.com/api/leaders/major-league/data"
        f"?pos=all&stats={stats_type}&lg=all&qual=1"
        f"&season={year}&season1={year}"
        f"&month=0&team=0&ind=0&pageitems=2000&pagenum=1&type=8"
    )
    data = _get_json(url, f"actuals/{stats_type}/{year}")
    if isinstance(data, dict):
        # FG leaderboard wraps rows under "data"
        if "data" in data and isinstance(data["data"], list):
            data = data["data"]
    if not isinstance(data, list):
        print(f"    [actuals/{stats_type}/{year}] unexpected response type")
        return []
    print(f"    [actuals/{stats_type}/{year}] {len(data)} rows")
    return data


# ══════════════════════════════════════════════════════════════════════
# Player keying + stat extraction
# ══════════════════════════════════════════════════════════════════════

def _pid(row: dict) -> str | None:
    for key in ("playerid", "PlayerId", "playerId", "xMLBAMID"):
        v = row.get(key)
        if v is None:
            continue
        try:
            return str(int(float(v)))
        except (ValueError, TypeError):
            return str(v)
    return None


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _stat_line(row: dict, stats: list[str]) -> dict[str, float]:
    line = {}
    for s in stats:
        v = _to_float(row.get(s))
        if v is not None:
            line[s] = v
    return line


# ══════════════════════════════════════════════════════════════════════
# Residual computation
# ══════════════════════════════════════════════════════════════════════

def build_residuals(
    proj_rows: list,
    actual_rows: list,
    stats: list[str],
) -> dict[str, dict]:
    """
    Match proj and actual by playerid. Return a dict:
        {pid: {"name":..., "projected": {stat: v}, "actual": {stat: v},
               "residual": {stat: actual - projected}}}
    """
    proj_by_id:   dict[str, dict] = {}
    actual_by_id: dict[str, dict] = {}

    for row in proj_rows or []:
        pid = _pid(row)
        if pid:
            proj_by_id[pid] = row
    for row in actual_rows or []:
        pid = _pid(row)
        if pid:
            actual_by_id[pid] = row

    out: dict[str, dict] = {}
    for pid, prow in proj_by_id.items():
        arow = actual_by_id.get(pid)
        if arow is None:
            continue
        p_line = _stat_line(prow, stats)
        a_line = _stat_line(arow, stats)
        residual = {}
        for s in stats:
            pv = p_line.get(s)
            av = a_line.get(s)
            if pv is None or av is None:
                continue
            residual[s] = av - pv
        if not residual:
            continue
        out[pid] = {
            "pid":       pid,
            "name":      prow.get("PlayerName") or prow.get("Name") or "",
            "team":      arow.get("Team") or prow.get("Team") or "",
            "projected": p_line,
            "actual":    a_line,
            "residual":  residual,
        }
    return out


# ══════════════════════════════════════════════════════════════════════
# Year-over-year residuals (projection-free primary path)
# ══════════════════════════════════════════════════════════════════════
# For players who played in both year A and year B:
#
#     yoy_residual[stat] = actual_B[stat] - actual_A[stat]
#
# The stdev of YoY residuals is:
#     Var(actual_B - actual_A) = Var(actual_B) + Var(actual_A)
#                                - 2*Cov(actual_B, actual_A)
# If the two seasons are independent samples of the same latent talent
# (Cov ≈ 0 in variance terms — true talent contributes mean, not
# variance), then YoY sigma ≈ sqrt(2) × single-season sigma. So the
# per-stat actual single-season sigma is approximately yoy_sigma / √2.
#
# This gives us a CLEAN sigma estimate using only historical actuals.
# No dependency on whether FG returns historical projections correctly.
#
# Selection bias note: only players who qualified in BOTH years appear
# in the YoY pool, which biases toward healthy/durable players. That's
# actually what we want — we're measuring "healthy player single-season
# variance" as our base sigma, and layering injury expectation on top
# via the Zimmerman-calibrated code in sim_data_fetch.py. Stacking both
# sources on the same players would double-count injury.
# ══════════════════════════════════════════════════════════════════════

import math as _m
YOY_SCALE = 1.0 / _m.sqrt(2)  # converts YoY sigma to single-season sigma


def build_yoy_residuals(
    actuals_by_year: dict[int, list],
    stats: list[str],
    years: list[int],
) -> dict[str, dict]:
    """
    Build year-over-year residual records using only actuals.

    For each unique (player, year_pair) where the player qualified in
    both years, produces one record with year_A stats as "projected"
    (so it plugs into the existing summarize_residuals machinery),
    year_B stats as "actual", and B - A as "residual".

    Returns {key: record} where key is "{year_a}-{year_b}:{pid}".
    """
    by_year: dict[int, dict[str, dict]] = {}
    for y in years:
        idx: dict[str, dict] = {}
        for row in actuals_by_year.get(y, []) or []:
            pid = _pid(row)
            if pid:
                idx[pid] = row
        by_year[y] = idx

    out: dict[str, dict] = {}
    # Use consecutive year pairs: (y0, y1), (y1, y2), etc.
    years_sorted = sorted(years)
    for i in range(len(years_sorted) - 1):
        ya, yb = years_sorted[i], years_sorted[i + 1]
        idx_a = by_year[ya]
        idx_b = by_year[yb]
        for pid, row_a in idx_a.items():
            row_b = idx_b.get(pid)
            if row_b is None:
                continue
            a_line = _stat_line(row_a, stats)
            b_line = _stat_line(row_b, stats)
            residual = {}
            for s in stats:
                av = a_line.get(s)
                bv = b_line.get(s)
                if av is None or bv is None:
                    continue
                residual[s] = bv - av
            if not residual:
                continue
            key = f"{ya}-{yb}:{pid}"
            out[key] = {
                "pid":       pid,
                "name":      row_a.get("PlayerName") or row_a.get("Name") or "",
                "team":      row_b.get("Team") or row_a.get("Team") or "",
                "year_a":    ya,
                "year_b":    yb,
                # Plug into existing summarize/correlation machinery by
                # treating year-A as "projected" and year-B as "actual"
                "projected": a_line,
                "actual":    b_line,
                "residual":  residual,
            }
    return out


def summarize_yoy(
    records: dict[str, dict],
    stats: list[str],
    label: str,
    qualifier,
) -> dict[str, dict]:
    """
    Like summarize_residuals, but ALSO reports the single-season sigma
    estimate (residual_sigma / √2) — this is the number the sim needs.

    The residual_cv column is the YoY CV before the √2 rescale;
    single_cv is after.
    """
    out: dict[str, dict] = {}
    qualified = [r for r in records.values() if qualifier(r)]
    print(f"\n════════ {label}: YoY residual summary "
          f"(n_qualified={len(qualified)}) ════════")
    print(f"  {'stat':<6s}  {'n':>5s}  {'mean_A':>9s}  {'mean_B':>9s}  "
          f"{'bias':>7s}  {'yoy_σ':>8s}  {'yoy_cv':>8s}  "
          f"{'1yr_σ':>8s}  {'1yr_cv':>8s}")
    for s in stats:
        a_vals, b_vals, resids = [], [], []
        for rec in qualified:
            av = rec["projected"].get(s)
            bv = rec["actual"].get(s)
            rv = rec["residual"].get(s)
            if av is None or bv is None or rv is None:
                continue
            a_vals.append(av)
            b_vals.append(bv)
            resids.append(rv)
        n = len(resids)
        if n < 2:
            out[s] = {"n": n}
            print(f"  {s:<6s}  {n:>5d}")
            continue
        ma = sum(a_vals) / n
        mb = sum(b_vals) / n
        bias = mb - ma
        yoy_sigma = statistics.stdev(resids)
        one_sigma = yoy_sigma * YOY_SCALE  # divide by √2
        # CV relative to the mean across BOTH years (more stable than one side)
        mu = (ma + mb) / 2
        if s in RATIO_STATS:
            denom = abs(mu) if abs(mu) > 0.01 else 0.01
        else:
            denom = abs(mu) if abs(mu) > 1.0 else 1.0
        yoy_cv = yoy_sigma / denom
        one_cv = one_sigma / denom
        out[s] = {
            "n":                 n,
            "mean_year_a":       ma,
            "mean_year_b":       mb,
            "bias":              bias,
            "yoy_sigma":         yoy_sigma,
            "yoy_cv":            yoy_cv,
            "single_year_sigma": one_sigma,
            "single_year_cv":    one_cv,
        }
        print(f"  {s:<6s}  {n:>5d}  {ma:>9.2f}  {mb:>9.2f}  "
              f"{bias:>+7.2f}  {yoy_sigma:>8.3f}  {yoy_cv*100:>7.1f}%  "
              f"{one_sigma:>8.3f}  {one_cv*100:>7.1f}%")
    return out


# ══════════════════════════════════════════════════════════════════════
# Per-stat residual sigma rollup
# ══════════════════════════════════════════════════════════════════════

def qualify_hitter(rec: dict) -> bool:
    proj = rec.get("projected", {}) or {}
    return proj.get("PA", 0.0) >= MIN_PA_HITTER


def classify_pitcher(rec: dict) -> str:
    proj = rec.get("projected", {}) or {}
    ip = proj.get("IP", 0.0)
    sv = proj.get("SV", 0.0)
    if ip >= MIN_IP_STARTER and sv < 5:
        return "SP"
    if ip >= MIN_IP_RELIEVER:
        return "RP"
    return "NONE"


def summarize_residuals(
    records: dict[str, dict],
    stats: list[str],
    label: str,
    qualifier,
) -> dict[str, dict]:
    """
    Per stat, compute:
        mean_proj       mean of the projection pool
        mean_actual     mean of the actual pool
        residual_mean   bias (actual - projected on average)
        residual_sigma  std of the residuals
        residual_cv     residual_sigma / mean_proj (ratio stats use
                        residual_sigma / |mean_proj|)

    The residual_cv is directly comparable to the inter_system_cv we
    computed in sim_data_fetch.py — their ratio is our scaling factor.
    """
    out: dict[str, dict] = {}
    qualified = [r for r in records.values() if qualifier(r)]
    print(f"\n════════ {label}: residual summary "
          f"(n_qualified={len(qualified)}) ════════")
    print(f"  {'stat':<6s}  {'n':>5s}  {'mean_proj':>9s}  "
          f"{'mean_act':>9s}  {'bias':>7s}  "
          f"{'resid_σ':>9s}  {'resid_cv':>9s}")
    for s in stats:
        projs, acts, resids = [], [], []
        for rec in qualified:
            pv = rec["projected"].get(s)
            av = rec["actual"].get(s)
            rv = rec["residual"].get(s)
            if pv is None or av is None or rv is None:
                continue
            projs.append(pv)
            acts.append(av)
            resids.append(rv)
        n = len(resids)
        if n < 2:
            out[s] = {"n": n}
            print(f"  {s:<6s}  {n:>5d}")
            continue
        mp = sum(projs) / n
        ma = sum(acts) / n
        bias = ma - mp
        rsig = statistics.stdev(resids)
        if s in RATIO_STATS:
            denom = abs(mp) if abs(mp) > 0.01 else 0.01
        else:
            denom = abs(mp) if abs(mp) > 1.0 else 1.0
        rcv = rsig / denom
        out[s] = {
            "n":              n,
            "mean_proj":      mp,
            "mean_actual":    ma,
            "bias":           bias,
            "residual_sigma": rsig,
            "residual_cv":    rcv,
        }
        print(f"  {s:<6s}  {n:>5d}  {mp:>9.2f}  {ma:>9.2f}  "
              f"{bias:>+7.2f}  {rsig:>9.3f}  {rcv*100:>8.1f}%")
    return out


# ══════════════════════════════════════════════════════════════════════
# Residual correlation matrix (within-player cross-stat correlation)
# ══════════════════════════════════════════════════════════════════════

def compute_correlation_matrix(
    records: dict[str, dict],
    stats: list[str],
    qualifier,
) -> tuple[list[list[float]], int]:
    """
    Return the Pearson correlation matrix of residuals across stats for
    the qualified player pool, plus the sample size.

    Uses only the statistics module — no numpy required — so the script
    runs on any vanilla Python env. For the leading eigenvector (the
    single-factor lambda loadings) we do a power iteration below.
    """
    qualified = [r for r in records.values() if qualifier(r)]
    k = len(stats)
    mat = [[0.0] * k for _ in range(k)]

    # Build column vectors per stat
    cols: list[list[float]] = [[] for _ in range(k)]
    # Only include players with non-null residuals for EVERY stat so the
    # matrix is balanced
    full = []
    for rec in qualified:
        rd = rec.get("residual", {}) or {}
        if all(s in rd for s in stats):
            full.append(rec)
    for rec in full:
        for i, s in enumerate(stats):
            cols[i].append(rec["residual"][s])
    n = len(full)
    if n < 5:
        return mat, n

    # Per-column mean and stdev
    means = [sum(c) / n for c in cols]
    sds   = [
        math.sqrt(sum((x - means[i]) ** 2 for x in cols[i]) / max(n - 1, 1))
        for i in range(k)
    ]

    # Pearson correlation
    for i in range(k):
        for j in range(k):
            if sds[i] == 0 or sds[j] == 0:
                mat[i][j] = 0.0 if i != j else 1.0
                continue
            cov_sum = 0.0
            for t in range(n):
                cov_sum += (cols[i][t] - means[i]) * (cols[j][t] - means[j])
            cov = cov_sum / max(n - 1, 1)
            mat[i][j] = cov / (sds[i] * sds[j])
    return mat, n


def print_correlation_matrix(
    mat: list[list[float]],
    stats: list[str],
    label: str,
    n: int,
) -> None:
    print(f"\n════════ {label}: residual correlation matrix "
          f"(n={n}) ════════")
    hdr = "       " + "  ".join(f"{s:>6s}" for s in stats)
    print(hdr)
    for i, s in enumerate(stats):
        row = f"  {s:<5s}" + "  ".join(
            f"{mat[i][j]:+6.2f}" for j in range(len(stats))
        )
        print(row)


def power_iteration(
    mat: list[list[float]],
    iters: int = 100,
) -> tuple[list[float], float]:
    """
    Compute the leading eigenvector of a symmetric matrix via simple
    power iteration. Returns (eigenvector, eigenvalue).

    We need this for the single-factor lambda loadings: the normalized
    leading eigenvector IS the latent "boom/bust" factor loading for
    each stat.
    """
    k = len(mat)
    v = [1.0 / math.sqrt(k)] * k
    val = 0.0
    for _ in range(iters):
        nv = [0.0] * k
        for i in range(k):
            s = 0.0
            for j in range(k):
                s += mat[i][j] * v[j]
            nv[i] = s
        norm = math.sqrt(sum(x * x for x in nv))
        if norm == 0:
            break
        v = [x / norm for x in nv]
        # Rayleigh quotient
        rq_num = 0.0
        for i in range(k):
            for j in range(k):
                rq_num += v[i] * mat[i][j] * v[j]
        val = rq_num
    return v, val


def print_factor_loadings(
    mat: list[list[float]],
    stats: list[str],
    label: str,
) -> list[float]:
    v, val = power_iteration(mat)
    print(f"\n════════ {label}: leading factor (eigenvalue={val:.3f}) ════════")
    print(f"  {'stat':<6s}  {'loading':>8s}")
    for s, x in zip(stats, v):
        print(f"  {s:<6s}  {x:>+8.3f}")
    return v


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def _fingerprint(rows: list, n: int = 10) -> tuple:
    """Extract a small signature from a row list for cross-year comparison.

    Returns (row_count, top-n-playerids-by-order). If two years return
    the same fingerprint, the endpoint almost certainly ignored the year
    parameter and silently returned the same data both times.
    """
    if not isinstance(rows, list):
        return (0, ())
    pids = []
    for row in rows[:n]:
        pid = _pid(row) if isinstance(row, dict) else None
        pids.append(pid or "")
    return (len(rows), tuple(pids))


def run_year(year: int) -> dict:
    """Fetch projections + actuals for a single year, return hitter and
    pitcher residual tables plus the raw actuals for the YoY path."""
    print(f"\n── {year} ────────────────────────────────────────")
    t0 = time.time()

    proj_bat = fetch_preseason_projections(year, "bat")
    proj_pit = fetch_preseason_projections(year, "pit")
    act_bat  = fetch_actuals(year, "bat")
    act_pit  = fetch_actuals(year, "pit")

    hit = build_residuals(proj_bat, act_bat, HITTER_STATS)
    pit = build_residuals(proj_pit, act_pit, PITCHER_STATS)

    print(f"  [{year}] built {len(hit)} hitter residuals, "
          f"{len(pit)} pitcher residuals in {time.time()-t0:.1f}s")
    return {
        "hitters":    hit,
        "pitchers":   pit,
        "proj_bat_fp": _fingerprint(proj_bat),
        "proj_pit_fp": _fingerprint(proj_pit),
        "act_bat":    act_bat,
        "act_pit":    act_pit,
    }


def merge_years(years_data: dict[int, dict], side: str) -> dict[str, dict]:
    """Concatenate years, prefixing pid with year so repeat players
    across years count as separate data points."""
    merged: dict[str, dict] = {}
    for year, yd in years_data.items():
        for pid, rec in yd[side].items():
            merged[f"{year}:{pid}"] = rec
    return merged


def _diagnose_projection_endpoint(years_data: dict[int, dict]) -> bool:
    """Detect when the FG projections endpoint ignored the year param.

    Returns True if the projections look trustworthy (distinct per year).
    Prints a loud warning and returns False if identical signatures
    appear across years — in that case the projection-based residual
    path is unreliable and we should lean on the YoY path.
    """
    years = sorted(years_data.keys())
    if len(years) < 2:
        return True
    print("\n════════ Projection endpoint diagnostic ════════")
    trustworthy = True
    for tag, key in (("bat", "proj_bat_fp"), ("pit", "proj_pit_fp")):
        fps = [(y, years_data[y].get(key)) for y in years]
        for (y1, f1), (y2, f2) in zip(fps, fps[1:]):
            n1 = f1[0] if f1 else 0
            n2 = f2[0] if f2 else 0
            if n1 == 0 or n2 == 0:
                print(f"  — {tag}: {y1} ({n1} rows) vs {y2} ({n2} rows) — "
                      f"empty pool, skipping comparison")
                continue
            if f1 == f2:
                print(f"  ⚠ {tag}: {y1} and {y2} returned IDENTICAL data "
                      f"(n={n1}, same top-10 pids) — "
                      f"FG projections endpoint is ignoring year param.")
                trustworthy = False
            else:
                print(f"  ✓ {tag}: {y1} ({n1} rows) vs {y2} ({n2} rows) — distinct")
    if not trustworthy:
        print("  → Projection-based residual path is UNRELIABLE.")
        print("  → Relying on YoY residual path (projection-free) instead.")
    return trustworthy


def main() -> int:
    print(f"[backtest] Pulling {BACKTEST_YEARS} projections + actuals…")

    years_data: dict[int, dict] = {}
    for y in BACKTEST_YEARS:
        years_data[y] = run_year(y)

    # ── Diagnostic: did the projections endpoint actually honor year? ─
    proj_trustworthy = _diagnose_projection_endpoint(years_data)

    merged_hit = merge_years(years_data, "hitters")
    merged_pit = merge_years(years_data, "pitchers")
    print(f"\n[backtest] Projection pool: {len(merged_hit)} hitter-seasons, "
          f"{len(merged_pit)} pitcher-seasons")

    # ── PROJECTION-BASED path (may be junk if diagnostic failed) ─────
    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║  PATH 1 — Projection-based residuals                     ║")
    if not proj_trustworthy:
        print("║  ⚠ UNRELIABLE — projections identical across years      ║")
    print("╚══════════════════════════════════════════════════════════╝")

    hit_resid = summarize_residuals(
        merged_hit, HITTER_STATS, "Hitters (PA ≥ 200)", qualify_hitter,
    )
    sp_resid = summarize_residuals(
        merged_pit, PITCHER_STATS, f"Starters (IP ≥ {MIN_IP_STARTER})",
        lambda r: classify_pitcher(r) == "SP",
    )
    rp_resid = summarize_residuals(
        merged_pit, PITCHER_STATS, f"Relievers (IP ≥ {MIN_IP_RELIEVER})",
        lambda r: classify_pitcher(r) == "RP",
    )

    # ── YoY path (projection-free — always trustworthy) ──────────────
    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║  PATH 2 — Year-over-year residuals (projection-free)     ║")
    print("║  single_year_sigma = yoy_sigma / √2                       ║")
    print("╚══════════════════════════════════════════════════════════╝")

    actuals_bat_by_year = {y: years_data[y]["act_bat"] for y in BACKTEST_YEARS}
    actuals_pit_by_year = {y: years_data[y]["act_pit"] for y in BACKTEST_YEARS}

    yoy_hit = build_yoy_residuals(actuals_bat_by_year, HITTER_STATS, BACKTEST_YEARS)
    yoy_pit = build_yoy_residuals(actuals_pit_by_year, PITCHER_STATS, BACKTEST_YEARS)
    print(f"\n[backtest] YoY pool: {len(yoy_hit)} hitter-pairs, "
          f"{len(yoy_pit)} pitcher-pairs")

    # For YoY, qualification is on year-A actual PA/IP (we stored that
    # under "projected" so the existing qualifier functions just work).
    yoy_hit_summary = summarize_yoy(
        yoy_hit, HITTER_STATS, "Hitters (PA_A ≥ 200)", qualify_hitter,
    )
    yoy_sp_summary = summarize_yoy(
        yoy_pit, PITCHER_STATS, f"Starters (IP_A ≥ {MIN_IP_STARTER})",
        lambda r: classify_pitcher(r) == "SP",
    )
    yoy_rp_summary = summarize_yoy(
        yoy_pit, PITCHER_STATS, f"Relievers (IP_A ≥ {MIN_IP_RELIEVER})",
        lambda r: classify_pitcher(r) == "RP",
    )

    # ── Residual correlation matrices ─────────────────────────────────
    # Use YoY records as the primary source when projections are junk;
    # the YoY residuals ARE the "real variance" we want to correlate.
    if proj_trustworthy:
        corr_hit_src, corr_pit_src = merged_hit, merged_pit
        corr_label = "projection residuals"
    else:
        corr_hit_src, corr_pit_src = yoy_hit, yoy_pit
        corr_label = "YoY residuals"
    print(f"\n[backtest] Correlation source: {corr_label}")

    hit_corr, hit_n = compute_correlation_matrix(
        corr_hit_src, HITTER_STATS, qualify_hitter,
    )
    print_correlation_matrix(hit_corr, HITTER_STATS, "Hitters", hit_n)
    hit_load = print_factor_loadings(hit_corr, HITTER_STATS, "Hitters")

    sp_corr, sp_n = compute_correlation_matrix(
        corr_pit_src, PITCHER_STATS,
        lambda r: classify_pitcher(r) == "SP",
    )
    print_correlation_matrix(sp_corr, PITCHER_STATS, "Starters", sp_n)
    sp_load = print_factor_loadings(sp_corr, PITCHER_STATS, "Starters")

    rp_corr, rp_n = compute_correlation_matrix(
        corr_pit_src, PITCHER_STATS,
        lambda r: classify_pitcher(r) == "RP",
    )
    print_correlation_matrix(rp_corr, PITCHER_STATS, "Relievers", rp_n)
    rp_load = print_factor_loadings(rp_corr, PITCHER_STATS, "Relievers")

    # ── Write cache ──────────────────────────────────────────────────
    cache = {
        "generated_at":  datetime.now().isoformat(),
        "years":         BACKTEST_YEARS,
        "hitter_stats":  HITTER_STATS,
        "pitcher_stats": PITCHER_STATS,
        "projection_endpoint_trustworthy": proj_trustworthy,
        "correlation_source": corr_label,
        "residual_summary": {
            "hitters":  hit_resid,
            "starters": sp_resid,
            "relievers": rp_resid,
        },
        "yoy_summary": {
            "hitters":  yoy_hit_summary,
            "starters": yoy_sp_summary,
            "relievers": yoy_rp_summary,
        },
        "correlation": {
            "hitters":  {"matrix": hit_corr, "n": hit_n, "factor_loading": hit_load},
            "starters": {"matrix": sp_corr,  "n": sp_n,  "factor_loading": sp_load},
            "relievers": {"matrix": rp_corr, "n": rp_n,  "factor_loading": rp_load},
        },
        "sample_sizes": {
            "hitter_seasons":  len(merged_hit),
            "pitcher_seasons": len(merged_pit),
            "yoy_hitter_pairs":  len(yoy_hit),
            "yoy_pitcher_pairs": len(yoy_pit),
        },
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=1)
    size_kb = os.path.getsize(OUTPUT_FILE) / 1024
    print(f"\n[backtest] Wrote {OUTPUT_FILE} ({size_kb:.0f} KB)")
    if proj_trustworthy:
        print("[backtest] Next: divide each residual_cv by the inter_system_cv")
        print("           from sim_data_cache.json — that ratio is the sigma")
        print("           scaling factor for the sim.")
    else:
        print("[backtest] Next: use yoy_summary[*][single_year_cv] as the")
        print("           actual sigma. Compare to inter_system_cv from")
        print("           sim_data_cache.json to derive the scaling factor.")
    print("[backtest] Done. No dashboard files were modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
