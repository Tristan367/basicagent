@echo off
rem Starting the app, for somebody who has never opened a terminal. Double-click
rem this and the app opens in its own window; close the window and it stops.
rem
rem The venv's own Python, not whatever `python` happens to mean today: the
rem dependencies live in there, and a system Python would start and then fail on
rem the first import with a message nobody could act on.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo This has not been set up yet. Double-click install.bat first, wait for
    echo it to finish, then run this again.
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" basicagent.py %*
if errorlevel 1 pause
