"""
sim_projections.py
──────────────────
Dashboard-integration glue for the Monte Carlo finish-probability sim.

The standalone sim_module.py runs end-to-end (roster parsing → lineup LAP →
Monte Carlo → standings table). For the dashboard we only want the MIDDLE
piece — the variability math — applied to the lineup the dashboard already
picked in lineup_optimizer.build_season_projections(). This module bridges
the two:

    sim_projections.build_sim_payload(parsed_league)
        -> {
            "role_models":  { hitter|SP|RP: {stats, sigma_cv, chol} },
            "closer_cfg":   { sv_threshold, base_p, era_bump_380, era_bump_420,
                              p_cap, saves_transfer },
            "players":      { espn_id: {mu, role, volume_proj, expected_loss,
                                        mlb_team, injury_tier} },
           }

That dict is merged into the phase-3 payload in fantasy.py so the in-browser
JS Monte Carlo can:
  • sample each player with correlated Cholesky shocks using the measured
    YoY single-year sigma from sim_backtest_cache.json,
  • trim counting stats by a Beta-distributed injury fraction (Zimmerman
    expected_loss from sim_data_cache.json),
  • apply the discrete closer role-change event (same MLB-team pool).

No network fetches, no heavy math — everything inlined from the existing
caches. The JS side has to do the sampling + rollup itself since the
dashboard is a static HTML file with no backend.
"""

from __future__ import annotations

import json
import os
import unicodedata
from typing import Any, Dict, List, Optional


# ── Path resolution ───────────────────────────────────────────────────────
_BASE = os.path.dirname(os.path.abspath(__file__))
SIM_DATA_CACHE   = os.path.join(_BASE, "sim_data_cache.json")
SIM_BT_CACHE     = os.path.join(_BASE, "sim_backtest_cache.json")


# ── Closer role-change knobs ─────────────────────────────────────────────
# Season-level P(lose job) bucketed by projected ERA. Elite closers (Clase
# etc.) are mostly locked in; shaky/bad closers have real turnover risk.
CLOSER_CFG = {
    "sv_threshold":    15,      # SV projection above this = projected closer
    "elite_era":       3.00,    # ERA ≤ this → elite bucket
    "average_era":     3.80,    # ERA ≤ this → average bucket
    "shaky_era":       4.20,    # ERA ≤ this → shaky bucket; above = bad
    "p_elite":         0.05,    # elite closer fire rate
    "p_average":       0.20,    # average closer fire rate
    "p_shaky":         0.40,    # shaky closer fire rate
    "p_bad":           0.50,    # bad closer fire rate
    "p_cap":           0.65,    # absolute cap (belt-and-suspenders)
    "saves_transfer":  0.70,    # fraction of saves that transfer to successor
    "hld_compensation": 5.0,    # +HLD for demoted closers
}

# ── Stat-list lists (must stay aligned with sim_backtest_fetch.py) ────────
BT_HIT_STATS = ["PA", "R", "HR", "RBI", "SB", "SO", "OBP", "AVG"]
BT_PIT_STATS = ["IP", "W", "SO", "ERA", "WHIP", "SV", "HLD", "K/9", "BB/9"]

# Team-abbreviation alias (FG's 3-letter → ESPN's 2-letter). Same map as
# sim_module._FG_TO_ESPN_TEAM.
_FG_TO_ESPN_TEAM = {
    "SDP": "SD",  "SFG": "SF",  "KCR": "KC",  "TBR": "TB",
    "WSN": "WSH", "ATH": "OAK",
}


def _norm_team(tm: Optional[str]) -> str:
    t = (tm or "").strip().upper()
    return _FG_TO_ESPN_TEAM.get(t, t)


def _norm_name(nm: str) -> str:
    """NFKD strip diacritics → lowercase → punctuation scrub → drop common
    suffixes. Same behaviour as sim_module._norm_name."""
    if not nm:
        return ""
    s = unicodedata.normalize("NFKD", nm)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    for ch in (".", ",", "'", "`"):
        s = s.replace(ch, "")
    s = s.replace("-", " ")
    toks = [t for t in s.split() if t not in ("jr", "sr", "ii", "iii", "iv")]
    return " ".join(toks)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        if f != f:  # NaN
            return default
        return f
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════════════════════════
# Role-model builder (sigma_cv + Cholesky) — direct port of
# sim_module.build_role_models, pruned to return JSON-safe dicts.
# ══════════════════════════════════════════════════════════════════════

def _safe_cholesky(mat: "list[list[float]] | Any") -> list:
    """Cholesky factor of a (possibly slightly non-PD) correlation matrix.
    Returns a list-of-lists — JSON-safe. Falls back to eigen-clipping."""
    import numpy as np
    m = np.array(mat, dtype=np.float64)
    m = 0.5 * (m + m.T)
    for jitter in (0.0, 1e-6, 1e-4, 1e-2):
        try:
            L = np.linalg.cholesky(m + jitter * np.eye(m.shape[0]))
            return L.tolist()
        except np.linalg.LinAlgError:
            continue
    w, V = np.linalg.eigh(m)
    w = np.clip(w, 1e-6, None)
    m_fixed = V @ np.diag(w) @ V.T
    m_fixed = 0.5 * (m_fixed + m_fixed.T)
    return np.linalg.cholesky(m_fixed).tolist()


def build_role_models(bt_cache: dict) -> Dict[str, Dict[str, Any]]:
    """Return {role: {stats, sigma_cv, chol}} for hitter / SP / RP.

    All values JSON-serialisable. sigma_cv is a plain list aligned to the
    stat order; chol is a list-of-lists (lower triangular).
    """
    out: Dict[str, Dict[str, Any]] = {}
    groups = [
        ("hitter", "hitters",  BT_HIT_STATS),
        ("SP",     "starters", BT_PIT_STATS),
        ("RP",     "relievers", BT_PIT_STATS),
    ]
    for role, key, stats in groups:
        corr_block = (bt_cache.get("correlation") or {}).get(key, {})
        mat = corr_block.get("matrix") or []
        if not mat or len(mat) != len(stats):
            # Fallback: identity correlation — sampling still works, just
            # without cross-stat correlation.
            mat = [[1.0 if i == j else 0.0 for j in range(len(stats))]
                   for i in range(len(stats))]
        yoy = (bt_cache.get("yoy_summary") or {}).get(key, {})
        sigma_cv: list[float] = []
        for s in stats:
            rec = yoy.get(s) or {}
            cv = rec.get("single_year_cv")
            sigma_cv.append(float(cv) if cv is not None else 0.0)
        out[role] = {
            "stats":     stats,
            "sigma_cv":  sigma_cv,
            "chol":      _safe_cholesky(mat),
        }
    return out


# ══════════════════════════════════════════════════════════════════════
# Player sim index (injury + volume + role)
# ══════════════════════════════════════════════════════════════════════

def _median(vs: list[float]) -> float:
    if not vs:
        return 0.0
    s = sorted(vs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def build_player_sim_index(sim_cache: dict) -> Dict[str, Dict[str, Any]]:
    """
    Walk sim_data_cache and return lookup dicts keyed by:
        by_mlbam:     int(mlbam) → rec
        by_name_team: (norm_name, norm_team) → rec
        by_name:      norm_name → [rec]   (fallback when team mismatches)

    Each rec holds:
        name, team, mlbam, fg_id, is_pitcher, role (SP/RP/hitter),
        expected_loss, injury_tier, volume_proj, mu (from median across
        the 3 projection systems — same as sim_module.build_player_consensus).
    """
    by_mlbam: Dict[int, dict] = {}
    by_name_team: Dict[tuple, dict] = {}
    by_name: Dict[str, list] = {}

    for side in ("hitters", "pitchers"):
        is_pit = side == "pitchers"
        stats_list = BT_PIT_STATS if is_pit else BT_HIT_STATS
        for fg_id, p in (sim_cache.get(side) or {}).items():
            by_sys = p.get("by_system") or {}
            if not by_sys:
                continue

            # Median μ across projection systems (skipping BATX's bogus
            # SV/HLD=0 for relievers — mirrors sim_module behaviour).
            mu: Dict[str, float] = {}
            for s in stats_list:
                vals = []
                for row in by_sys.values():
                    v = row.get(s)
                    if v is None:
                        continue
                    try:
                        fv = float(v)
                    except (TypeError, ValueError):
                        continue
                    if fv != fv:
                        continue
                    if s in ("SV", "HLD") and fv == 0:
                        continue
                    vals.append(fv)
                if vals:
                    mu[s] = _median(vals)
            if not mu:
                continue

            # Role / injury fields live at the TOP level of the player
            # record (not under an "injury" sub-dict). The standalone sim
            # module had a latent bug looking under p["injury"] which meant
            # expected_loss was always 0 — we fix it here.
            expected_loss = _safe_float(p.get("expected_loss"), 0.0)
            injury_tier   = p.get("injury_flag_tier") or "none"
            role          = p.get("role") or ("SP" if is_pit else "hitter")
            if is_pit and role == "SP":
                # Infer SP vs RP from the projection when the Zimmerman
                # field is missing/unreliable.
                ip = mu.get("IP", 0.0)
                sv = mu.get("SV", 0.0)
                if ip < 80 or sv >= 5:
                    role = "RP"

            volume = mu.get("PA", 0.0) if not is_pit else mu.get("IP", 0.0)

            rec = {
                "name":          p.get("name") or "",
                "team":          _norm_team(p.get("team")),
                "mlbam":         p.get("mlbam"),
                "fg_id":         fg_id,
                "is_pitcher":    is_pit,
                "role":          role,
                "expected_loss": expected_loss,
                "injury_tier":   injury_tier,
                "volume_proj":   volume,
                "mu":            mu,
            }

            try:
                m = rec["mlbam"]
                if m is not None:
                    by_mlbam[int(m)] = rec
            except (TypeError, ValueError):
                pass
            nm = _norm_name(rec["name"])
            tm = rec["team"]
            if nm and tm:
                by_name_team[(nm, tm)] = rec
            if nm:
                by_name.setdefault(nm, []).append(rec)

    return {"by_mlbam": by_mlbam, "by_name_team": by_name_team, "by_name": by_name}


# ══════════════════════════════════════════════════════════════════════
# Replacement-level production rates (for injury gap-fill)
# ══════════════════════════════════════════════════════════════════════

# 10-team × 15 hitters = 150 rostered; 10 × 8 pitchers = 80 rostered.
# Replacement pool = the next band of players just below rostered level
# (i.e. the top of what's left on the waiver wire). These are the guys a
# real manager actually picks up when a star goes down.
_HIT_ROSTERED_N     = 150
_HIT_REPLACEMENT_N  = 60        # players ranked 151..210 → avg these
_PIT_ROSTERED_N     = 80
_PIT_REPLACEMENT_N  = 40        # players ranked 81..120 → split SP/RP


def _hitter_replacement_rates(fut_h: list) -> Dict[str, float]:
    """Per-PA rates from the waiver-wire-adjacent hitter pool. Returns
    {R, HR, RBI, SB, SO, OBP} where counting stats are per-PA and OBP is
    a PA-weighted average."""
    if not fut_h:
        return {}
    # fut_h is already sorted by dollar desc in compute_fantasy_dollar_values
    pool = fut_h[_HIT_ROSTERED_N : _HIT_ROSTERED_N + _HIT_REPLACEMENT_N]
    if not pool:
        pool = fut_h[-_HIT_REPLACEMENT_N:]  # degenerate fallback

    sum_pa  = 0.0
    sum_r   = 0.0
    sum_hr  = 0.0
    sum_rbi = 0.0
    sum_sb  = 0.0
    sum_so  = 0.0
    sum_obpxpa = 0.0
    for e in pool:
        p  = e.get("player") or {}
        pa = _safe_float(p.get("PA_p"))
        if pa <= 0:
            continue
        sum_pa     += pa
        sum_r      += _safe_float(p.get("R_p"))
        sum_hr     += _safe_float(p.get("HR_p"))
        sum_rbi    += _safe_float(p.get("RBI_p"))
        sum_sb     += _safe_float(p.get("SB_p"))
        sum_so     += _safe_float(p.get("SO_p"))
        sum_obpxpa += _safe_float(p.get("OBP_p")) * pa
    if sum_pa <= 0:
        return {}
    return {
        "R":   round(sum_r   / sum_pa, 6),
        "HR":  round(sum_hr  / sum_pa, 6),
        "RBI": round(sum_rbi / sum_pa, 6),
        "SB":  round(sum_sb  / sum_pa, 6),
        "SO":  round(sum_so  / sum_pa, 6),
        "OBP": round(sum_obpxpa / sum_pa, 6),
    }


def _pitcher_replacement_rates(fut_p: list) -> Dict[str, Dict[str, float]]:
    """Split replacement rates by SP vs RP (using projected IP as the
    classifier). Returns {"SP": {...}, "RP": {...}} with counting stats
    per IP and ERA/WHIP as IP-weighted averages.

    SP replacement = the waiver-wire-adjacent SPs (IP_p ≥ 80).
    RP replacement = the waiver-wire-adjacent RPs (IP_p < 80)."""
    if not fut_p:
        return {}

    # Split & sort
    sp_pool = [e for e in fut_p if _safe_float((e.get("player") or {}).get("IP_p")) >= 80]
    rp_pool = [e for e in fut_p if _safe_float((e.get("player") or {}).get("IP_p")) < 80]
    # Both halves are already sorted by dollar desc (parent was sorted)
    # SP rostered ≈ 50 / 10 teams, RP rostered ≈ 20 / 10 teams
    sp_replacement = sp_pool[50:110] or sp_pool[-30:] if sp_pool else []
    rp_replacement = rp_pool[20:60]  or rp_pool[-20:] if rp_pool else []

    def _rollup(pool: list) -> Dict[str, float]:
        sum_ip     = 0.0
        sum_w      = 0.0
        sum_so     = 0.0
        sum_sv     = 0.0
        sum_hld    = 0.0
        sum_eraxip = 0.0
        sum_whpxip = 0.0
        for e in pool:
            p  = e.get("player") or {}
            ip = _safe_float(p.get("IP_p"))
            if ip <= 0:
                continue
            sum_ip     += ip
            sum_w      += _safe_float(p.get("W_p"))
            sum_so     += _safe_float(p.get("SO_p"))
            sum_sv     += _safe_float(p.get("SV_p"))
            sum_hld    += _safe_float(p.get("HLD_p"))
            sum_eraxip += _safe_float(p.get("ERA_p"))  * ip
            sum_whpxip += _safe_float(p.get("WHIP_p")) * ip
        if sum_ip <= 0:
            return {}
        return {
            "W":    round(sum_w   / sum_ip, 6),
            "SO":   round(sum_so  / sum_ip, 6),
            "SV":   round(sum_sv  / sum_ip, 6),
            "HLD":  round(sum_hld / sum_ip, 6),
            "ERA":  round(sum_eraxip / sum_ip, 4),
            "WHIP": round(sum_whpxip / sum_ip, 4),
        }

    out: Dict[str, Dict[str, float]] = {}
    sp_rates = _rollup(sp_replacement)
    rp_rates = _rollup(rp_replacement)
    if sp_rates: out["SP"] = sp_rates
    if rp_rates: out["RP"] = rp_rates
    return out


def build_replacement_rates(fantasy_data: Optional[dict]) -> Dict[str, Dict[str, float]]:
    """Build hitter / SP / RP per-volume replacement rates from the dashboard's
    own dollar-value pool (average of OOPSY/BatX/Steamer). Returns:
        {"hitter": {...per PA...}, "SP": {...per IP...}, "RP": {...per IP...}}
    Empty dict if fantasy_data is missing.
    """
    if not fantasy_data:
        return {}
    out: Dict[str, Dict[str, float]] = {}
    h = _hitter_replacement_rates(fantasy_data.get("fut_h") or [])
    if h:
        out["hitter"] = h
    p = _pitcher_replacement_rates(fantasy_data.get("fut_p") or [])
    out.update(p)
    return out


# ══════════════════════════════════════════════════════════════════════
# Attach sim fields to the phase-3 payload
# ══════════════════════════════════════════════════════════════════════

def _lookup_sim_rec(idx: dict, espn_rec: dict) -> Optional[dict]:
    """Three-pass match: mlbam → (norm_name, team) → unique name."""
    # Pass 1: mlbam (pulled from the fdata entry the dashboard attached in
    # parse_espn_rosters — the FG row carries the Chadwick mlbam id)
    fd = espn_rec.get("fdata") or {}
    fg_player = fd.get("player") or {}
    mlbam = fg_player.get("mlbam")
    try:
        if mlbam is not None:
            hit = idx["by_mlbam"].get(int(mlbam))
            if hit:
                return hit
    except (TypeError, ValueError):
        pass

    nm = _norm_name(espn_rec.get("name") or fg_player.get("name") or "")
    tm = _norm_team(espn_rec.get("team") or fg_player.get("team") or "")

    # Pass 2: name + team
    if nm and tm:
        hit = idx["by_name_team"].get((nm, tm))
        if hit:
            return hit

    # Pass 3: unique name only
    if nm:
        rows = idx["by_name"].get(nm, [])
        if len(rows) == 1:
            return rows[0]

    return None


def _build_mu_from_fdata(espn_rec: dict, is_pitcher: bool) -> Dict[str, float]:
    """Build a μ dict keyed by sim-module stat name from the fdata.player
    block the dashboard already attached. This is what the JS sampler
    consumes — we ship the dashboard's own 3-system averaged projections
    so the sim's μ exactly matches the standings table the user is
    staring at, and we apply sigma on top."""
    fd = espn_rec.get("fdata") or {}
    p  = fd.get("player") or {}
    if is_pitcher:
        return {
            "IP":   _safe_float(p.get("IP_p")),
            "W":    _safe_float(p.get("W_p")),
            "SO":   _safe_float(p.get("SO_p")),
            "ERA":  _safe_float(p.get("ERA_p")),
            "WHIP": _safe_float(p.get("WHIP_p")),
            "SV":   _safe_float(p.get("SV_p")),
            "HLD":  _safe_float(p.get("HLD_p")),
        }
    else:
        return {
            "PA":   _safe_float(p.get("PA_p")),
            "R":    _safe_float(p.get("R_p")),
            "HR":   _safe_float(p.get("HR_p")),
            "RBI":  _safe_float(p.get("RBI_p")),
            "SB":   _safe_float(p.get("SB_p")),
            "SO":   _safe_float(p.get("SO_p")),
            "OBP":  _safe_float(p.get("OBP_p")),
        }


def build_sim_payload(parsed_league: dict,
                      *,
                      fantasy_data: Optional[dict] = None,
                      verbose: bool = False) -> Dict[str, Any]:
    """
    Build the "sim_cfg" block the JS side needs.

    Returns:
        {
            "ok":          True/False,
            "reason":      str (if not ok),
            "role_models": {hitter|SP|RP: {stats, sigma_cv, chol}},
            "closer_cfg":  CLOSER_CFG dict,
            "players":     { espn_id (str): {
                "role":          "hitter"|"SP"|"RP",
                "is_pitcher":    bool,
                "mlb_team":      "NYY" (for closer grouping),
                "mu":            {stat_name: float, ...},
                "volume_proj":   float,  # PA (hitter) or IP (pitcher)
                "expected_loss": float,  # Zimmerman expected_loss
                "injury_tier":   "none"|"mild"|"moderate"|"severe",
            }},
            "unmatched":   [{"name", "team", "espn_id", "is_pitcher"}, ...],
        }

    Hitters without a sim_data_cache match still get entries — they use
    the dashboard's own projections as μ, role='hitter', and zero injury
    loss. Same for pitchers (default role='SP'). The sampler will still
    apply correlated sigma shocks using the measured single-year CV, so
    they're perturbed even without a Zimmerman injury profile.
    """
    try:
        if not os.path.exists(SIM_BT_CACHE):
            return {"ok": False, "reason": f"missing {os.path.basename(SIM_BT_CACHE)}"}
        if not os.path.exists(SIM_DATA_CACHE):
            return {"ok": False, "reason": f"missing {os.path.basename(SIM_DATA_CACHE)}"}
        with open(SIM_BT_CACHE, "r", encoding="utf-8") as f:
            bt_cache = json.load(f)
        with open(SIM_DATA_CACHE, "r", encoding="utf-8") as f:
            sim_cache = json.load(f)
    except Exception as e:
        return {"ok": False, "reason": f"cache load: {e}"}

    try:
        role_models = build_role_models(bt_cache)
    except Exception as e:
        return {"ok": False, "reason": f"role_models: {e}"}

    # Replacement-level per-volume rates for injury gap-fill. Computed from
    # the dashboard's own dollar-value pool (avg of 3 proj systems), so when
    # a player misses time the sim fills the gap with production equivalent
    # to a waiver-wire pickup rather than leaving it empty.
    replacement_rates = build_replacement_rates(fantasy_data)

    idx = build_player_sim_index(sim_cache)

    players: Dict[str, Dict[str, Any]] = {}
    unmatched: list = []
    matched_count = 0
    fallback_count = 0
    total_active = 0

    for team in parsed_league.get("teams", []):
        for rec in (team.get("hitters", []) + team.get("pitchers", [])):
            if rec.get("inactive"):
                continue
            total_active += 1
            espn_id = rec.get("espn_id")
            if espn_id is None:
                continue

            is_pit = bool(rec.get("is_pitcher"))
            sim_rec = _lookup_sim_rec(idx, rec)

            mu = _build_mu_from_fdata(rec, is_pit)
            mlb_team = _norm_team(rec.get("team") or "")

            if sim_rec is not None:
                matched_count += 1
                role = sim_rec.get("role") or ("SP" if is_pit else "hitter")
                expected_loss = _safe_float(sim_rec.get("expected_loss"))
                injury_tier   = sim_rec.get("injury_tier") or "none"
                # Use sim_cache's volume_proj (the Zimmerman-calibrated
                # denominator) rather than rebuilding from fdata — the
                # Beta injury math needs the same units Zimmerman used.
                volume_proj   = _safe_float(sim_rec.get("volume_proj"))
                if volume_proj <= 0:
                    # Fallback: use fdata's PA/IP so we never divide by zero
                    volume_proj = mu.get("PA", 0.0) if not is_pit else mu.get("IP", 0.0)
            else:
                fallback_count += 1
                unmatched.append({
                    "espn_id":    espn_id,
                    "name":       rec.get("name") or "",
                    "team":       mlb_team,
                    "is_pitcher": is_pit,
                })
                # Infer role from eligibility/projection when we have no
                # sim_data_cache match.
                if is_pit:
                    ip = mu.get("IP", 0.0)
                    sv = mu.get("SV", 0.0)
                    role = "RP" if (ip < 80 or sv >= 5) else "SP"
                else:
                    role = "hitter"
                expected_loss = 0.0
                injury_tier   = "none"
                volume_proj   = mu.get("PA", 0.0) if not is_pit else mu.get("IP", 0.0)

            players[str(espn_id)] = {
                "role":          role,
                "is_pitcher":    is_pit,
                "mlb_team":      mlb_team,
                "mu":            mu,
                "volume_proj":   round(volume_proj, 2),
                "expected_loss": round(expected_loss, 2),
                "injury_tier":   injury_tier,
            }

    if verbose:
        print(f"  [sim_payload] matched {matched_count}/{total_active} "
              f"({fallback_count} fallback to projection-only)")
        if replacement_rates:
            print(f"  [sim_payload] replacement rates: {sorted(replacement_rates.keys())}")

    return {
        "ok":                True,
        "role_models":       role_models,
        "closer_cfg":        CLOSER_CFG,
        "replacement_rates": replacement_rates,
        "players":           players,
        "unmatched":         unmatched,
        "matched":           matched_count,
        "fallback":          fallback_count,
    }
