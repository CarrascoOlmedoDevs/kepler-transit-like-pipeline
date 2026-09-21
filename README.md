# Kepler Transit-Like Signal Pipeline

### Pipeline de búsqueda, vetting y benchmarking de señales transit-like en Kepler

**Autor: Javier Carrasco**

[![Pipeline](https://img.shields.io/badge/pipeline-2.0-45b8ac)](#benchmark-ciego-pipeline-20)
[![Python](https://img.shields.io/badge/Python-3.11-3776ab)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Proprietary-red)](LICENSE)

Proyecto ejecutable en **Python 3.11 / Windows 10–11**. Descarga curvas reales de
Kepler desde MAST usando Lightkurve, busca caídas periódicas con BLS, vuelve a medir
sus características y las prioriza con un Random Forest y pruebas físicas sencillas.
No confirma planetas, no valida descubrimientos y no estima tasas de ocurrencia.

El repositorio implementa el flujo completo: descarga y caché de curvas Kepler,
preprocesamiento, búsqueda BLS iterativa, refinamiento adaptativo de duración,
masking, deduplicación global, familias y armónicos, extracción de features,
Random Forest, vetting físico, ranking y una evaluación end-to-end ciega.

## Licencia y estado del desarrollo

El desarrollo actual del proyecto es **propietario**. El uso, copia, modificación,
redistribución o explotación de las versiones propietarias requiere autorización
escrita del titular. Consulte [`LICENSE`](LICENSE).

**Pipeline 2.0 tuvo una publicación histórica bajo Apache License 2.0.** Ese cambio
de licencia no revoca los derechos que ya hubieran sido concedidos sobre copias de
aquellas versiones históricas. La frontera exacta de licencias y los commits
correspondientes están documentados en
[`LICENSE_HISTORY.md`](LICENSE_HISTORY.md).

Las librerías de terceros y los datos astronómicos públicos conservan sus propias
licencias y condiciones. La licencia propietaria del proyecto no reclama propiedad
sobre observaciones públicas, catálogos de terceros ni hechos científicos.

## Benchmark ciego Pipeline 2.0

La evaluación congelada utiliza exclusivamente 92 estrellas del split TEST con
123 señales KOI `CANDIDATE`. El scanner recibe solamente el KepID y la curva; los
periodos, épocas y duraciones KOI se usan después de la búsqueda para medir recovery.

| Métrica | Resultado |
|---|---:|
| Fundamental raw recovery | 75/123 (61.0%) |
| Fundamental family recovery | 73/123 (59.3%) |
| Fundamental + harmonic recovery | 77/123 (62.6%) |
| Top 1 fundamental | 49/123 (39.8%) |
| Top 5 fundamental | 73/123 (59.3%) |
| MRR, denominador 123 | 0.485 |
| RF positivo condicionado a BLS fundamental | 60/75 (80.0%) |

Los resultados, tablas, gráficas, manifiesto y protocolo están en
[`docs/benchmark/pipeline-2.0`](docs/benchmark/pipeline-2.0/README.md).
La evaluación documenta una posible colisión de familias por resonancia/armónico
como problema conocido de Pipeline 2.0; no se corrigió tras observar el benchmark.

## Instalación

En PowerShell, desde este directorio:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe kepler_rf_detector.py --help
```

Si `py` no registra Python, use la ruta a `Python311\python.exe` para crear el
entorno. No se necesita activar PowerShell ni cambiar su política de ejecución.
En el resto del README, `python` representa el ejecutable de ese entorno.
`requirements.txt` fija las dependencias directas. `requirements-lock.txt` registra
también las transitivas del entorno Windows verificado; para reproducirlo exactamente
use `pip install -r requirements-lock.txt`. No mezcle Python 3.13 con este entorno.

## Primer comando y flujo completo

```powershell
python kepler_rf_detector.py catalog
python kepler_rf_detector.py build --per-class 100 --force
python kepler_rf_detector.py train
python kepler_rf_detector.py evaluate
python kepler_rf_detector.py scan --kic 11442793 --max-planets 5 --save-curves
python kepler_rf_detector.py targets --limit 1000
python kepler_rf_detector.py scan-list --input data/kepler_targets.csv --limit 100 --workers 4
```

El primer comando recomendado es **`catalog`**. `build --per-class 500` intenta
obtener 500 señales válidas de cada clase; puede durar horas o días y descargar
muchos GB. No se entrega un modelo presentado como validado a partir de unos pocos
ejemplos. Consulte `VERIFICATION.md` para distinguir la comprobación pequeña real
del entrenamiento recomendado. `scan` exige un modelo; `--no-model` permite una
comprobación explícita de BLS/vetting con `rf_probability=NaN`.

El repositorio no incluye curvas MAST, features de entrenamiento ni el modelo
`joblib`: son artefactos reproducibles, grandes y dependientes del esquema vigente.
`build --force` genera las features con todos los quarters y reutiliza las curvas
ya presentes en la caché local. Para **reanudar el build**, repita el comando sin
`--force`.

Comprobaciones pequeñas:

```powershell
python -m unittest discover -s tests -v
python smoke_test.py
python verify.py
python kepler_rf_detector.py scan --kic 11442793 --quarter 5 --period-max 40 --no-model
```

El smoke test usa datos **sintéticos** y escribe en `results/smoke/`. Un quarter real
es una prueba de I/O, no garantiza tránsitos detectables para cualquier estrella.
Para KIC 11442793 se recomienda la cobertura completa. Nunca se reduce automáticamente
el umbral para fabricar una detección.

## Fuentes oficiales

* [NASA Exoplanet Archive TAP](https://exoplanetarchive.ipac.caltech.edu/docs/TAP/usingTAP.html):
  `https://exoplanetarchive.ipac.caltech.edu/TAP/sync`, formato CSV.
* [KOI DR25](https://exoplanetarchive.ipac.caltech.edu/docs/PurposeOfKOITable.html):
  tabla **`q1_q17_dr25_koi`**, no la tabla acumulativa ni la entrega suplementaria.
* [Columnas KOI](https://exoplanetarchive.ipac.caltech.edu/docs/API_kepcandidate_columns.html):
  se usa **`koi_pdisposition`**, la disposición Kepler CANDIDATE/FALSE POSITIVE,
  como etiqueta 1/0. `koi_disposition` conserva la disposición del archivo para
  cross-match; CONFIRMED no es una tercera clase. Los objetos confirmados cuya
  disposición de búsqueda sea CANDIDATE permanecen en esa clase.
* [Lightkurve search](https://lightkurve.github.io/lightkurve/reference/api/lightkurve.search_lightcurve.html)
  y [download_all](https://lightkurve.github.io/lightkurve/reference/api/lightkurve.SearchResult.download_all.html):
  MAST, misión Kepler, autor Kepler y cadencia larga. Se descargan los productos
  secuencialmente para hacer retries individuales y controlar tráfico.
* [Astropy BLS](https://docs.astropy.org/en/stable/timeseries/bls.html).
* [Catálogo estelar Kepler](https://exoplanetarchive.ipac.caltech.edu/docs/API_keplerstellar_columns.html):
  `targets` consulta `keplerstellar`, deduplica KepID y ordena por ID. No limita los
  objetivos a estrellas ya etiquetadas como KOI.

El catálogo local incluye fecha UTC, query y SHA256 en `catalog_metadata.json`.
`catalog --force` actualiza la copia; sin ese flag no solicita la red.

## Arquitectura y archivos

```text
kepler_exoplanet_detector/
  kepler_rf_detector.py       CLI con argparse
  config.py                  configuración validada y serializable
  requirements.txt
  requirements-lock.txt
  README.md
  VERIFICATION.md
  smoke_test.py
  verify.py                  compilación, tests, CLI y smoke con logs
  kepler_detector/
    __init__.py
    catalog.py               consultas TAP y caché de catálogos
    mast.py                  Curve, descarga, caché NPZ/FITS, tiempos
    preprocessing.py         filtrado por segmento y detrending
    bls.py                   búsqueda guiada/ciega, armónicos, deduplicación
    features.py              definición única de features y vistas CNN
    vetting.py               flags y fórmula pública del score
    builder.py               dataset y checkpoints por KOI
    ml.py                    RF, split por KepID, métricas y persistencia
    scanner.py               búsqueda iterativa y batch con procesos
    plotting.py              gráficas con backend sin pantalla
    crossmatch.py            identificación posterior por período
    anomaly.py               IsolationForest opcional funcional
    utils.py                 logging, retries, hashes y escritura atómica
  tests/
    __init__.py
    test_pipeline.py
  data/                      CSV, manifests y progreso del build
  cache/
    lightcurves/             KIC_XXXXXXXX.npz o KIC_XXXXXXXX_Qxx.npz
    mast/                    FITS cacheados por Lightkurve
  models/                    RF joblib y métricas JSON
  results/                   gráficas ML, ranking batch, carpetas KIC
  logs/                      pipeline.log, build_errors.log, pruebas
  docs/benchmark/            resultados compactos de evaluaciones congeladas
```

`--root RUTA` está disponible después de cada subcomando; permite separar experimentos
y datasets. La raíz por defecto es el directorio del script, no el directorio actual.
Los CSV de entrenamiento contienen KepID, nombre KOI, etiqueta y sólo medidas obtenidas
de la curva. El RF recibe una lista explícita de columnas de `features.py`: nunca ID,
etiqueta, disposición, métricas NASA o resultado del cross-match.

## Descarga y preprocesamiento

Se prefieren y exigen **PDCSAP_FLUX**, con `quality_bitmask='default'` de Lightkurve;
no se cambia silenciosamente a SAP. Cada producto se normaliza por su mediana.
Se eliminan tiempos/flujos no finitos y flux <= 0. Errores ausentes se sustituyen por
una estimación robusta basada en diferencias consecutivas. Se conservan tiempos
**BKJD = BJD(TDB) − 2454833.0**, sin recentrar la época a cero. Los FITS originales
y metadatos permiten auditar la procedencia.

El detrending usa una mediana móvil de 5 días por defecto. Se divide en los límites
de productos y huecos > max(0.5 días, 10 cadencias). No interpola a través de huecos.
Se exige ventana >= 5 veces la duración máxima BLS. El clipping es sólo superior
(7 sigma del ruido por diferencias), para no eliminar eclipses profundos como
"outliers". Los segmentos <15 puntos se descartan; se exigen >=50 puntos totales.
Es una decisión conservadora: artefactos negativos también sobreviven y deben pasar
vetting. Tránsitos con duty cycle muy alto, variabilidad rápida y bordes de segmento
siguen siendo difíciles. La restricción de ventana no garantiza una profundidad
sin sesgo para todos los objetos.

`--flatten-window`, `--sigma-clip` y `--durations` son configurables. Ventanas y
duraciones se expresan en **días**, no cadencias/horas. `--save-curves` guarda
`original.npz` (ya normalizada/filtrada por calidad) y `detrended.npz`; los flujos
instrumentales originales están en los FITS de MAST.

## BLS y búsqueda múltiple

TRAIN usa únicamente el período KOI para buscar alrededor de ±0.2%, con época,
profundidad y duración medidas nuevamente. No usa época/duración oficiales aunque
se archivan para auditoría. Esto puede localizar otra señal dominante cercana y
no modela TTV. SEARCH no lee el catálogo hasta el cross-match posterior.

SEARCH explora 0.5–300 días por defecto, con máximo efectivo adicional
`baseline/min_transits`, y duraciones 0.05, 0.1, 0.2, 0.3, 0.45 días. El máximo de
duraciones debe ser menor que el período mínimo. La malla inicial tiene 30.000
períodos logarítmicos y objetivo likelihood. Este espaciado relativo conserva una
deriva temporal comparable a períodos cortos y largos. Se refinan 12 máximos locales
separados en log-período y sus armónicos P/2 y 2P. La malla refinada limita la deriva
de fase a aproximadamente duración mínima/4 a través de la baseline.

La duración se refina sobre una malla densa. Si el óptimo toca un borde, la malla
se amplía automáticamente dentro de los límites del período, la cadencia y la
ventana de detrending. `duration_at_boundary=True` conserva el hecho de que hubo
saturación aunque el segundo ajuste se desplace; el ranking lo penaliza.

Dos picos son aliases cuando están a <0.5% en período y se solapan al menos 50%
de sus ventanas de tránsito. Los armónicos N·P y P/N, N=2..8, se conservan y se
anotan por separado. La agrupación se hace globalmente entre todas las iteraciones
en una `CandidateFamily`; una fase diferente puede seguir siendo una señal distinta.
La malla gruesa **no garantiza completitud** para
tránsitos débiles y estrechos; aumente `--coarse-points` y `--refine-peaks` para
pruebas de sensibilidad. No es un cálculo de significancia global/FAP.

Después de cada detección se enmascaran sus eventos usando período, época y duración.
El ancho crece de 1.5 a 4 duraciones cuando el pico local no disminuye lo suficiente,
con límites sobre la fracción eliminada. `mask_effectiveness` registra la reducción
de potencia BLS alrededor del período eliminado. Las features siguientes usan esa misma
curva residual; no se sustrae un modelo planetario. Se detiene con SNR <7.1,
menos de 2 eventos observados, ausencia de picos independientes, menos de
max(100, 20% de los puntos originales), o `--max-planets`. La metadata incluye la
razón exacta. El nombre del flag no convierte los resultados en planetas.

## Definición de características

Las funciones son idénticas durante entrenamiento e inferencia. Se excluyen de la
línea base ventanas de 1.5 duraciones alrededor de fases 0 y 0.5. Las medias son
ponderadas por 1/error², con piso de error igual al MAD escalado fuera de tránsito.
La incertidumbre de una media es la raíz del máximo entre 1/suma(pesos) y varianza/n;
la profundidad incluye también la incertidumbre de la línea base.

| Feature | Definición / unidades |
|---|---|
| period_days, duration_hours | Mejor período BLS en días y duración ×24 |
| depth_ppm, depth_snr | Media exterior − interior, en ppm; dividida por su error |
| bls_power, bls_log_likelihood | Salidas Astropy con objetivo likelihood; son redundantes deliberadamente |
| number_of_transits | Número de ciclos con al menos 2 puntos dentro del tránsito |
| duty_cycle | Duración/período |
| odd_depth_ppm, even_depth_ppm | Profundidad de los ciclos impares/pares, época BLS como ciclo 0 |
| odd_even_sigma | Diferencia absoluta/error combinado de profundidades |
| secondary_depth_ppm, secondary_snr | Medida en fase 0.5 con la misma duración |
| secondary_abs_snr | Valor absoluto de la significancia en fase 0.5 |
| secondary_depth_ratio | Profundidad secundaria/primaria (NaN si primaria <=0) |
| half_period_depth | Profundidad en **ppm** al plegar con P/2, época fija; duración min(D,P/4) |
| double_period_score | Potencia BLS a 2P / potencia a P, misma curva/ruido/duración, época reoptimizada |
| oot_rms_ppm, in_transit_rms_ppm | Desviación estándar poblacional fuera/dentro |
| transit_symmetry_score | 1/(1+significancia de diferencia entre mitades del tránsito) |
| baseline_days, number_of_points | Max(t)−min(t), puntos válidos de la curva usada |
| number_of_in_transit_points | Puntos dentro de una duración del centro |
| phase_coverage | Fracción de 100 bins de fase con al menos un punto |
| transit_coverage | Eventos observados/eventos geométricamente esperados, truncado a [0,1] |
| fit_improvement | max(0,(chi² constante−chi² caja)/chi² constante) |
| duration_at_boundary | El refinamiento de duración tocó un borde del grid |

Además se guardan profundidades y SNR por evento, mediana, MAD, consistencia,
quarters independientes, contribución máxima de un tránsito y jackknife. Las listas
se serializan como JSON dentro del CSV para conservar una fila por detección.

NaN representa medida no disponible; infinitos se transforman en NaN. El RF imputa
medianas aprendidas **sólo en entrenamiento** y añade indicadores de ausencia.
Features totalmente ausentes conservan la columna. La falta de cobertura odd/even
o secundaria se indica también en vetting, no se interpreta como prueba superada.

## Vetting e interpretación del score

Flags: odd/even >3 sigma; eclipse secundario positivo o brightening negativo en
fase 0.5 cuando `abs(secondary_snr)>3`; potencia a 2P >1.15
veces P; profundidad a P/2 >80% de la primaria y secundaria significativa; SNR
insuficiente; pocos tránsitos; borde de duración; mala cobertura; dominio por un
solo evento; tests no medibles. La secundaria de fase 0.5 no
cubre binarias excéntricas con eclipse desplazado. El odd/even comparte línea
base y su error combinado es conservador. Estos errores no modelan ruido rojo.

Las salidas usan `false-positive-like`, `low-confidence candidate` o
`interesting candidate`. `rf_probability` es la fracción media de clase CANDIDATE
predicha por árboles sobre etiquetas DR25; **no está calibrada** ni equivale a
P(planeta|datos). El balance artificial altera los priors reales.

Fórmula exacta, implementada en `vetting.py`:

```text
s = clip(depth_snr / 20, 0, 1)
n = clip(number_of_transits / 3, 0, 1)
f = sqrt(clip(fit_improvement, 0, 1))
c = clip(transit_depth_consistency, 0, 1)
base = 100 * (0.45*s + 0.20*n + 0.15*f + 0.20*c)
priority_score = base * penalizaciones explícitas de flags
```

Medidas no finitas se sustituyen por 0 **sólo en esta heurística**, manteniendo NaN
en CSV y flags de ausencia. El score está en [0,100]; no es probabilidad científica.
`rf_probability` se informa por separado y no entra en `priority_score`.

## Entrenamiento y evaluación

`build` baraja las estrellas con semilla fija, reutiliza cada curva para sus KOI y
guarda cada señal completada. Nunca emplea métricas de vetting oficiales como
features. No exige SNR alto durante build: descartarlo eliminaría negativos difíciles.
La selección que logra medir un tránsito y las descargas fallidas introducen sesgo
de selección; el manifiesto y logs permiten cuantificarlo.

`train` emplea `GroupShuffleSplit` (25% de estrellas para test, semilla 42). Elige
el primer split que conserva ambas clases, nunca el de mejores métricas. Requiere
al menos 4 estrellas por clase, pero ese mínimo sólo sirve para pruebas de software.
Todas las señales de un KepID quedan juntas. RF: 400 árboles, hoja mínima 2,
`class_weight='balanced'`, `random_state=42`, un hilo para reproducibilidad.
Se guarda el modelo entrenado sólo en el conjunto train, las listas de estrellas,
versiones, hash del dataset y configuración de build. No se reajusta con test.

`evaluate` usa exactamente el test guardado y rechaza un dataset modificado.
Reporta accuracy, precision, recall, F1, ROC-AUC, Average Precision y matriz
de confusión (orden clases 0,1). Produce las cinco figuras/tablas solicitadas.
La importancia por impureza está sesgada hacia features continuas/correlacionadas;
no es una explicación causal. No hay búsqueda de hiperparámetros sobre test.

Hay cambio de distribución entre señales guiadas por período KOI y máximos de una
búsqueda ciega. Para investigación seria son necesarios negativos de búsquedas
ciegas, inyección-recuperación, validación externa, calibración y evaluación con
ruido correlacionado. Una puntuación alta requiere inspección de centroides, píxeles,
contaminación, efemérides y otras observaciones antes de cualquier afirmación física.

## Resultados y cross-match

Por estrella: `detections.csv` conserva cada detección BLS y `candidates.csv` una
fila por familia independiente ordenada por prioridad, curva original normalizada,
curva detrended, periodograma de la primera iteración, una figura plegada por señal
y `metadata.json`. Los nombres `candidate_01...` siguen el **orden de detección**;
`rank` sigue prioridad y `plot_file` enlaza la figura correcta. `family_id`, aliases,
armónicos y `n_detections` permiten reconstruir la agrupación. Cada figura tiene
vista global y tránsito centrado, puntos individuales, bins y caja BLS.

La asociación KOI se hace **después de detectar**, con
`abs(Pdetectado−Pkoi)/Pkoi <= --match-tolerance` (por defecto 1%). Sólo compara
la misma estrella y períodos directos, no llama coincidencia a un armónico. Guarda
`known_koi`, `matched_koi_name`, `known_period`, `period_difference_percent`,
`known_disposition`. Si existe `data/tce_dr25.csv`, también busca columnas TCE
compatibles y distingue KOI, TCE, false alarm conocido y señal completamente sin
match. Ninguna etiqueta sin match implica un planeta nuevo.

## Caché, reanudación y rendimiento

Las descargas llevan 4 intentos y backoff de 2, 4 y 8 segundos. Timeouts HTTP TAP:
30s conexión, 180s lectura; MAST usa además los timeouts de astroquery. Una curva
NPZ sólo se marca completa si todos los productos encontrados se descargaron.
Los FITS ya guardados se reutilizan tras una interrupción. Quarters de prueba y
cobertura completa usan claves distintas para no confundir un subconjunto con
todos los datos. No ejecute dos escritores sobre la misma raíz simultáneamente.

`build_progress.json` registra éxito/fallo por KOI; el CSV se escribe atómicamente
tras cada éxito. Al reanudar, filas existentes mandan sobre el checkpoint. Se omiten
fallos anteriores salvo `--retry-failed`. `--force` reconstruye features pero conserva
descargas. Si se cambia configuración/catálogo, build exige `--force` u otra raíz.
Puede aumentar `--per-class` sin invalidar el dataset previo.

`scan-list` deduplica objetivos, escribe un checkpoint al completar cada estrella y
continúa ante sus errores. Los checkpoints incluyen huella de configuración, modelo,
features y catálogo. Cambiar cualquiera invalida el resultado correspondiente.
Una estrella sin candidatos sigue siendo un trabajo completado. Se guardan fallos
y se reintentan con `--retry-failed`; `--force` rehace todos los objetivos de la lista.
`batch_summary.csv` reúne candidatos del input de esa ejecución, ordenados por score;
estrellas vacías/fallidas están en `batch_progress.json`.

Por defecto hay un worker. Se permiten 1–4 procesos independientes y un semáforo
global limita a **2 estrellas descargando simultáneamente**. Cada proceso carga sólo
una estrella; curvas completas y cajas BLS consumen RAM proporcional a su longitud.
Las figuras se cierran. No hay cientos de conexiones en paralelo. Detalles y
tracebacks van a `logs/pipeline.log`; la consola sólo muestra resumen de cada fallo.

## Extensiones y reproducibilidad

`features.binned_views` produce vistas globales de 2001 bins y locales de 201 bins,
con cuentas y NaN donde no hay cobertura. RF está desacoplado de estas vistas:
se puede añadir una CNN tipo AstroNet y ensemble sin cambiar BLS. Esta versión
no incluye entrenamiento CNN ni promete rendimiento de AstroNet.

`anomaly.fit_anomaly(frame, output)` entrena y guarda un IsolationForest opcional;
`anomaly_scores(model, frame)` devuelve rareza (más alto = más anómalo), no probabilidad
de planeta. No se entrena por defecto. Un autoencoder/One-Class SVM podría implementar
la misma interfaz sin alterar el pipeline.

Para reproducir un experimento conserve requirements-lock, catálogo y hash, FITS/NPZ,
manifest, CSV, joblib y metadata. Registre todos los flags. Semillas fijas y cachés
reproducen la selección; cambios de librerías/archivos de archivo pueden cambiar
resultados. No se garantiza igualdad bit a bit entre plataformas. Los modelos joblib
sólo deben cargarse de fuentes de confianza.
