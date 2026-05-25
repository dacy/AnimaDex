@echo off
REM Activate the venv and start AnimaDex.
cd /d "%~dp0"
if not exist .venv (
    echo No .venv found -- run install.bat first.
    exit /b 1
)
call .venv\Scripts\activate.bat
python -m animadex serve %*
