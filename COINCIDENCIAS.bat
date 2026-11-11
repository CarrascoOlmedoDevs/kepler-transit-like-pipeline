@echo off
cd /d "%~dp0"
title Sakanet - coincidencias de fechas
.venv\Scripts\python.exe coincidencias.py
if not errorlevel 1 start "" "results\seguimiento\coincidencias"
echo.
pause
