@echo off
cd /d "C:\Users\alexj\OneDrive\Documents\Claude\Projects\MLB Daily Results"
echo Downloading Baseball Savant CSVs to inspect columns...
python check_savant.py
pause
