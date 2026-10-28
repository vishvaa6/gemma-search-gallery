@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title GemmaGallery - AI Search Gallery

rem ================================================================
rem   GemmaGallery Launcher
rem   Starts the AI-Powered Multimodal Gallery web application.
rem   Dependencies and verification are handled by install.bat.
rem ================================================================

rem Include common Python and Node.js installation paths across Windows
set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Programs\Python\Python313;%LOCALAPPDATA%\Programs\Python\Python313\Scripts;%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;%LOCALAPPDATA%\Programs\Python\Python310;%LOCALAPPDATA%\Programs\Python\Python310\Scripts;C:\Program Files\Python313;C:\Program Files\Python312;C:\Program Files\Python311;C:\Program Files\Python310;C:\Python313;C:\Python312;C:\Python311;C:\Python310;C:\Program Files\nodejs;C:\Program Files (x86)\nodejs;%LOCALAPPDATA%\Programs\node;%APPDATA%\npm;%PATH%"

set "PYTHON=%~dp0backend\.venv\Scripts\python.exe"

set "NO_PAUSE=0"
for %%A in (%*) do (
    if /i "%%~A"=="--no-pause" set "NO_PAUSE=1"
)

rem ----------------------------------------------------------------
rem Check if GemmaGallery is installed
rem ----------------------------------------------------------------
if not exist "%PYTHON%" goto :not_installed
if not exist "%~dp0frontend\dist\index.html" (
    if not exist "%~dp0frontend\node_modules\vite\package.json" goto :not_installed
)

rem ----------------------------------------------------------------
rem Free ports 8000 and 5173
rem ----------------------------------------------------------------
powershell -NoProfile -Command "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { 8000, 5173 -contains $_.LocalPort } | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

rem ----------------------------------------------------------------
rem Launch GemmaGallery
rem ----------------------------------------------------------------
echo.
echo ================================================================
echo   GemmaGallery - AI-Powered Multimodal Semantic Gallery
echo ================================================================
echo   Web UI: http://127.0.0.1:8000
echo   Press Ctrl+C in this terminal window to stop the server.
echo ================================================================
echo.

"%PYTHON%" -u "backend\run_all.py" %*
set "RUN_RESULT=%ERRORLEVEL%"

if not "%RUN_RESULT%"=="0" (
    echo.
    echo [!] GemmaGallery exited with error code %RUN_RESULT%.
    if "%NO_PAUSE%"=="0" pause
)
exit /b %RUN_RESULT%

:not_installed
echo.
echo ================================================================
echo   [!] GemmaGallery is not installed yet!
echo ================================================================
echo.
echo   Dependencies have not been set up in this environment.
echo   Please run install.bat first to install all dependencies.
echo.
set "PROMPT_INSTALL=y"
set /p "PROMPT_INSTALL=Would you like to run install.bat now? [Y/n]: "
if /i "%PROMPT_INSTALL%"=="" set "PROMPT_INSTALL=y"
if /i "%PROMPT_INSTALL%"=="y" (
    echo.
    echo [*] Starting setup installer...
    call "%~dp0install.bat"
    exit /b
)

echo.
echo Setup cancelled. Run install.bat when you are ready.
if "%NO_PAUSE%"=="0" pause
exit /b 1
