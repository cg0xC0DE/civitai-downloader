@echo off
setlocal

set ROOT=%~dp0
cd /d "%ROOT%"

set FRONTEND_DIR=%ROOT%frontend
set PORT=53134

echo "Frontend dir: %FRONTEND_DIR%"
echo "Starting frontend on http://localhost:%PORT% ..."

npx serve %FRONTEND_DIR% -l %PORT%
