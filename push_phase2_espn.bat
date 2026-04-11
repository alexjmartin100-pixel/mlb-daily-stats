@echo off
cd /d "C:\Users\alexj\OneDrive\Documents\Claude\Projects\MLB Daily Results"

rem Phase 2: ESPN bookmarklet + parser + lineup optimizer + Season Projections sub-tab.
rem The commit is already created in this repo by Claude — this script just pushes it.

git stash
git pull --rebase
git stash pop

git push

echo.
echo Done! Phase 2 pushed.
echo Check https://mlb-stats-ae429.web.app/ in ~2 minutes.
echo (You will need to drop espn_rosters.json into the project folder for
echo  the Season Projections tab to populate — see espn_bookmarklet.html.)
pause
