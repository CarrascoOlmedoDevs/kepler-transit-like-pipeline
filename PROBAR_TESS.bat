@echo off
cd /d "%~dp0"
echo Escaneando TIC 25155310 (WASP-126, planeta conocido de ~3.29 dias) en el sector 1 de TESS...
".venv\Scripts\python.exe" kepler_rf_detector.py scan --tic 25155310 --sector 1 --no-model
echo.
echo Resultados y graficas en results\TIC_25155310
pause
