"""TAP síncrono oficial; la tabla y la selección de etiquetas quedan auditables."""
from io import StringIO
import json
import logging
from pathlib import Path
import pandas as pd
import requests
from .utils import retry, atomic_csv, atomic_json, file_hash

TAP = 'https://exoplanetarchive.ipac.caltech.edu/TAP/sync'
QUERY = ('select kepid,kepoi_name,koi_disposition,koi_pdisposition,koi_period,'
         'koi_time0bk,koi_duration from q1_q17_dr25_koi')
STELLAR_QUERY = (
    "select kepid,ra,dec,kepmag,teff,logg,feh,mass,radius,dens,dist,"
    "jmag,hmag,kmag,dutycycle,dataspan,rrmscdpp06p0,nconfp,nkoi,ntce,st_quarters "
    "from keplerstellar where st_delivname='q1_q17_dr25_stellar'"
)


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


def get_stellar_catalog(root, force=False):
    """Uniform Q1-Q17 DR25 stellar catalog used to choose scan targets."""
    root = Path(root)
    path = root / 'data/kepler_stellar_dr25.csv'
    if path.exists() and not force:
        return pd.read_csv(path)
    frame = tap(STELLAR_QUERY)
    required = {'kepid', 'kepmag', 'teff', 'logg', 'dutycycle', 'dataspan', 'ntce'}
    if frame.empty or not required.issubset(frame.columns):
        raise ValueError('Catálogo estelar DR25 sin columnas/filas esperadas')
    frame = frame.dropna(subset=['kepid']).drop_duplicates('kepid').copy()
    frame['kepid'] = frame.kepid.astype(int)
    atomic_csv(frame, path)
    atomic_json({'url': TAP, 'query': STELLAR_QUERY, 'rows': len(frame),
                 'sha256': file_hash(path),
                 'downloaded_utc': pd.Timestamp.now(tz='UTC').isoformat()},
                root / 'data/stellar_catalog_metadata.json')
    logging.getLogger('kepler').info('Catálogo estelar DR25: %d estrellas guardadas', len(frame))
    return frame


def _excluded_model_kepids(root):
    path = Path(root) / 'models/kepler_exominer_lite_metadata.json'
    if not path.exists():
        return set()
    metadata = json.loads(path.read_text(encoding='utf-8'))
    return set(map(int, metadata.get('train_kepids', []))) | set(map(int, metadata.get('test_kepids', [])))


def select_discovery_targets(stellar, koi_kepids, model_kepids, limit=250):
    """Select quiet, well-covered DR25 dwarfs with no known KOI/TCE."""
    if limit < 1:
        raise ValueError('limit debe ser positivo')
    frame = stellar.copy()
    numeric = ('kepid', 'kepmag', 'teff', 'logg', 'dutycycle', 'dataspan',
               'rrmscdpp06p0', 'nkoi', 'ntce', 'nconfp')
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors='coerce')
    excluded = set(map(int, koi_kepids)) | set(map(int, model_kepids))
    eligible = frame[
        frame.kepid.notna()
        & ~frame.kepid.fillna(-1).astype(int).isin(excluded)
        & frame.kepmag.between(8.0, 15.5, inclusive='both')
        & frame.teff.between(3500, 7500, inclusive='both')
        & frame.logg.ge(3.5)
        & frame.dutycycle.ge(0.8)
        & frame.dataspan.ge(1000)
        & frame.rrmscdpp06p0.gt(0)
        & frame.nkoi.fillna(0).eq(0)
        & frame.ntce.fillna(0).eq(0)
        & frame.nconfp.fillna(0).eq(0)
    ].copy()
    eligible = eligible.sort_values(
        ['rrmscdpp06p0', 'dutycycle', 'dataspan', 'kepid'],
        ascending=[True, False, False, True],
    )
    eligible.insert(0, 'queue_rank', range(1, len(eligible) + 1))
    return eligible.iloc[:limit].reset_index(drop=True)


def discovery_targets(root, limit=250, force_catalog=False):
    root = Path(root)
    stellar = get_stellar_catalog(root, force_catalog)
    koi = get_catalog(root)
    model_kepids = _excluded_model_kepids(root)
    selected = select_discovery_targets(stellar, set(koi.kepid.astype(int)), model_kepids, limit)
    if selected.empty:
        raise ValueError('No hay estrellas elegibles con los filtros de descubrimiento')
    path = root / 'data/kepler_discovery_targets.csv'
    atomic_csv(selected, path)
    atomic_json({
        'source': 'q1_q17_dr25_stellar', 'rows': len(selected),
        'catalog_rows': len(stellar), 'excluded_koi_stars': int(koi.kepid.nunique()),
        'excluded_cnn_stars': len(model_kepids),
        'filters': {'kepmag': [8.0, 15.5], 'teff': [3500, 7500], 'logg_min': 3.5,
                    'dutycycle_min': 0.8, 'dataspan_min_days': 1000,
                    'known_koi_tce_cfp': False, 'ranking': 'lowest rrmscdpp06p0'},
        'sha256': file_hash(path),
        'created_utc': pd.Timestamp.now(tz='UTC').isoformat(),
    }, root / 'data/discovery_targets_metadata.json')
    return selected


def discovery_batches(root, batch_size=10, source=None, output=None, id_column='kepid'):
    """Split the saved discovery queue into deterministic, auditable CSV batches.
    Kepler por defecto; TESS pasa su cola, carpeta y columna `tic`."""
    root = Path(root)
    if batch_size < 1:
        raise ValueError('batch_size debe ser positivo')
    source = Path(source) if source else root / 'data/kepler_discovery_targets.csv'
    if not source.exists():
        raise FileNotFoundError(f'Falta {source.name}')
    frame = pd.read_csv(source)
    required = {'queue_rank', id_column}
    if frame.empty or not required.issubset(frame.columns):
        raise ValueError(f'La cola de descubrimiento no contiene queue_rank/{id_column}')
    if frame.queue_rank.duplicated().any() or frame[id_column].duplicated().any():
        raise ValueError(f'La cola contiene queue_rank o {id_column} duplicados')
    frame = frame.rename(columns={id_column: 'kepid'}) if id_column != 'kepid' else frame

    output = Path(output) if output else root / 'data/discovery_batches'
    output.mkdir(parents=True, exist_ok=True)
    previous_path = output / 'manifest.csv'
    previous = {}
    if previous_path.exists():
        old = pd.read_csv(previous_path)
        previous = {str(row.file): row for row in old.itertuples(index=False)}
    created = pd.Timestamp.now(tz='UTC').isoformat()
    source_sha256 = file_hash(source)
    records = []
    for offset in range(0, len(frame), batch_size):
        number = offset // batch_size + 1
        batch = frame.iloc[offset:offset + batch_size].copy()
        filename = f'batch_{number:02d}.csv'
        path = output / filename
        atomic_csv(batch.rename(columns={'kepid': id_column}), path)
        old = previous.get(filename)
        same_batch = (old is not None and int(old.start_rank) == int(batch.queue_rank.iloc[0])
                      and int(old.end_rank) == int(batch.queue_rank.iloc[-1])
                      and int(old.first_kepid) == int(batch.kepid.iloc[0])
                      and int(old.last_kepid) == int(batch.kepid.iloc[-1]))
        records.append({
            'batch_id': number,
            'file': filename,
            'start_rank': int(batch.queue_rank.iloc[0]),
            'end_rank': int(batch.queue_rank.iloc[-1]),
            'star_count': len(batch),
            'first_kepid': int(batch.kepid.iloc[0]),
            'last_kepid': int(batch.kepid.iloc[-1]),
            'status': str(old.status) if same_batch else 'pending',
            'completed_stars': int(old.completed_stars) if same_batch else 0,
            'source_sha256': source_sha256,
            'created_utc': created,
        })
    manifest = pd.DataFrame(records)
    atomic_csv(manifest, output / 'manifest.csv')
    return manifest


def replenish_discovery_batches(root, block_size=250, batch_size=10):
    """Append the next deterministic target block and rebuild the batch manifest."""
    root = Path(root)
    if block_size < 1 or batch_size < 1 or block_size % batch_size:
        raise ValueError('block_size debe ser positivo y divisible por batch_size')
    target_path = root / 'data/kepler_discovery_targets.csv'
    existing = pd.read_csv(target_path) if target_path.exists() else pd.DataFrame()
    current = len(existing)
    stellar = get_stellar_catalog(root)
    koi = get_catalog(root)
    selected = select_discovery_targets(
        stellar, set(koi.kepid.astype(int)), _excluded_model_kepids(root), current + block_size
    )
    if len(selected) < current + block_size:
        raise ValueError('No quedan suficientes estrellas elegibles para completar la reserva')
    if current and existing.kepid.astype(int).tolist() != selected.iloc[:current].kepid.astype(int).tolist():
        raise ValueError('La selecciÃ³n actual ya no coincide con el prefijo reproducible del catÃ¡logo')
    atomic_csv(selected, target_path)
    metadata_path = root / 'data/discovery_targets_metadata.json'
    metadata = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
    replenished_utc = pd.Timestamp.now(tz='UTC').isoformat()
    history = list(metadata.get('replenishment_history', []))
    history.append({'previous_targets': current, 'added_targets': block_size,
                    'total_targets': len(selected), 'created_utc': replenished_utc})
    metadata.update(rows=len(selected), sha256=file_hash(target_path),
                    replenished_utc=replenished_utc,
                    replenishment_history=history,
                    replenishment_block_size=block_size)
    atomic_json(metadata, metadata_path)
    manifest = discovery_batches(root, batch_size)
    return {'previous_targets': current, 'new_targets': block_size,
            'total_targets': len(selected), 'total_batches': len(manifest)}


def update_discovery_batch_status(root, batch_index, status, completed_stars, folder='data/discovery_batches'):
    """Atomically persist panel progress in the discovery manifest."""
    path = Path(root) / folder / 'manifest.csv'
    if not path.exists():
        return
    manifest = pd.read_csv(path)
    mask = manifest.batch_id.eq(int(batch_index))
    if mask.sum() != 1:
        raise ValueError(f'Lote {batch_index} ausente o duplicado en el manifiesto')
    manifest.loc[mask, 'status'] = str(status)
    manifest.loc[mask, 'completed_stars'] = int(completed_stars)
    atomic_csv(manifest, path)
