@echo off
cd /d "C:\Users\alexj\OneDrive\Documents\Claude\Projects\MLB Daily Results"

echo Step 1: Fetching remote changes...
git fetch origin

echo Step 2: Merging remote into local (keeping our mlb_daily_stats.html)...
git merge origin/main --no-edit

REM If mlb_daily_stats.html conflicts, always keep our local generated version
git checkout --ours mlb_daily_stats.html 2>nul
git add mlb_daily_stats.html 2>nul

REM Stage updated scripts
git add fetch_mlb_stats.py

REM Commit if there's anything to commit
git diff --cached --quiet
if %errorlevel% neq 0 (
    git commit -m "Update FanGraphs Stuff+ integration; resolve HTML conflict"
)

echo Step 3: Pushing to GitHub...
git push origin main

echo.
if %errorlevel% equ 0 (
    echo SUCCESS - GitHub is now up to date!
) else (
    echo Push failed - see error above.
)
pause
