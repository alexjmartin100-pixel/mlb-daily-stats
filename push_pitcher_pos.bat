@echo off
cd /d "%~dp0"

echo === Staging pitcher-position badge changes ===
git add fetch_mlb_stats.py html_template.py fantasy.py push_pitcher_pos.bat

git diff --cached --quiet
if %errorlevel% equ 0 (
    echo No new changes to commit.
) else (
    git commit -m "Show SP/RP position badge next to pitchers everywhere"
)

echo === Stashing any remaining local changes ===
git stash --include-untracked

echo === Pulling latest with rebase ===
git pull --rebase

echo === Restoring stash ===
git stash pop 2>nul

echo === Pushing to remote ===
git push

echo.
echo === Triggering GitHub Actions workflow ===
gh workflow run daily.yml

echo === Done! Check https://mlb-stats-ae429.web.app/mlb_daily_stats.html in ~2 minutes. ===
pause
