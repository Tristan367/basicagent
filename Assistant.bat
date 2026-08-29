@echo off
rem Starting the app, for somebody who has never opened a terminal. Double-click
rem this and the app opens in its own window; close the window and it stops.
rem
rem The venv's own Python, not whatever `python` happens to mean today: the
rem dependencies live in there, and a system Python would start and then fail on
rem the first import with a message nobody could act on.
rem
rem Two places to look, because the installer copies the app out of the folder
rem it was unzipped into. Somebody who kept the download and double-clicks this
rem copy of it should get the app, not a lecture about installing it again.
setlocal
cd /d "%~dp0"

set "APP=%~dp0"
if not exist "%APP%.venv\Scripts\python.exe" set "APP=%LOCALAPPDATA%\Programs\Assistant\"

if not exist "%APP%.venv\Scripts\python.exe" (
    echo.
    echo This has not been set up yet. Double-click install.bat first, wait for
    echo it to finish, then run this again.
    echo.
    pause
    exit /b 1
)

cd /d "%APP%"
".venv\Scripts\python.exe" basicagent.py %*
if errorlevel 1 pause
