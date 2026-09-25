# KIC 5610665 — artículo y análisis reproducible

**Javier Carrasco (Saka)** · Sakanet

- [Artículo revisado](PAPER.md): Kepler, eventos individuales, controles y discusión.
- [Respuesta a la revisión](REVIEW_RESPONSE.md).
- [Apéndice TESS](APENDICE_TESS.md): métodos, doce configuraciones y figuras.
- [Verificación](VERIFICATION.md).

## Datos

`data/candidates_original.csv` y `detections_original.csv` son las exportaciones originales intactas. `metadata_sanitized.json` conserva valores científicos y nombres FITS, eliminando prefijos de rutas de Windows. La CNN posterior se guarda separadamente en `cnn_reported.csv`, pues no figura en el CSV original.

`data/kepler_review` contiene cobertura, banderas de rolling band, muestras de eventos, auditoría de reproducción de medidas, comparación de efemérides, pruebas por subconjuntos y duración física. `data/null_controls` conserva las diez búsquedas (nominal, inversión y ocho reordenaciones), semillas, órdenes, configuración, criterios de parada y detecciones.

`data/catalog_checks` conserva consultas, respuestas, fechas y hashes. Las tablas actuales KOI/TCE/nombres Kepler no contienen filas para este KIC. La copia FPWG es de **2016**: no debe interpretarse como una consulta actual. También se conservan la lista TESScut y la comparación de fechas sectoriales con efemérides.

`data/download_manifest.json` referencia **28 productos**: las 18 curvas Kepler, nueve TPF consultados y el recorte TESS sector 40. Los FITS se obtienen del archivo astronómico; los productos derivados pequeños se incluyen. `data/source_snapshot.json` identifica el código científico de referencia. `CHECKSUMS.sha256` verifica archivos incluidos.

Los registros de clasificación posteriores comunicados y parámetros estelares inicialmente citados se conservan como antecedentes en `reported_context.json`; para parámetros verificados se debe usar `catalog_checks/stellar.csv`. Las miniaturas IRSA son imágenes de contexto, no series de tránsito. No se incluyen simulaciones de órbitas como evidencia.

## Reproducción

Clonar este repositorio y situarse en `papers/KIC_5610665`. Instalar las versiones de `requirements.txt`. Los scripts de Kepler importan los módulos del repositorio; la referencia exacta se registra en `data/source_snapshot.json`.

Ejecutar por orden:

1. `python scripts/download_data.py raw`
2. `python scripts/review_kepler.py raw reproduced/kepler`
3. `python scripts/supplementary_checks.py reproduced/kepler`
4. `python scripts/null_controls.py raw reproduced/null_controls 8`
5. `python scripts/vet_null_controls.py raw reproduced/null_controls reproduced/null_vetting.json`
6. `python scripts/analizar_tess40.py raw/KIC_5610665_TESS_sector40_7x7.fits reproduced/tess40`
7. `python scripts/kepler_pixels.py raw reproduced/kepler_pixels.png`

El control utiliza dos procesos y puede tardar varios minutos por realización. No modifica el escáner ni los archivos originales. La reordenación preserva estructura dentro de trimestre, pero altera estaciones y ventana global: sus frecuencias no son probabilidades calibradas. No se aportan los pesos RF/CNN ni se reproduce su inferencia.

`query_catalogs.py` repite las cuatro consultas TAP y sobrescribe sus respuestas/fechas: para conservar esta entrega, ejecutarlo en una copia. El descargador comprueba hashes; TESScut puede regenerar encabezados, por lo que un cambio se conserva como `.changed` y requiere inspección.

Los NPZ completos se generan durante la reproducción y no se versionan. Las dos figuras Kepler nuevas se generan a partir de los datos reconstruidos y cajas exportadas, sin presentar un nuevo ajuste planetario. El script `supplementary_checks.py` escribe sus resúmenes en `data/kepler_review`.

Código sujeto a la licencia del repositorio; observaciones y catálogos sujetos a las condiciones de los proveedores. Estado científico: señal sin confirmar.

La captura comprimida `data/catalog_checks/tess_sector_dates.html.gz` conserva las fechas utilizadas. Tras descomprimirla, `audit_sector_dates.py` reproduce la comparación sectorial. Las cadencias de otros sectores no fueron descargadas.
