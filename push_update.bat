@echo off
cd /d "C:\Users\alexj\OneDrive\Documents\Claude\Projects\MLB Daily Results"

git add fetch_mlb_stats.py .github\workflows\daily.yml requirements.txt

git diff --cached --quiet
if %errorlevel% equ 0 (
    echo No new changes to commit.
) else (
    git commit -m "Update scripts"
)

git stash
git pull --rebase
git stash pop
git push

echo.
echo Done! Check https://mlb-stats-ae429.web.app/mlb_daily_stats.html in ~2 minutes.
pause
