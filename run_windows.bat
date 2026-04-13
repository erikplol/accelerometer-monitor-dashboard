@echo off
setlocal EnableExtensions

REM Move to repository root (directory of this script).
cd /d "%~dp0"

set "VENV_DIR=.venv"

where py >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3"
) else (
    where python >nul 2>&1
    if %errorlevel%==0 (
        set "PYTHON_CMD=python"
    ) else (
        echo [ERROR] Python was not found in PATH.
        echo Install Python 3, then rerun this script.
        exit /b 1
    )
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating virtual environment in %VENV_DIR%...
    call %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        exit /b 1
    )
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment.
    exit /b 1
)

echo Upgrading pip...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] Failed to upgrade pip.
    exit /b 1
)

echo Installing dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    exit /b 1
)

if not defined MAVLINK_PORT set "MAVLINK_PORT=AUTO"
if not defined WTVB_PORT set "WTVB_PORT=AUTO"

echo Starting app with MAVLINK_PORT=%MAVLINK_PORT% and WTVB_PORT=%WTVB_PORT%...
python app.py

exit /b %errorlevel%