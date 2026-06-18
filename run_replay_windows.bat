@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set "VENV_DIR=.venv"

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found at %VENV_DIR%.
    echo Run run_windows.bat once to create it.
    exit /b 1
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment.
    exit /b 1
)

echo Starting replay app...
python app_replay.py

exit /b %errorlevel%
