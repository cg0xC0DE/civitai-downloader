@echo off
setlocal

set ROOT=%~dp0
pushd "%ROOT%"

set VENV_PY=%ROOT%venv\Scripts\python.exe
if not exist "%VENV_PY%" (
  echo venv not found. Please create it first.
  echo python -m venv venv
  popd
  exit /b 1
)

REM 安装依赖
echo Installing dependencies...
"%VENV_PY%" -m pip install requests -q

REM 启动后端服务器
echo Starting Civitai Downloader Backend on http://localhost:53133 ...
"%VENV_PY%" server.py --port 53133

popd
endlocal
