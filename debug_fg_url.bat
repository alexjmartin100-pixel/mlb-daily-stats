@echo off
cd /d "C:\Users\alexj\OneDrive\Documents\Claude\Projects\MLB Daily Results"
echo Intercepting FanGraphs API calls...
echo A Chrome window will open - do NOT close it until the script finishes.
echo.
python debug_fg_url.py
pause
