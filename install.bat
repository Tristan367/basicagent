@echo off
rem Windows has no `python3`, and telling somebody to work out whether their
rem computer calls it `python`, `py` or a path in AppData is the step where a
rem non-technical person stops -- which on Windows is nearly everybody using
rem this. Double-clicking this file is the whole instruction.
rem
rem `py` is the launcher that ships with python.org's installer and knows about
rem every version on the machine; `python` is what a Microsoft Store install
rem leaves on PATH. Whichever answers first is used.
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 install.py %*
    goto done
)

where python >nul 2>nul
if %errorlevel%==0 (
    python install.py %*
    goto done
)

echo.
echo Python is not installed on this computer, or it is not on the PATH.
echo.
echo Get it from https://www.python.org/downloads/ -- during the install,
echo tick "Add python.exe to PATH" on the first screen. Then run this again.
echo.

:done
rem Double-clicked from Explorer there is no console to read afterwards, so
rem hold the window open. Run from a terminal this is one extra key.
echo.
pause
