@echo off
REM ─────────────────────────────────────────────────────────────────
REM  MLB Scheduler Setup – run this ONCE to configure everything
REM  Must be run as Administrator
REM ─────────────────────────────────────────────────────────────────

echo ========================================
echo  MLB Daily Stats - Scheduler Setup
echo ========================================
echo.

REM ── 1. Install Firebase CLI if missing ───────────────────────────
echo [1/4] Checking Firebase CLI...
where firebase >nul 2>&1
if %errorlevel% neq 0 (
    echo        Installing Firebase CLI via npm...
    npm install -g firebase-tools
    echo        Done.
) else (
    echo        Firebase CLI already installed.
)

REM ── 2. Log into Firebase ─────────────────────────────────────────
echo.
echo [2/4] Logging into Firebase...
echo        A browser window will open - sign in with your Google account.
firebase login
echo        Done.

REM ── 3. Create Task: run MLB script at 10:15 AM daily ─────────────
echo.
echo [3/4] Creating MLB Stats daily task (10:15 AM)...
schtasks /create /tn "MLB Daily Stats" /tr "\"C:\Users\alexj\OneDrive\Documents\Claude\Projects\MLB Daily Results\run_mlb_stats.bat\"" /sc daily /st 10:15 /ru "%USERNAME%" /rl highest /f
if %errorlevel% equ 0 (
    echo        Task created successfully.
) else (
    echo        Task creation failed - try running this script as Administrator.
)

REM ── 4. Create Task: sleep at 11:00 AM daily ──────────────────────
echo.
echo [4/4] Creating Sleep task (11:00 AM)...
schtasks /create /tn "MLB Sleep After Stats" /tr "rundll32.exe powrprof.dll,SetSuspendState 0,1,0" /sc daily /st 11:00 /ru "%USERNAME%" /rl highest /f
if %errorlevel% equ 0 (
    echo        Sleep task created successfully.
) else (
    echo        Sleep task creation failed - try running as Administrator.
)

echo.
echo ========================================
echo  Setup complete!
echo  - MLB script runs daily at 10:15 AM
echo  - Machine sleeps at 11:00 AM
echo ========================================
echo.
echo To test right now, run: run_mlb_stats.bat
pause
