@echo off
rem  assay -- ONE file to start everything.  Double-click me.
rem
rem    1. finds Python 3.10+ (offers to install it if missing)
rem    2. makes the virtualenv and installs the 4 packages
rem    3. starts the dashboard and opens your browser
rem
rem  Already set up?  Steps 1-2 take a second and then it just starts.
rem
rem  This file ONLY forwards.  The "find Python" menu lives in install.bat and
rem  the real work in install.py -- two copies of that menu would drift, and
rem  the one that drifts is the one a fresh machine hits.
call "%~dp0install.bat" --serve %*
exit /b %ERRORLEVEL%
