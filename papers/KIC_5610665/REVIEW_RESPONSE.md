# Respuesta a la revisión científica

26 de septiembre de 2026. [Artículo revisado](PAPER.md).

| Observación | Actuación y resultado |
|---|---|
| Falta la curva Kepler | Se reconstruyen las 18 curvas, se añade plegado y seis paneles de eventos con caja fija y residuos. Profundidad global 75,17 ± 7,50 ppm, error interno explícito. |
| Cobertura y bordes | Tabla por ciclo con trimestre físico, módulo/canal, puntos, cobertura y distancia al borde. Q10/Q12 conservan 28,7/30,8 %. Q5 existe, módulo 9/canal 25, pero la ventana está en un hueco. |
| Dependencia de dos eventos | Pesan 72,4 % según el diagnóstico positivo; retirarlos deja SNR 5,56 a efeméride fija. Errores locales no incorporan toda la incertidumbre de fondo. |
| Períodos 213 y 215 días | Sin cadencias compartidas de tránsito primario, secundario ni enmascaramiento directo. No se heredan alertas solo por proximidad de período. Se conserva la posibilidad de sistemáticas amplias compartidas. |
| Duraciones repetidas | Discusión conjunta; la bandera también registra haber tocado un límite intermedio, no necesariamente el límite final. |
| Falsas alarmas | Búsqueda nominal, curva invertida y ocho permutaciones completas de trimestres con mismos parámetros, parada y enmascaramiento. Es un piloto sin ML y sin calibración poblacional; no replica el reordenamiento estacional DR25. Resultados completos en `data/null_controls`. |
| Rolling band | QUALITY y RB_LEVEL en TPF: no aparecen los bits de rolling band en las cadencias de las ventanas; se guardan máximos para cinco duraciones. No se interpreta RB_LEVEL > 0 como bandera por sí solo. |
| Sensibilidad TESS | Se adelanta la insuficiencia de esta extracción. La tabla de doce configuraciones y figuras pasan al apéndice. Se revisan los diez sectores disponibles; solo S40 contiene un centro nominal dentro de las fechas del sector. |
| Duración física | Parámetros estelares DR25 recuperados con incertidumbres; duración central circular de planeta pequeño ≈12,34 h, coherente a este nivel. |
| Novedad | Consultas fechadas KOI acumulativa/TCE DR25/keplernames sin filas. FPWG histórico 2016 sin coincidencia; la comprobación de FPWG actual no se logró y queda explícitamente abierta. |
| Redacción | Se elimina la referencia a conversaciones, se corrige Kp y se simplifica la precisión de la predicción TESS. La incertidumbre y la procedencia se concentran en métodos y discusión. |

Dos precisiones metodológicas: con ruido blanco constante, duplicar el error de una media requiere aproximadamente cuatro veces menos puntos, no dos. La ausencia de sensibilidad de la extracción TESS empleada no demuestra que todas las reducciones y combinaciones de eventos posibles sean incapaces de medir esa profundidad.

## Segunda revisión: comprobaciones realizadas

- Se confirman χ² = 11,76/3 y 29,08/5; se incorporan al resumen y a la sección 3.1.1. Q7 tiene cobertura del 86,5 %: se usa «mayor cobertura». Q14 difiere 2,95σ de los otros tres eventos al incorporar el error de la referencia; 3,4σ trata 75 ppm como exactos.
- Se extraen CROWDSAP/FLFRCSAP de los 18 FITS con hashes. CROWDSAP 1,0000 frente a 0,9995 no explica la variación por dilución modelada del objetivo. Se añade figura y tabla por detector; no se atribuye causalidad a la estación. PDCSAP ya incorpora estas correcciones.
- Cero de ocho admite cota unilateral ilustrativa del 95 % del 31,2 % (bilateral de colas iguales: 36,9 %), condicionada al modelo Bernoulli. Se enumeran las 27 detecciones: ocho reordenadas sobre umbral tienen alerta de duración, pero siete también otras. Once de doce detecciones sobre umbral tienen alertas contando la nominal; esto no mide fiabilidad.
- Se interpreta la ausencia de TCE sin equiparar MES oficial con SNR de Sakanet. Q7 tiene RB_LEVEL máximo de 0,554 a 24 cadencias, pero existen máximos >1 a duraciones cortas. Se cita la definición y la errata oficial de unidades RBTDUR.
- Se unifican comas decimales en tablas del artículo, se retira semilla de nominal/inversión y se cita AllWISE en su contexto. Los formatos numéricos de CSV/JSON/código permanecen compatibles con máquinas.
- No se ha realizado todavía inyección-recuperación, ajuste de ruido correlacionado ni localización PRF.
