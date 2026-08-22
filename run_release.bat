@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"

if not exist "dist\HydroBulletin\HydroBulletin.exe" (
    echo ERROR: Run build_exe.bat first.
    pause
    exit /b 1
)

start "" "dist\HydroBulletin\HydroBulletin.exe"
endlocal
