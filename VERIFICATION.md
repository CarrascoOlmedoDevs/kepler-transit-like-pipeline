# Verificación ejecutada

Fecha: 20 de septiembre de 2026. Windows, Python **3.11.9**, dependencias fijadas en
`requirements-lock.txt`. Las rutas locales dependen del checkout y no forman parte
del protocolo reproducible.
El entorno usado durante la construcción está en `..\.venv\Scripts\python.exe`.

## Comprobaciones offline

```powershell
..\.venv\Scripts\python.exe verify.py
```

Resultado: compilación correcta; **17 tests OK**; CLI `--help` exit 0;
smoke test exit 0; `pip check`: **No broken requirements found**.
Los informes reproducibles están en `logs/verification.json`, `logs/tests.txt`,
`logs/help.txt`, `logs/smoke.txt` y `logs/dependencies.txt`.

Los tests comprueban: extracción y profundidad; odd/even; secundaria; armónicos;
cross-match; deduplicación; score y valores no finitos; conservación de tránsitos;
BLS guiado/ciego; vistas CNN; caché; imports; configuración; build reanudable;
búsqueda de dos señales con máscaras; batch tolerante a fallos/reanudable;
conservación de candidatos si un residual no es medible; split ML por estrella.

Smoke: señal sintética de **3.2 días**, recuperación dentro de 0.005 días,
figuras generadas bajo `results/smoke/`. Es una prueba del software, separada de
la siguiente evidencia real. Los tests sintéticos del clasificador no se usan
como modelo entregado ni como métricas científicas.

## Catálogos reales

```powershell
python kepler_rf_detector.py catalog
python kepler_rf_detector.py targets --limit 1000
```

Se descargaron **8.054 KOI** desde TAP, tabla `q1_q17_dr25_koi`, y **1.000 KepID**
desde `keplerstellar`. Query, fecha y SHA256 del catálogo KOI en
`data/catalog_metadata.json`. Las consultas y descargas funcionaron con acceso a red
autorizado; el entorno restringido bloqueaba sockets antes de concederlo.

## Build, entrenamiento y evaluación con datos reales

```powershell
python kepler_rf_detector.py build --per-class 4 --quarter 5
python kepler_rf_detector.py train
python kepler_rf_detector.py evaluate
python kepler_rf_detector.py build --per-class 4 --quarter 5
```

Resultado: **8 señales / 8 estrellas**, 4 CANDIDATE y 4 FALSE POSITIVE según
`koi_pdisposition`. Se procesaron curvas PDCSAP reales Q5. Dos señales adicionales
fallaron por cobertura de tránsito insuficiente y quedaron registradas; el build
continuó. La segunda ejecución conservó las 8 filas sin volver a procesarlas.

Split: 6 estrellas de entrenamiento y 2 de test, sin solapamiento. Las métricas
obtenidas (accuracy, precision, recall, F1, ROC-AUC y AP = 1.0; matriz [[1,0],[0,1]])
**no son una estimación útil de rendimiento**, porque el test tiene sólo dos
objetos. Se registran para verificar que todas las rutas de evaluación funcionan.
`small_sample_warning=true` se guarda en el JSON y scan advierte sobre el modelo.

Artefactos: `data/training_features.csv`, manifests y progreso;
`models/kepler_rf.joblib`, `models/kepler_rf_metrics.json`;
`results/feature_importance.csv/.png`, `confusion_matrix.png`, `roc_curve.png`,
`precision_recall_curve.png`, `test_predictions.csv`.

**No se ejecutó un entrenamiento de 500+500 señales.** Para construirlo use
`build --per-class 500 --force` una vez y luego reanude sin `--force`. Esto requiere
mucho más tráfico, espacio y tiempo. El modelo incluido es exclusivamente demostrativo.

## Estrella real: KIC 11442793

Comando final:

```powershell
python kepler_rf_detector.py scan --kic 11442793 --max-planets 3 --save-curves
```

Lightkurve encontró **14 productos long cadence**, quarters
1,2,3,4,5,6,7,9,10,11,13,14,15,17. Se descargaron todos los disponibles, sin
inventar quarters ausentes. Baseline: **1459.489 días**, 51.471 puntos después del
preprocesamiento. BLS ciego 0.5–300 días, sin usar períodos KOI. Tiempo con curvas
cacheadas: **43.1 segundos** en esta máquina; incluye tres iteraciones BLS.

| Orden detección | Período (días) | SNR | Resultado |
|---|---:|---:|---|
| 1 | 165.808135 | 11.36 | false-positive-like; odd/even y preferencia por doble período |
| 2 | 210.600419 | 20.72 | interesting candidate; coincide posteriormente con K00351.02 |

La diferencia relativa con el período conocido de K00351.02 es **0.000458%**.
La primera señal está cerca de la mitad del período de otro KOI de 331.597 días,
que queda fuera del máximo predeterminado de 300 días. La heurística la penaliza:
**unmatched candidate no significa descubrimiento**. La búsqueda se detuvo cuando
el siguiente pico medible no superó el umbral; no se inventó un tercer candidato.

El RF aplicado es el pequeño modelo Q5. Scan avisa además del cambio de distribución
entre Q5 y la cobertura completa. Sus valores son una comprobación de inferencia,
no evidencia de naturaleza planetaria. La disposición `CONFIRMED` que aparece en
`known_disposition` es metadato del archivo, nunca una afirmación de este detector.

Las imágenes de las dos señales se abrieron e inspeccionaron visualmente:
vista global/local, eje temporal, puntos/bins, modelo BLS y título completos.
Resultados y curvas NPZ en `results/KIC_11442793/`.

Una comprobación anterior de **Q5 únicamente** produjo cero candidatos para esta
estrella. La malla inicial lineal en frecuencia también recuperaba un subarmónico
de 210 días; se cambió a malla logarítmica, que recuperó 210.600419 días y redujo
el tiempo de refinamiento. Esto ilustra la incompletitud de la búsqueda, no la
elimina. La implementación conserva umbrales fijos y documenta su sensibilidad.

## Batch real, paralelismo y reanudación

```powershell
python kepler_rf_detector.py scan-list --input data/verification_targets.csv --quarter 5 --period-max 40 --max-planets 2 --coarse-points 5000 --refine-peaks 6 --workers 2
```

El input contiene KIC **7730747**, **6545018** y **−1** (fallo deliberado, no una
estrella). Los dos objetivos reales se completaron con RF, vetting, figuras y CSV;
el ID inválido quedó como failed. El batch devolvió exit 0, mantuvo las otras
estrellas y produjo `results/batch_summary.csv` y `batch_progress.json`.

La repetición exacta informó **0 pendientes / 3 objetivos**, sin volver a analizar
las estrellas. `--retry-failed` permite reintentar fallos; por defecto también se
recuerda el ID inválido para no repetir indefinidamente un fallo permanente.

## Límites pendientes de validación científica

No se ha medido completitud/pureza mediante inyección-recuperación masiva ni validado
el clasificador con una muestra grande. Malla finita, duraciones discretas, ruido
rojo, TTV, eclipses desplazados y contaminación pueden causar falsos negativos o
positivos. Picos cercanos a armónicos pueden sobrevivir a la máscara; el vetting
los ayuda a priorizar pero no resuelve toda degeneración. Los datos, el código y
los checkpoints funcionan; las conclusiones científicas requieren análisis adicional.
