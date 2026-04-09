import subprocess, sys, os, json, unicodedata, time
from datetime import date, timedelta, datetime
import pybaseball          # type: ignore
import pandas as pd
import numpy as np
import requests
import statsapi            # type: ignore  (MLB-StatsAPI)

    import io

from config import *

def ta_norm(name: str) -> str:
    """Normalize a player name for Team Alex matching."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_s = "".join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_s.lower().replace(".", "").strip()


# Statcast pitch code → FanGraphs name aliases (for column detection)
SC_TO_FG_ALIASES = {
    "FF": ["ff", "fa", "4seam", "fourseam"],
    "FA": ["fa", "ff", "4seam"],
    "SI": ["si", "ft", "sinker", "twoseam"],
    "FC": ["fc", "cutter"],
    "SL": ["sl", "slider"],
    "ST": ["st", "sweeper"],
    "SV": ["sv", "slurve"],
    "CH": ["ch", "changeup", "change"],
    "FS": ["fs", "splitter", "split"],
    "CU": ["cu", "cb", "curve", "curveball"],
    "KC": ["kc", "knucklecurve"],
    "KN": ["kn", "knuckleball"],
}

# FanGraphs velocity column → Statcast pitch code
FG_VELO_COL_MAP = {
    "vFA (pfx)": "FF", "vFA": "FF",
    "vFF (pfx)": "FF", "vFF": "FF",
    "vSI (pfx)": "SI", "vSI": "SI",
    "vFT (pfx)": "SI", "vFT": "SI",
    "vFC (pfx)": "FC", "vFC": "FC",
    "vSL (pfx)": "SL", "vSL": "SL",
    "vST (pfx)": "ST", "vST": "ST",
    "vCH (pfx)": "CH", "vCH": "CH",
    "vFS (pfx)": "FS", "vFS": "FS",
    "vCB (pfx)": "CU", "vCB": "CU",
    "vCU (pfx)": "CU", "vCU": "CU",
    "vKC (pfx)": "KC", "vKC": "KC",
    "vKN (pfx)": "KN", "vKN": "KN",
}

# ── Utility helpers ────────────────────────────────────────────────────────
def outs_to_ip(outs: int) -> str:
    return f"{outs // 3}.{outs % 3}"

def ip_to_float(ip_str: str) -> float:
    p = ip_str.split(".")
    return int(p[0]) + int(p[1]) / 3.0 if len(p) == 2 else float(p[0])

def last_events(df: pd.DataFrame) -> pd.Series:
    return (df.sort_values("pitch_number")
              .groupby("at_bat_number")["events"].last().dropna())

def calc_outs(df: pd.DataFrame) -> int:
    return sum(OUT_WEIGHTS.get(e, 0) for e in last_events(df))

def team_for_batter(row: pd.Series):
    top = row.get("inning_topbot") == "Top"
    return (str(row.get("away_team", "")).upper() if top else str(row.get("home_team", "")).upper(),
            str(row.get("home_team", "")).upper() if top else str(row.get("away_team", "")).upper())

def team_for_pitcher(row: pd.Series):
    top = row.get("inning_topbot") == "Top"
    return (str(row.get("home_team", "")).upper() if top else str(row.get("away_team", "")).upper(),
            str(row.get("away_team", "")).upper() if top else str(row.get("home_team", "")).upper())

def title_name(s: str) -> str:
    return " ".join(w.capitalize() for w in s.split())

def norm_name(s: str) -> str:
    return s.strip().lower().replace(".", "").replace("-", " ")

def safe_float(v, prec=1):
    if v is None:
        return None
    try:
        f = float(v)
        return None if (pd.isna(f) or np.isinf(f) or f == 0) else round(f, prec)
    except Exception:
        return None

def safe_barrels(batted: pd.DataFrame) -> int:
    if "barrel" in batted.columns:
        ct = int(pd.to_numeric(batted["barrel"], errors="coerce").fillna(0).sum())
        if ct > 0:
            return ct
    ev = pd.to_numeric(batted.get("launch_speed", pd.Series(dtype=float)), errors="coerce")
    la = pd.to_numeric(batted.get("launch_angle", pd.Series(dtype=float)), errors="coerce")
    count = 0
    for e, a in zip(ev, la):
        if pd.isna(e) or pd.isna(a) or e < 98:
            continue
        lo, hi = max(26 - (e - 98), 8), min(30 + (e - 98), 50)
        if lo <= a <= hi:
            count += 1
    return count

def calc_runs_allowed(df: pd.DataFrame) -> int:
    r0 = df.iloc[0]
    if r0.get("inning_topbot") == "Top":
        pre_col, post_col = "away_score", "post_away_score"
    else:
        pre_col, post_col = "home_score", "post_home_score"
    if pre_col not in df.columns:
        return 0
    pre  = pd.to_numeric(df[pre_col],  errors="coerce").fillna(0)
    post = pd.to_numeric(df[post_col], errors="coerce").fillna(0)
    return int((post - pre).clip(lower=0).sum())

# ── Playwright: real Chromium browser to bypass Cloudflare on FanGraphs ──────
