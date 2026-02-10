@echo off
setlocal

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

REM Start backend server
echo Starting Civitai Downloader Backend on http://localhost:53133 ...
"%VENV_PY%" backend\server.py --port 53133

popd
endlocal
