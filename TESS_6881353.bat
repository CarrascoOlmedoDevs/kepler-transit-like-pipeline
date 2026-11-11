@echo off
cd /d "%~dp0"
title Sakanet - KIC 6881353 en TESS
.venv\Scripts\python.exe tess_comprobar.py
if not errorlevel 1 start "" "results\seguimiento\KIC_6881353\tess"
echo.
pause
