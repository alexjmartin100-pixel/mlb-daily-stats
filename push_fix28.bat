@echo off
echo Pushing fix: explicitly pip install playwright before browser install...
cd /d "%~dp0"
git add .github\workflows\daily.yml
git commit -m "Fix: explicitly pip install playwright before python -m playwright install"
git push origin main
echo Done!
pause
