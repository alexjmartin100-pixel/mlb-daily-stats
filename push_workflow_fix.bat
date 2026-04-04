@echo off
cd /d "C:\Users\alexj\OneDrive\Documents\Claude\Projects\MLB Daily Results"
git add .github\workflows\daily.yml
git commit -m "Fix workflow: only commit mlb_daily_stats.html"
git push origin main
echo Done!
pause
