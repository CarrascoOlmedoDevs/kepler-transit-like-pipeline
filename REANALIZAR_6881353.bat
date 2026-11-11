@echo off
cd /d "%~dp0"
title Sakanet - reanalizar KIC 6881353
.venv\Scripts\python.exe reanalizar_estrella.py 6881353
echo.
pause
