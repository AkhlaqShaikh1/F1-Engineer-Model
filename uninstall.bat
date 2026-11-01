@echo off
title F1 25 Race Engineer - Uninstall
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Startup')+'\F1 25 Race Engineer.lnk'); Get-CimInstance Win32_Process -Filter \"Name like 'python%%'\" | Where-Object { $_.CommandLine -like '*f1-engineer*app.py*' -or $_.CommandLine -like '*%~dp0app.py*' -or $_.CommandLine -like '*%~dp0launch.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Engineer stopped. Also clear F1 25 Launch Options in Steam. Delete this folder to remove it completely.
pause
