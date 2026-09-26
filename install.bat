@echo off
rem =====================================================================
rem  Windows installer -- finds Python, then hands over to install.py
rem
rem  Double-click it, or:   install.bat --check
rem
rem  NOTE: this file is deliberately ASCII-only.  cmd.exe re-reads a batch
rem  file by BYTE OFFSET as it executes, so switching the code page in the
rem  middle of a file that also contains multi-byte characters can corrupt
rem  the parse position.  All human-facing text comes from install.py,
rem  which sets up a real UTF-8 stdout (see assay/hashseed.py).
rem =====================================================================
chcp 65001 >nul 2>&1
cd /d "%~dp0"

rem -- "py -3" first: the bare "python" on a fresh Windows is often the
rem    Microsoft Store stub, which opens the Store instead of running.
set "PY="
py -3 -c "import sys;sys.exit(0 if sys.version_info[:2]>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"

if not defined PY (
  python -c "import sys;sys.exit(0 if sys.version_info[:2]>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)

if not defined PY (
  echo.
  echo   [X] No Python 3.10+ found.
  echo.
  echo       Download: https://www.python.org/downloads/windows/
  echo       While installing, TICK "Add python.exe to PATH".
  echo.
  echo       Already installed?  Open a NEW terminal and run:  py -3 -V
  echo.
  pause
  exit /b 2
)

%PY% "%~dp0install.py" %*
set "RC=%ERRORLEVEL%"

rem -- Restate the verdict in ASCII as the VERY LAST thing on screen.
rem    install.py already prints a verdict, but if it could not start
rem    at all (wrong interpreter, missing file) nothing would say so.
rem    The log file is what survives the window closing.
echo.
if "%RC%"=="0" (echo   [OK]   finished, exit code 0) else (echo   [FAIL] exit code %RC%)
echo   Log:  %~dp0install.log

rem -- Keep the window open when double-clicked, so the result is readable.
rem    (When run from a terminal there is already a prompt to come back to,
rem     but pausing there is harmless and costs one keypress.)
echo.
pause
exit /b %RC%
