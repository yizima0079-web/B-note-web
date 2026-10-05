@echo off
REM ============================================================
REM  BiliRecall MVP - one-click Windows launcher (CMD)
REM  ASCII-only. Double-click this file.
REM  Flow: venv -> deps -> .env (first run only) -> start server
REM  First run: Notepad opens, edit, save, close it, then press
REM  any key HERE and the server starts. No need to re-run.
REM ============================================================
setlocal
cd /d "%~dp0"
title BiliRecall MVP - local server

echo ================================================================
echo   BiliRecall MVP local server
echo   Keep this window OPEN while you use the website.
echo   Closing this window stops the server. That is normal.
echo ================================================================
echo.

REM ---- step 0: find python + version check ----
where python >nul 2>nul
if errorlevel 1 (
  echo [PROBLEM] Python is not installed or not in PATH.
  echo.
  echo   Install Python 3.11 or 3.12 from https://www.python.org/downloads/
  echo   IMPORTANT: tick "Add python.exe to PATH" in the installer.
  echo   Then double-click run.bat again.
  echo.
  pause
  exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" 2>nul
if errorlevel 1 (
  echo [PROBLEM] Your Python is too old. Need 3.11 or newer.
  python --version
  pause
  exit /b 1
)
echo [OK] Python found.
echo.

REM ---- step 1: venv ----
if not exist ".venv" (
  echo [STEP 1/4] Creating .venv ... one time, about 10 seconds.
  python -m venv .venv
  if errorlevel 1 (
    echo [PROBLEM] Could not create .venv. Screenshot this window.
    pause
    exit /b 1
  )
) else (
  echo [OK] STEP 1/4 .venv exists.
)
call ".venv\Scripts\activate.bat"
echo.

REM ---- step 2: deps ----
python -c "import uvicorn, fastapi, sqlalchemy" >nul 2>nul
if errorlevel 1 (
  echo [STEP 2/4] Installing libraries ... one time, 1-3 minutes.
  python -m pip install --upgrade pip
  pip install -r backend\requirements.txt
  if errorlevel 1 (
    echo [PROBLEM] Library install failed.
    echo   If your Python is 3.14 run: python -m pip install -U pip
    echo   then double-click run.bat again.
    pause
    exit /b 1
  )
) else (
  echo [OK] STEP 2/4 Libraries installed.
)
echo.

REM ---- step 3: config, first run only, then CONTINUE in same run ----
REM NOTE: the backend reads .env from the "backend" folder, so we keep
REM the real config file at backend\.env and a root copy for reference.
if not exist "backend\.env" (
  echo [STEP 3/4] First run: creating config file now.
  if exist ".env" (
    copy /Y ".env" "backend\.env" >nul
  ) else (
    copy /Y ".env.example" "backend\.env" >nul
  )
  echo.
  echo   ============================================================
  echo   ONE-TIME SETUP - Notepad is opening the config for you.
  echo   1. In Notepad change:
  echo        SECRET_KEY=   any long random text
  echo        APP_PASSWORD= your website login password
  echo        LLM_API_KEY=  your AI key, e.g. DeepSeek sk-...
  echo   2. Save with Ctrl+S, then CLOSE Notepad.
  echo   3. Come back to THIS window and press any key.
  echo      The server will start right away. No re-run needed.
  echo   ============================================================
  echo.
  start "" /wait notepad "backend\.env"
  echo   Waiting ... press any key after you saved the config
  pause >nul
  copy /Y "backend\.env" ".env" >nul
  echo [OK] STEP 3/4 Config saved, continuing.
) else (
  echo [OK] STEP 3/4 Config exists.
)
REM keep root .env in sync with backend\.env (the file Notepad opens)
if exist "backend\.env" copy /Y "backend\.env" ".env" >nul

REM show which keys are configured (values masked)
python -c "import re;s=open(r'backend\.env',encoding='utf-8',errors='ignore').read();[print('[CFG]',k,'=','SET' if v.strip() else '(EMPTY)') for k,v in re.findall(r'^(SECRET_KEY|APP_PASSWORD|LLM_API_KEY|LLM_MODEL|TENCENT_APPID|TENCENT_SECRET_ID|TENCENT_SECRET_KEY)\s*=\s*(.*)$',s,re.M)]"
echo.

echo.

REM ---- step 4: start server + open browser ----
echo [STEP 4/4] Starting local server ...
echo            Website: http://127.0.0.1:8000
echo            Login with the APP_PASSWORD from backend\.env
echo.
start "" http://127.0.0.1:8000
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
cd ..
echo.
echo The server stopped. If you did not close it yourself,
echo read the lines above for the error message.
pause
