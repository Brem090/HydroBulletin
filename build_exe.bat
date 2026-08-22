@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"
set "EXIT_CODE=0"

python -m pip install -r requirements-build.txt
if errorlevel 1 goto :error

python -m PyInstaller --noconfirm --clean HydroBulletin.spec
if errorlevel 1 goto :error

echo.
echo Build created: dist\HydroBulletin\HydroBulletin.exe
echo Test the complete dist\HydroBulletin folder on a clean Windows computer.
goto :finish

:error
set "EXIT_CODE=1"
echo.
echo ERROR: Could not build HydroBulletin for Windows.

:finish
pause
endlocal & exit /b %EXIT_CODE%
