"""
check_savant.py  —  Download Baseball Savant pitch-arsenal-stats CSVs and
show exactly what columns + sample data come back so we can confirm whether
Stuff+ is present and what the column names are.

Run:  python check_savant.py
Output:  savant_sample_FF_2025.csv  (opens in Excel automatically)
"""

import requests, sys, os
from io import StringIO

try:
    import pandas as pd
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pandas", "-q"])
    import pandas as pd

HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

print("=" * 60)
print("Baseball Savant  —  pitch-arsenal-stats CSV inspector")
print("=" * 60)

# ── Test 1: pitch-arsenal-stats per pitch type (the new approach) ──────────
print("\n[1] pitch-arsenal-stats  (new endpoint, per-pitch-type)")
for year in (2026, 2025):
    for pt in ("FF", "SL", "CH"):          # sample a few pitch types
        url = "https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats"
        try:
            r = requests.get(url,
                params={"type": "pitcher", "pitchType": pt, "year": year,
                        "min": 0, "minPitches": 0, "team": "", "csv": "true"},
                headers=HDRS, timeout=25)
            r.raise_for_status()
            df = pd.read_csv(StringIO(r.text))
            if len(df) == 0:
                print(f"  {year} {pt}: 0 rows returned")
                continue
            print(f"\n  {year} {pt}: {len(df)} rows")
            print(f"  Columns: {list(df.columns)}")
            stuff_cols = [c for c in df.columns if "stuff" in c.lower() or "pitching" in c.lower()]
            print(f"  Stuff/pitching cols: {stuff_cols}")
            if stuff_cols:
                sample = df[stuff_cols].dropna(how="all").head(3)
                print(f"  Sample values:\n{sample.to_string()}")
            # Save first successful CSV for Excel inspection
            out_path = os.path.join(OUT_DIR, f"savant_sample_{pt}_{year}.csv")
            df.to_csv(out_path, index=False)
            print(f"  Saved → {out_path}")
            break   # one pitch type per year is enough to see columns
        except Exception as e:
            print(f"  {year} {pt}: ERROR — {e}")
    else:
        continue
    break

# ── Test 2: old pitch-arsenals endpoint (what was failing before) ──────────
print("\n[2] pitch-arsenals  (old endpoint, type=n_stuff_plus)")
for year in (2025, 2026):
    url2 = "https://baseballsavant.mlb.com/leaderboard/pitch-arsenals"
    try:
        r2 = requests.get(url2,
            params={"year": year, "min": 0, "type": "n_stuff_plus",
                    "hand": "", "pos": "P", "teamId": "", "csv": "true"},
            headers=HDRS, timeout=25)
        r2.raise_for_status()
        df2 = pd.read_csv(StringIO(r2.text))
        print(f"\n  {year}: {len(df2)} rows")
        stuff_cols2 = [c for c in df2.columns if "stuff" in c.lower()]
        print(f"  Columns (stuff): {stuff_cols2[:8]}")
        if stuff_cols2:
            non_null = df2[stuff_cols2[0]].notna().sum()
            print(f"  Non-null in first Stuff+ col: {non_null}/{len(df2)}")
        out2 = os.path.join(OUT_DIR, f"savant_OLD_{year}.csv")
        df2.to_csv(out2, index=False)
        print(f"  Saved → {out2}")
    except Exception as e:
        print(f"  {year}: ERROR — {e}")

print("\nDone. Open the .csv files in Excel to inspect the full column list.")
input("Press Enter to exit…")
