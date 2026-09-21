# Pipeline 2.0: continuación operacional congelada

Se mantienen las funciones originales de detección, extracción, clasificación,
familias, vetting, ranking y matching. La cohorte sigue siendo exactamente la
lista congelada de 92 KepID y 123 KOI CANDIDATE del TEST.

Las primeras 22 estrellas NO se recalculan. Sus archivos se verifican mediante
SHA256, incluidos CSV, figuras, metadata y registros de intento.

Solo las interrupciones externas de KIC 5812960, 6043562, 6070337 y 6263593
autorizan reintentos desde cero. No son reintentos por mala recuperación.
5812960 y 6043562 se continuaron desde el panel; sus restos originales están en
`results/local_panel/blind/history`. Los demás restos están en
`operational_retry_archive`. El manifiesto registra ambos archivos históricos.
La cohorte restante conserva su orden por KepID, con hasta cuatro procesos.

La ampliación del informe es exclusivamente posterior al scan. No modifica el
protocolo original ni el matching: tolerancia relativa de periodo 1%, ventana
de epoch max(1.5 * duración KOI / 24, 2 * 1765.5 / 86400) días, relaciones
armónicas 2..8 y sus inversas, asignación uno-a-uno original sin cambios.
RF positivo sigue significando puntuación >=0.5, separado del recall BLS.

Los bins de periodo, profundidad y número de tránsitos están documentados en
evaluation_manifest.json. En cada bin se presentan N y recuperaciones, sin
inferir una función de completitud de la población Kepler general. El catálogo
local congelado no contiene koi_depth: el análisis de profundidad será marcado
como no disponible. No se utilizará la profundidad detectada como sustituto.

El número de tránsitos para el análisis secundario es un indicador de cobertura:
número de ventanas de efemérides KOI con al menos un timestamp finito cacheado,
antes del preprocessing. Semiventana = max(duración KOI / 48, media cadencia
Kepler) días. No equivale al número de eventos detectados o de tránsitos fiables.
El cálculo se realiza solo después del scan y no alimenta el detector.

not_recovered.csv incluirá pérdidas raw, recuperaciones solo armónicas y pérdidas
raw -> familia. Una causa como masking, bajo SNR o falta de información residual
no se atribuirá sin evidencia guardada. En esos casos se usará unknown.

KNOWN ISSUE PIPELINE 2.0: possible resonance/harmonic family collision.
Se conserva el comportamiento observado en KIC 5219234, sin corregirlo aquí.

KIC 10854555 se ejecuta al final, separado de todos los denominadores, MRR,
análisis por bins y tiempos de la cohorte oficial. No se inicia Pipeline 2.1.
