@echo off
cd /d "%~dp0"

echo === Adding and committing changes ===
git add html_template.py
git commit -m "Fix team badges: separate TEAM column in Season Leaders, Compare tables"

echo === Stashing any remaining changes ===
git stash --include-untracked

echo === Pulling latest with rebase ===
git pull --rebase

echo === Restoring stash ===
git stash pop

echo === Pushing to remote ===
git push

echo === Done! ===
pause
