"""
sim_module.py
─────────────
Step 3 of the sim refactor: the Monte Carlo finish-probability engine.

Consumes (all pre-built by earlier steps):
  • sim_data_cache.json     — per-player consensus projections by system,
                              plus Zimmerman-calibrated injury expected_loss
  • sim_backtest_cache.json — YoY single-year σ per stat × role, plus the
                              full within-player residual correlation matrix
  • espn_rosters.json       — 10-team league snapshot with roster slots

Outputs:
  • Console table: per-team finish distribution (P(1st), P(top-3), P(last),
    expected roto points, 90 % CI on final rank)
  • sim_results.json with the full per-trial team totals for later analysis

Architecture notes (see sim_constants_proposal.md for the original design):
  • BASE σ is the MEASURED YoY single-year CV from the backtest, not the
    hand-waved guesses in the proposal. The proposal was ~2× too tight.
  • Within-player cross-stat correlation uses a Cholesky factor of the
    MEASURED residual correlation matrix (not a single-factor λ model).
  • Injury expected_loss is Zimmerman-calibrated per-player — already done
    in sim_data_fetch.py and stored in sim_data_cache.json.
  • Closer role change is a DISCRETE event (job lost / kept) because the
    Gaussian shock model can't capture bimodal save/hold outcomes.

Standalone — does NOT import from fantasy.py, lineup_optimizer.py, or any
dashboard module. Safe to run without touching the live pipeline.

Usage:
    python sim_module.py                  # N = 50 000
    python sim_module.py --trials 10000   # quick pass
    python sim_module.py --seed 42        # reproducible
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
import unicodedata
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment


# ══════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════

# ── League setup ───────────────────────────────────────────────────────
# Honey Nut Chourios — 10-team H2H, 12 categories (6 H, 6 P).
HIT_CATS       = ["R", "HR", "RBI", "SO", "SB", "OBP"]
PIT_CATS       = ["W", "SO", "SV", "HLD", "ERA", "WHIP"]
HIT_NEG_CATS   = {"SO"}                       # hitter K — lower is better
PIT_NEG_CATS   = {"ERA", "WHIP"}              # lower is better
NUM_TEAMS      = 10

# ── Backtest cache stat lists (MUST match sim_backtest_fetch constants) ─
BT_HIT_STATS = ["PA", "R", "HR", "RBI", "SB", "SO", "OBP", "AVG"]
BT_PIT_STATS = ["IP", "W", "SO", "ERA", "WHIP", "SV", "HLD", "K/9", "BB/9"]
RATIO_STATS  = {"OBP", "AVG", "ERA", "WHIP", "K/9", "BB/9"}

# ── Rookie confidence bump (Q2 in the planning step) ──────────────────
# The YoY pool over-represents established players (both-year qualifiers).
# Rookies get a small σ bump; everyone else gets 1.0x.
CONF_MULT_ROOKIE = 1.15
ROOKIE_PA_THRESHOLD = 200        # career PA < this = rookie
ROOKIE_IP_THRESHOLD = 50         # career IP < this = rookie

# ── Closer role-change model (from sim_constants_proposal.md §6) ──────
CLOSER_SV_THRESHOLD   = 15       # SV projection above this = projected closer
CLOSER_BASE_P         = 0.30     # 30% baseline chance of losing the job
CLOSER_ERA_BUMP_380   = 0.10     # +10% if projected ERA > 3.80
CLOSER_ERA_BUMP_420   = 0.20     # +20% if projected ERA > 4.20 (replaces)
CLOSER_P_CAP          = 0.65
CLOSER_SAVES_TRANSFER = 0.70     # 70% of remaining saves go to successor

# ── Starter rotation spot (lighter — proposal §7) ─────────────────────
STARTER_AT_RISK_IP_MAX = 130     # SPs with IP_proj below this are at-risk
STARTER_LOSS_P         = 0.20
STARTER_IP_TRANSFERRED = 0.50    # half their remaining IP disappears

# ── ESPN slot IDs (mirror parse_espn_rosters.py) ──────────────────────
INACTIVE_LINEUP_SLOTS = {17, 19}  # IL, NA
PITCHER_ELIGIBLE_SLOTS = {13, 14, 15}

# Hitter lineup slot labels (only the slots ESPN actually uses as
# starting hitter spots — these are the columns in the assignment
# problem). Pitcher starting slots (13/14/15) are ignored because
# Alex wants every rostered pitcher to contribute, not just the
# starting 6.
HITTER_STARTER_SLOTS = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}
BENCH_SLOT_ID = 16

# Fallback hitter lineup config if the ESPN settings block is missing.
# Matches Alex's "Honey Nut Chourios" league: C, 1B, 2B, 3B, SS,
# OF×3, MI, CI, UTIL → 11 starters.
DEFAULT_HITTER_LINEUP_COUNTS: dict[int, int] = {
    0: 1, 1: 1, 2: 1, 3: 1, 4: 1,
    5: 3, 6: 1, 7: 1, 12: 1,
}

# For the roto value function: hitters with fewer than this many
# projected PA are excluded from the mean/std baseline. Keeps
# fringe callups from dragging the league average down.
HIT_VALUE_QUAL_PA = 200

# ── Files ──────────────────────────────────────────────────────────────
SIM_CACHE_FILE  = "sim_data_cache.json"
BT_CACHE_FILE   = "sim_backtest_cache.json"
ROSTER_FILE     = "espn_rosters.json"
RESULTS_FILE    = "sim_results.json"


# ══════════════════════════════════════════════════════════════════════
# Small utilities
# ══════════════════════════════════════════════════════════════════════

def _norm_name(nm: str) -> str:
    """Lowercase, strip diacritics, punctuation, and common suffixes.

    ESPN's export uses plain ASCII ("Jose Ramirez") while FG's
    projection rows preserve diacritics ("José Ramírez"). Without the
    NFKD decomposition pass below we drop ~8 players per join.
    """
    nm = (nm or "").strip()
    # NFKD decomposition separates base chars from combining marks;
    # filtering out combining marks gives us ASCII fallback.
    nm = unicodedata.normalize("NFKD", nm)
    nm = "".join(ch for ch in nm if not unicodedata.combining(ch))
    nm = nm.lower()
    for junk in [".", ",", "'", "-"]:
        nm = nm.replace(junk, "")
    for suf in [" jr", " sr", " ii", " iii", " iv"]:
        if nm.endswith(suf):
            nm = nm[: -len(suf)]
    return " ".join(nm.split())


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _median(vs: list[float]) -> float:
    return statistics.median(vs) if vs else 0.0


# ══════════════════════════════════════════════════════════════════════
# Cache loading
# ══════════════════════════════════════════════════════════════════════

def load_caches() -> tuple[dict, dict, dict]:
    """Load all three inputs. Fail loudly if any is missing."""
    missing = [f for f in (SIM_CACHE_FILE, BT_CACHE_FILE, ROSTER_FILE)
               if not os.path.exists(f)]
    if missing:
        raise SystemExit(
            f"[sim] Missing required cache files: {missing}. "
            f"Run sim_data_fetch.py and sim_backtest_fetch.py first."
        )
    sim_cache = json.load(open(SIM_CACHE_FILE))
    bt_cache  = json.load(open(BT_CACHE_FILE))
    rosters   = json.load(open(ROSTER_FILE))
    return sim_cache, bt_cache, rosters


# ══════════════════════════════════════════════════════════════════════
# Per-player consensus projections + injury loss from sim_data_cache
# ══════════════════════════════════════════════════════════════════════

def build_player_consensus(sim_cache: dict) -> dict[str, dict]:
    """
    Flatten sim_data_cache into {fg_id → record}.

    Each record holds:
        name, team, fg_id, mlbam, pos, is_pitcher,
        mu[stat]         — median across projection systems
        expected_loss    — Zimmerman injury expected_loss (if computed)
        injury_tier      — none|mild|moderate|severe
        volume_proj      — PA for hitters, IP for pitchers
        role             — hitter|SP|RP
    """
    out: dict[str, dict] = {}
    for side in ("hitters", "pitchers"):
        for fg_id, p in sim_cache.get(side, {}).items():
            by_sys = p.get("by_system") or {}
            if not by_sys:
                continue

            # Consensus mu = median across the systems that returned this
            # player for each stat. We use the backtest-stat lists so
            # everything downstream is aligned.
            stats_list = BT_PIT_STATS if side == "pitchers" else BT_HIT_STATS
            mu: dict[str, float] = {}
            for s in stats_list:
                vals = []
                for row in by_sys.values():
                    v = _to_float(row.get(s))
                    if v is None:
                        continue
                    # BATX returns structural SV/HLD = 0 for every reliever —
                    # those zeros would drag the median. Skip them.
                    if s in ("SV", "HLD") and v == 0:
                        continue
                    vals.append(v)
                if vals:
                    mu[s] = _median(vals)

            if not mu:
                continue

            # Injury data (may be absent if sim_data_fetch was run before
            # the Zimmerman block was added)
            inj = p.get("injury") or {}
            expected_loss = _to_float(inj.get("expected_loss")) or 0.0
            injury_tier   = inj.get("injury_flag_tier") or "none"
            role          = inj.get("role") or ("SP" if side == "pitchers" else "hitter")

            # Infer SP vs RP from projected IP/SV when the Zimmerman field
            # is missing (older sim_data_cache format)
            if side == "pitchers" and role == "SP":
                ip = mu.get("IP", 0.0)
                sv = mu.get("SV", 0.0)
                if ip < 80 or sv >= 5:
                    role = "RP"

            volume = mu.get("PA", 0.0) if side == "hitters" else mu.get("IP", 0.0)

            out[fg_id] = {
                "fg_id":        fg_id,
                "mlbam":        p.get("mlbam"),
                "name":         p.get("name") or "",
                "team":         p.get("team") or "",
                "pos":          p.get("pos") or "",
                "is_pitcher":   side == "pitchers",
                "mu":           mu,
                "expected_loss": expected_loss,
                "injury_tier":  injury_tier,
                "volume_proj":  volume,
                "role":         role,
            }

    # Add a per-hitter roto-value score used by the lineup optimizer.
    # This is a linear proxy: sum of z-scored projected contributions
    # across the 6 hitter categories. It only needs to rank-order
    # players for the assignment problem — the actual sim still draws
    # full correlated shocks downstream, so small inaccuracies here
    # don't bias the finish distribution, they just pick which
    # borderline bench guy gets the UTIL nod.
    _attach_hit_value(out)
    return out


def _attach_hit_value(consensus: dict[str, dict]) -> None:
    """
    Mutate consensus in place, adding a `hit_value` float to every
    hitter record. Uses equal-weighted z-scores across (R, HR, RBI,
    SB, OBP, SO) computed over the pool of qualified hitters
    (PA ≥ HIT_VALUE_QUAL_PA). Strikeouts contribute negatively.

    OBP is used as a rate — multiplied by PA/600 inside the z so a
    full-time .340 scores higher than a 200-PA .340 bat. That way
    the optimizer naturally favours fuller-time regulars for the
    UTIL slot when positional needs are already met.
    """
    cats_pos = ("R", "HR", "RBI", "SB")
    # Build the qualified-hitter population
    pool = [
        r for r in consensus.values()
        if not r["is_pitcher"]
        and (r["mu"].get("PA") or 0.0) >= HIT_VALUE_QUAL_PA
    ]
    if not pool:
        for r in consensus.values():
            if not r["is_pitcher"]:
                r["hit_value"] = 0.0
        return

    stats_matrix: dict[str, list[float]] = {c: [] for c in cats_pos}
    so_list: list[float] = []
    obp_scaled: list[float] = []
    for r in pool:
        mu = r["mu"]
        pa = mu.get("PA") or 0.0
        for c in cats_pos:
            stats_matrix[c].append(mu.get(c) or 0.0)
        so_list.append(mu.get("SO") or 0.0)
        obp_scaled.append((mu.get("OBP") or 0.0) * pa / 600.0)

    def _stats(xs: list[float]) -> tuple[float, float]:
        m = float(np.mean(xs))
        s = float(np.std(xs)) or 1.0
        return m, s

    stat_params = {c: _stats(stats_matrix[c]) for c in cats_pos}
    so_m, so_s = _stats(so_list)
    obp_m, obp_s = _stats(obp_scaled)

    for rec in consensus.values():
        if rec["is_pitcher"]:
            continue
        mu = rec["mu"]
        pa = mu.get("PA") or 0.0
        val = 0.0
        for c in cats_pos:
            m, s = stat_params[c]
            val += ((mu.get(c) or 0.0) - m) / s
        val -= ((mu.get("SO") or 0.0) - so_m) / so_s
        val += (((mu.get("OBP") or 0.0) * pa / 600.0) - obp_m) / obp_s
        rec["hit_value"] = val


# ══════════════════════════════════════════════════════════════════════
# σ vector + Cholesky for each role group from sim_backtest_cache
# ══════════════════════════════════════════════════════════════════════

def _as_sigma_vec(
    yoy_summary_role: dict,
    stats: list[str],
) -> np.ndarray:
    """Return σ CV vector for a role group in the given stat order.
    Uses single_year_cv (which is yoy_cv / √2)."""
    out = np.zeros(len(stats), dtype=np.float64)
    for i, s in enumerate(stats):
        rec = yoy_summary_role.get(s) or {}
        cv = rec.get("single_year_cv")
        out[i] = float(cv) if cv is not None else 0.0
    return out


def _safe_cholesky(mat: np.ndarray) -> np.ndarray:
    """
    Cholesky factor of a possibly-slightly-non-PD correlation matrix.

    Adds a small diagonal loading if numpy complains. Falls back to the
    eigenvalue-clip recovery if that still fails.
    """
    m = 0.5 * (mat + mat.T)  # enforce exact symmetry
    for jitter in (0.0, 1e-6, 1e-4, 1e-2):
        try:
            return np.linalg.cholesky(m + jitter * np.eye(m.shape[0]))
        except np.linalg.LinAlgError:
            continue
    # Last resort: clip negative eigenvalues to zero and reconstruct
    w, V = np.linalg.eigh(m)
    w = np.clip(w, 1e-6, None)
    m_fixed = V @ np.diag(w) @ V.T
    m_fixed = 0.5 * (m_fixed + m_fixed.T)
    return np.linalg.cholesky(m_fixed)


def build_role_models(bt_cache: dict) -> dict[str, dict]:
    """
    Return {role: {stats, sigma_cv, corr, chol}} for hitter / SP / RP.

    stats  — ordered list of stat names matching the corr matrix rows
    sigma_cv — array of single_year_cv per stat
    corr   — full residual correlation matrix
    chol   — lower-triangular Cholesky factor
    """
    out: dict[str, dict] = {}

    groups = [
        ("hitter", "hitters",  BT_HIT_STATS),
        ("SP",     "starters", BT_PIT_STATS),
        ("RP",     "relievers", BT_PIT_STATS),
    ]
    for role, key, stats in groups:
        corr_block = bt_cache["correlation"].get(key, {})
        mat = np.array(corr_block.get("matrix") or [], dtype=np.float64)
        if mat.size == 0 or mat.shape[0] != len(stats):
            raise SystemExit(
                f"[sim] Backtest correlation matrix missing / wrong shape "
                f"for {key}: expected {len(stats)}x{len(stats)}, "
                f"got {mat.shape}."
            )
        yoy  = bt_cache["yoy_summary"].get(key, {})
        sigma = _as_sigma_vec(yoy, stats)

        # Some stats (e.g. starter SV/HLD, reliever SV for non-closers)
        # have tiny-to-zero μ in practice — the shock is still drawn but
        # scaled by μ downstream, so zero σ there is fine.
        out[role] = {
            "stats":    stats,
            "sigma_cv": sigma,
            "corr":     mat,
            "chol":     _safe_cholesky(mat),
        }
    return out


# ══════════════════════════════════════════════════════════════════════
# ESPN roster join: ESPN player → consensus record
# ══════════════════════════════════════════════════════════════════════

def _espn_team_abbrev(proteam_id: int) -> str:
    table = {
        1: "BAL", 2: "BOS", 3: "LAA", 4: "CHW", 5: "CLE",
        6: "DET", 7: "KC",  8: "MIL", 9: "MIN", 10: "NYY",
        11: "OAK", 12: "SEA", 13: "TEX", 14: "TOR", 15: "ATL",
        16: "CHC", 17: "CIN", 18: "HOU", 19: "LAD", 20: "WSH",
        21: "NYM", 22: "PHI", 23: "PIT", 24: "STL", 25: "SD",
        26: "SF",  27: "COL", 28: "MIA", 29: "ARI", 30: "TB",
    }
    return table.get(proteam_id or 0, "")


# FanGraphs uses 3-letter codes for several teams (SDP, SFG, KCR, …),
# while ESPN uses 2-letter codes (SD, SF, KC, …). Without this map
# the (name, team) pass silently fails for every player whose name
# isn't globally unique — which is how Mason Miller (two of them in
# the consensus pool) slipped through the join even after the
# diacritic fix. The map is applied to the consensus side at index
# build time; ESPN already gives us the canonical short code.
_FG_TO_ESPN_TEAM = {
    "SDP": "SD",
    "SFG": "SF",
    "KCR": "KC",
    "TBR": "TB",
    "WSN": "WSH",
    "ATH": "OAK",  # FG has tagged the A's as ATH in recent seasons
}


def _norm_team(tm: str) -> str:
    t = (tm or "").strip().upper()
    return _FG_TO_ESPN_TEAM.get(t, t)


def _build_name_index(
    consensus: dict[str, dict],
) -> tuple[dict, dict, dict]:
    """Build three lookup dicts for joining ESPN players to consensus
    records: by (norm_name, team), by norm_name only (if unique), and
    by mlbam id."""
    by_nm_team: dict = {}
    by_nm:      dict = {}
    by_mlbam:   dict = {}
    for rec in consensus.values():
        nm = _norm_name(rec["name"])
        tm = _norm_team(rec["team"])
        if nm and tm:
            by_nm_team[(nm, tm)] = rec
        if nm:
            by_nm.setdefault(nm, []).append(rec)
        m = rec.get("mlbam")
        if m is not None:
            try:
                by_mlbam[int(m)] = rec
            except (TypeError, ValueError):
                pass
    return by_nm_team, by_nm, by_mlbam


def _extract_hitter_lineup_counts(rosters: dict) -> dict[int, int]:
    """
    Pull the league's starting-hitter slot counts from the ESPN
    settings blob. Only hitter slots are returned — pitcher slots
    are handled separately (all rostered pitchers count, no
    optimization needed).
    """
    raw = rosters.get("raw") or rosters
    s = (raw.get("settings") or {}).get("rosterSettings") or {}
    counts_raw = s.get("lineupSlotCounts") or {}
    out: dict[int, int] = {}
    for k, v in counts_raw.items():
        try:
            slot = int(k)
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        if slot in HITTER_STARTER_SLOTS:
            out[slot] = n
    if not out:
        print("[sim] warning: no hitter lineup counts in ESPN settings, "
              "using DEFAULT_HITTER_LINEUP_COUNTS")
        out = dict(DEFAULT_HITTER_LINEUP_COUNTS)
    return out


def _optimize_hitter_lineup(
    hitters: list[dict],
    slot_counts: dict[int, int],
) -> tuple[list[dict], list[dict]]:
    """
    Given the matched hitters on a team, pick the best legal lineup
    respecting positional eligibility. Returns (starters, bench).

    The assignment problem is small (≤14 hitters × ≤12 slot
    instances) so we use scipy's Hungarian implementation directly.
    Ineligible player/slot pairs get a sentinel +INF cost that's
    still finite so scipy accepts the matrix.
    """
    if not hitters:
        return [], []

    # Flatten slot counts into a list of individual slot targets,
    # e.g. {5:3, 0:1} → [5, 5, 5, 0]
    slot_targets: list[int] = []
    for slot, n in sorted(slot_counts.items()):
        slot_targets.extend([slot] * n)

    n_hit = len(hitters)
    n_slots = len(slot_targets)

    # Cost matrix — scipy minimises, so use negative value
    BIG = 1e9
    cost = np.full((n_hit, n_slots), BIG, dtype=np.float64)
    for i, p in enumerate(hitters):
        elig = set(p.get("eligibleSlots") or [])
        val  = float(p.get("hit_value") or 0.0)
        # Shift value so every eligible cell is negative (scipy
        # minimises; by keeping even low-value eligibles below BIG
        # we guarantee the solver prefers "bad eligible" over
        # "ineligible"). The +20 offset is a safe headroom against
        # the widest plausible z-sum (~±8 across 6 cats).
        score = -(val + 20.0)
        for j, slot in enumerate(slot_targets):
            if slot in elig:
                cost[i, j] = score

    # If there are more hitters than slots, scipy returns min(M,N)
    # matches — exactly what we want (the unmatched hitters go to
    # bench).
    row_ind, col_ind = linear_sum_assignment(cost)

    starters: list[dict] = []
    starter_rows = set()
    for r, c in zip(row_ind, col_ind):
        if cost[r, c] >= BIG:  # ineligible fallback
            continue
        rec = dict(hitters[r])
        rec["lineup_slot"] = int(slot_targets[c])
        starters.append(rec)
        starter_rows.add(int(r))

    bench = [
        hitters[i] for i in range(n_hit) if i not in starter_rows
    ]
    return starters, bench


def join_rosters(
    rosters: dict,
    consensus: dict[str, dict],
) -> dict:
    """
    Walk the ESPN roster blob and build per-team player lists.

    Team composition rules (per Alex's roto configuration):
      • Hitters: only the optimal starting lineup contributes — we
        solve an assignment problem against the league's positional
        slot counts and drop the bench entirely.
      • Pitchers: every rostered pitcher counts (starting + bench),
        reflecting how daily-lineup pitching rotations accumulate
        stats across the week.
      • IL (slot 17) and NA (slot 19) are always skipped.
    """
    raw = rosters.get("raw") or rosters
    by_nm_team, by_nm, by_mlbam = _build_name_index(consensus)
    slot_counts = _extract_hitter_lineup_counts(rosters)
    n_start_hit = sum(slot_counts.values())

    teams_out: list = []
    unmatched: list = []
    join_stats = {
        "starters_h": 0, "pitchers": 0,
        "benched_h":  0, "unmatched": 0, "inactive": 0,
    }

    def _match(p: dict) -> dict | None:
        """Three-pass match: mlbam → name+team → unique name."""
        mlbam = p.get("id")
        nm = _norm_name(p.get("fullName", "") or "")
        tm = _espn_team_abbrev(p.get("proTeamId", 0))
        if mlbam is not None:
            try:
                rec = by_mlbam.get(int(mlbam))
                if rec is not None:
                    return rec
            except (TypeError, ValueError):
                pass
        if nm and tm:
            rec = by_nm_team.get((nm, tm))
            if rec is not None:
                return rec
        if nm:
            hits = by_nm.get(nm, [])
            if len(hits) == 1:
                return hits[0]
        return None

    for t in raw.get("teams", []):
        team_obj = {
            "team_id": t.get("id"),
            "name":    t.get("name", f"Team {t.get('id')}"),
            "abbrev":  (t.get("abbrev") or "").strip(),
            "players": [],
        }

        team_hitters: list[dict] = []
        team_pitchers: list[dict] = []

        for entry in t.get("roster", {}).get("entries", []):
            slot = entry.get("lineupSlotId")
            if slot in INACTIVE_LINEUP_SLOTS:
                join_stats["inactive"] += 1
                continue
            ppe = entry.get("playerPoolEntry") or {}
            p   = ppe.get("player") or {}
            full = p.get("fullName", "") or ""
            elig = p.get("eligibleSlots", []) or []
            is_pit_elig = any(s in PITCHER_ELIGIBLE_SLOTS for s in elig)

            rec = _match(p)
            if rec is None:
                unmatched.append({
                    "name": full,
                    "team": _espn_team_abbrev(p.get("proTeamId", 0)),
                    "is_pitcher": is_pit_elig,
                })
                join_stats["unmatched"] += 1
                continue

            rec = dict(rec)
            rec["espn_slot"] = slot
            rec["eligibleSlots"] = elig

            if is_pit_elig:
                team_pitchers.append(rec)
            else:
                team_hitters.append(rec)

        # Optimize hitter lineup — drop the bench entirely
        starters, bench = _optimize_hitter_lineup(team_hitters, slot_counts)
        join_stats["starters_h"] += len(starters)
        join_stats["benched_h"]  += len(bench)
        join_stats["pitchers"]   += len(team_pitchers)

        team_obj["players"] = starters + team_pitchers
        team_obj["bench_hitters"] = bench  # retained for diagnostics
        teams_out.append(team_obj)

    slot_cfg_str = ", ".join(
        f"{k}×{v}" for k, v in sorted(slot_counts.items())
    )
    print(f"[sim] Hitter lineup ({n_start_hit} starters): {slot_cfg_str}")
    print(f"[sim] Join: {join_stats['starters_h']} starting hitters + "
          f"{join_stats['pitchers']} pitchers matched, "
          f"{join_stats['benched_h']} bench hitters dropped, "
          f"{join_stats['unmatched']} unmatched, "
          f"{join_stats['inactive']} IL/NA skipped")
    if join_stats["unmatched"]:
        print("[sim] first 10 unmatched:")
        for u in unmatched[:10]:
            print(f"       {u['name']:<24} {u['team']:<4} "
                  f"{'P' if u['is_pitcher'] else 'H'}")

    return {
        "league_id":  rosters.get("leagueId"),
        "season_id":  rosters.get("seasonId"),
        "teams":      teams_out,
        "unmatched":  unmatched,
    }


# ══════════════════════════════════════════════════════════════════════
# Vectorized sampling — the heart of the sim
# ══════════════════════════════════════════════════════════════════════

def _sample_player(
    player: dict,
    role_model: dict,
    n_trials: int,
    rng: np.random.Generator,
) -> dict[str, np.ndarray]:
    """
    Draw n_trials samples for one player, correlated according to the
    Cholesky of the residual correlation matrix.

    Returns {stat: (n_trials,) float array} — only the stats the player
    has a μ for.
    """
    stats = role_model["stats"]         # ordered
    chol  = role_model["chol"]          # (k, k)
    cvs   = role_model["sigma_cv"]      # (k,)
    k     = len(stats)

    mu_vec = np.zeros(k, dtype=np.float64)
    have_stat = np.zeros(k, dtype=bool)
    for i, s in enumerate(stats):
        v = player["mu"].get(s)
        if v is not None:
            mu_vec[i]    = v
            have_stat[i] = True

    # Rookie confidence bump
    conf = 1.0
    if player["is_pitcher"]:
        if player["volume_proj"] < ROOKIE_IP_THRESHOLD:
            conf = CONF_MULT_ROOKIE
    else:
        if player["volume_proj"] < ROOKIE_PA_THRESHOLD:
            conf = CONF_MULT_ROOKIE

    # σ per stat = |μ| × CV × conf  (for ratio stats we floor |μ| so tiny
    # true values don't collapse σ to zero)
    sigma = np.zeros(k, dtype=np.float64)
    for i, s in enumerate(stats):
        if not have_stat[i]:
            continue
        m = abs(mu_vec[i])
        if s in RATIO_STATS:
            m = max(m, 0.01)
        else:
            m = max(m, 1.0)
        sigma[i] = m * cvs[i] * conf

    # Correlated shocks: z ~ N(0, I), shock = L @ z
    # We generate (n_trials, k) iid normals and right-multiply by L.T
    # so each row becomes one correlated shock vector.
    iid = rng.standard_normal(size=(n_trials, k))
    shocks = iid @ chol.T      # (n_trials, k)

    # samples[stat, trial] = μ + σ × shock
    samples: dict[str, np.ndarray] = {}
    for i, s in enumerate(stats):
        if not have_stat[i]:
            continue
        vals = mu_vec[i] + sigma[i] * shocks[:, i]
        # Floor counting stats at 0 — no negative HRs
        if s not in RATIO_STATS:
            np.maximum(vals, 0.0, out=vals)
        # OBP and AVG clipped to [0, 1]
        if s in ("OBP", "AVG"):
            np.clip(vals, 0.0, 1.0, out=vals)
        # ERA / WHIP clipped to [0, ∞) — already guaranteed, but ratio
        # stats can draw negative shocks that exceed μ. Floor at 0.
        if s in ("ERA", "WHIP", "K/9", "BB/9"):
            np.maximum(vals, 0.0, out=vals)
        samples[s] = vals

    return samples


def _apply_injury(
    samples: dict[str, np.ndarray],
    player: dict,
    n_trials: int,
    rng: np.random.Generator,
) -> None:
    """
    Multiply counting-stat samples by (1 − injury_fraction) per trial.

    injury_fraction is drawn from a Beta centered on the Zimmerman
    expected_loss fraction, with modest dispersion. Ratio stats are not
    affected (OBP doesn't change when you play less).
    """
    vol = player["volume_proj"]
    if vol <= 0:
        return
    exp_loss_frac = player["expected_loss"] / vol
    exp_loss_frac = max(0.0, min(exp_loss_frac, 0.9))

    # Beta parametrization: mean = exp_loss_frac, concentration = 8.
    # At mean = 0.1 → (α=0.8, β=7.2) → std ≈ 0.10 → realistic
    # At mean = 0.4 → (α=3.2, β=4.8) → std ≈ 0.16 → wider
    conc = 8.0
    a = max(exp_loss_frac * conc, 0.05)
    b = max((1 - exp_loss_frac) * conc, 0.05)
    frac = rng.beta(a, b, size=n_trials)
    keep = 1.0 - frac

    for s, arr in samples.items():
        if s in RATIO_STATS:
            continue
        arr *= keep


def _apply_closer_role(
    pitcher_samples: list[tuple[dict, dict[str, np.ndarray]]],
    n_trials: int,
    rng: np.random.Generator,
) -> None:
    """
    For every pitcher with projected SV > CLOSER_SV_THRESHOLD, draw a
    "loses role" event per trial. If fired: transfer 70% of remaining
    saves (already-sampled) to the next-best-ERA reliever on the same
    MLB team; demote the former closer's SV to near-zero; give him a
    small HLD bump as compensation.

    pitcher_samples is a list of (player_rec, sample_dict) tuples.
    Operates in-place on sample_dict["SV"] and ["HLD"].
    """
    # Index by MLB team
    by_mlb_team: dict[str, list[tuple[dict, dict]]] = {}
    for p, samp in pitcher_samples:
        tm = (p.get("team") or "").upper()
        by_mlb_team.setdefault(tm, []).append((p, samp))

    for tm, roster in by_mlb_team.items():
        closers = [
            (p, samp) for (p, samp) in roster
            if (p["mu"].get("SV", 0.0) > CLOSER_SV_THRESHOLD)
        ]
        if not closers:
            continue

        for closer_p, closer_samp in closers:
            # Per-season probability of losing the job
            prob = CLOSER_BASE_P
            era = closer_p["mu"].get("ERA", 4.0)
            if era > 4.20:
                prob += CLOSER_ERA_BUMP_420
            elif era > 3.80:
                prob += CLOSER_ERA_BUMP_380
            prob = min(prob, CLOSER_P_CAP)

            fired = rng.random(n_trials) < prob  # (n_trials,) bool

            # Find the best successor on same MLB team (non-closer
            # reliever with the lowest projected ERA). If no successor
            # in our fantasy-owned pool, the saves just vaporize.
            successor_samp = None
            best_era = float("inf")
            for (p2, samp2) in roster:
                if p2 is closer_p:
                    continue
                if p2.get("role") != "RP":
                    continue
                e2 = p2["mu"].get("ERA", 99.0)
                if e2 < best_era:
                    best_era = e2
                    successor_samp = samp2

            if "SV" in closer_samp:
                sv_orig = closer_samp["SV"].copy()
                # For fired trials, closer keeps 30% of saves (the early
                # part of the season before getting demoted)
                closer_samp["SV"][fired] *= (1.0 - CLOSER_SAVES_TRANSFER)
                # Transfer the 70% to successor (if present)
                if successor_samp is not None and "SV" in successor_samp:
                    transferred = sv_orig[fired] * CLOSER_SAVES_TRANSFER
                    successor_samp["SV"][fired] += transferred
            # Compensate with more holds when fired
            if "HLD" in closer_samp:
                # Demoted closers often pick up HLDs — add 5 per fired trial
                closer_samp["HLD"][fired] += 5.0


# ══════════════════════════════════════════════════════════════════════
# Team rollup + ranking
# ══════════════════════════════════════════════════════════════════════

def roll_team_totals(
    team: dict,
    player_samples: dict[str, dict[str, np.ndarray]],
    n_trials: int,
) -> dict[str, np.ndarray]:
    """
    Sum each player's per-trial samples into team category totals.

    For counting stats (R, HR, RBI, SO_h, SB, W, K_p, SV, HLD): sum.
    For ratio stats (OBP, ERA, WHIP): compute the weighted average
    from the underlying components when available, else fall back to
    simple mean. For v1 we use a weighted mean by PA (hitters) and IP
    (pitchers) to produce a defensible ratio-stat estimate.
    """
    totals: dict[str, np.ndarray] = {}
    zeros = lambda: np.zeros(n_trials, dtype=np.float64)

    # Counting stats accumulators
    sum_R   = zeros(); sum_HR  = zeros(); sum_RBI = zeros()
    sum_SOh = zeros(); sum_SB  = zeros()
    sum_W   = zeros(); sum_SOp = zeros(); sum_SV  = zeros(); sum_HLD = zeros()

    # Ratio stat accumulators: weighted sums + total weights
    sum_PA     = zeros()
    sum_OBPxPA = zeros()
    sum_IP     = zeros()
    sum_ERAxIP = zeros()
    sum_WHPxIP = zeros()

    for p in team["players"]:
        samp = player_samples.get(p["fg_id"])
        if samp is None:
            continue
        if p["is_pitcher"]:
            ip  = samp.get("IP")
            w   = samp.get("W")
            so  = samp.get("SO")
            sv  = samp.get("SV")
            hld = samp.get("HLD")
            era = samp.get("ERA")
            whp = samp.get("WHIP")
            if w   is not None: sum_W   += w
            if so  is not None: sum_SOp += so
            if sv  is not None: sum_SV  += sv
            if hld is not None: sum_HLD += hld
            if ip is not None:
                sum_IP += ip
                if era is not None:
                    sum_ERAxIP += era * ip
                if whp is not None:
                    sum_WHPxIP += whp * ip
        else:
            pa  = samp.get("PA")
            r   = samp.get("R")
            hr  = samp.get("HR")
            rbi = samp.get("RBI")
            so  = samp.get("SO")
            sb  = samp.get("SB")
            obp = samp.get("OBP")
            if r   is not None: sum_R   += r
            if hr  is not None: sum_HR  += hr
            if rbi is not None: sum_RBI += rbi
            if so  is not None: sum_SOh += so
            if sb  is not None: sum_SB  += sb
            if pa is not None:
                sum_PA += pa
                if obp is not None:
                    sum_OBPxPA += obp * pa

    # Stash counting stats
    totals["R"]   = sum_R
    totals["HR"]  = sum_HR
    totals["RBI"] = sum_RBI
    totals["SB"]  = sum_SB
    totals["W"]   = sum_W
    totals["SV"]  = sum_SV
    totals["HLD"] = sum_HLD

    # Hitter SO is shared name; we track as HIT_SO
    totals["SO_h"] = sum_SOh
    totals["SO_p"] = sum_SOp

    # Weighted ratios with safe divisors
    pa_floor  = np.maximum(sum_PA, 1.0)
    ip_floor  = np.maximum(sum_IP, 1.0)
    totals["OBP"]  = sum_OBPxPA / pa_floor
    totals["ERA"]  = sum_ERAxIP / ip_floor
    totals["WHIP"] = sum_WHPxIP / ip_floor

    return totals


def rank_and_score(
    team_totals: list[dict[str, np.ndarray]],
    n_trials: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Standard rotisserie scoring across the 12 league categories.

    For each category in each trial, rank all 10 teams (1 = best). For
    negative cats (hitter K, ERA, WHIP), rank in ascending order. For
    positive cats, rank in descending order. Sum the ranks across cats →
    that's the team's roto "points" for the trial — lower is better.

    Then convert "points" to final finish rank (1-10 by ascending sum).

    Returns:
        (points, finish)  — both shape (num_teams, n_trials), int.
    """
    T = len(team_totals)
    # Stack each cat into a (T, n_trials) matrix
    cat_names = [
        ("R",    "H", False),
        ("HR",   "H", False),
        ("RBI",  "H", False),
        ("SO_h", "H", True),   # negative
        ("SB",   "H", False),
        ("OBP",  "H", False),
        ("W",    "P", False),
        ("SO_p", "P", False),
        ("SV",   "P", False),
        ("HLD",  "P", False),
        ("ERA",  "P", True),   # negative
        ("WHIP", "P", True),   # negative
    ]

    points = np.zeros((T, n_trials), dtype=np.float64)
    for cat, _, neg in cat_names:
        stack = np.stack([tt[cat] for tt in team_totals], axis=0)  # (T, n)
        # argsort: indices of teams sorted ascending. For positive cats
        # we want DESCENDING (highest = best = lowest rank index), so
        # sort by -stack.
        if neg:
            order = np.argsort(stack, axis=0)
        else:
            order = np.argsort(-stack, axis=0)
        # ranks[team, trial] = 1-based position in sorted order
        ranks = np.empty_like(order, dtype=np.float64)
        for r in range(T):
            ranks[order[r, :], np.arange(n_trials)] = r + 1
        # Accumulate: lower is better in our points convention
        points += ranks

    # Convert to final finish (1 = best total points which means lowest sum)
    finish_order = np.argsort(points, axis=0)   # (T, n_trials)
    finish = np.empty_like(finish_order, dtype=np.int32)
    for r in range(T):
        finish[finish_order[r, :], np.arange(n_trials)] = r + 1
    return points, finish


# ══════════════════════════════════════════════════════════════════════
# Main sim driver
# ══════════════════════════════════════════════════════════════════════

def run_simulation(
    league: dict,
    role_models: dict,
    n_trials: int,
    seed: int | None = None,
) -> dict:
    rng = np.random.default_rng(seed)
    t0 = time.time()

    # Per-player samples
    all_samples: dict[str, dict[str, np.ndarray]] = {}
    # Pitcher samples with player refs — needed for the closer role logic,
    # which requires the same-MLB-team grouping across fantasy teams
    pitcher_refs: list[tuple[dict, dict]] = []

    for team in league["teams"]:
        for p in team["players"]:
            role = p["role"] if p["role"] in role_models else (
                "SP" if p["is_pitcher"] else "hitter"
            )
            rm = role_models[role]
            samp = _sample_player(p, rm, n_trials, rng)
            _apply_injury(samp, p, n_trials, rng)
            all_samples[p["fg_id"]] = samp
            if p["is_pitcher"]:
                pitcher_refs.append((p, samp))

    # Closer role-change (post-injury so saves transferred reflect
    # the already-injured successor)
    _apply_closer_role(pitcher_refs, n_trials, rng)

    # Team totals
    team_totals = [
        roll_team_totals(team, all_samples, n_trials)
        for team in league["teams"]
    ]

    # Ranking
    points, finish = rank_and_score(team_totals, n_trials)

    # Per-team finish distribution + category means
    # (Category means give a sanity-check view: if a team's jump is
    # driven by one cat (e.g. SV from a single closer) that's fragile,
    # while a jump spread across 6+ cats is a stable signal.)
    cat_order = [
        ("R", "R"), ("HR", "HR"), ("RBI", "RBI"),
        ("SO_h", "Kh"), ("SB", "SB"), ("OBP", "OBP"),
        ("W", "W"), ("SO_p", "Kp"), ("SV", "SV"),
        ("HLD", "HLD"), ("ERA", "ERA"), ("WHIP", "WHIP"),
    ]

    per_team: list[dict] = []
    for i, team in enumerate(league["teams"]):
        f = finish[i, :]
        dist = [float(np.mean(f == k)) for k in range(1, NUM_TEAMS + 1)]
        mean_finish = float(np.mean(f))
        mean_points = float(np.mean(points[i, :]))
        lo, hi = np.percentile(f, [5, 95])
        cat_means = {
            label: float(np.mean(team_totals[i][key]))
            for key, label in cat_order
        }
        per_team.append({
            "team_id":       team["team_id"],
            "name":          team["name"],
            "abbrev":        team["abbrev"],
            "n_players":     len(team["players"]),
            "expected_rank": mean_finish,
            "expected_roto": mean_points,
            "finish_dist":   dist,
            "rank_ci90":     [float(lo), float(hi)],
            "p_first":       dist[0],
            "p_top3":        sum(dist[:3]),
            "p_top5":        sum(dist[:5]),
            "p_last":        dist[-1],
            "cat_means":     cat_means,
        })

    per_team.sort(key=lambda x: x["expected_rank"])
    elapsed = time.time() - t0
    print(f"\n[sim] {n_trials} trials × {NUM_TEAMS} teams "
          f"in {elapsed:.1f}s")

    return {
        "n_trials": n_trials,
        "elapsed":  elapsed,
        "teams":    per_team,
    }


# ══════════════════════════════════════════════════════════════════════
# Reporting
# ══════════════════════════════════════════════════════════════════════

def print_standings(results: dict) -> None:
    print("\n════════ Projected Standings "
          "(sorted by expected rank) ════════")
    print(f"  {'rank':<5s}  {'team':<28s}  {'E[rank]':>7s}  "
          f"{'P(1st)':>7s}  {'P(top3)':>8s}  {'P(top5)':>8s}  "
          f"{'P(last)':>8s}  {'90% CI':>10s}")
    for rnk, t in enumerate(results["teams"], start=1):
        lo, hi = t["rank_ci90"]
        print(f"  {rnk:>4d}.  {t['name']:<28s}  {t['expected_rank']:>7.2f}  "
              f"{t['p_first']*100:>6.1f}%  {t['p_top3']*100:>7.1f}%  "
              f"{t['p_top5']*100:>7.1f}%  {t['p_last']*100:>7.1f}%  "
              f"{lo:>4.1f}-{hi:<4.1f}")


def print_category_breakdown(results: dict) -> None:
    """
    Print per-team mean totals across the 12 roto categories. This is
    the sanity-check view the standings table can't give you — if
    Team X jumped three spots, look here to see whether it was one
    category carrying the whole move or a broad-based improvement.

    Counting stats are printed as integers; rate stats (OBP, ERA,
    WHIP) to 3 decimals. Columns are ordered hitters first, then
    pitchers, to match the natural roto layout.
    """
    hit_cols  = [("R",   "R",   "{:>4.0f}"),
                 ("HR",  "HR",  "{:>4.0f}"),
                 ("RBI", "RBI", "{:>4.0f}"),
                 ("Kh",  "Kh",  "{:>4.0f}"),
                 ("SB",  "SB",  "{:>4.0f}"),
                 ("OBP", "OBP", "{:>5.3f}")]
    pit_cols  = [("W",    "W",    "{:>4.0f}"),
                 ("Kp",   "Kp",   "{:>5.0f}"),
                 ("SV",   "SV",   "{:>4.0f}"),
                 ("HLD",  "HLD",  "{:>4.0f}"),
                 ("ERA",  "ERA",  "{:>5.2f}"),
                 ("WHIP", "WHIP", "{:>5.2f}")]
    all_cols = hit_cols + pit_cols

    print("\n════════ Category Means (mean team total across "
          f"{results['n_trials']} trials) ════════")
    header = f"  {'team':<28s} " + " ".join(
        f"{label:>5s}" for _, label, _ in all_cols
    )
    print(header)
    for t in results["teams"]:
        cm = t["cat_means"]
        row_vals = []
        for key, _, fmt in all_cols:
            v = cm.get(key, 0.0)
            # All format strings are fixed-width (≤5 chars); pad to 5
            # so the header lines up even for the wider "{:>5.2f}"
            s = fmt.format(v)
            row_vals.append(f"{s:>5s}")
        print(f"  {t['name']:<28s} " + " ".join(row_vals))


def write_results(results: dict, path: str = RESULTS_FILE) -> None:
    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_trials":     results["n_trials"],
        "elapsed_sec":  results["elapsed"],
        "teams":        results["teams"],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    size = os.path.getsize(path) / 1024
    print(f"[sim] Wrote {path} ({size:.0f} KB)")


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(description="MLB fantasy Monte Carlo sim")
    ap.add_argument("--trials", type=int, default=50_000,
                    help="Number of Monte Carlo trials (default 50000)")
    ap.add_argument("--seed", type=int, default=None,
                    help="RNG seed for reproducibility")
    args = ap.parse_args()

    print(f"[sim] Loading caches…")
    sim_cache, bt_cache, rosters = load_caches()
    consensus = build_player_consensus(sim_cache)
    print(f"[sim] consensus pool: {len(consensus)} players "
          f"({sum(1 for p in consensus.values() if not p['is_pitcher'])} H, "
          f"{sum(1 for p in consensus.values() if p['is_pitcher'])} P)")

    role_models = build_role_models(bt_cache)
    for role, rm in role_models.items():
        k = len(rm['stats'])
        diag_ok = "OK" if np.allclose(np.diag(rm['corr']), 1.0, atol=0.05) else "!"
        print(f"[sim] role {role}: {k} stats, chol {rm['chol'].shape}, "
              f"diag {diag_ok}")

    league = join_rosters(rosters, consensus)
    print(f"[sim] league: {len(league['teams'])} teams")

    results = run_simulation(league, role_models, args.trials, args.seed)
    print_standings(results)
    print_category_breakdown(results)
    write_results(results)
    print("[sim] Done. No dashboard files were modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
