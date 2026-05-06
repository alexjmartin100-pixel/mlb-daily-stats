@echo off
cd /d "C:\Users\alexj\Projects\MLB Daily Results"

echo =========================================================
echo  Push roster + search-perf fixes to GitHub
echo.
echo  1) Roster pipeline: adds zero-stat placeholders for ESPN-
echo     rostered players not in FG auction-calc data so low-tier
echo     RPs (Jack Perkins, etc.) stop silently disappearing from
echo     team rosters in the trade machine and waiver wire.
echo.
echo  2) Fantasy Hitters/Pitchers search: was freezing the page
echo     for 20-30s per keystroke. Now uses a pre-cached
echo     data-search attribute, skips the wasteful applyFantColors
echo     re-run on every filter, and debounces input by 80ms.
echo =========================================================
echo.

REM Clear any stale .git\index.lock left over from a prior aborted operation.
if exist ".git\index.lock" (
    echo Clearing stale .git\index.lock...
    del /f /q ".git\index.lock"
)

REM The local espn_rosters.json got truncated somehow (working tree is shorter
REM than HEAD). HEAD has the valid snapshot the user pushed earlier today, so
REM restore from there before staging — otherwise we'd push the broken file
REM and the next GHA run would crash on json.JSONDecodeError.
echo [0/3] Restoring espn_rosters.json from HEAD (working tree is truncated)...
git checkout HEAD -- espn_rosters.json

REM Discard any stale local HTML — GHA regenerates it.
git checkout HEAD -- mlb_daily_stats.html 2>nul

REM Stage the code fix.
echo [1/3] Staging code changes...
git add fantasy.py push_roster_fix.bat 2>nul

git diff --cached --quiet
if %errorlevel% equ 0 (
    echo   No staged changes to commit.
    set NOTHING_STAGED=1
) else (
    echo [2/3] Committing...
    git commit -m "fantasy: keep ESPN-rostered players on team when missing from FG; un-freeze hitter/pitcher search"
    if errorlevel 1 (
        echo ERROR: commit failed.
        pause
        exit /b 1
    )
)

echo [3/3] Syncing with remote and pushing...
git stash --include-untracked >nul 2>&1
git pull --rebase
if errorlevel 1 (
    echo.
    echo Rebase hit a conflict. Resolve it, then run:
    echo    git rebase --continue
    echo    git stash pop
    echo    git push
    pause
    exit /b 1
)
git stash pop >nul 2>&1
git push
if errorlevel 1 (
    echo ERROR: push failed.
    pause
    exit /b 1
)

echo.
echo =========================================================
echo  Code pushed. Now trigger a GitHub Actions run to rebuild
echo  the dashboard HTML with the new roster pipeline:
echo.
echo    1. Open https://github.com/alexjmartin100-pixel/mlb-daily-stats/actions
echo    2. Click "MLB Stats Daily Update" in the left sidebar
echo    3. Click the "Run workflow" dropdown on the right
echo    4. Click the green "Run workflow" button
echo.
echo  Takes ~7 minutes, then live site updates ~2 min after:
echo  https://mlb-stats-ae429.web.app/mlb_daily_stats.html
echo  (You may need to hard-refresh: Ctrl+Shift+R)
echo =========================================================
pause
