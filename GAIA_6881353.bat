@echo off
cd /d "%~dp0"
title Sakanet - vecinos Gaia KIC 6881353
.venv\Scripts\python.exe examen_pixeles.py --solo-gaia
echo.
pause
