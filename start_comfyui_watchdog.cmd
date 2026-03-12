@echo off
setlocal

REM "============================================================"
REM "ComfyUI Watchdog - auto restart script"
REM "============================================================"

REM "---- config ----"
set COMFYUI_BAT=C:\ComfyUI_windows_portable\run_nvidia_gpu.bat
set CHECK_INTERVAL=10
REM "---- config end ----"

REM "auto-detect ComfyUI directory from bat path"
for %%F in ("%COMFYUI_BAT%") do set COMFYUI_DIR=%%~dpF

if not exist "%COMFYUI_BAT%" (
    echo "[Watchdog] ERROR: ComfyUI startup script not found: %COMFYUI_BAT%"
    pause
    exit /b 1
)

echo "[Watchdog] ComfyUI Watchdog started"
echo "[Watchdog] Startup script: %COMFYUI_BAT%"
echo "[Watchdog] Check interval: %CHECK_INTERVAL%s"
echo.

:loop
REM Kill existing processes on port 8188 to avoid duplicates
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8188 " ^| findstr "LISTENING"') do (
  taskkill /PID %%a /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo "[Watchdog] [%date% %time%] Starting ComfyUI ..."
pushd "%COMFYUI_DIR%"
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --disable-auto-launch --disable-cuda-malloc
popd

echo.
echo "[Watchdog] [%date% %time%] ComfyUI exited (code: %ERRORLEVEL%). Restarting in %CHECK_INTERVAL%s ..."
timeout /t %CHECK_INTERVAL% /nobreak >nul
goto loop
