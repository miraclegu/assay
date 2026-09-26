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
  rem -- write the log FIRST, then show it.  This branch is the one a
  rem    fresh machine actually hits, so it is the one that most needs a
  rem    durable record -- `pause` only helps while the window is open.
  rem    A parenthesised redirect writes the whole block in one go; batch
  rem    has no tee, and echoing everything twice would drift.
  rem -- Say WHAT this machine actually has.  "not found" alone cannot
  rem    tell apart: not installed / too old / Microsoft Store stub /
  rem    installed but not on PATH.  Those need different fixes.
  > "%~dp0install.log" echo   [X] No Python 3.10+ found.
  >>"%~dp0install.log" echo.
  >>"%~dp0install.log" echo   ---- what this machine has ----
  >>"%~dp0install.log" echo   $ py -0p
  py -0p        >>"%~dp0install.log" 2>&1
  >>"%~dp0install.log" echo   $ python -V
  python -V     >>"%~dp0install.log" 2>&1
  >>"%~dp0install.log" echo   $ where python
  where python  >>"%~dp0install.log" 2>&1
  >>"%~dp0install.log" echo   -------------------------------
  >>"%~dp0install.log" echo.
  >>"%~dp0install.log" echo       Download: https://www.python.org/downloads/windows/
  >>"%~dp0install.log" echo       While installing, TICK "Add python.exe to PATH".
  >>"%~dp0install.log" echo       Then close this window, open a NEW one, and run install.bat again.
  >>"%~dp0install.log" echo.
  >>"%~dp0install.log" echo   [FAIL] exit code 2 -- no interpreter, nothing was installed.
  echo.
  type "%~dp0install.log"
  echo   Log:  %~dp0install.log
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
