# Panel local Kepler

Abrir `ABRIR_PANEL_LOCAL.bat` en la raíz, o ejecutar
`.venv\Scripts\python.exe panel_local.py --open`.
Dirección: http://127.0.0.1:8768. Solo accesible desde este PC.
El servidor arranca sin ejecutar análisis. Cerrar la pestaña no detiene el servidor
ni los trabajos: usar Pausar o Detener antes de apagar el ordenador.

## Controles

- Iniciar: lista de KIC, una estrella cada vez, Config() del scanner sin modificaciones.
- Pausar: termina la estrella actual y no comienza la siguiente.
- Continuar: omite completadas y fallidas; repite únicamente los intentos interrumpidos,
  archivando antes sus archivos parciales y el registro original.
- Detener: cancela el proceso propio y sus descendientes; conserva archivos y progreso.

## ExoMiner-lite

El panel incluye un módulo experimental separado para construir las cinco vistas
morfológicas, entrenar la CNN y volver a evaluar el conjunto test. Muestra si están
disponibles el dataset escalar, las vistas, TensorFlow y el modelo, además del
registro y las métricas generadas. TensorFlow se instala de forma opcional con
`pip install -r requirements-cnn.txt`; el panel no instala dependencias por sí solo.

ExoMiner-lite comparte la restricción de un único proceso activo con las colas KIC.
No modifica el vetting, `priority_score` ni la evaluación ciega congelada.
Su score representa compatibilidad con la etiqueta DR25, no validación planetaria.

Tras entrenar el modelo, los nuevos análisis copian la CNN a su workspace aislado y
añaden `cnn_probability` a las tablas, sin modificar el ranking RF. La cola piloto
oficial se genera con `stellar-catalog` y `discovery-targets`.

Los intentos fallidos no se reejecutan automáticamente. Los resultados de nuevas
colas están aislados en `results/local_panel/<id>/workspace`; se copian las curvas
cacheadas, el modelo y catálogos existentes, sin borrar los originales.

La evaluación ciega existente aparece pausada. Se verifican todos sus hashes
antes de continuar. Se reutilizan sus funciones originales y workspace sin catálogo;
el control 10854555 va después de la cohorte y fuera de sus métricas. Los intentos
interrumpidos originales se archivan en `results/local_panel/blind/history`.
Los resultados por estrella aparecen al terminar cada scan; los agregados finales
se generan cuando acaba toda la cohorte y el control. La web publicada es una
instantánea independiente y NO se actualiza ni controla este servidor.

## Seguridad y límites

Servidor ligado a loopback, comprobación de Host/Origin, token anti-CSRF y rutas
permitidas. No admite comandos arbitrarios ni se publica en Internet. No se debe
exponer mediante un proxy/túnel. Los procesos se ejecutan con los permisos del usuario.
Un único trabajo activo por panel. No lanzar simultáneamente el evaluador por consola.
Si el servidor se cierra inesperadamente, el trabajador detecta su ausencia y sale;
la próxima sesión no reanuda nada automáticamente. La pausa puede tardar lo que
tarde la estrella actual. No es una suspensión a mitad de BLS.

Pruebas: `python -m unittest discover -s tests -v`. Las pruebas del panel usan scans
simulados. La prueba de cancelación crea exclusivamente procesos de espera; debe
ejecutarse fuera de un sandbox que prohíba taskkill.
La herramienta WebMCP de lectura es opcional, detecta soporte y no inicia trabajos;
no ha sido validada en un navegador con soporte WebMCP.
