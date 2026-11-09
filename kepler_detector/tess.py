"""TESS: catálogo de TOI y cola de descubrimiento (estrellas SPOC de 2 min sin TOI).

Las señales TESS se cruzan con los TOI del NASA Exoplanet Archive. La cola de
descubrimiento recorre los sectores en orden, toma las estrellas observadas con
cadencia de 2 min por SPOC, descarta las que ya tienen un TOI y baraja cada sector
con una semilla fija, para que la cola sea reproducible y auditable."""
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .utils import atomic_csv, atomic_json, file_hash, retry

TOI_QUERY = 'select tid,toi,tfopwg_disp,pl_orbper from toi'
TOI_PATH = 'data/tess_toi.csv'
TARGETS_PATH = 'data/tess_discovery_targets.csv'
BATCH_DIR = 'data/tess_discovery_batches'
SEED = 20261109


def get_toi_catalog(root, force=False):
    root = Path(root)
    path = root / TOI_PATH
    if path.exists() and not force:
        return pd.read_csv(path)
    from .catalog import tap
    frame = tap(TOI_QUERY)
    if frame.empty or not {'tid', 'toi', 'pl_orbper'}.issubset(frame.columns):
        raise ValueError('Respuesta TOI sin columnas/filas esperadas')
    frame = frame.rename(columns={'tid': 'tic', 'tfopwg_disp': 'disposition', 'pl_orbper': 'period'})
    atomic_csv(frame, path)
    atomic_json({'query': TOI_QUERY, 'rows': len(frame), 'sha256': file_hash(path),
                 'downloaded_utc': pd.Timestamp.now(tz='UTC').isoformat()}, root / 'data/tess_toi_metadata.json')
    logging.getLogger('kepler').info('Catálogo TOI: %d filas guardadas', len(frame))
    return frame


def match_toi(tic, period, catalog, tolerance=0.01):
    """Columnas propias de TESS; el cruce KOI/TCE de Kepler queda vacío."""
    result = {'known_toi': False, 'matched_toi_name': '', 'known_toi_period': np.nan,
              'known_toi_disposition': ''}
    if catalog is None:
        return result
    result['catalog_match_status'] = 'completely unmatched signal'
    rows = catalog[(pd.to_numeric(catalog.tic, errors='coerce') == int(tic)) &
                   (pd.to_numeric(catalog.period, errors='coerce') > 0)]
    if rows.empty:
        return result
    periods = pd.to_numeric(rows.period, errors='coerce')
    difference = abs(periods - period) / periods
    index = difference.idxmin()
    if difference.loc[index] <= tolerance:
        disposition = '' if pd.isna(rows.loc[index, 'disposition']) else str(rows.loc[index, 'disposition'])
        result.update(known_toi=True, matched_toi_name=f'TOI {rows.loc[index, "toi"]}',
                      known_toi_period=float(periods.loc[index]), known_toi_disposition=disposition,
                      catalog_match_status=('previously known false alarm' if disposition.upper() in ('FP', 'FA')
                                            else 'known TOI'))
    return result


def sector_targets(sector):
    """TIC observados por SPOC con cadencia de 2 min en un sector (consulta MAST)."""
    from astroquery.mast import Observations
    table = retry(lambda: Observations.query_criteria(
        obs_collection='TESS', dataproduct_type='timeseries', provenance_name='SPOC',
        sequence_number=int(sector), t_exptime=[100, 140]))
    ids = set()
    for name in table['target_name']:
        try:
            ids.add(int(str(name).strip()))
        except ValueError:
            continue
    return sorted(ids)


def replenish(root, block_size=250, batch_size=10, max_sectors=100, fetch=sector_targets):
    """Añade estrellas de los siguientes sectores hasta sumar `block_size` y rehace los lotes."""
    root = Path(root)
    target_path = root / TARGETS_PATH
    existing = pd.read_csv(target_path) if target_path.exists() else pd.DataFrame(columns=['queue_rank', 'tic', 'sector'])
    toi = get_toi_catalog(root)
    excluded = set(pd.to_numeric(toi.tic, errors='coerce').dropna().astype(int))
    excluded |= set(existing.tic.astype(int))
    sector = int(existing.sector.max()) if len(existing) else 0
    added = []
    partial = None
    while len(added) < block_size and sector < max_sectors:
        if partial is None:
            sector += 1
            candidates = [tic for tic in fetch(sector) if tic not in excluded]
            np.random.default_rng(SEED + sector).shuffle(candidates)
            partial = candidates
        take = partial[:block_size - len(added)]
        partial = partial[len(take):] or None
        for tic in take:
            excluded.add(tic)
            added.append({'tic': int(tic), 'sector': sector})
    if not added:
        raise ValueError('No quedan estrellas TESS de 2 min sin TOI en los sectores disponibles')
    start = int(existing.queue_rank.max()) + 1 if len(existing) else 1
    frame = pd.concat([existing, pd.DataFrame([dict(queue_rank=start + i, **row) for i, row in enumerate(added)])],
                      ignore_index=True)
    atomic_csv(frame, target_path)
    from .catalog import discovery_batches
    manifest = discovery_batches(root, batch_size, source=target_path, output=root / BATCH_DIR, id_column='tic')
    atomic_json({'rows': len(frame), 'last_sector': int(frame.sector.max()), 'sha256': file_hash(target_path),
                 'toi_sha256': file_hash(root / TOI_PATH),
                 'updated_utc': pd.Timestamp.now(tz='UTC').isoformat()}, root / 'data/tess_discovery_metadata.json')
    return {'previous_targets': start - 1, 'new_targets': len(added), 'total_targets': len(frame),
            'total_batches': len(manifest)}
