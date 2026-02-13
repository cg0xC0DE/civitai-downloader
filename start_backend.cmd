@echo off
setlocal EnableDelayedExpansion

set ROOT=%~dp0
pushd "%ROOT%"

set VENV_PY=%ROOT%backend\venv\Scripts\python.exe
if not exist "%VENV_PY%" (
  echo venv not found. Please create it first.
  echo python -m venv venv
  popd
  exit /b 1
)

REM Install dependencies
echo Installing dependencies...
"%VENV_PY%" -m pip install requests azure-storage-blob websocket-client openai -q

set RESTART_DELAY=5

:loop
REM Kill existing processes on port 53133 to avoid duplicates
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":53133 " ^| findstr "LISTENING"') do (
  taskkill /PID %%a /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

REM Start backend server
echo [%date% %time%] Starting Civitai Downloader Backend on http://localhost:53133 ...
"%VENV_PY%" backend\server.py --port 53133
set "exitcode=!errorlevel!"

echo [%date% %time%] Backend exited (code: !exitcode!). Restarting in %RESTART_DELAY%s ...
timeout /t %RESTART_DELAY% /nobreak >nul
goto loop
