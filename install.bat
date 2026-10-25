@echo off
setlocal
title F1 25 Race Engineer - Install

set "PYW="
for /f "delims=" %%p in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%p"
if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not defined PYW (
    echo Python not found. Installing Python 3.12 with winget...
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
)
if not exist "%PYW%" (
    echo Python install failed. Install Python 3 from python.org, then run this again.
    pause
    exit /b 1
)

rem Auto-start: a shortcut in the Windows Startup folder. The engineer idles until F1 25 sends telemetry.
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\F1 25 Race Engineer.lnk'); $s.TargetPath='%PYW%'; $s.Arguments='\"%~dp0app.py\"'; $s.WorkingDirectory='%~dp0'; $s.Save()"
start "" "%PYW%" "%~dp0app.py"

echo.
echo Installed. The engineer now starts with Windows and waits for F1 25.
echo In F1 25: Settings ^> Telemetry Settings ^> UDP Telemetry On, IP 127.0.0.1, Port 20777, Format 2025.
echo Use Borderless or Windowed display mode to see the overlay.
echo Optional radio button: run "Bind Radio Button.bat".
pause
