# KIC 5610665 — corrección exploratoria de TESS sector 40

## Resultado
Se ha extraído fotometría de apertura y sustraído un fondo local. Se han corregido tendencias temporales excluyendo una región de ±0,65 días alrededor del centro previsto. Esta extracción no confirma ni descarta una señal de 75 ppm.

- Archivo original: KIC_5610665_TESS_sector40_7x7.fits (TESSCut, sector 40, cámara 2, CCD 4).
- Coordenadas: 288.910601°, +40.800590°.
- 3927 cadencias originales; 3917 con QUALITY=0 y píxeles finitos.
- 80 cadencias válidas dentro de la duración nominal de 13,4316 horas.
- Centro supuesto: BTJD(TDB) 2401,495199513156. Obtenido de BKJD 260,54511369116005 + 20 × 215,39750429110552 días.
- La conversión del centro a fecha civil es aproximada: BJD es un tiempo referido al baricentro; la etiqueta UTC no incluye una inversión geométrica precisa para TESS.

## Procedimiento
Los FLUX_BKG de este recorte son cero. Se estima el fondo mediante la mediana de 11 píxeles del cuartil más tenue de la imagen mediana, a más de 2,5 píxeles del objetivo. Se resta esa estimación a cada píxel de la apertura.

Se comparan una apertura central 2×2 y radios de 1,5 y 2 píxeles centrados en la posición WCS. No se aplicó corrección de dilución ni un ajuste PRF de las estrellas vecinas. El fondo estimado puede incluir alas estelares.

Para visualizar todo el sector se usa una spline cúbica con nudos cada 3 días, separada en los huecos de más de medio día. Para medir la ventana se comparan ajustes lineales y cuadráticos en intervalos de ±2 y ±3 días. Todos los ajustes excluyen ±0,65 días del centro (31,2 horas en total). Los datos de la ventana no se recortan según su brillo.

## Medidas
La elección principal, fijada como apertura central 2×2 y ajuste local lineal ±2 días, da una depresión media aparente de 623 ppm. La dispersión robusta de medidas equivalentes en 24 ventanas de control es 591 ppm. Este segundo número es un diagnóstico empírico de variabilidad residual, no una incertidumbre gaussiana calibrada ni una probabilidad de falso positivo. Los controles se solapan y no son independientes.

Las 12 combinaciones dan profundidades desde −382 hasta +1333 ppm. La dependencia del método indica que la profundidad no es estable. En la elección principal, la dispersión robusta por cadencia es 1463 ppm. Aun la aproximación ideal de ruido independiente (1463/√80 ≈164 ppm) supera 75 ppm; las correlaciones y el ajuste de fondo empeoran la situación.

Se comprobó que introducir una caída sintética de 75 ppm en la ventana protegida, manteniendo el ajuste exterior, cambia la profundidad medida en 74,95 ppm. Esto verifica que el ajuste local enmascarado conserva esa señal sintética; no demuestra que pueda detectarse entre el ruido ni sustituye un estudio completo de inyección y recuperación.

## Límites y siguiente comprobación útil
La efeméride se extrapola unos doce años desde su época inicial sin una incertidumbre de período o época estimada. No se ha hecho una búsqueda con desplazamiento del centro. Tampoco se han modelado estrellas contaminantes, variación espacial del fondo ni movimiento de la imagen. No se ha comparado esta extracción con productos QLP/SPOC/TGLC.

El resultado es una curva con tendencia reducida y un diagnóstico de su sensibilidad. La caída aparente no es una detección independiente sólida del candidato de Kepler. Para mejorar la medición conviene comparar una reducción oficial o con separación de fuentes y estimar la incertidumbre de la efeméride de Kepler.

## Archivos
- TESS40_tendencia_corregida.png: antes/después y detalle de la ventana.
- apertura_y_fondo.png: ubicación del objetivo, apertura y píxeles de fondo.
- curva_corregida.csv: tiempos TDB, fotometría y tendencias; las columnas locales solo son válidas dentro de ±2 días.
- sensibilidad_aperturas.csv: resultados de las 12 combinaciones.
- resumen.json: parámetros y resultados numéricos.
- analizar_tess40.py: código completo reproducible (numpy, scipy, astropy, matplotlib).

Ejecución: python analizar_tess40.py KIC_5610665_TESS_sector40_7x7.fits directorio_salida
