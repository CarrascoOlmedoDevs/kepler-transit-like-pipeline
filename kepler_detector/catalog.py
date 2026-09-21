"""TAP síncrono oficial; la tabla y la selección de etiquetas quedan auditables."""
from io import StringIO
import logging
import pandas as pd
import requests
from .utils import retry, atomic_csv, atomic_json, file_hash

TAP = 'https://exoplanetarchive.ipac.caltech.edu/TAP/sync'
QUERY = ('select kepid,kepoi_name,koi_disposition,koi_pdisposition,koi_period,'
         'koi_time0bk,koi_duration from q1_q17_dr25_koi')


def tap(query):
    def fetch():
        response = requests.get(TAP, params={'query': query, 'format': 'csv'}, timeout=(30, 180))
        response.raise_for_status()
        if '<html' in response.text[:200].lower() or '<VOTABLE' in response.text[:500]:
            raise RuntimeError('TAP devolvió un error: ' + response.text[:500])
        return pd.read_csv(StringIO(response.text))
    return retry(fetch)


def get_catalog(root, force=False):
    path = root / 'data/koi_dr25.csv'
    if path.exists() and not force:
        return pd.read_csv(path)
    frame = tap(QUERY)
    required = {'kepid', 'kepoi_name', 'koi_pdisposition', 'koi_period'}
    if not required.issubset(frame.columns) or frame.empty:
        raise ValueError('Respuesta TAP sin columnas/filas esperadas')
    atomic_csv(frame, path)
    atomic_json({'url': TAP, 'query': QUERY, 'rows': len(frame), 'sha256': file_hash(path),
                 'downloaded_utc': pd.Timestamp.now(tz='UTC').isoformat()}, root / 'data/catalog_metadata.json')
    logging.getLogger('kepler').info('Catálogo DR25: %d KOI guardados', len(frame))
    return frame


def targets(root, limit=1000, force=False):
    if limit < 1:
        raise ValueError('limit debe ser positivo')
    path = root / 'data/kepler_targets.csv'
    if path.exists() and not force:
        previous = pd.read_csv(path)
        if len(previous) >= limit:
            return previous.iloc[:limit]
    frame = tap(f'select distinct top {int(limit)} kepid from keplerstellar order by kepid')
    if 'kepid' not in frame or frame.empty:
        raise ValueError('No se recibieron objetivos Kepler')
    atomic_csv(frame.drop_duplicates('kepid'), path)
    return frame
