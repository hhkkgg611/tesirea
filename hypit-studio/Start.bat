@echo off
setlocal
cd /d "%~dp0"
title Hypit Studio - Keep this window open
if not exist ".venv\Scripts\python.exe" goto needs_setup
".venv\Scripts\python.exe" "%~dp0windows.py" start
if errorlevel 1 goto failed
exit /b 0
:needs_setup
echo Run Setup.bat first.
pause
exit /b 1
:failed
echo.
echo The studio stopped. Read the error above or START-HERE-WINDOWS.txt.
pause
exit /b 1
