@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"
python -c "import pyright" > nul 2>&1
if errorlevel 1 (
    echo ERROR: Install developer dependencies first:
    echo python -m pip install -r requirements-dev.txt
    pause
    exit /b 1
)
python -m pyright
set "EXIT_CODE=%ERRORLEVEL%"
pause
endlocal & exit /b %EXIT_CODE%
