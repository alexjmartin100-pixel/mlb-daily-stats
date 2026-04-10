"""
Lineup optimizer + season-projection z-scores for the ESPN league standings.

Phase 2 of the trade-machine standings work — pairs with parse_espn_rosters.py.

Pipeline:
    parse_league(...)            # ESPN snapshot -> teams w/ joined fdata
        -> build_season_projections(parsed_league)
              -> for each team: optimize_hitter_lineup() + aggregate_team_*()
              -> compute_league_zscores() across all 10 teams
              -> returns one dict per team w/ totals, z-score per cat, ranks

Hitter optimizer: maximum-weight bipartite assignment via scipy LAP, where
the weight is the FG auction-calc dollar value and the constraint is the
ESPN eligibleSlots list. The result is the league-defined "best 11" for
each team given current rostered hitters.

Pitchers: no slot optimization — every active (non-IL/NA) pitcher counts,
matching the league design.
"""

from typing import List, Dict, Any, Optional, Tuple
import numpy as np
from scipy.optimize import linear_sum_assignment


# ── 12 fantasy categories ──────────────────────────────────────────────────
HITTER_CATS = ["R", "HR", "RBI", "SO", "SB", "OBP"]
PITCHER_CATS = ["W", "SO", "SV", "HLD", "ERA", "WHIP"]
ALL_CATS = HITTER_CATS + PITCHER_CATS

# Lower-is-better categories — z-score is negated for these.
LOWER_BETTER = {"SO_h", "ERA", "WHIP"}   # hitter K, ERA, WHIP

# Rate stats need weighted averaging instead of summation.
HITTER_RATE_STATS = {"OBP"}
PITCHER_RATE_STATS = {"ERA", "WHIP"}

# Pretty labels for the UI (hitter K shown as "K" but tracked as "SO_h" internally)
CAT_LABELS = {
    "R": "R", "HR": "HR", "RBI": "RBI", "SO_h": "K", "SB": "SB", "OBP": "OBP",
    "W": "W", "SO_p": "K",   "SV": "SV", "HLD": "HLD", "ERA": "ERA", "WHIP": "WHIP",
}

# Final per-team stat order, hitter first then pitcher.
TEAM_CAT_ORDER = ["R", "HR", "RBI", "SO_h", "SB", "OBP",
                  "W", "SO_p", "SV", "HLD", "ERA", "WHIP"]


# ── Hitter slot definition ─────────────────────────────────────────────────
# Each entry is (slot_id, label) and there's one entry per *slot instance*,
# so OF appears 3 times. The optimizer assigns one player to each instance.
HITTER_SLOT_INSTANCES = [
    (0,  "C"),
    (1,  "1B"),
    (2,  "2B"),
    (3,  "3B"),
    (4,  "SS"),
    (5,  "OF"),
    (5,  "OF"),
    (5,  "OF"),
    (6,  "MI"),
    (7,  "CI"),
    (12, "UTIL"),
]
N_HITTER_SLOTS = len(HITTER_SLOT_INSTANCES)  # 11


# ── Helpers ────────────────────────────────────────────────────────────────
def _f(v) -> float:
    """Coerce to float, treating None/NaN as 0."""
    if v is None:
        return 0.0
    try:
        f = float(v)
        if f != f:  # NaN
            return 0.0
        return f
    except (TypeError, ValueError):
        return 0.0


def _player_dollar(rec: Dict) -> float:
    """Dollar value from the joined fdata entry."""
    return _f((rec.get("fdata") or {}).get("dollar"))


def _player_proj(rec: Dict, key: str) -> float:
    """Projected stat from the joined fdata entry's player dict."""
    fd = rec.get("fdata") or {}
    p = fd.get("player") or {}
    return _f(p.get(key))


# ── Hitter lineup optimization (scipy LAP) ─────────────────────────────────
def optimize_hitter_lineup(hitters: List[Dict]) -> List[Dict]:
    """
    Assign the best 11 starting hitters using maximum-weight bipartite matching.

    Each rostered hitter is a "row"; each of the 11 slot instances is a "column".
    Weight is the player's dollar value if eligible for that slot, else -infinity.

    Returns a list of 11 player records (one per slot), each annotated with
    a "_slot" key giving the (slot_id, label) tuple they were assigned to.

    If a team has fewer than 11 eligible hitters, slots are left unfilled (the
    returned list is shorter than 11). This shouldn't happen for a real league
    roster but is handled defensively.
    """
    if not hitters:
        return []

    n_players = len(hitters)
    n_slots = N_HITTER_SLOTS

    # Cost matrix for linear_sum_assignment minimizes — we want to maximize $,
    # so negate. Use a very-large finite penalty for ineligible cells (LAP
    # rejects pure -inf).
    INELIGIBLE = 1e9
    cost = np.full((max(n_players, n_slots), n_slots), INELIGIBLE, dtype=float)

    for i, rec in enumerate(hitters):
        elig = set(rec.get("elig") or [])
        dollar = _player_dollar(rec)
        for j, (slot_id, _label) in enumerate(HITTER_SLOT_INSTANCES):
            if slot_id in elig:
                # Negate so the LAP minimizer maximizes dollar value.
                # Add small constant so even $0 players strictly beat ineligible.
                cost[i, j] = -(dollar + 1.0)

    # Square the matrix with dummy rows if we have fewer players than slots.
    # (Dummy rows stay at INELIGIBLE cost, which will leave slots unfilled.)
    row_ind, col_ind = linear_sum_assignment(cost)

    starters: List[Dict] = []
    for r, c in zip(row_ind, col_ind):
        if r < n_players and cost[r, c] < INELIGIBLE / 2:
            rec = dict(hitters[r])
            rec["_slot"] = HITTER_SLOT_INSTANCES[c]
            starters.append(rec)

    # Sort starters by their slot order for stable display
    slot_order = {tuple(s): i for i, s in enumerate(HITTER_SLOT_INSTANCES)}
    starters.sort(key=lambda r: slot_order.get(tuple(r["_slot"]), 99))
    return starters


# ── Stat aggregation ───────────────────────────────────────────────────────
def aggregate_hitter_stats(starters: List[Dict]) -> Dict[str, float]:
    """
    Sum counting stats and PA-weight OBP across the optimized hitter lineup.

    Returns dict with keys: R, HR, RBI, SO_h, SB, OBP, _PA (for debugging).
    """
    out = {"R": 0.0, "HR": 0.0, "RBI": 0.0, "SO_h": 0.0, "SB": 0.0, "OBP": 0.0, "_PA": 0.0}
    obp_num = 0.0  # Σ OBP_i × PA_i
    pa_total = 0.0
    for rec in starters:
        out["R"]    += _player_proj(rec, "R_p")
        out["HR"]   += _player_proj(rec, "HR_p")
        out["RBI"]  += _player_proj(rec, "RBI_p")
        out["SO_h"] += _player_proj(rec, "SO_p")
        out["SB"]   += _player_proj(rec, "SB_p")
        pa = _player_proj(rec, "PA_p")
        obp = _player_proj(rec, "OBP_p")
        if pa > 0 and obp > 0:
            obp_num += obp * pa
            pa_total += pa
    out["OBP"] = (obp_num / pa_total) if pa_total > 0 else 0.0
    out["_PA"] = pa_total
    return out


def aggregate_pitcher_stats(pitchers: List[Dict]) -> Dict[str, float]:
    """
    Sum counting stats and IP-weight ERA/WHIP across all rostered pitchers.

    Returns dict with keys: W, SO_p, SV, HLD, ERA, WHIP, _IP.
    """
    out = {"W": 0.0, "SO_p": 0.0, "SV": 0.0, "HLD": 0.0, "ERA": 0.0, "WHIP": 0.0, "_IP": 0.0}
    era_num = 0.0   # Σ ERA_i × IP_i
    whip_num = 0.0  # Σ WHIP_i × IP_i
    ip_total = 0.0
    for rec in pitchers:
        out["W"]    += _player_proj(rec, "W_p")
        out["SO_p"] += _player_proj(rec, "SO_p")
        out["SV"]   += _player_proj(rec, "SV_p")
        out["HLD"]  += _player_proj(rec, "HLD_p")
        ip  = _player_proj(rec, "IP_p")
        era = _player_proj(rec, "ERA_p")
        whp = _player_proj(rec, "WHIP_p")
        if ip > 0:
            if era > 0:
                era_num += era * ip
            if whp > 0:
                whip_num += whp * ip
            ip_total += ip
    out["ERA"]  = (era_num  / ip_total) if ip_total > 0 else 0.0
    out["WHIP"] = (whip_num / ip_total) if ip_total > 0 else 0.0
    out["_IP"]  = ip_total
    return out


# ── League-wide z-scores + ranks ───────────────────────────────────────────
def compute_league_zscores(team_rows: List[Dict]) -> List[Dict]:
    """
    Add per-category z-scores and ranks to each team row in-place.

    team_rows is a list of dicts that each have a "stats" sub-dict (combined
    hitter + pitcher stats keyed by TEAM_CAT_ORDER). After this call each row
    will additionally have:
        - "z":     {cat: z-score (lower-is-better already negated)}
        - "rank":  {cat: 1-based rank, 1 = best}
        - "z_total": sum of all 12 z-scores
        - "rank_total": 1-based overall rank

    Returns the same list (mutated for convenience).
    """
    if not team_rows:
        return team_rows

    # Per-category mean / std across the league
    for cat in TEAM_CAT_ORDER:
        vals = np.array([t["stats"][cat] for t in team_rows], dtype=float)
        mu = float(vals.mean())
        sig = float(vals.std())
        if sig < 1e-9:
            sig = 1e-9
        for t in team_rows:
            v = t["stats"][cat]
            z = (v - mu) / sig
            if cat in LOWER_BETTER:
                z = -z
            t.setdefault("z", {})[cat] = round(z, 3)

    # Ranks: per-cat, then total
    for cat in TEAM_CAT_ORDER:
        order = sorted(team_rows, key=lambda t: t["z"][cat], reverse=True)
        for i, t in enumerate(order, start=1):
            t.setdefault("rank", {})[cat] = i

    for t in team_rows:
        t["z_total"] = round(sum(t["z"][c] for c in TEAM_CAT_ORDER), 3)

    order_total = sorted(team_rows, key=lambda t: t["z_total"], reverse=True)
    for i, t in enumerate(order_total, start=1):
        t["rank_total"] = i

    return team_rows


# ── Top-level entry point ──────────────────────────────────────────────────
def build_season_projections(parsed_league: Dict[str, Any],
                             *, verbose: bool = True) -> List[Dict[str, Any]]:
    """
    Run the full Phase-2 pipeline against a parsed ESPN league.

    Args:
        parsed_league: output of parse_espn_rosters.parse_league()

    Returns:
        List of team rows, sorted by rank_total (best to worst). Each row:
            {
                "team_id":    int,
                "name":       str,
                "abbrev":     str,
                "starters":   [hitter_record, ...],   # 11 best
                "pitchers":   [pitcher_record, ...],  # all rostered
                "stats":      {cat: float, ...},      # 12 totals
                "z":          {cat: float, ...},
                "rank":       {cat: int, ...},
                "z_total":    float,
                "rank_total": int,
            }
    """
    team_rows: List[Dict[str, Any]] = []

    for team in parsed_league.get("teams", []):
        starters = optimize_hitter_lineup(team.get("hitters", []))
        pitchers = team.get("pitchers", []) or []

        h_stats = aggregate_hitter_stats(starters)
        p_stats = aggregate_pitcher_stats(pitchers)
        stats = {
            "R":    round(h_stats["R"],    1),
            "HR":   round(h_stats["HR"],   1),
            "RBI":  round(h_stats["RBI"],  1),
            "SO_h": round(h_stats["SO_h"], 1),
            "SB":   round(h_stats["SB"],   1),
            "OBP":  round(h_stats["OBP"],  4),
            "W":    round(p_stats["W"],    1),
            "SO_p": round(p_stats["SO_p"], 1),
            "SV":   round(p_stats["SV"],   1),
            "HLD":  round(p_stats["HLD"],  1),
            "ERA":  round(p_stats["ERA"],  3),
            "WHIP": round(p_stats["WHIP"], 3),
        }

        team_rows.append({
            "team_id":  team.get("team_id"),
            "name":     team.get("name"),
            "abbrev":   team.get("abbrev"),
            "starters": starters,
            "pitchers": pitchers,
            "stats":    stats,
        })

    compute_league_zscores(team_rows)
    team_rows.sort(key=lambda t: t["rank_total"])

    if verbose:
        print(f"  [LINEUP] Computed projections for {len(team_rows)} teams")
        print(f"  [LINEUP] Standings (z-total):")
        for t in team_rows:
            print(f"    {t['rank_total']:2}. {t['name']:32}  z={t['z_total']:+.2f}")

    return team_rows


# ── CLI smoke test ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, json, types
    if len(sys.argv) < 2:
        print("Usage: python lineup_optimizer.py <espn_rosters.json>")
        sys.exit(1)

    # Stub heavy modules so we can import the parser without dragging in
    # the whole fantasy.py / fetch_mlb_stats / player_cards chain.
    sys.modules.setdefault('fetch_mlb_stats', types.ModuleType('fetch_mlb_stats'))
    sys.modules['fetch_mlb_stats']._FANT = {}
    sys.modules['fetch_mlb_stats'].main = lambda: None
    sys.modules.setdefault('player_cards', types.ModuleType('player_cards'))

    from parse_espn_rosters import parse_league

    # For the smoke test we need an fdata-like object — load a cached pickle
    # if one exists, otherwise build it via fantasy.py.
    import os, pickle
    cache = "/tmp/fdata_cache.pkl"
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            fdata = pickle.load(f)
        print(f"  Loaded fdata from cache ({cache})")
    else:
        print("  No fdata cache — please run with cached pickle for now.")
        sys.exit(2)

    parsed = parse_league(sys.argv[1], fdata, verbose=True)
    rows = build_season_projections(parsed, verbose=True)
    print()
    print("  Top team detail:")
    top = rows[0]
    print(f"    {top['name']}")
    for cat in TEAM_CAT_ORDER:
        label = CAT_LABELS.get(cat, cat)
        print(f"      {label:5}  {top['stats'][cat]:>10}   "
              f"z={top['z'][cat]:+.2f}  rank={top['rank'][cat]}")
