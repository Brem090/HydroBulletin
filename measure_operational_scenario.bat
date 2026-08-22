@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"

set "INPUT_FOLDER=%~1"
if not defined INPUT_FOLDER set "INPUT_FOLDER=demo_data\full_private"

if not exist "%INPUT_FOLDER%\" (
    echo ERROR: Input folder not found: %INPUT_FOLDER%
    echo Required: ZRUR52 and ZRUR71 for 07-09.08.2026 and SYNOP for 08.08.2026.
    pause
    exit /b 1
)

for /f %%I in ('powershell.exe -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "RUN_STAMP=%%I"
if not defined RUN_STAMP (
    echo ERROR: Could not create the run timestamp.
    pause
    exit /b 1
)

set "WORK_DIR=validation_results\measurement_%RUN_STAMP%"

python scripts\validate_operational_scenario.py --input-folder "%INPUT_FOLDER%" --samples 5 --work-dir "%WORK_DIR%"
if errorlevel 1 (
    echo ERROR: The performance measurement failed.
    pause
    exit /b 1
)

echo.
echo Reports: %WORK_DIR%
start "" "%WORK_DIR%"
pause
endlocal
