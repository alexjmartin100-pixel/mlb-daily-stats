#!/usr/bin/env python3
"""
MLB Daily Stats Dashboard Generator  v3.1
=========================================
Data sources:
  • Baseball Savant (Statcast)  — pitch-by-pitch game data
  • FanGraphs                   — single-game Stuff+/Location+/per-pitch Stuff+
                                  season-avg velocity per pitch type
  • MLB Stats API (statsapi)    — SB / CS box-score data

Run once each morning; mlb_daily_stats.html is updated in the same folder.
"""

import subprocess, sys, os, json, unicodedata, time
from datetime import date, timedelta, datetime
# Fix Unicode output on Windows (cp1252 can't handle checkmarks etc.)
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Auto-install (skipped on GitHub Actions / CI where requirements.txt is used) ──
if not os.environ.get("SKIP_AUTO_INSTALL"):
    print("Checking dependencies…")
    _pip_flags = ["--break-system-packages"] if sys.platform != "win32" else []
    for _pkg in ("pybaseball", "pandas", "numpy", "requests", "cloudscraper", "MLB-StatsAPI", "playwright", "playwright-stealth"):
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", _pkg, "-q"] + _pip_flags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    print("  Installing Playwright Chromium (cached after first run)…")
    subprocess.check_call(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

import pybaseball          # type: ignore
import pandas as pd
import numpy as np
import requests
import statsapi            # type: ignore  (MLB-StatsAPI)

pybaseball.cache.enable()

# ── Constants ──────────────────────────────────────────────────────────────
PITCH_NAMES = {
    "FF": "4-Seam",  "SI": "Sinker",   "FC": "Cutter",
    "SL": "Slider",  "ST": "Sweeper",  "SV": "Slurve",
    "CH": "Change",  "FS": "Splitter", "FO": "Fork",
    "CU": "Curve",   "KC": "K-Curve",  "CS": "Slow Curve",
    "KN": "Knuckle", "EP": "Eephus",   "FA": "Fastball",
    "SC": "Screw",
}
PITCH_COLORS = {
    "FF": "#e74c3c", "SI": "#c0392b", "FA": "#e74c3c",
    "FC": "#e67e22",
    "SL": "#f1c40f", "ST": "#f39c12", "SV": "#d35400",
    "CH": "#2ecc71", "FS": "#27ae60", "FO": "#16a085",
    "CU": "#3498db", "KC": "#2980b9", "CS": "#1abc9c",
    "KN": "#9b59b6", "EP": "#8e44ad", "SC": "#95a5a6",
}
OUT_WEIGHTS = {
    "strikeout": 1,                  "strikeout_double_play": 2,
    "field_out": 1,                  "grounded_into_double_play": 2,
    "double_play": 2,                "force_out": 1,
    "fielders_choice_out": 1,        "fielders_choice": 1,
    "sac_fly": 1,                    "sac_bunt": 1,
    "other_out": 1,                  "triple_play": 3,
    "sac_fly_double_play": 2,
}
WHIFF_DESC     = frozenset({"swinging_strike", "swinging_strike_blocked", "foul_tip"})
HIT_EVENTS     = frozenset({"single", "double", "triple", "home_run"})
FASTBALL_TYPES = frozenset({"FF", "SI", "FC", "FA"})
ALL_PT_CODES   = ["FF","FA","SI","FC","SL","ST","SV","CH","FS",
                  "CU","KC","KN","SC","FO","EP","CS"]

# ── Team Alex roster ───────────────────────────────────────────────────────
# Starts EMPTY — the dashboard's "My Team" tab lets the user add/remove
# players interactively (stored in Firestore when logged in, localStorage
# otherwise). No hardcoded roster or ESPN-roster dependency.
TEAM_ALEX_NAMES = set()

# ── Firebase Web App Config ────────────────────────────────────────────────
# Get these from Firebase Console → Project Settings → Your apps → Web app config.
# projectId/authDomain are pre-filled. Fill in apiKey, messagingSenderId, appId.
# ALSO: enable Email/Password auth in Firebase Console → Authentication → Sign-in method
# AND: set Firestore rules to allow authenticated reads/writes to users/{uid} docs.
FIREBASE_WEB_CONFIG = {
    "apiKey":            "AIzaSyDnOOHGhc7qqVZn41kkKpe_XtwYfQyHHTw",
    "authDomain":        "mlb-stats-ae429.firebaseapp.com",
    "projectId":         "mlb-stats-ae429",
    "storageBucket":     "mlb-stats-ae429.firebasestorage.app",
    "messagingSenderId": "742969160514",
    "appId":             "1:742969160514:web:a6c66d31ad9bc600714edb",
    "measurementId":     "G-NV8103G0QZ",
}

# ── FanGraphs ID overrides ─────────────────────────────────────────────────
# Maps MLBAM player ID → FanGraphs player ID for pitchers whose fg_id is
# missing or wrong in pybaseball's Chadwick register (stale for recent debuts).
# Values can be int (standard FG numeric ID) or str (e.g. "sa..." minor-league IDs).
# Add entries here whenever a pitcher regularly pitches but has no Stuff+ shown.
FG_ID_OVERRIDES: dict = {
    # Only needed for players whose Chadwick entry has a WRONG (not missing) FG ID.
    # Players with a blank Chadwick key_fangraphs are handled automatically via the
    # xMLBAMID→playerid mapping extracted from the FanGraphs leaderboard at startup.
    691725: 30091,   # Andrew Painter (PHI) — Chadwick has stale minor-league ID sa3017880
}

