@echo off
setlocal
cd /d "%~dp0"
echo === Instalando Sakanet ===

set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if exist "%PYEXE%" goto :tengo_python
for /f "delims=" %%i in ('py -3.11 -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
if exist "%PYEXE%" goto :tengo_python

echo Python 3.11 no encontrado. Instalandolo...
winget install -e --id Python.Python.3.11 --scope user --silent --accept-package-agreements --accept-source-agreements
set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if exist "%PYEXE%" goto :tengo_python

echo winget no ha funcionado. Descargando el instalador de python.org...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile \"$env:TEMP\python-3.11.9-amd64.exe\""
if not exist "%TEMP%\python-3.11.9-amd64.exe" ( echo ERROR: no se pudo descargar Python. & pause & exit /b 1 )
"%TEMP%\python-3.11.9-amd64.exe" /quiet InstallAllUsers=0 PrependPath=0 Include_launcher=1 Include_test=0
if not exist "%PYEXE%" ( echo ERROR: Python no se instalo correctamente. & pause & exit /b 1 )

:tengo_python
echo Usando Python: %PYEXE%
if not exist ".venv\Scripts\python.exe" "%PYEXE%" -m venv .venv
if errorlevel 1 ( echo ERROR creando el entorno. & pause & exit /b 1 )
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 ( echo ERROR instalando dependencias. & pause & exit /b 1 )
".venv\Scripts\python.exe" smoke_test.py
echo.
echo === Sakanet instalado. Abre el panel con ABRIR_PANEL_LOCAL.bat ===
pause
