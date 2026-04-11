@echo off
echo Stashing unstaged changes...
git stash --include-untracked
echo.
echo Pulling remote changes...
git pull --rebase
echo.
echo Restoring stashed changes...
git stash pop
echo.
echo Pushing...
git push
echo.
echo Done! Check: https://github.com/alexjmartin100-pixel/mlb-daily-stats/actions
pause
