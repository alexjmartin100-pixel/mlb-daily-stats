@echo off
cd /d "%~dp0"

echo === Stashing any local changes ===
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

echo === Done! ===
pause
