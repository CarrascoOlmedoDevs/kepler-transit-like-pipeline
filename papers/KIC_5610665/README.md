# KIC 5610665 — dossier de investigación

**Javier Carrasco (Saka)** · Sakanet  
[Borrador del artículo](PAPER.md)

Estado: señal sin confirmar. Los scores RF/CNN no son probabilidades de planeta.

## Contenido

- `PAPER.md`: manuscrito, resultados completos, discusión y referencias.
- `data/candidates_original.csv`, `detections_original.csv`: exportaciones originales, sin modificación.
- `data/metadata_sanitized.json`: metadatos con prefijos de rutas de Windows eliminados; se conservan nombres de archivo y valores científicos.
- `data/cnn_reported.csv`: evaluación CNN posterior comunicada en texto, independiente del CSV original sin CNN.
- `data/tess40_*`: curva extraída, las doce configuraciones y resumen numérico.
- `data/2mass_original.tbl`, `irsa_findercharts.zip`: productos IRSA suministrados; no son series de tránsito.
- `data/download_manifest.json`: 16 productos originales Kepler/TESS consultados, URLs, tamaños y SHA256. Incluye también trimestres descargados durante la exploración que no entran en la figura final.
- `figures/`: imágenes de Kepler dentro/fuera de ventanas y diagnóstico TESS.
- `scripts/`: reproducción del análisis TESS, la comparación Kepler y descargas verificadas.
- `requirements.txt`: versiones del entorno de esta reproducción.
- `CHECKSUMS.sha256`: integridad de los archivos incluidos.

Los FITS originales se obtienen del archivo mediante el manifiesto, siguiendo la política de este repositorio. Las observaciones completas de la búsqueda Kepler, pesos RF/CNN, catálogos congelados, CSV actualizado con CNN y las cinco figuras plegadas originales no están disponibles en este dossier. No se presenta una reproducción completa del descubrimiento. Las simulaciones visuales de órbitas no forman parte de la evidencia.

## Reproducir

Desde esta carpeta, con Python y acceso a MAST:

```bash
python -m pip install -r requirements.txt
python scripts/download_data.py raw
python scripts/analizar_tess40.py raw/KIC_5610665_TESS_sector40_7x7.fits reproduced/tess40
python scripts/kepler_pixels.py raw reproduced/kepler_pixels.png
```

El primer análisis crea `reproduced/tess40` y, por ello, su directorio padre. Si se ejecuta solo la figura Kepler, crear previamente `reproduced`. Las descargas usan la biblioteca estándar y no necesitan credenciales. TESScut puede regenerar encabezados: ante un hash diferente el descargador conserva el archivo como `.changed` y se detiene, sin sustituir silenciosamente la referencia.

Se volvió a ejecutar el análisis TESS con el FITS disponible y se comparó el resumen numérico con el original. Ver `VERIFICATION.md`. No se ha vuelto a ejecutar el escáner ni su entrenamiento. La figura Kepler nueva elimina la cruz aproximada de una versión anterior; no incluye un centroide astrométrico.

## Procedencia y límites

El artículo separa resultados exportados, cifras comunicadas y medidas reproducidas. Las capturas de interfaz, enlaces a localhost, transcripciones repetidas y miniaturas duplicadas no sustituyen los datos científicos. Se conserva un solo archivo IRSA. No se afirma haber confirmado la ausencia de una entrada en todos los catálogos ni haber descartado contaminación.

Código sujeto a la licencia del repositorio; datos de archivo sujetos a las condiciones de sus proveedores. La atribución de autoría no implica aprobación editorial definitiva del borrador.
