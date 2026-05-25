@echo off
REM AnimaDex installer (Windows).
REM
REM Usage:
REM   install.bat                     Core install.
REM   install.bat --with-scoring      Also install artist-scorer deps.
REM   install.bat --with-generation   Also install ComfyUI client deps.
REM   install.bat --all               Both extras.

setlocal EnableDelayedExpansion

cd /d "%~dp0"

set WITH_SCORING=0
set WITH_GENERATION=0
:argloop
if "%~1"=="" goto argsdone
if /I "%~1"=="--with-scoring"     set WITH_SCORING=1
if /I "%~1"=="--with-generation"  set WITH_GENERATION=1
if /I "%~1"=="--all"              ( set WITH_SCORING=1 & set WITH_GENERATION=1 )
shift
goto argloop
:argsdone

REM --- 1. Python check ---
where python >nul 2>nul
if errorlevel 1 (
    echo Python 3.11+ is required but 'python' was not found on PATH.
    exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo Using Python !PYVER!

REM --- 2. venv ---
if not exist .venv (
    echo Creating virtualenv in .venv\
    python -m venv .venv
)
call .venv\Scripts\activate.bat

REM --- 3. deps ---
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if "!WITH_SCORING!"=="1"     python -m pip install -r requirements-scoring.txt
if "!WITH_GENERATION!"=="1"  python -m pip install -r requirements-generation.txt
python -m pip install -e .

REM --- 4. config ---
if not exist config.toml (
    copy /Y config.toml.example config.toml >nul
    echo.
    echo ==^> Created config.toml from the example. Open it and set at least:
    echo       [server].secret_key  (run: python -m animadex genkey^)
    echo       [admin].password     (only if you want the admin inbox^)
    echo.
)

REM --- 5. data dir + schema ---
python -m animadex db-init

REM --- 6. seed samples (only on the first run, only if data dir is empty) ---
for /f "delims=" %%d in ('python -c "from animadex.config import load; print(load().paths.data_dir)"') do set DATA_DIR=%%d
if exist samples if not exist "!DATA_DIR!\.seeded" (
    echo.
    echo ==^> Seeding !DATA_DIR! from samples\ (one-time^)
    if not exist "!DATA_DIR!\characters\thumbs"  mkdir "!DATA_DIR!\characters\thumbs"
    if not exist "!DATA_DIR!\artists\thumbs"     mkdir "!DATA_DIR!\artists\thumbs"
    if not exist "!DATA_DIR!\copyrights\thumbs"  mkdir "!DATA_DIR!\copyrights\thumbs"
    if exist samples\images\characters\thumbs\*  xcopy /Y /Q samples\images\characters\thumbs\*  "!DATA_DIR!\characters\thumbs\" >nul
    if exist samples\images\artists\thumbs\*     xcopy /Y /Q samples\images\artists\thumbs\*     "!DATA_DIR!\artists\thumbs\" >nul
    if exist samples\images\copyrights\thumbs\*  xcopy /Y /Q samples\images\copyrights\thumbs\*  "!DATA_DIR!\copyrights\thumbs\" >nul
    python -m animadex build-db samples\characters.csv --mode characters
    python -m animadex build-db samples\artists.csv    --mode artists
    echo. > "!DATA_DIR!\.seeded"
)

echo.
echo Install complete. Start the app:  run.bat
endlocal
