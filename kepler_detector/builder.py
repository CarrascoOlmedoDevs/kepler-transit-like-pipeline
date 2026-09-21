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


def build(root, config, per_class=500, force=False, retry_failed=False):
    if per_class < 1:
        raise ValueError('per-class debe ser positivo')
    logger = logging.getLogger('kepler')
    (root / 'logs/build_errors.log').touch(exist_ok=True)
    catalog = get_catalog(root)
    manifest_path = root / 'data/build_manifest.json'
    manifest = {'config': config.to_dict(), **schema_metadata(),
                'catalog_sha256': file_hash(root / 'data/koi_dr25.csv'), 'label_column': 'koi_pdisposition'}
    # Serialización uniforme (tuplas -> listas).
    manifest = json.loads(json.dumps(manifest))
    previous = read_manifest(root)
    path = root / 'data/training_features.csv'
    progress_path = root / 'data/build_progress.json'
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
    chosen = catalog[catalog.koi_pdisposition.isin(['CANDIDATE', 'FALSE POSITIVE'])].copy()
    chosen['label'] = chosen.koi_pdisposition.map({'CANDIDATE': 1, 'FALSE POSITIVE': 0})
    chosen = chosen[np.isfinite(chosen.koi_period) & (chosen.koi_period > max(config.durations))]
    # Orden fijo por estrella: reutiliza curva/preprocesamiento para varios KOI.
    star_ids = chosen.kepid.unique()
    np.random.default_rng(config.seed).shuffle(star_ids)
    for kic in star_ids:
        signals = [row for row in chosen[chosen.kepid == kic].itertuples()
                   if counts[row.label] < per_class and row.kepoi_name not in completed
                   and (retry_failed or progress.get(row.kepoi_name, {}).get('status') != 'failed')]
        if not signals:
            continue
        start = time.monotonic()
        try:
            curve = preprocess(download(root, int(kic), config.quarter), config)
        except Exception as error:
            detail = traceback.format_exc()
            for row in signals:
                progress[row.kepoi_name] = {'status': 'failed', 'kepid': int(kic), 'error': str(error)}
            with open(root / 'logs/build_errors.log', 'a', encoding='utf-8') as stream:
                stream.write(f'KIC {kic}\n{detail}\n')
            logger.warning('Build KIC %s falló: %s', kic, error)
            atomic_json(progress, progress_path)
            continue
        for row in signals:
            if counts[row.label] >= per_class:
                continue
            try:
                candidates, _ = search(curve, config, known_period=row.koi_period)
                features = extract(curve, candidates[0])
                # Enforce the same ordered RF schema used by train/predict/evaluate.
                features = {**{name: features[name] for name in RF_FEATURE_COLUMNS},
                            **{name: value for name, value in features.items() if name not in RF_FEATURE_COLUMNS}}
                record = {'kepid': int(kic), 'kepoi_name': row.kepoi_name, 'label': int(row.label), **features}
                rows.append(record)
                completed.add(row.kepoi_name)
                counts[row.label] += 1
                atomic_csv(pd.DataFrame(rows), path)
                manifest['row_count'] = len(rows)
                atomic_json(manifest, manifest_path)
                progress[row.kepoi_name] = {'status': 'complete', 'kepid': int(kic)}
            except Exception as error:
                progress[row.kepoi_name] = {'status': 'failed', 'kepid': int(kic), 'error': str(error)}
                with open(root / 'logs/build_errors.log', 'a', encoding='utf-8') as stream:
                    stream.write(f'{row.kepoi_name}\n{traceback.format_exc()}\n')
                logger.warning('Build %s falló: %s', row.kepoi_name, error)
            atomic_json(progress, progress_path)
        logger.info('Build KIC %s %.1fs; FP=%s candidato=%s', kic, time.monotonic() - start, counts[0], counts[1])
        del curve
        if min(counts.values()) >= per_class:
            break
    if min(counts.values()) < per_class:
        logger.warning('Dataset parcial: %s; consulte logs/build_errors.log', counts)
    return counts
