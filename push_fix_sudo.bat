@echo off
echo Running fix_sudo.py...
python fix_sudo.py
echo.
echo Pushing to GitHub...
git add .github/workflows/daily.yml fix_sudo.py push_fix_sudo.bat
git commit -m "fix: remove sudo from playwright install-deps (sudo resets PATH)"
git pull --rebase origin main
git push origin main
echo.
echo Done! Go to GitHub Actions and trigger a new run.
pause
