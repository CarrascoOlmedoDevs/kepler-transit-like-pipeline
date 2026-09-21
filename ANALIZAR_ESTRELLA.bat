@echo off
setlocal
title Kepler - Analizar estrella

rem Inicia el analisis interactivo con el modelo Random Forest entrenado.
rem Solicita el numero KIC y la cantidad maxima de candidatos.
call "%~dp0desktop_launchers\09_Analizar_estrella.bat"

exit /b %ERRORLEVEL%
