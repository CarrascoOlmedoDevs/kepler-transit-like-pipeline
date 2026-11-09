import json
import logging
import time
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import numpy as np
import pandas as pd
from astropy.timeseries import BoxLeastSquares
from .mast import download, save_curve
from .preprocessing import preprocess
from .bls import (search, transit_mask, duplicate, harmonic_info, CandidateFamily)
from .features import extract, transit_consistency, FEATURES, CONSISTENCY_FEATURES
from .vetting import vet, priority_score
from .crossmatch import match
from .ml import load_model, predict
from .schema import FEATURE_SCHEMA_HASH
from .utils import setup, fingerprint, file_hash, atomic_csv, atomic_json
from . import FEATURE_VERSION, PIPELINE_VERSION
from . import plotting
from . import missions


def _local_bls_power(curve, candidate):
    if len(curve.time) < 50:
        return 0.0
    periods = np.linspace(candidate.period * 0.998, candidate.period * 1.002, 31)
    durations = np.unique(np.clip(candidate.duration * np.array([0.8, 1.0, 1.2]),
                                  1e-4, periods.min() * 0.25))
    try:
        result = BoxLeastSquares(curve.time, curve.flux, curve.error).power(
            periods, durations, objective='likelihood', oversample=5)
        return float(np.nanmax(result.power))
    except (ValueError, FloatingPointError):
        return 0.0


def adaptive_mask(curve, available, candidate, target_effectiveness=0.8,
                  min_width=1.5, max_removed_fraction=0.15):
    """Amplía con límites la ventana hasta suprimir el pico local."""
    residual = curve.subset(available)
    before = max(_local_bls_power(residual, candidate), 1e-12)
    best = None
    for width in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0):
        if width < min_width:
            continue
        remove = transit_mask(curve.time, candidate, width) & available
        removed_fraction = float(remove.sum() / max(1, available.sum()))
        if removed_fraction > max_removed_fraction and best is not None:
            break
        proposed = available & ~remove
        after = _local_bls_power(curve.subset(proposed), candidate)
        effectiveness = float(np.clip(1 - after / before, 0, 1))
        state = (effectiveness, proposed, width, removed_fraction)
        if best is None or effectiveness > best[0]:
            best = state
        if effectiveness >= target_effectiveness:
            break
    return best[1], best[0], best[2], best[3]


def build_families(rows, candidates, time):
    """Agrupa globalmente aliases de periodo y armónicos N/1/N."""
    families = []
    order = sorted(range(len(rows)), key=lambda index: rows[index]['priority_score'], reverse=True)
    for index in order:
        candidate = candidates[index]
        matched = None
        relation = None
        harmonic = None
        for family in families:
            if any(duplicate(candidate, candidates[member], time) for member in family.detections):
                matched, relation = family, 'alias'
                break
        if matched is None:
            for family in families:
                info = harmonic_info(candidate.period, family.primary_period, tolerance=0.01, max_order=8)
                if info is not None:
                    matched, relation, harmonic = family, 'harmonic', info
                    break
        if matched is None:
            matched = CandidateFamily(f'F{len(families) + 1:03d}', candidate.period, index,
                                      detections=[index])
            families.append(matched)
            relation = 'primary'
        else:
            matched.detections.append(index)
            if relation == 'alias':
                matched.aliases.append(candidate.period)
            else:
                matched.harmonics.append({'period': candidate.period, 'ratio': harmonic[0],
                                          'error_percent': harmonic[1]})
        row = rows[index]
        row.update(family_id=matched.family_id, family_relation=relation,
                   harmonic_of=(matched.family_id if relation == 'harmonic' else ''),
                   harmonic_ratio=(harmonic[0] if harmonic else np.nan),
                   harmonic_error_percent=(harmonic[1] if harmonic else np.nan),
                   is_family_primary=(relation == 'primary'))
        if relation == 'harmonic':
            flags = set(row['vetting_flags'].split('|')) - {''}
            flags.add('harmonic-duplicate')
            row['vetting_flags'] = '|'.join(sorted(flags))
            row['priority_score'] = priority_score(row, row['rf_probability'], row['vetting_flags'])
    for family in families:
        primary = rows[family.best_candidate]
        aliases = [rows[index]['period_days'] for index in family.detections
                   if rows[index]['family_relation'] == 'alias']
        harmonics = [{'period': rows[index]['period_days'],
                      'ratio': rows[index]['harmonic_ratio'],
                      'error_percent': rows[index]['harmonic_error_percent']}
                     for index in family.detections if rows[index]['family_relation'] == 'harmonic']
        for index in family.detections:
            rows[index].update(primary_period=primary['period_days'],
                               best_candidate=primary['detection_order'],
                               aliases=json.dumps(aliases, separators=(',', ':')),
                               harmonics=json.dumps(harmonics, separators=(',', ':')),
                               n_detections=family.n_detections)
    return families


def read_candidates(path):
    """Una estrella sin candidatos guarda un CSV vacío; leerlo no debe fallar."""
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def signature(root, config, no_model=False, mission='kepler'):
    model = root / 'models/kepler_rf.joblib'
    cnn_model = root / 'models/kepler_exominer_lite.keras'
    cnn_metadata = root / 'models/kepler_exominer_lite_metadata.json'
    catalog = root / 'data/koi_dr25.csv'
    return fingerprint({'config': config.to_dict(), 'feature_version': FEATURE_VERSION, 'pipeline_version': PIPELINE_VERSION,
                        'feature_schema_hash': FEATURE_SCHEMA_HASH,
                        'model': file_hash(model) if model.exists() and not no_model else None,
                        'cnn_model': file_hash(cnn_model) if cnn_model.exists() and not no_model else None,
                        'cnn_metadata': file_hash(cnn_metadata) if cnn_metadata.exists() and not no_model else None,
                        'catalog': file_hash(catalog) if catalog.exists() else None,
                        **({'mission': mission} if mission != 'kepler' else {})})


def scan(root, kic, config, force=False, no_model=False, semaphore=None, mission='kepler'):
    """`kic` es el identificador de la misión (KIC, EPIC o TIC)."""
    root = setup(root)
    info = missions.get(mission)
    mission = info.key
    name = info.label(kic)
    logger = logging.getLogger('kepler')
    started = time.monotonic()
    output = root / 'results' / info.folder(kic)
    output.mkdir(parents=True, exist_ok=True)
    meta_path = output / 'metadata.json'
    artifact = None if no_model else load_model(root)
    cnn_predictor = None
    if not no_model:
        try:
            from .cnn import load_predictor
            cnn_predictor = load_predictor(root)
        except RuntimeError as error:
            logger.warning('CNN no disponible: %s', error)
    sig = signature(root, config, no_model, mission)
    if not info.calibrated and not no_model:
        logger.warning('%s: RF/CNN entrenados con Kepler; probabilidades no calibradas para %s',
                       name, info.lk_mission)
    if meta_path.exists() and not force:
        previous = json.loads(meta_path.read_text(encoding='utf-8'))
        if previous.get('signature') == sig and previous.get('status') == 'complete':
            logger.info('%s: resultado recuperado del checkpoint', name)
            return read_candidates(output / 'candidates.csv')
    if artifact:
        if artifact.get('train_rows', 0) < 200:
            logger.warning('RF entrenado con sólo %d señales: modelo de prueba, sin rendimiento validado',
                           artifact.get('train_rows', 0))
        trained = artifact.get('build_manifest', {}).get('config', {})
        for key in ('flatten_window', 'sigma_clip', 'quarter'):
            if key in trained and trained[key] != config.to_dict()[key]:
                logger.warning('Distribución diferente al entrenamiento: %s (%s frente a %s)',
                               key, config.to_dict()[key], trained[key])
    # Metadata de ejecución incompleta invalida un checkpoint previo antes de escribir figuras.
    atomic_json({'status': 'running', 'signature': sig}, meta_path)
    if semaphore is None:
        raw = download(root, kic, config.quarter, mission=mission)
    else:
        with semaphore:
            raw = download(root, kic, config.quarter, mission=mission)
    curve = preprocess(raw, config)
    plotting.lightcurve(raw, output / 'lightcurve.png', f'{name}: PDCSAP normalizado por segmento')
    plotting.lightcurve(curve, output / 'detrended_lightcurve.png', f'{name}: detrended')
    if config.save_curves:
        save_curve(raw, output / 'original.npz')
        save_curve(curve, output / 'detrended.npz')
    catalog = tce_catalog = None
    if mission == 'kepler':  # KOI/TCE DR25 sólo describen estrellas Kepler.
        catalog_path = root / 'data/koi_dr25.csv'
        catalog = pd.read_csv(catalog_path) if catalog_path.exists() else None
        tce_path = root / 'data/tce_dr25.csv'
        tce_catalog = pd.read_csv(tce_path) if tce_path.exists() else None
    available = np.ones(len(curve.time), dtype=bool)
    found, rows, first_result = [], [], None
    stop_reason = 'max_detections reached'
    for iteration in range(config.max_planets):
        residual = curve.subset(available)
        if len(residual.time) < max(100, 0.2 * len(curve.time)):
            stop_reason = 'insufficient residual data'
            break
        peaks, power = search(residual, config)
        if first_result is None:
            first_result = power
        selected, features, consistency = None, None, None
        for peak in peaks:
            try:
                measured = extract(residual, peak)
                stability = transit_consistency(residual, peak)
            except ValueError as error:
                logger.debug('%s: pico %.6f no medible: %s', name, peak.period, error)
                continue
            selected, features, consistency = peak, measured, stability
            break
        if selected is None:
            stop_reason = 'no measurable independent peaks'
            break
        if features['depth_snr'] < config.min_snr or features['number_of_transits'] < config.min_transits:
            stop_reason = 'strongest remaining peak below SNR/event threshold'
            break
        rf = float(predict(artifact, [features])[0]) if artifact is not None else np.nan
        combined = {**features, **consistency}
        cnn_probability = np.nan
        if cnn_predictor is not None:
            try:
                from .cnn import predict_candidate
                cnn_probability = predict_candidate(cnn_predictor, residual, selected, features)
            except ValueError as error:
                logger.warning('%s: candidato CNN no medible: %s', name, error)
        vetting = vet(combined, config.min_snr, config.min_transits)
        ident = ({'kepid': int(kic)} if mission == 'kepler' else
                 {'mission': mission, 'target': name, 'target_id': int(kic),
                  'model_calibrated': False})
        row = {**ident, 'detection_order': iteration + 1, 'epoch_bkjd': selected.epoch,
               **combined, 'rf_probability': rf, 'rf_available': artifact is not None,
               'cnn_probability': cnn_probability, 'cnn_available': cnn_predictor is not None,
               **vetting,
               'priority_score': priority_score(combined, rf, vetting['vetting_flags']),
               **match(kic, selected.period, catalog, config.match_tolerance, tce_catalog)}
        row['plot_file'] = f'candidate_{iteration + 1:02d}_folded.png'
        plotting.folded(residual, selected, row, output / row['plot_file'], name)
        repeated = any(duplicate(selected, previous, curve.time) for previous in found)
        available, effectiveness, mask_width, removed_fraction = adaptive_mask(
            curve, available, selected, target_effectiveness=0.9 if repeated else 0.8,
            min_width=2.0 if repeated else 1.5,
            max_removed_fraction=0.20 if repeated else 0.15)
        row.update(mask_effectiveness=effectiveness, mask_width_duration=mask_width,
                   masked_fraction=removed_fraction)
        rows.append(row)
        found.append(selected)
        logger.info('%s: candidato P=%.6fd SNR=%.2f RF=%s CNN=%s',
                    name, selected.period, features['depth_snr'], rf, cnn_probability)
    plotting.periodogram(first_result, output / 'bls_periodogram.png')
    families = build_families(rows, found, curve.time) if rows else []
    raw = pd.DataFrame(rows)
    if len(raw):
        raw = raw.sort_values('priority_score', ascending=False)
    if len(raw):
        raw.insert(0, 'raw_rank', np.arange(1, len(raw) + 1))
    atomic_csv(raw, output / 'detections.csv')
    primary_rows = [rows[family.best_candidate] for family in families]
    result = pd.DataFrame(primary_rows)
    if len(result):
        result = result.sort_values('priority_score', ascending=False)
        result.insert(0, 'rank', np.arange(1, len(result) + 1))
        result['raw_detection_count'] = len(rows)
        result['candidate_family_count'] = len(families)
        result['duplicates_removed'] = sum(row['family_relation'] == 'alias' for row in rows)
        result['harmonic_detections'] = sum(row['family_relation'] == 'harmonic' for row in rows)
    atomic_csv(result, output / 'candidates.csv')
    extra = {} if mission == 'kepler' else {
        'mission': mission, 'target': name, 'model_calibrated': False,
        'model_warning': 'RF/CNN entrenados con Kepler DR25: usar sus probabilidades sólo como orientación'}
    atomic_json({'status': 'complete', 'signature': sig, 'kic': int(kic), **extra, 'config': config.to_dict(),
                 'source': curve.metadata, 'feature_version': FEATURE_VERSION,
                 'raw_detections': len(rows), 'candidate_families': len(families),
                 'candidates': len(families),
                 'duplicates_removed': sum(row['family_relation'] == 'alias' for row in rows),
                 'harmonic_detections': sum(row['family_relation'] == 'harmonic' for row in rows),
                 'duration_at_boundary': sum(bool(row['duration_at_boundary']) for row in rows),
                 'stop_reason': stop_reason, 'seconds': time.monotonic() - started,
                 'rf_available': artifact is not None, 'mask_width_duration_range': [1.5, 4.0],
                 'cnn_available': cnn_predictor is not None,
                 'warning': 'Candidatos de tránsito; no confirmación ni descubrimiento planetario'}, meta_path)
    logger.info('%s finalizado: %d detecciones, %d familias, %.1fs',
                name, len(rows), len(families), time.monotonic() - started)
    return result


def _worker(root, kic, config, force, no_model, semaphore, mission='kepler'):
    try:
        result = scan(Path(root), kic, config, force, no_model, semaphore, mission)
        return {'status': 'complete', 'candidates': len(result)}
    except Exception as error:
        label = missions.get(mission).label(kic)
        logging.getLogger('kepler').debug('Batch %s error', label, exc_info=True)
        logging.getLogger('kepler').warning('%s falló: %s', label, error)
        return {'status': 'failed', 'error': str(error)}


def scan_list(root, input_path, config, limit=None, workers=1, force=False, no_model=False, retry_failed=False,
              mission='kepler'):
    info = missions.get(mission)
    mission = info.key
    if not 1 <= workers <= 4:
        raise ValueError('workers debe estar entre 1 y 4; máximo 2 descargas simultáneas')
    if limit is not None and limit < 1:
        raise ValueError('limit debe ser positivo')
    frame = pd.read_csv(input_path)
    column = next((c for c in info.id_columns if c in frame), None)
    if column is None:
        raise ValueError(f'CSV de entrada debe incluir una columna: {", ".join(info.id_columns)}')
    kics = list(dict.fromkeys(frame[column].dropna().astype(int)))[:limit]
    if not no_model:
        load_model(root)  # Error global de configuración, antes de lanzar estrellas.
    suffix = '' if mission == 'kepler' else f'_{mission}'
    checkpoint = root / f'results/batch_progress{suffix}.json'
    progress = json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else {}
    sig = signature(root, config, no_model, mission)
    pending = []
    for kic in kics:
        entry = progress.get(str(kic), {})
        has_result = (root / 'results' / info.folder(kic) / 'candidates.csv').exists()
        valid = entry.get('signature') == sig
        if force or not valid or (entry.get('status') == 'complete' and not has_result) or (
            entry.get('status') == 'failed' and retry_failed) or entry.get('status') not in ('complete', 'failed'):
            pending.append(kic)
    logging.getLogger('kepler').info('Batch: %d pendientes / %d objetivos', len(pending), len(kics))

    def checkpoint_result(kic, outcome):
        progress[str(kic)] = {**outcome, 'signature': sig}
        atomic_json(progress, checkpoint)

    if workers == 1:
        for kic in pending:
            checkpoint_result(kic, _worker(str(root), kic, config, force, no_model, None, mission))
    elif pending:
        with multiprocessing.Manager() as manager:
            semaphore = manager.BoundedSemaphore(2)
            with ProcessPoolExecutor(max_workers=workers) as pool:
                jobs = {pool.submit(_worker, str(root), kic, config, force, no_model, semaphore, mission): kic
                        for kic in pending}
                for future in as_completed(jobs):
                    kic = jobs[future]
                    try:
                        outcome = future.result()
                    except Exception as error:
                        outcome = {'status': 'failed', 'error': str(error)}
                    checkpoint_result(kic, outcome)
    tables = []
    for kic in kics:
        entry = progress.get(str(kic), {})
        if entry.get('signature') == sig and entry.get('status') == 'complete':
            table = read_candidates(root / 'results' / info.folder(kic) / 'candidates.csv')
            tables.append(table)
    id_column = 'kepid' if mission == 'kepler' else 'target'
    summary = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame(columns=[id_column, 'priority_score'])
    summary = summary.sort_values('priority_score', ascending=False)
    atomic_csv(summary, root / f'results/batch_summary{suffix}.csv')
    return progress
