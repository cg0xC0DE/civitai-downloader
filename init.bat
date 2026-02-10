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

:CHECK_PYTHON
python --version >nul 2>&1
if errorlevel 1 (
    echo "[ERROR] Python is not detected. Please install Python 3.10+ and ensure it is in PATH."
    echo "        Download: https://www.python.org/downloads/"
    set /p _="After installing, press ENTER to re-check..."
    goto CHECK_PYTHON
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do echo "[OK] Python %%v detected."

:CHECK_PIP
pip --version >nul 2>&1
if errorlevel 1 (
    echo "[ERROR] pip is not detected. Please ensure pip is installed with Python."
    echo "        Try: python -m ensurepip --upgrade"
    set /p _="After installing, press ENTER to re-check..."
    goto CHECK_PIP
)
echo "[OK] pip detected."

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
if not exist "backend\venv" (
    echo "[INFO] Creating Python virtual environment..."
    python -m venv backend\venv
    if errorlevel 1 (
        echo "[ERROR] Failed to create virtual environment."
        exit /b 1
    )
    echo "[OK] Virtual environment created."
) else (
    echo "[SKIP] Virtual environment already exists."
)

:: Activate virtual environment
call backend\venv\Scripts\activate.bat

:: Install Python dependencies
echo "[INFO] Installing Python dependencies..."
pip install -r backend\requirements.txt
if errorlevel 1 (
    echo "[ERROR] Failed to install Python dependencies."
    exit /b 1
)
echo "[OK] Python dependencies installed."

:: Create required directories
if not exist "backend\cache" (
    mkdir backend\cache
    echo "[OK] Created backend\cache directory."
) else (
    echo "[SKIP] backend\cache already exists."
)

if not exist "backend\output" (
    mkdir backend\output
    echo "[OK] Created backend\output directory."
) else (
    echo "[SKIP] backend\output already exists."
)

if not exist "backend\workflows" (
    mkdir backend\workflows
    echo "[OK] Created backend\workflows directory."
) else (
    echo "[SKIP] backend\workflows already exists."
)

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
echo "        Get your token at: https://civitai.com/user/account -> API Keys"
echo.
set "CIVITAI_API_TOKEN="
set /p CIVITAI_API_TOKEN="  Enter your CIVITAI_API_TOKEN: "

echo "[INFO] Writing backend\credential.py ..."
(
    echo "# -*- coding: utf-8 -*-"
    echo "\"\"\"" 
    echo "Civitai API 凭证配置"
    echo "请在此处填入你的 API Token，此文件已加入 .gitignore，不会被提交。"
    echo "获取方式：https://civitai.com/user/account -> API Keys"
    echo "\"\"\""
    echo.
    echo "CIVITAI_API_TOKEN = '!CIVITAI_API_TOKEN!'"
) > backend\credential.py
echo "[OK] backend\credential.py created."
echo.

:: ---- Credential 2: backend\llm\credential.py (OpenAI API) ----

:CRED2
if exist "backend\llm\credential.py" (
    echo "[SKIP] backend\llm\credential.py already exists."
    goto CRED3
)

echo "[INFO] Configuring OpenAI API credentials (for aesthetic analysis)..."
echo.
set "OPENAI_API_KEY="
set /p OPENAI_API_KEY="  Enter your OPENAI_API_KEY: "

set "OPENAI_API_BASE="
set /p OPENAI_API_BASE="  Enter OPENAI_API_BASE (press ENTER to use official API): "

set "OPENAI_MODEL=gpt-4o"
set /p OPENAI_MODEL="  Enter OPENAI_MODEL (press ENTER for gpt-4o): "

echo "[INFO] Writing backend\llm\credential.py ..."
(
    echo "# -*- coding: utf-8 -*-"
    echo "\"\"\""
    echo "LLM API 凭证配置"
    echo "请在此处填入你的 API Key，此文件已加入 .gitignore，不会被提交。"
    echo "\"\"\""
    echo.
    echo "# OpenAI API Key"
    echo "OPENAI_API_KEY = '!OPENAI_API_KEY!'"
    echo.
    echo "# 可选：自定义 API Base URL（用于兼容第三方代理/中转）"
    echo "# 留空则使用 OpenAI 官方地址 https://api.openai.com/v1"
    echo "OPENAI_API_BASE = '!OPENAI_API_BASE!'"
    echo.
    echo "# 模型名称（默认 gpt-4o）"
    echo "OPENAI_MODEL = '!OPENAI_MODEL!'"
) > backend\llm\credential.py
echo "[OK] backend\llm\credential.py created."
echo.

:: ---- Credential 3: backend\azure_blob\credentials.py (Azure Storage) ----

:CRED3
if exist "backend\azure_blob\credentials.py" (
    echo "[SKIP] backend\azure_blob\credentials.py already exists."
    goto DONE
)

echo "[INFO] Configuring Azure Blob Storage credentials (for gallery cloud storage)..."
echo "        Format: DefaultEndpointsProtocol=https;AccountName=xxx;AccountKey=xxx;EndpointSuffix=core.windows.net"
echo.
set "AZURE_CONN_STR="
set /p AZURE_CONN_STR="  Enter your CONNECTION_STRING: "

echo "[INFO] Writing backend\azure_blob\credentials.py ..."
(
    echo "# Azure Storage connection string"
    echo "# Format: DefaultEndpointsProtocol=https;AccountName=xxx;AccountKey=xxx;EndpointSuffix=core.windows.net"
    echo "CONNECTION_STRING = '!AZURE_CONN_STR!'"
) > backend\azure_blob\credentials.py
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
echo "    - backend\config.py          (paths, ports)"
echo "    - backend\credential.py      (Civitai API token)"
echo "    - backend\llm\credential.py  (OpenAI API key)"
echo "    - backend\azure_blob\credentials.py (Azure Storage)"
echo.
echo "============================================================"
echo.

endlocal
