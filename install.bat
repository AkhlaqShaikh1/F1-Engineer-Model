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

rem Radio voice: Piper neural TTS (separate GPL-3.0 package) and two British male voices
set "PY=%PYW:pythonw.exe=python.exe%"
echo Installing the radio voice (one time, about 150 MB)...
"%PY%" -m pip install --quiet --user piper-tts
if not exist "%~dp0voices" mkdir "%~dp0voices"
pushd "%~dp0voices"
"%PY%" -m piper.download_voices en_GB-alan-medium en_GB-northern_english_male-medium
popd

rem No auto-start: Steam runs launch.py with the game, so the engineer lives only while F1 25 runs.
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Startup')+'\F1 25 Race Engineer.lnk')"

echo.
echo Installed. Last step, in Steam: F1 25 ^> Properties ^> Launch Options, paste:
echo   "%PYW%" "%~dp0launch.py" %%command%%
echo The engineer then starts and stops with the game.
echo In F1 25: Settings ^> Telemetry Settings ^> UDP Telemetry On, IP 127.0.0.1, Port 20777, Format 2025.
echo Use Borderless or Windowed display mode to see the overlay.
echo Optional radio button: run "Bind Radio Button.bat".
pause
