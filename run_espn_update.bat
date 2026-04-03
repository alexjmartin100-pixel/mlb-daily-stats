@echo off
:: ── run_espn_update.bat ───────────────────────────────────────────────────
:: Point Windows Task Scheduler at this file.
:: It pulls the latest repo, runs the ESPN cache updater, and pushes results.
:: ─────────────────────────────────────────────────────────────────────────

cd /d "C:\Users\alexj\OneDrive\Documents\Claude\Projects\MLB Daily Results"

:: Pull any changes first so we're not pushing on top of stale commits
git pull origin main

:: Run the cache updater (reads ESPN_USERNAME / ESPN_PASSWORD env vars)
python update_espn_cache.py

:: Log the last run time to a local file (not committed)
echo Last run: %DATE% %TIME% > espn_cache_last_run.txt

pause
