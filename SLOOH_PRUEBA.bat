@echo off
cd /d "%~dp0"
title Sakanet - prueba Slooh KIC 6881353
if "%~1"=="" (echo Arrastra la carpeta con los FITS de Slooh encima de este .bat & pause & exit /b 1)
.venv\Scripts\python.exe slooh_psf.py "%~1"
if not errorlevel 1 start "" "results\seguimiento\KIC_6881353\slooh"
echo.
pause
