@echo off
cd /d "%~dp0"
title Sakanet - analisis de KIC 6881353
.venv\Scripts\python.exe analisis_candidato.py
if not errorlevel 1 start "" "results\seguimiento\KIC_6881353\analisis"
echo.
pause
