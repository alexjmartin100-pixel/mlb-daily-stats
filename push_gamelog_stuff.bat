@echo off
echo Pushing game-log Stuff+ fix (xvfb + Playwright in CI, remove SZN labels)...
cd /d "%~dp0"
git add fetch_mlb_stats.py .github\workflows\daily.yml
git commit -m "Fix: game-log Stuff+ in CI (xvfb + playwright install), remove SZN column labels"
git push origin main
echo Done! Run #27 will trigger automatically on push (or trigger manually from GitHub Actions).
pause
