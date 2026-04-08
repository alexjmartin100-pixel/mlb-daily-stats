@echo off
echo ============================================
echo  Patching Playwright cookie injection...
echo ============================================
python patch_pw_cookie.py
if errorlevel 1 (
    echo.
    echo PATCH FAILED - see error above
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Committing and pushing to GitHub...
echo ============================================
git add fetch_mlb_stats.py
git commit -m "fix: inject fg_cookie.txt into Playwright browser context for type=0 FG API"
git push

echo.
echo ============================================
echo  Done! Now trigger a workflow run:
echo  https://github.com/alexjmartin100-pixel/mlb-daily-stats/actions
echo ============================================
pause
