@echo off
cd /d "C:\Users\alexj\Projects\MLB Daily Results"

echo =========================================================
echo  Push CI fix: pin firebase-tools + retry deploy
echo  (edits .github\workflows\daily.yml)
echo =========================================================
echo.

REM Clear any stale .git\index.lock left over from a prior aborted operation.
if exist ".git\index.lock" (
    echo Clearing stale .git\index.lock...
    del /f /q ".git\index.lock"
)

REM Discard any stale local HTML regen - GHA is the source of truth for it.
git checkout HEAD -- mlb_daily_stats.html 2>nul

echo [1/3] Staging workflow change...
git add .github\workflows\daily.yml push_ci_deploy_fix.bat

git diff --cached --quiet
if %errorlevel% equ 0 (
    echo   No staged changes to commit.
    goto :done
)

echo [2/3] Committing...
git commit -m "CI: pin firebase-tools to 13.35.1 and retry deploy 3x to fix intermittent Firebase auth failures"
if errorlevel 1 (
    echo ERROR: commit failed.
    pause
    exit /b 1
)

echo [3/3] Syncing with remote and pushing...
REM Remote almost always has new GHA commits, so --rebase is required.
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

:done
echo.
echo =========================================================
echo  Done. Trigger a run to confirm the deploy now succeeds:
echo    1. https://github.com/alexjmartin100-pixel/mlb-daily-stats/actions
echo    2. "MLB Stats Daily Update" -^> "Run workflow"
echo =========================================================
pause
