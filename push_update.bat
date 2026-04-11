@echo off
cd /d "C:\Users\alexj\Projects\MLB Daily Results"

git add fetch_mlb_stats.py fantasy.py parse_espn_rosters.py lineup_optimizer.py player_cards.py html_template.py data_fetch.py config.py utils.py pitching_leaderboard.py batting_leaderboard.py .github\workflows\daily.yml requirements.txt push_update.bat push_phase2_espn.bat push_team_fix.bat

git diff --cached --quiet
if %errorlevel% equ 0 (
    echo No new changes to commit.
) else (
    git commit -m "Update scripts"
)

git pull --rebase
git push

echo.
echo Done! Check https://mlb-stats-ae429.web.app/mlb_daily_stats.html in ~2 minutes.
pause
