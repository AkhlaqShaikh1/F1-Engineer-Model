@echo off
title F1 25 Race Engineer - Radio Button
set "PY="
for /f "delims=" %%p in ('where python 2^>nul') do if not defined PY set "PY=%%p"
if not defined PY set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
"%PY%" "%~dp0app.py" --bind
echo Restart the engineer (uninstall.bat then install.bat, or reboot) to use the new button.
pause
