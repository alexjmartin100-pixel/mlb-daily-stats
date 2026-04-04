@echo off
cd /d "C:\Users\alexj\OneDrive\Documents\Claude\Projects\MLB Daily Results"

echo Recovering repo — restoring all files...

REM Reset corrupted git index
del /f .git\index 2>nul

REM Undo the bad commit and get back to clean state
git fetch origin
git reset --hard origin/main

REM Now re-add all the essential files properly
git add fetch_mlb_stats.py
git add requirements.txt
git add .github\workflows\daily.yml
git add mlb_daily_stats.html

REM Commit everything together
git diff --cached --quiet
if %errorlevel% neq 0 (
    git commit -m "Restore all files + fix workflow commit step"
)

REM Force push to fix remote
git push origin main --force

echo.
echo Done! Check GitHub to confirm all files are back.
pause
