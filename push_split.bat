@echo off
echo ============================================================
echo  Splitting fetch_mlb_stats.py into modules
echo ============================================================
echo.

echo Running split_modules.py...
python split_modules.py
if errorlevel 1 (
    echo.
    echo SPLIT FAILED — aborting push.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Pushing split modules to GitHub
echo ============================================================
echo.

git add config.py utils.py fangraphs.py data_fetch.py batting_leaderboard.py player_cards.py pitching_leaderboard.py html_template.py fantasy.py fetch_mlb_stats.py split_modules.py push_split.bat
git commit -m "refactor: split fetch_mlb_stats.py into 9 modules for maintainability"
git pull --rebase origin main
git push origin main

echo.
echo ============================================================
echo  DONE! Modules pushed to GitHub.
echo  Trigger a new workflow run to verify everything works.
echo ============================================================
pause
