@echo off
setlocal
cd /d "%~dp0"
title Hypit Studio - Windows Setup
py -3.13 -c "import sysconfig; assert sysconfig.get_platform() == 'win-amd64'" >nul 2>&1
if not errorlevel 1 goto use_py
python -c "import sys,sysconfig; assert sys.version_info[:2] == (3,13) and sysconfig.get_platform() == 'win-amd64'" >nul 2>&1
if not errorlevel 1 goto use_python
echo.
echo Install Python 3.13 - Windows installer, 64-bit - from:
echo https://www.python.org/downloads/windows/
echo Enable "Add python.exe to PATH" and the Python launcher during installation.
echo Then double-click Setup.bat again.
echo See START-HERE-WINDOWS.txt for details.
pause
exit /b 1
:use_py
py -3.13 "%~dp0windows.py" setup
goto finished
:use_python
python "%~dp0windows.py" setup
:finished
if errorlevel 1 goto failed
echo.
echo Ready. Double-click Start.bat to open the app.
pause
exit /b 0
:failed
echo.
echo Setup did not complete. Read the error above, then try again.
pause
exit /b 1
