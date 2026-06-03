"""
ESPN Fantasy roster parser.

Loads the bookmarklet-exported JSON snapshot of an ESPN fantasy baseball league
and joins each player to the existing FG projection table (fdata['fut_h'] /
fdata['fut_p']) by normalized name + MLB team abbreviation.

Output is a structured league dict with each team's active hitters and pitchers,
ready for lineup optimization and z-score computation downstream.

Phase 1 of the trade-machine standings work — see also lineup_optimizer.py.
"""

import json
import os
from typing import Optional, List, Dict, Any

from utils import norm_name


# ── ESPN slot ID → human label ─────────────────────────────────────────────
# Reference: ESPN's internal lineup slot taxonomy for fantasy baseball.
SLOT_LABELS = {
    0: "C", 1: "1B", 2: "2B", 3: "3B", 4: "SS",
    5: "OF", 6: "MI", 7: "CI",
    8: "LF", 9: "CF", 10: "RF", 11: "DH",
    12: "UTIL",
    13: "P", 14: "SP", 15: "RP",
    16: "BE", 17: "IL", 19: "NA",
}

# ── Hitter lineup configuration (Honey Nut Chourios league) ────────────────
# Each tuple is (slot_id, count). Total = 11 starting hitters.
HITTER_LINEUP_SLOTS = [
    (0,  1),   # C
    (1,  1),   # 1B
    (2,  1),   # 2B
    (3,  1),   # 3B
    (4,  1),   # SS
    (5,  3),   # OF (×3)
    (6,  1),   # MI (2B/SS)
    (7,  1),   # CI (1B/3B)
    (12, 1),   # UTIL
]
HITTER_LINEUP_SIZE = sum(c for _, c in HITTER_LINEUP_SLOTS)  # 11

# Pitchers: per the league design we count ALL rostered pitchers
# (no slot optimization). IL pitchers are still excluded.
PITCHER_ELIGIBLE_SLOTS = {13, 14, 15}  # P / SP / RP

# Slots that mean "this player is not contributing" — they still belong to
# their team (so the trade machine tags them with a team_id and the FA picker
# doesn't offer them), but the lineup optimizer skips them via rec["inactive"].
INACTIVE_LINEUP_SLOTS = {17, 19}  # IL, NA


# ── ESPN proTeamId → standard MLB abbreviation ─────────────────────────────
# ESPN's internal MLB team IDs are 1–30 in roughly alphabetical order.
ESPN_PRO_TEAM_ABBREV = {
     1: "BAL",  2: "BOS",  3: "LAA",  4: "CHW",  5: "CLE",
     6: "DET",  7: "KC",   8: "MIL",  9: "MIN", 10: "NYY",
    11: "OAK", 12: "SEA", 13: "TEX", 14: "TOR", 15: "ATL",
    16: "CHC", 17: "CIN", 18: "HOU", 19: "LAD", 20: "WSH",
    21: "NYM", 22: "PHI", 23: "PIT", 24: "STL", 25: "SD",
    26: "SF",  27: "COL", 28: "MIA", 29: "ARI", 30: "TB",
}


def _is_pitcher(eligible_slots: List[int]) -> bool:
    """A player is a pitcher iff they're eligible for any P/SP/RP slot."""
    return any(s in PITCHER_ELIGIBLE_SLOTS for s in eligible_slots)


# A few common name aliases between ESPN and FanGraphs that the simple
# norm_name() pass doesn't catch. Extend as we find more.
NAME_ALIASES = {
    # esp_norm: fg_norm
    "luisangel acuna":   "luis acuna",
    "michael harris ii": "michael harris",
    "ronald acuna jr":   "ronald acuna",
    "vladimir guerrero jr": "vladimir guerrero",
    "fernando tatis jr": "fernando tatis",
}


def _alias(nm: str) -> str:
    return NAME_ALIASES.get(nm, nm)


def load_espn_snapshot(json_path: str) -> Dict[str, Any]:
    """Load the bookmarklet JSON file."""
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _last_name(nm: str) -> str:
    """Return the last whitespace-separated token of a name, post-norm.
    Handles two-word surnames poorly but that's what Pass-4 is for."""
    parts = nm.split()
    return parts[-1] if parts else ""


def _build_fg_index(fut_h: list, fut_p: list) -> Dict:
    """
    Build lookup tables over the existing FG projection list.

    Returns a dict with four views:
      - by_name_team:  {(norm_name, team_abbrev): entry}
      - by_name:       {norm_name: [entry, entry, ...]}    (collision-aware fallback)
      - by_mlbam:      {int(mlbam_id): entry}              (future use w/ Chadwick)
      - by_last_team:  {(last_name, team_abbrev): [entry, ...]} (nickname fallback)
    """
    by_name_team: Dict = {}
    by_name: Dict = {}
    by_mlbam: Dict = {}
    by_last_team: Dict = {}
    by_last: Dict = {}   # last_name → [(entry, is_pitcher, first_initial), ...]
    for src, _is_pit in ((fut_h or [], False), (fut_p or [], True)):
        for entry in src:
            p = entry.get("player", {}) or {}
            nm = _alias(norm_name(p.get("name", "") or ""))
            tm = (p.get("team") or "").upper().strip()
            if not nm:
                continue
            if tm:
                by_name_team[(nm, tm)] = entry
            by_name.setdefault(nm, []).append(entry)
            last = _last_name(nm)
            if last:
                if tm:
                    by_last_team.setdefault((last, tm), []).append(entry)
                by_last.setdefault(last, []).append((entry, _is_pit, nm[0]))
            try:
                m = p.get("mlbam")
                if m is not None:
                    by_mlbam[int(m)] = entry
            except (TypeError, ValueError):
                pass
    return {
        "by_name_team": by_name_team,
        "by_name": by_name,
        "by_mlbam": by_mlbam,
        "by_last_team": by_last_team,
        "by_last": by_last,
    }


def _lookup_player(fg_idx: Dict, espn_player: Dict, espn_pro_abbrev: str,
                   is_pitcher: bool = False) -> Optional[Dict]:
    """
    Try to find this ESPN player in the FG projection table.

    Match priority:
      1. Normalized full name + MLB team
      2. Normalized full name alone, if exactly one match exists
      3. Last-name + MLB team, if exactly one FG entry matches
         (handles Cam/Cameron, Matt/Matthew, Nick/Nicky, etc. — any
         nickname/full-name mismatch that shares a last name and team)
      4. Last-name alone, if exactly one FG entry of the SAME player type
         (pitcher/hitter) has that surname. Catches a nickname mismatch that
         ALSO has a team mismatch (e.g. ESPN lists NYY but FG lists a
         different/blank team, or ESPN/FG use different abbreviations like
         CHW vs CWS). Gated on player type + surname uniqueness so we can't
         grab the wrong person.
      5. None
    """
    nm = _alias(norm_name(espn_player.get("fullName", "") or ""))
    if not nm:
        return None
    tm = espn_pro_abbrev or ""

    # Pass 1: name + team (strongest)
    hit = fg_idx["by_name_team"].get((nm, tm))
    if hit is not None:
        return hit

    # Pass 2: unique name match
    name_hits = fg_idx["by_name"].get(nm, [])
    if len(name_hits) == 1:
        return name_hits[0]

    # Pass 3: last-name + team, unique match. Covers nickname vs full-name
    # mismatches (e.g. ESPN "Cam Schlittler" vs FG "Cameron Schlittler") as
    # long as there's only one player with that surname on that MLB team.
    last = _last_name(nm)
    if last and tm:
        lt_hits = fg_idx["by_last_team"].get((last, tm), [])
        if len(lt_hits) == 1:
            return lt_hits[0]

    # Pass 4: last-name alone among FG players of the same type AND sharing the
    # same first initial. This rescues owned players who otherwise leak into the
    # free-agent list when the team abbreviation doesn't line up between ESPN and
    # FG. The type + first-initial + surname-uniqueness gates mean a nickname
    # (Cam/Cameron, Matt/Matthew — same initial) matches, while an unrelated
    # same-surname player (John vs Luis Garcia) does not.
    if last:
        esp_init = nm[0] if nm else ""
        type_hits = [e for (e, ip, fi) in fg_idx.get("by_last", {}).get(last, [])
                     if ip == is_pitcher and fi == esp_init]
        if len(type_hits) == 1:
            return type_hits[0]

    return None


def parse_league(json_path: str, fdata: dict, *, verbose: bool = True) -> Dict[str, Any]:
    """
    Parse the ESPN snapshot and join each player to the FG projection table.

    Args:
        json_path : path to the bookmarklet-exported JSON file
        fdata     : output of fantasy_data() — dict with 'fut_h' and 'fut_p'
        verbose   : if True, print join coverage stats

    Returns:
        {
            "league_id":  str,
            "season_id":  str,
            "fetched_at": ISO string,
            "teams": [
                {
                    "team_id":  int,
                    "name":     str,        # display name
                    "abbrev":   str,        # ESPN abbrev (often noisy)
                    "hitters":  [ player_record, ... ],
                    "pitchers": [ player_record, ... ],
                },
                ...
            ],
            "unmatched": [
                {"espn_id", "name", "team", "is_pitcher"}, ...
            ]
        }

    Each player_record is:
        {
          "espn_id":  int,           # ESPN player ID
          "name":     str,           # ESPN fullName
          "team":     str,           # MLB team abbreviation
          "elig":     [int, ...],    # ESPN eligibleSlots list (slot IDs)
          "fdata":    {...},         # the matched fdata entry from fut_h/fut_p
                                     # — contains 'dollar', 'player' (with R/HR/RBI/...
                                     #   and R_p/HR_p/... projected season totals)
          "is_pitcher": bool,
        }
    """
    snap = load_espn_snapshot(json_path)
    raw = snap.get("raw", snap)
    fg_idx = _build_fg_index(fdata.get("fut_h", []), fdata.get("fut_p", []))

    teams_out: List[Dict] = []
    unmatched: List[Dict] = []
    counts = {"hitters_matched": 0, "pitchers_matched": 0,
              "inactive_kept": 0, "total_active": 0}

    for t in raw.get("teams", []):
        team_obj = {
            "team_id": t.get("id"),
            "name":    t.get("name", f"Team {t.get('id')}"),
            "abbrev":  (t.get("abbrev") or "").strip(),
            "hitters":  [],
            "pitchers": [],
        }
        for entry in t.get("roster", {}).get("entries", []):
            slot = entry.get("lineupSlotId")
            inactive = slot in INACTIVE_LINEUP_SLOTS
            if inactive:
                counts["inactive_kept"] += 1
            else:
                counts["total_active"] += 1

            ppe = entry.get("playerPoolEntry", {}) or {}
            p = ppe.get("player", {}) or {}
            espn_id = p.get("id")
            full = p.get("fullName", "") or ""
            elig = p.get("eligibleSlots", []) or []
            pro_abbrev = ESPN_PRO_TEAM_ABBREV.get(p.get("proTeamId", 0), "")
            is_pit = _is_pitcher(elig)

            fd_entry = _lookup_player(fg_idx, p, pro_abbrev, is_pit)
            if fd_entry is None:
                # Only track unmatched for active players — inactive guys with
                # no FG projection row can't show up in TRADE_HITTERS anyway,
                # so they don't cause the FA-picker leak.
                if not inactive:
                    unmatched.append({
                        "espn_id":   espn_id,
                        "name":      full,
                        "team":      pro_abbrev,
                        "is_pitcher": is_pit,
                    })
                continue

            rec = {
                "espn_id":    espn_id,
                "name":       full,
                "team":       pro_abbrev,
                "elig":       elig,
                "fdata":      fd_entry,
                "is_pitcher": is_pit,
                "inactive":   inactive,   # True for IL/NA; lineup optimizer skips these
            }
            if is_pit:
                team_obj["pitchers"].append(rec)
                if not inactive:
                    counts["pitchers_matched"] += 1
            else:
                team_obj["hitters"].append(rec)
                if not inactive:
                    counts["hitters_matched"] += 1

        teams_out.append(team_obj)

    if verbose:
        total_matched = counts["hitters_matched"] + counts["pitchers_matched"]
        active = counts["total_active"]
        miss = active - total_matched
        rate = (100.0 * total_matched / active) if active else 0.0
        print(f"  [ESPN] {len(teams_out)} teams, {active} active roster spots "
              f"({counts['inactive_kept']} IL/NA kept as inactive)")
        print(f"  [ESPN] Joined {total_matched}/{active} ({rate:.1f}%) — "
              f"{counts['hitters_matched']} hitters, {counts['pitchers_matched']} pitchers")
        if miss:
            print(f"  [ESPN] {miss} unmatched (showing first 10):")
            for u in unmatched[:10]:
                kind = "P" if u["is_pitcher"] else "H"
                print(f"         {kind}  {u['name']:24}  {u['team']:4}  espn_id={u['espn_id']}")

    return {
        "league_id":  snap.get("leagueId"),
        "season_id":  snap.get("seasonId"),
        "fetched_at": snap.get("fetchedAt"),
        "teams":      teams_out,
        "unmatched":  unmatched,
    }


# ── Convenience: standalone test against the uploaded sample ───────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python parse_espn_rosters.py <espn_rosters.json>")
        sys.exit(1)
    snap = load_espn_snapshot(sys.argv[1])
    raw = snap["raw"]
    print(f"League: {snap.get('leagueId')}  Season: {snap.get('seasonId')}")
    print(f"Fetched: {snap.get('fetchedAt')}")
    print(f"# teams: {len(raw['teams'])}")
    for t in raw["teams"]:
        active = sum(1 for e in t["roster"]["entries"]
                     if e.get("lineupSlotId") not in INACTIVE_LINEUP_SLOTS)
        print(f"  {t.get('name', t.get('id'))}: {active} active")
