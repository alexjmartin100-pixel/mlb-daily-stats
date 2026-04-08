@echo off
echo Running fix_workflow_127.py...
python fix_workflow_127.py
echo.
echo Pushing to GitHub...
git add requirements.txt .github/workflows/daily.yml fix_workflow_127.py push_fix_127.bat
git commit -m "fix: add playwright to requirements.txt + sudo for install-deps"
git pull --rebase origin main
git push origin main
echo.
echo Done! Go to GitHub Actions and trigger a new run.
pause
