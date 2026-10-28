@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title GemmaGallery - Setup and Verification

rem Locate common user installations without depending on a new terminal session.
set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Programs\Python\Python313;%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python310;C:\Program Files\nodejs;C:\Program Files (x86)\nodejs;%LOCALAPPDATA%\Programs\node;%APPDATA%\npm;%PATH%"
set "NO_PAUSE=0"
set "CHECK_ONLY=0"
set "SHOW_HELP=0"
for %%A in (%*) do (
    if /i "%%~A"=="--no-pause" set "NO_PAUSE=1"
    if /i "%%~A"=="--check-only" set "CHECK_ONLY=1"
    if /i "%%~A"=="--help" set "SHOW_HELP=1"
    if /i "%%~A"=="-h" set "SHOW_HELP=1"
)
if "%SHOW_HELP%"=="1" goto :show_help

set "APP_PYTHON=%~dp0backend\.venv\Scripts\python.exe"
if exist "%APP_PYTHON%" goto :run_installer
if "%CHECK_ONLY%"=="1" (
    echo [FAIL] No virtual environment found. Run install.bat first.
    goto :setup_failed
)

echo Bootstrap [1/2]: locating Python for the isolated environment...
call :create_venv
if errorlevel 1 goto :setup_failed
if not exist "%APP_PYTHON%" goto :setup_failed
echo Bootstrap [2/2]: virtual environment ready.

:run_installer
"%APP_PYTHON%" -u "backend\installer.py" %*
exit /b %ERRORLEVEL%

:create_venv
where uv >nul 2>&1
if not errorlevel 1 goto :use_uv
where py >nul 2>&1
if not errorlevel 1 goto :use_py
where python >nul 2>&1
if not errorlevel 1 goto :use_python
where python3 >nul 2>&1
if not errorlevel 1 goto :use_python3
echo [FAIL] Python 3.10+ is required. Install Python and re-run install.bat.
exit /b 1

:use_uv
rem uv environments are not seeded with pip unless --seed is specified.
call uv venv --python 3.11 --seed "backend\.venv"
exit /b %ERRORLEVEL%

:use_py
py -3.11 -m venv "backend\.venv" 2>nul
if not errorlevel 1 exit /b 0
py -3.12 -m venv "backend\.venv" 2>nul
if not errorlevel 1 exit /b 0
py -3.10 -m venv "backend\.venv" 2>nul
if not errorlevel 1 exit /b 0
py -3 -m venv "backend\.venv"
exit /b %ERRORLEVEL%

:use_python
python -m venv "backend\.venv"
exit /b %ERRORLEVEL%

:use_python3
python3 -m venv "backend\.venv"
exit /b %ERRORLEVEL%

:show_help
echo Usage: install.bat [--check-only] [--force] [--skip-model] [--no-launch] [--no-pause]
echo   --check-only  Verify existing dependencies without installing or building.
echo   --force       Reinstall dependencies, rebuild the UI, and re-download model files.
echo   --skip-model  Skip model checks explicitly; semantic search stays unverified.
exit /b 0

:setup_failed
echo [FAIL] Setup could not prepare the Python environment. Review the error above.
if "%NO_PAUSE%"=="0" pause
exit /b 1
