# Verificación de la revisión científica

26 de septiembre de 2026.

## Reproducción Kepler

- Se descargaron los 18 FITS de curvas de luz Q0–Q17 indicados por el manifiesto original y se registraron sus SHA256.
- La lectura con máscara `default`, normalización por producto y preprocesamiento original conserva 65263 y 65232 puntos, respectivamente.
- Tras aplicar los anchos de enmascaramiento exportados quedan exactamente los recuentos originales en las cinco iteraciones, incluyendo 63181 antes de la candidata principal.
- Las cinco profundidades y SNR medidas a las efemérides originales se reproducen con diferencias inferiores a 10⁻⁸ ppm y 10⁻⁸. Los diagnósticos individuales coinciden a los seis decimales exportados.
- Se volvió a ejecutar la búsqueda nominal completa: se recuperaron los cinco períodos, duraciones y SNR originales, en el mismo orden. Tolerancias comprobadas: 10⁻¹⁰ días, 10⁻¹⁰ horas y 10⁻⁷ en SNR.
- Las figuras de los seis eventos y del plegado se inspeccionaron visualmente. Representan un modelo de caja fijo, no un nuevo ajuste físico. Las muestras representadas se conservan.
- El problema de etiquetas de trimestre se identificó en `features.transit_consistency`, que utiliza índices de segmento. Se documentó la traducción sin modificar el código científico congelado.

## Controles y comprobaciones

- Se ejecutaron una inversión y ocho permutaciones de trimestres con semillas 42–49, junto con la realización nominal. Los JSON registran todas las detecciones aceptadas y motivos de parada; no se sustituyeron ensayos desfavorables.
- El vetting determinista complementario reconstruye cada detección a su efeméride y anchos de máscara guardados. Comprueba que el SNR coincida dentro de 10⁻⁷ antes de aplicar las banderas originales.
- La inversión se aplica antes del preprocesamiento; el clipping superior puede producir recuentos distintos. Las reordenaciones modifican la ventana global y la estructura estacional. No se reclama una FAP calibrada ni la reproducción del protocolo DR25.
- Se revisaron QUALITY y RB_LEVEL en los TPF disponibles para las ventanas nominales. Se distinguieron cadencias TPF de cadencias con PDCSAP finito y calidad aceptada.
- Se guardaron respuestas y fechas de cuatro consultas TAP. Se verificó la columna `kepid`, sin depender de su posición en el encabezado. FPWG actual queda pendiente; la copia histórica y el intento fallido están identificados como tales.
- El análisis TESS original ya se había reproducido con igualdad exacta del resumen numérico. La revisión de otros sectores compara fechas de calendario; no sustituye sus tiempos por cadencia ni la propagación de incertidumbre de efemérides.

## Integridad y límites

Los scripts nuevos se compilaron y los enlaces locales del artículo y apéndice se verificaron. `CHECKSUMS.sha256` contiene los archivos de la entrega, excluido el propio manifiesto. Los hashes de los módulos científicos utilizados se guardan en `data/source_snapshot.json`.

No se volvió a entrenar RF/CNN ni se recuperaron sus pesos. No se realizó ajuste PRF, validación planetaria, inferencia con ruido correlacionado, comprobación exhaustiva de vecinos o inyección-recuperación de completitud. La reproducibilidad numérica no certifica la interpretación física.
