@echo off
rem Setting the app up on Windows, by double-clicking.
rem
rem Windows has no `python3`, and telling somebody to work out whether their
rem computer calls it `python`, `py` or a path in AppData is the step where a
rem non-technical person stops -- which on Windows is nearly everybody using
rem this. Double-clicking this file is the whole instruction.
rem
rem `py` is the launcher that ships with python.org's installer and knows about
rem every version on the machine; `python` is what a Microsoft Store install
rem leaves on PATH. Whichever answers first is used, and if neither does, this
rem goes and gets Python rather than printing a sentence about it.
setlocal

rem Two layouts, one file. In the download, everything the app is made of sits
rem in `app` so that the folder somebody unzips holds four things instead of
rem twenty. In the source repository it sits beside this file. Looking for
rem `app\install.py` tells the two apart, and means the file people actually
rem receive is the same one that gets tested here.
set "APP=%~dp0"
if exist "%~dp0app\install.py" set "APP=%~dp0app\"
cd /d "%APP%"

set "PYWHY=is not on this computer yet"
call :findpython
if not defined PY call :getpython
if not defined PY goto :nopython

%PY% install.py %*
rem Exit code 3 means the installer found a Python and it was the wrong
rem version -- too new for one of the parts, which is the ordinary case on a
rem computer bought this year. It has already said so; this fetches the right
rem one and starts again rather than leaving somebody at a dead end.
if errorlevel 3 goto :wrongversion
goto :done


:wrongversion
set "PYWHY=on this computer is a version some of the parts do not support yet"
call :getpython
if not defined PY goto :nopython
%PY% install.py %*
goto :done


:findpython
rem Sets PY to something that can run install.py, or leaves it empty. The
rem installer sorts out the version itself and knows where to look for a
rem better one; all this has to do is find any Python at all.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
rem A Python installed a moment ago by this script is on the user's PATH in the
rem registry but not in this already-running console, so it is looked for where
rem the per-user installer puts it as well.
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python313\python.exe""
goto :eof


:getpython
rem Fetching Python, because "install Python and run this again" is not an
rem instruction, it is a dead end. The person reading it was told this app was
rem not technical, is looking at a black window full of text they did not
rem expect, and has no way to judge which of the eight things on python.org's
rem download page is the one they want.
rem
rem This is the official installer from python.org, checked against the exact
rem SHA-256 of the file the CPython release manager signed, and run per-user so
rem that it never asks for an administrator. If any part of that does not hold,
rem it stops and opens the download page instead of guessing.
set "PYVER=3.13.15"
set "PYURL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe"
set "PYSUM=edec09c4853aeae9ac36efb8c9f95b6b8e2fee65eee56d9767a8b7c69c574403"
set "PYSETUP=%TEMP%\python-%PYVER%-amd64.exe"

echo.
echo   This app is built on Python, which %PYWHY%.
echo.
echo   Python is free, it comes from python.org, and I can fetch and install
echo   it for you now. It takes a couple of minutes, it does not need an
echo   administrator, and it changes nothing else on your computer.
echo.
choice /C YN /N /M "   Get Python now? Press Y for yes, N to do it yourself: "
if errorlevel 2 goto :eof

where curl >nul 2>nul || goto :eof
echo.
echo   Downloading Python %PYVER% from python.org (about 30 MB)...
curl -L --fail -o "%PYSETUP%" "%PYURL%"
if errorlevel 1 goto :eof
if not exist "%PYSETUP%" goto :eof

echo.
echo   Checking the download is the file python.org signed...
set "SUM="
for /f "skip=1 delims=" %%h in ('certutil -hashfile "%PYSETUP%" SHA256 2^>nul') do (
    if not defined SUM set "SUM=%%h"
)
set "SUM=%SUM: =%"
if /i not "%SUM%"=="%PYSUM%" (
    echo   It is not. Nothing has been run. Getting Python by hand instead.
    del "%PYSETUP%" >nul 2>nul
    goto :eof
)
echo   It is.
echo.
echo   Installing Python. A window of its own will appear; leave it alone
echo   and it will close itself.
start /wait "" "%PYSETUP%" /passive InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
del "%PYSETUP%" >nul 2>nul
rem The exact path, not the launcher. `py -3` picks the newest Python on the
rem machine, which on a computer that already had a too-new one is the same
rem one we just worked around.
set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python313\python.exe""
if not defined PY call :findpython
if defined PY echo   Python is installed.
goto :eof


:nopython
echo.
echo   Python still is not here, so this cannot carry on.
echo.
echo   I am opening the page to get it from. Download the one that says
echo   "Windows installer (64-bit)", run it, and on its first screen tick
echo   "Add python.exe to PATH". Then open this file again.
echo.
start "" "https://www.python.org/downloads/windows/"

:done
rem Double-clicked from Explorer there is no console to read afterwards, so
rem hold the window open. Run from a terminal this is one extra key.
echo.
pause
