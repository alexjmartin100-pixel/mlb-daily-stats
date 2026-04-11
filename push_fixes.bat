@echo off
echo Adding and committing fixes...
git add player_cards.py batting_leaderboard.py pitching_leaderboard.py
git commit -m "fix: player card tm() colors + Savant API retry logic for xwOBA/chase/whiff"

echo.
echo Stashing any loose files...
git stash --include-untracked

echo.
echo Pulling remote changes...
git pull --rebase

echo.
echo Restoring stash...
git stash pop

echo.
echo Pushing...
git push

echo.
echo Done! Check: https://github.com/alexjmartin100-pixel/mlb-daily-stats/actions
pause
