@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"
python main.py --gui
set "EXIT_CODE=%ERRORLEVEL%"
pause
endlocal & exit /b %EXIT_CODE%
