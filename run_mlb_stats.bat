@echo off
REM ─────────────────────────────────────────────────────────────────
REM  MLB Daily Stats – Windows Task Scheduler launcher
REM
REM  HOW TO SCHEDULE (one-time setup):
REM   1. Open Windows Task Scheduler (search for it in Start menu)
REM   2. Click "Create Basic Task…"
REM   3. Name it: "MLB Daily Stats"
REM   4. Trigger: Daily, at 10:00 AM
REM   5. Action: Start a program
REM   6. Program/script: Full path to this .bat file
REM      e.g.  C:\Users\alexj\OneDrive\Documents\MLB Daily Results\run_mlb_stats.bat
REM   7. Finish.
REM
REM  The HTML dashboard will be refreshed each morning at 10 AM.
REM ─────────────────────────────────────────────────────────────────

cd /d "%~dp0"

REM -- Install / upgrade pybaseball silently --------------------------
python -m pip install pybaseball pandas numpy --upgrade -q

REM -- Fetch yesterday's data and regenerate the dashboard -----------
python fetch_mlb_stats.py

REM -- Optional: open the dashboard in the default browser -----------
REM start mlb_daily_stats.html

echo.
echo Done! Open mlb_daily_stats.html to view today's dashboard.
pause
