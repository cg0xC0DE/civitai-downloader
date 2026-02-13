@echo off
setlocal EnableDelayedExpansion

set ROOT=%~dp0
cd /d "%ROOT%"

set FRONTEND_DIR=%ROOT%frontend
set PORT=53134
set RESTART_DELAY=5

:loop
REM Kill existing frontend serve processes to avoid duplicates
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
  taskkill /PID %%a /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo [%date% %time%] Starting frontend on http://localhost:%PORT% ...
npx serve %FRONTEND_DIR% -l %PORT%
set "exitcode=!errorlevel!"

echo [%date% %time%] Frontend exited (code: !exitcode!). Restarting in %RESTART_DELAY%s ...
timeout /t %RESTART_DELAY% /nobreak >nul
goto loop
