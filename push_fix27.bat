@echo off
echo Pushing fix: use python -m playwright install chromium...
cd /d "%~dp0"
git add .github\workflows\daily.yml
git commit -m "Fix: use python -m playwright install (CLI not on PATH in CI)"
git push origin main
echo Done!
pause
