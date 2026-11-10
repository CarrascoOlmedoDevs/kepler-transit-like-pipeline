@echo off
cd /d "%~dp0"
echo Descargando imagenes de fondo de la NASA para el panel...
".venv\Scripts\python.exe" descargar_imagenes_nasa.py
pause
