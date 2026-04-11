@echo off
cd /d "C:\Users\alexj\OneDrive\Documents\Claude\Projects\MLB Daily Results"

echo === Current status ===
git status

echo.
echo === Pulling latest with rebase (in case GHA bot committed) ===
git pull --rebase

echo.
echo === Pushing 5 local commits with team badge fixes ===
git push

echo.
echo === Triggering GitHub Actions workflow to regenerate data ===
gh workflow run daily.yml

echo.
echo Done! Wait ~3-5 minutes then refresh https://mlb-stats-ae429.web.app/
pause
