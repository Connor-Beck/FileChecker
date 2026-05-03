@echo off
setlocal

cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
)

python -m filechecker
if errorlevel 1 (
    echo.
    echo FileChecker exited with an error.
    pause
)
