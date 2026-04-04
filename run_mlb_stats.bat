@echo off
REM ─────────────────────────────────────────────────────────────────
REM  MLB Daily Stats – local runner
REM  Runs the Python script, pushes HTML to GitHub, deploys to Firebase
REM ─────────────────────────────────────────────────────────────────

cd /d "C:\Users\alexj\OneDrive\Documents\Claude\Projects\MLB Daily Results"

REM ── 1. Run the stats script ───────────────────────────────────────
echo [1/3] Running MLB stats script...
python fetch_mlb_stats.py >> run_log.txt 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python script failed. Check run_log.txt for details.
    exit /b 1
)
echo        Done.

REM ── 2. Push updated HTML to GitHub ───────────────────────────────
echo [2/3] Pushing to GitHub...
git add mlb_daily_stats.html manifest.json sw.js icon-192.png icon-512.png
git diff --cached --quiet
if %errorlevel% neq 0 (
    git commit -m "Daily stats update"
    git pull --rebase
    git push
    echo        Pushed.
) else (
    echo        No HTML changes to push.
)

REM ── 3. Deploy to Firebase ─────────────────────────────────────────
echo [3/3] Deploying to Firebase...
where firebase >nul 2>&1
if %errorlevel% equ 0 (
    firebase deploy --only hosting --non-interactive --project mlb-stats-ae429 >> run_log.txt 2>&1
    echo        Deployed.
) else (
    echo        Firebase CLI not found - skipping deploy.
    echo        Run setup_scheduler.bat once to install it.
)

echo.
echo All done! Dashboard live at https://mlb-stats-ae429.web.app/mlb_daily_stats.html
