@echo off
echo Running fix_type0.py...
python fix_type0.py
echo.
echo Pushing to GitHub...
git add fetch_mlb_stats.py fix_type0.py push_fix_type0.bat
git commit -m "fix: change FG leaderboard API from type=0 to type=8 (Cloudflare bypass)"
git pull --rebase origin main
git push origin main
echo.
echo Done! Go to GitHub Actions and trigger a new run.
pause
