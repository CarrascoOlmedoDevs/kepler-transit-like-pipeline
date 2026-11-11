@echo off
cd /d "%~dp0"
title Sakanet - examen de pixeles KIC 6881353
.venv\Scripts\python.exe examen_pixeles.py
if not errorlevel 1 start "" "results\seguimiento\KIC_6881353\pixeles"
echo.
pause
