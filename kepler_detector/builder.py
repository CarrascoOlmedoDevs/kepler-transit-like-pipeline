import json
import logging
import time
import traceback
import numpy as np
import pandas as pd
from .catalog import get_catalog
from .mast import download
from .preprocessing import preprocess
from .bls import search
from .features import extract
from .utils import atomic_csv, atomic_json, read_frame, file_hash
from .schema import RF_FEATURE_COLUMNS, schema_metadata, compatible, read_manifest, timestamp
from . import ml_paths

# Disposiciones TFOPWG: planeta confirmado/conocido frente a falso positivo/falsa alarma.
# Los PC (aún sin veredicto) se excluyen: muchos acabarán siendo falsos positivos.
TESS_LABELS = {'CP': 1, 'KP': 1, 'FP': 0, 'FA': 0}


def selection(catalog, config):
    """KOI etiquetados y orden fijo de estrellas (semilla de la configuración)."""
    chosen = catalog[catalog.koi_pdisposition.isin(['CANDIDATE', 'FALSE POSITIVE'])].copy()
    chosen['label'] = chosen.koi_pdisposition.map({'CANDIDATE': 1, 'FALSE POSITIVE': 0})
    chosen = chosen[np.isfinite(chosen.koi_period) & (chosen.koi_period > max(config.durations))]
    star_ids = chosen.kepid.unique()
    np.random.default_rng(config.seed).shuffle(star_ids)
    return chosen, star_ids


def tess_selection(toi, config):
    """TOI con disposición TFOPWG definitiva, con las mismas columnas que `selection`."""
    frame = toi.copy()
    frame['disposition'] = frame['disposition'].astype(str).str.strip().str.upper()
    frame = frame[frame.disposition.isin(TESS_LABELS)]
    period = pd.to_numeric(frame.period, errors='coerce')
    tic = pd.to_numeric(frame.tic, errors='coerce')
    chosen = pd.DataFrame({'kepid': tic, 'kepoi_name': 'TOI-' + frame.toi.astype(str),
                           'koi_period': period, 'label': frame.disposition.map(TESS_LABELS)})
    chosen = chosen[np.isfinite(chosen.koi_period) & (chosen.koi_period > max(config.durations))
                    & chosen.kepid.notna()].drop_duplicates('kepoi_name')
    chosen['kepid'] = chosen.kepid.astype(int)
    chosen['label'] = chosen.label.astype(int)
    star_ids = chosen.kepid.unique()
    np.random.default_rng(config.seed).shuffle(star_ids)
    return chosen.reset_index(drop=True), star_ids


def labelled(root, config, mission='kepler'):
    """(señales etiquetadas, orden de estrellas, ruta del catálogo) para la misión."""
    if mission == 'tess':
        from .tess import get_toi_catalog, TOI_PATH
        chosen, star_ids = tess_selection(get_toi_catalog(root), config)
        return chosen, star_ids, root / TOI_PATH
    ml_paths.get(mission)
    chosen, star_ids = selection(get_catalog(root), config)
    return chosen, star_ids, root / 'data/koi_dr25.csv'


def signal_record(curve, config, kic, kepoi_name, period, label):
    """Variables de un KOI con BLS guiado por su periodo conocido (mismo esquema RF)."""
    candidates, _ = search(curve, config, known_period=period)
    features = extract(curve, candidates[0])
    # Enforce the same ordered RF schema used by train/predict/evaluate.
    features = {**{name: features[name] for name in RF_FEATURE_COLUMNS},
                **{name: value for name, value in features.items() if name not in RF_FEATURE_COLUMNS}}
    return {'kepid': int(kic), 'kepoi_name': kepoi_name, 'label': int(label), **features}


def star_records(root, kic, signals, config, mission='kepler'):
    """Para el reparto: descarga una estrella y mide todos sus KOI/TOI.
    signals = [(nombre, period, label), ...]. Un fallo de descarga se propaga."""
    curve = preprocess(download(root, int(kic), config.quarter, mission=mission), config)
    results = []
    for name, period, label in signals:
        try:
            results.append({'status': 'complete', 'record': signal_record(curve, config, kic, name, period, label)})
        except Exception as error:
            results.append({'status': 'failed', 'kepoi_name': name, 'error': f'{type(error).__name__}: {error}'})
    return results


def build(root, config, per_class=500, force=False, retry_failed=False, mission='kepler'):
    if per_class < 1:
        raise ValueError('per-class debe ser positivo')
    paths = ml_paths.get(mission)
    logger = logging.getLogger('kepler')
    errors = root / paths.errors
    errors.touch(exist_ok=True)
    chosen, star_ids, catalog_path = labelled(root, config, mission)
    manifest_path = root / paths.manifest
    manifest = {'config': config.to_dict(), **schema_metadata(),
                'catalog_sha256': file_hash(catalog_path), 'label_column': paths.label_column,
                **({'mission': mission} if mission != 'kepler' else {})}
    # Serialización uniforme (tuplas -> listas).
    manifest = json.loads(json.dumps(manifest))
    previous = read_manifest(root, mission)
    path = root / paths.dataset
    progress_path = root / paths.progress
    if not force and any(p.exists() for p in (path, progress_path, manifest_path)):
        if not compatible(previous):
            logger.warning('Training feature cache is incompatible with the current feature schema.\n'
                           'Rebuilding features using cached Kepler light curves.')
            force = True
        elif any(previous.get(key) != manifest[key] for key in manifest):
            raise ValueError('Configuración/catálogo cambió: use otro --root o build --force')
    frame = pd.DataFrame() if force else read_frame(path)
    progress = {} if force or not progress_path.exists() else json.loads(progress_path.read_text(encoding='utf-8'))
    if force:
        atomic_csv(pd.DataFrame(columns=['kepid', 'kepoi_name', 'label', *RF_FEATURE_COLUMNS]), path)
        atomic_json({}, progress_path)
    manifest.update(created_at=timestamp() if force else previous.get('created_at', timestamp()),
                    row_count=len(frame))
    atomic_json(manifest, manifest_path)
    rows = frame.to_dict('records')
    completed = {r['kepoi_name'] for r in rows}
    counts = {label: sum(r['label'] == label for r in rows) for label in (0, 1)}
    # Orden fijo por estrella: reutiliza curva/preprocesamiento para varios KOI.
    for kic in star_ids:
        signals = [row for row in chosen[chosen.kepid == kic].itertuples()
                   if counts[row.label] < per_class and row.kepoi_name not in completed
                   and (retry_failed or progress.get(row.kepoi_name, {}).get('status') != 'failed')]
        if not signals:
            continue
        start = time.monotonic()
        try:
            curve = preprocess(download(root, int(kic), config.quarter, mission=mission), config)
        except Exception as error:
            detail = traceback.format_exc()
            for row in signals:
                progress[row.kepoi_name] = {'status': 'failed', 'kepid': int(kic), 'error': str(error)}
            with open(errors, 'a', encoding='utf-8') as stream:
                stream.write(f'{kic}\n{detail}\n')
            logger.warning('Build KIC %s falló: %s', kic, error)
            atomic_json(progress, progress_path)
            continue
        for row in signals:
            if counts[row.label] >= per_class:
                continue
            try:
                record = signal_record(curve, config, kic, row.kepoi_name, row.koi_period, row.label)
                rows.append(record)
                completed.add(row.kepoi_name)
                counts[row.label] += 1
                atomic_csv(pd.DataFrame(rows), path)
                manifest['row_count'] = len(rows)
                atomic_json(manifest, manifest_path)
                progress[row.kepoi_name] = {'status': 'complete', 'kepid': int(kic)}
            except Exception as error:
                progress[row.kepoi_name] = {'status': 'failed', 'kepid': int(kic), 'error': str(error)}
                with open(errors, 'a', encoding='utf-8') as stream:
                    stream.write(f'{row.kepoi_name}\n{traceback.format_exc()}\n')
                logger.warning('Build %s falló: %s', row.kepoi_name, error)
            atomic_json(progress, progress_path)
        logger.info('Build KIC %s %.1fs; FP=%s candidato=%s', kic, time.monotonic() - start, counts[0], counts[1])
        del curve
        if min(counts.values()) >= per_class:
            break
    if min(counts.values()) < per_class:
        logger.warning('Dataset parcial: %s; consulte %s', counts, paths.errors)
    return counts
