@echo off
REM ─────────────────────────────────────────────────────────────────
REM  Test run - runs the script for yesterday and saves full log
REM  Check test_log.txt afterward to see if FanGraphs Stuff+ worked
REM ─────────────────────────────────────────────────────────────────

cd /d "C:\Users\alexj\OneDrive\Documents\Claude\Projects\MLB Daily Results"

echo Running test for 2026-03-29...
echo Output is being saved to test_log.txt
echo This will take about 2 minutes...
echo.

python fetch_mlb_stats.py 2026-03-29 > test_log.txt 2>&1

echo.
echo Done! Checking results...
echo.

REM Show Stuff+/Playwright/arsenal lines from the log
echo ── Stuff+ / FanGraphs / Playwright results ──
findstr /i /c:"stuff+" /c:"arsenal" /c:"pitch-arsenal" /c:"playwright" /c:"fangraphs" /c:"stealth" test_log.txt
echo ────────────────────────────────────────────
echo.
echo Full log saved to test_log.txt
pause
