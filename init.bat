@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

echo.
echo "============================================================"
echo "  Civitai Downloader - Initialization Script"
echo "============================================================"
echo.

:: ============================================================
::   Phase 1: Baseline Environment Check (Blocking)
:: ============================================================
echo "[Phase 1] Checking system dependencies..."
echo.

:CHECK_UV
set "HAS_UV="
set "PYTHON_EXE="
set "PYTHON_ARGS="
set "PY_VER="

uv --version >nul 2>&1
if not errorlevel 1 set "HAS_UV=1"

if not defined HAS_UV (
    "%USERPROFILE%\.local\bin\uv.exe" --version >nul 2>&1
    if not errorlevel 1 (
        set "HAS_UV=1"
        set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    )
)

if defined HAS_UV (
    echo "[OK] uv detected."
    for /f "delims=" %%p in ('uv python find 3.12 2^>nul') do set "PYTHON_EXE=%%p"
    if not defined PYTHON_EXE (
        echo "[INFO] Python 3.12 not found, installing via uv..."
        uv python install 3.12
        for /f "delims=" %%p in ('uv python find 3.12 2^>nul') do set "PYTHON_EXE=%%p"
    )
    if defined PYTHON_EXE (
        for /f "delims=" %%v in ('"!PYTHON_EXE!" -c "import sys; print(sys.version.split()[0])"') do set "PY_VER=%%v"
        echo "[OK] Python !PY_VER! detected via uv."
    ) else (
        echo "[ERROR] Failed to install Python 3.12 via uv."
        exit /b 1
    )
    goto PHASE1_DONE
)

:: Fallback: detect Python without uv
:CHECK_PYTHON
for /f "delims=" %%p in ('where python 2^>nul') do (
    if not defined PYTHON_EXE (
        echo %%p | find /i "\WindowsApps\" >nul
        if errorlevel 1 (
            "%%p" --version >nul 2>&1
            if not errorlevel 1 set "PYTHON_EXE=%%p"
        )
    )
)

if not defined PYTHON_EXE (
    python --version >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=python"
)

if not defined PYTHON_EXE (
    py -3 --version >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=py"
        set "PYTHON_ARGS=-3"
    )
)

if not defined PYTHON_EXE (
    py --version >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=py"
)

if not defined PYTHON_EXE (
    python3 --version >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=python3"
)

if not defined PYTHON_EXE (
    echo "[ERROR] Python is not detected. Please install uv (https://docs.astral.sh/uv/) or Python 3.10+."
    echo "        uv:     powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\""
    echo "        Python: https://www.python.org/downloads/"
    set /p _="After installing, press ENTER to re-check..."
    goto CHECK_UV
)

for /f "delims=" %%v in ('"%PYTHON_EXE%" %PYTHON_ARGS% -c ^"import sys; print(sys.version.split()[0])^"') do set "PY_VER=%%v"
"%PYTHON_EXE%" %PYTHON_ARGS% -c "import sys; raise SystemExit(0 if sys.version_info[:2]>=(3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo "[ERROR] Python %PY_VER% detected, but Python 3.10+ is required."
    set /p _="After upgrading, press ENTER to re-check..."
    goto CHECK_PYTHON
)
echo "[OK] Python %PY_VER% detected via: %PYTHON_EXE% %PYTHON_ARGS%."

:CHECK_PIP
"%PYTHON_EXE%" %PYTHON_ARGS% -m pip --version >nul 2>&1
if errorlevel 1 (
    echo "[ERROR] pip is not detected. Please ensure pip is installed with Python."
    echo "        Try: %PYTHON_EXE% %PYTHON_ARGS% -m ensurepip --upgrade"
    set /p _="After installing, press ENTER to re-check..."
    goto CHECK_PIP
)
echo "[OK] pip detected."

:PHASE1_DONE
echo.
echo "[Phase 1] All dependencies satisfied."
echo.

:: ============================================================
::   Phase 2: Automated Installation (Non-Interactive)
:: ============================================================
echo "============================================================"
echo "  Phase 2: Automated Installation"
echo "============================================================"
echo.

:: Create backend virtual environment
if exist "backend\venv" goto SKIP_VENV
echo "[INFO] Creating Python virtual environment..."
if defined HAS_UV (
    uv venv --python 3.12 backend\venv
) else (
    "%PYTHON_EXE%" %PYTHON_ARGS% -m venv backend\venv
)
if errorlevel 1 (
    echo "[ERROR] Failed to create virtual environment."
    exit /b 1
)
echo "[OK] Virtual environment created."
goto AFTER_VENV
:SKIP_VENV
echo "[SKIP] Virtual environment already exists."
:AFTER_VENV

:: Activate virtual environment
call backend\venv\Scripts\activate.bat

:: Install Python dependencies
echo "[INFO] Installing Python dependencies..."
if defined HAS_UV (
    uv pip install -r backend\requirements.txt --python backend\venv\Scripts\python.exe
) else (
    python -m pip install -r backend\requirements.txt
)
if errorlevel 1 (
    echo "[ERROR] Failed to install Python dependencies."
    exit /b 1
)
echo "[OK] Python dependencies installed."

:: Create required directories
if not exist "backend\cache" mkdir backend\cache
if not exist "backend\output" mkdir backend\output
if not exist "backend\workflows" mkdir backend\workflows
echo "[OK] Required directories ensured."

echo.
echo "[Phase 2] Installation complete."
echo.

:: ============================================================
::   Phase 3: Credential Configuration (Interactive)
:: ============================================================
echo "============================================================"
echo "  Phase 3: Credential Configuration"
echo "============================================================"
echo.

:: ---- Credential 1: backend\credential.py (Civitai API Token) ----

if exist "backend\credential.py" (
    echo "[SKIP] backend\credential.py already exists."
    goto CRED2
)

echo "[INFO] Configuring Civitai API credentials..."
echo "        Get your token at: https://civitai.com/user/account"
echo.
set "CIVITAI_API_TOKEN="
set /p CIVITAI_API_TOKEN="  Enter your CIVITAI_API_TOKEN: "

echo "[INFO] Writing backend\credential.py ..."
echo # -*- coding: utf-8 -*-> backend\credential.py
echo CIVITAI_API_TOKEN = '!CIVITAI_API_TOKEN!'>> backend\credential.py
echo "[OK] backend\credential.py created."
echo.

:: ---- Credential 2: backend\llm\credential.py (OpenAI API) ----

:CRED2
if exist "backend\llm\credential.py" (
    echo "[SKIP] backend\llm\credential.py already exists."
    goto CRED3
)

echo "[INFO] Configuring OpenAI API credentials..."
echo.
set "OPENAI_API_KEY="
set /p OPENAI_API_KEY="  Enter your OPENAI_API_KEY: "

set "OPENAI_API_BASE="
set /p OPENAI_API_BASE="  Enter OPENAI_API_BASE (press ENTER to use official API): "

set "OPENAI_MODEL=gpt-4o"
set /p OPENAI_MODEL="  Enter OPENAI_MODEL (press ENTER for gpt-4o): "

echo "[INFO] Writing backend\llm\credential.py ..."
echo # -*- coding: utf-8 -*-> backend\llm\credential.py
echo OPENAI_API_KEY = '!OPENAI_API_KEY!'>> backend\llm\credential.py
echo OPENAI_API_BASE = '!OPENAI_API_BASE!'>> backend\llm\credential.py
echo OPENAI_MODEL = '!OPENAI_MODEL!'>> backend\llm\credential.py
echo "[OK] backend\llm\credential.py created."
echo.

:: ---- Credential 3: backend\azure_blob\credentials.py (Azure Storage) ----

:CRED3
if exist "backend\azure_blob\credentials.py" (
    echo "[SKIP] backend\azure_blob\credentials.py already exists."
    goto DONE
)

echo "[INFO] Configuring Azure Blob Storage credentials..."
echo "        Format: DefaultEndpointsProtocol=https;AccountName=xxx;AccountKey=xxx;EndpointSuffix=core.windows.net"
echo.
set "AZURE_CONN_STR="
set /p AZURE_CONN_STR="  Enter your CONNECTION_STRING: "

echo "[INFO] Writing backend\azure_blob\credentials.py ..."
echo # Azure Storage connection string> backend\azure_blob\credentials.py
echo CONNECTION_STRING = '!AZURE_CONN_STR!'>> backend\azure_blob\credentials.py
echo "[OK] backend\azure_blob\credentials.py created."
echo.

:: ============================================================
::   Done
:: ============================================================
:DONE
echo.
echo "============================================================"
echo "  Initialization Complete!"
echo "============================================================"
echo.
echo "  To start the application:"
echo "    1. Run: start_backend.cmd"
echo "    2. Run: start_comfyui_watchdog.cmd"
echo "    3. Open: http://localhost:53133"
echo.
echo "  Configuration files:"
echo "    - backend\config.py              (paths, ports)"
echo "    - backend\credential.py          (Civitai API token)"
echo "    - backend\llm\credential.py      (OpenAI API key)"
echo "    - backend\azure_blob\credentials.py  (Azure Storage)"
echo.
echo "============================================================"
echo.

endlocal
