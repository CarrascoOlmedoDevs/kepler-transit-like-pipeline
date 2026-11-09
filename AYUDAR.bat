@echo off
setlocal
cd /d "%~dp0"
title Sakanet - equipo ayudante
if not exist ".venv\Scripts\python.exe" (
  echo Sakanet no esta instalado en este PC. Instalando primero...
  call INSTALAR.bat
  if not exist ".venv\Scripts\python.exe" ( echo No se pudo instalar. & pause & exit /b 1 )
)
if /i "%~1"=="reconfigurar" del /q ayudante.json 2>nul
echo === Sakanet: ayudando al PC coordinador ===
echo (Para cambiar direccion, token o numero de estrellas: AYUDAR.bat reconfigurar)
echo.
if /i "%~1"=="reconfigurar" (
  ".venv\Scripts\python.exe" ayudante.py
) else (
  ".venv\Scripts\python.exe" ayudante.py %*
)
echo.
echo Ayudante detenido.
pause
