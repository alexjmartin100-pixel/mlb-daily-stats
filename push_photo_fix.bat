@echo off
echo Adding and committing photo fix...
git add player_cards.py
git commit -m "style: show full player head/hat in card photo (contain instead of cover)"

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
