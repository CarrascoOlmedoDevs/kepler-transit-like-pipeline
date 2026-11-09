@echo off
cd /d "%~dp0"
title Sakanet - panel en modo red
echo Cerrando el panel anterior si sigue abierto en segundo plano...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and ($_.CommandLine -like '*panel_local.py*' -or $_.CommandLine -like '*ayudante_local*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
timeout /t 2 /nobreak >nul
echo Abriendo el panel y aceptando equipos ayudantes de la red local...
echo Si Windows pregunta por el firewall, permite el acceso en redes PRIVADAS.
echo Este PC tambien analiza estrellas del reparto (1 a la vez).
echo NO cierres esta ventana mientras quieras que los ayudantes trabajen.
echo.
".venv\Scripts\python.exe" panel_local.py --open --lan
pause
