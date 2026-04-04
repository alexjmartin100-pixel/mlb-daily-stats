@echo off
echo Pushing schedule fix: move cron earlier to compensate for GitHub delay...
cd /d "%~dp0"
git add .github\workflows\daily.yml
git commit -m "Fix schedule: move cron to 11:00 UTC (7am EDT) to account for GitHub runner delays"
git push origin main
echo Done!
pause
