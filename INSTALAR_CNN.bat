@echo off
cd /d "%~dp0"
title Sakanet - instalar CNN (TensorFlow)
echo Instalando TensorFlow para la CNN (unos 500 MB, tarda unos minutos)...
.venv\Scripts\python.exe -m pip install -r requirements-cnn.txt
if errorlevel 1 (echo. & echo ERROR instalando TensorFlow. & pause & exit /b 1)
.venv\Scripts\python.exe -c "import tensorflow as tf; print(\"TensorFlow\", tf.__version__, \"listo\")"
echo.
echo Listo. En el panel: tarjeta ExoMiner-lite CNN, pulsa 1 Construir vistas y luego 2 Entrenar.
pause
