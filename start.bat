@echo off
rem  assay -- THE one file.  Double-click me.
rem
rem    1. finds Python 3.10+ (offers to install it if missing)
rem    2. makes the virtualenv and installs the packages
rem    3. starts the dashboard and opens your browser
rem
rem  Already set up?  Steps 1-2 take a second and then it just starts.
rem  Only want to look without changing anything?   start.bat --check
rem
rem  There is exactly ONE .bat on purpose.  Two of them means the messages
rem  have to name the other one ("now run install.bat"), and then whichever
rem  file you double-clicked is not the file you are told to run next.
rem
rem  (ASCII-only: after `chcp` cmd.exe keeps re-reading this file by BYTE
rem   OFFSET, so multi-byte text here corrupts the parse -- and it does not
rem   error, it just skips commands.  All Chinese comes from install.py.)
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set "LOG=%~dp0install.log"

rem -- `py -3` FIRST: on a fresh Windows a bare `python` is often the Microsoft
rem    Store stub, which opens the Store instead of running anything.
set "PY="
py -3 -c "import sys;sys.exit(0 if sys.version_info[:2]>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"

if not defined PY (
  python -c "import sys;sys.exit(0 if sys.version_info[:2]>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)

rem -- goto a LABEL, do not open a () block here:  `set /p` inside a
rem    parenthesised block reads the value from BEFORE the block (batch expands
rem    %VAR% at parse time), so the menu below would always take the same
rem    branch -- and that failure is silent.
if not defined PY goto nopy

%PY% "%~dp0install.py" --serve %*
set "RC=%ERRORLEVEL%"

rem -- Restate the verdict in ASCII as the VERY LAST thing on screen.
echo.
if "%RC%"=="0" goto vok
if "%RC%"=="4" goto vsrv
echo   [FAIL] exit code %RC% -- the environment is NOT ready
goto vend
:vok
echo   [OK]   finished, exit code 0
goto vend
:vsrv
echo   [!]    the environment is fine, but the dashboard stopped.
echo          The reason is in the log (scroll up, or open it).
:vend
echo   Log:  %LOG%

echo.
pause
exit /b %RC%


:nopy
rem -- This branch is the one a fresh machine actually hits, so it is the one
rem    that most needs a durable record: install.py never ran, so its own log
rem    writing never happened either.  Write the log FIRST, then show it.
rem -- Say WHAT this machine has.  "not found" alone cannot tell apart:
rem    not installed / older than 3.10 / Microsoft Store stub / not on PATH.
rem    Those four need different fixes.
> "%LOG%" echo   [X] No Python 3.10+ found.
>>"%LOG%" echo.
>>"%LOG%" echo   ---- what this machine has ----
>>"%LOG%" echo   $ py -0p
py -0p           >>"%LOG%" 2>&1
>>"%LOG%" echo   $ python -V
python -V        >>"%LOG%" 2>&1
>>"%LOG%" echo   $ where python
where python     >>"%LOG%" 2>&1
>>"%LOG%" echo   $ winget --version
winget --version >>"%LOG%" 2>&1
>>"%LOG%" echo   -------------------------------
echo.
type "%LOG%"

echo.
echo   ==========================================================
echo    assay needs Python 3.10 or newer.  Pick one:
echo   ==========================================================
echo.
echo    [1] Install it for me now      ^<-- recommended
echo        . winget if present, else download from python.org
echo        . installs for THIS USER only - no admin rights needed
echo        . about 27 MB;  adds python to PATH automatically
echo.
echo    [2] Show me how to do it myself
echo.
echo    [3] Quit, do nothing
echo.
set "ANS="
set /p "ANS=   Type 1, 2 or 3 then press Enter:  "
if "%ANS%"=="1" goto autopy
if "%ANS%"=="2" goto manualpy

echo.
echo   Nothing was installed.  Full log:  %LOG%
echo.
pause
exit /b 2


:manualpy
echo.
echo   ----------------------------------------------------------
echo    Install Python by hand -- 4 steps
echo   ----------------------------------------------------------
echo.
echo    1. Open      https://www.python.org/downloads/windows/
echo    2. Download  "Windows installer (64-bit)"  under the newest
echo                 3.12 or 3.13 release
echo    3. Run it, and on the FIRST screen tick BOTH boxes:
echo.
echo           [x] Use admin privileges when installing py.exe
echo           [x] Add python.exe to PATH        ^<-- easy to miss
echo.
echo       then click "Install Now".
echo.
echo    4. CLOSE this window.  Open a NEW one.  Run %~nx0 again.
echo       (A PATH change never reaches a window that is already open --
echo        skipping this step looks exactly like "it did not install".)
echo.
echo   Check it worked:   py -3 -V      should print  Python 3.12.x
echo.
echo   Log:  %LOG%
echo.
pause
exit /b 2


:autopy
echo.
echo   -- [1/2] trying winget ... --
winget --version >nul 2>&1
if errorlevel 1 (
  echo      winget is not available on this machine, falling back.
  goto dlpy
)
winget install --id Python.Python.3.12 --exact --source winget --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
  echo      winget did not finish, falling back to a direct download.
  goto dlpy
)
goto afterinst

:dlpy
echo.
echo   -- [2/2] downloading python-3.12.10-amd64.exe from python.org (~27 MB) ... --
set "PYEXE=%TEMP%\assay-python-3.12.10-amd64.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile '%PYEXE%'"
if errorlevel 1 goto instfail
echo   -- installing (for this user, PATH on) ... this takes a minute --
"%PYEXE%" /passive InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
if errorlevel 1 goto instfail

:afterinst
echo.
echo   ==========================================================
echo    Python is installed.
echo   ==========================================================
echo.
echo    [!] THIS window still has the OLD PATH -- that is how Windows
echo        works, nothing went wrong.
echo.
echo        CLOSE this window.  Open a NEW one.  Run %~nx0 again.
echo.
echo   Check it worked:   py -3 -V      should print  Python 3.12.x
echo.
pause
exit /b 2

:instfail
echo.
echo   [X] The automatic install did not finish.
echo       Run %~nx0 again and choose [2] for the manual steps.
echo.
echo   Log:  %LOG%
echo.
pause
exit /b 2
