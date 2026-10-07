@echo off
REM ─ Market Analytics — Windows packager ───────────────────────────────
REM
REM Produces dist\MarketAnalytics\MarketAnalytics.exe + matching folder.
REM
REM Prereqs (one-time):
REM   - Python 3.11+ on PATH
REM   - Node.js 18+ on PATH (only for the npm build step; the shipped
REM     app uses its own bundled portable Node)
REM   - A working internet connection (downloads pip deps, npm deps,
REM     pyinstaller, and portable Node ~30 MB)
REM
REM Usage:
REM   build_windows.bat
REM
REM Output:
REM   dist\MarketAnalytics\MarketAnalytics.exe
REM   dist\MarketAnalytics\_internal\... (~400-600 MB depending on
REM     installed deps)

setlocal enabledelayedexpansion

REM ─ Cd to repo root regardless of where the user ran from ─────────────
pushd "%~dp0\.."
set REPO=%CD%

echo.
echo [1/6] Sanity-check Python + Node
where python  >nul 2>&1 || (echo ERROR: python not on PATH & goto :err)
where node    >nul 2>&1 || (echo ERROR: node not on PATH   & goto :err)
where npm     >nul 2>&1 || (echo ERROR: npm not on PATH    & goto :err)
python --version
node --version

echo.
echo [2/6] Install Python deps + pyinstaller into .venv
if not exist .venv (
  python -m venv .venv || goto :err
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip || goto :err
python -m pip install -e ".[dev]" || goto :err
python -m pip install pyinstaller || goto :err

echo.
echo [3/6] Build the Next.js standalone bundle (ui\.next\standalone)
pushd ui
call npm ci || goto :err
call npm run build || goto :err
popd

echo.
echo [4/6] Stage everything PyInstaller needs into bundle\
if exist bundle rmdir /s /q bundle
mkdir bundle\ui

REM Next.js standalone is self-contained: server.js + node_modules + .next.
xcopy /e /q /y ui\.next\standalone\* bundle\ui\ >nul
REM standalone DOES NOT include .next\static or public — copy alongside.
mkdir bundle\ui\.next\static 2>nul
xcopy /e /q /y ui\.next\static\* bundle\ui\.next\static\ >nul
if exist ui\public (
  mkdir bundle\ui\public 2>nul
  xcopy /e /q /y ui\public\* bundle\ui\public\ >nul
)

REM Python source + configs the API touches on import.
xcopy /e /q /y src     bundle\src\     >nul
xcopy /e /q /y configs bundle\configs\ >nul

REM Portable Node — used at runtime by the launcher to run server.js
REM without depending on a system Node install.
if not exist bundle\node\node.exe (
  echo     downloading portable Node ^(~30 MB^)...
  set NODE_VER=v20.18.1
  set NODE_DIR=node-!NODE_VER!-win-x64
  set NODE_URL=https://nodejs.org/dist/!NODE_VER!/!NODE_DIR!.zip
  powershell -NoProfile -Command "Invoke-WebRequest -Uri '!NODE_URL!' -OutFile 'bundle\node.zip'" || goto :err
  powershell -NoProfile -Command "Expand-Archive -Path 'bundle\node.zip' -DestinationPath 'bundle' -Force" || goto :err
  REM Flatten: bundle\node-vX.Y.Z-win-x64\* → bundle\node\*
  if exist bundle\!NODE_DIR! (
    move bundle\!NODE_DIR! bundle\node >nul
  )
  del bundle\node.zip 2>nul
)

echo.
echo [5/6] Run PyInstaller
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
pyinstaller scripts\MarketAnalytics.spec --noconfirm || goto :err

echo.
echo [6/6] Done.
echo.
echo Built: %REPO%\dist\MarketAnalytics\MarketAnalytics.exe
echo Folder size:
powershell -NoProfile -Command "'{0:N1} MB' -f ((Get-ChildItem -Recurse 'dist\MarketAnalytics' | Measure-Object Length -Sum).Sum / 1MB)"
echo.
echo Double-click MarketAnalytics.exe to launch. On first launch sentence-
echo transformers will download BGE-M3 (~2 GB) into your user cache.
goto :eof

:err
echo.
echo BUILD FAILED.
popd
exit /b 1
