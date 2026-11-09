"""Entrenamiento RF repartido entre los equipos de la red.

Los ayudantes calculan las variables de los KOI de cada estrella (lo lento: descargar
y medir). El coordinador reproduce después la selección de `build` en el mismo orden de
estrellas y con el mismo tope por clase, escribe exactamente los mismos archivos que
`kepler_rf_detector.py build` y entrena/evalúa aquí con `train` y `evaluate`."""
import json
import math
import subprocess
import sys
import threading
import time

from pathlib import Path

import pandas as pd

CLI = Path(__file__).resolve().parent.parent / 'kepler_rf_detector.py'

from local_panel.jobs import read, write

MARGIN = 1.15          # Se reparte un 15 % de más para cubrir fallos sin otra ronda.
MAX_SIGNALS = 64       # Límite defensivo de KOI por estrella en una tarea.


def _config():
    from config import Config
    return Config()


def _plan(root, per_class, counts, start, star_ids, chosen):
    """Siguientes estrellas en orden hasta cubrir lo que falta por clase (con margen)."""
    target = {label: math.ceil(max(0, per_class - counts[label]) * MARGIN) for label in (0, 1)}
    projected = {0: 0, 1: 0}
    signals, ids, index = {}, [], start
    while index < len(star_ids) and any(projected[label] < target[label] for label in (0, 1)):
        kic = int(star_ids[index])
        index += 1
        rows = [row for row in chosen[chosen.kepid == kic].itertuples()
                if projected[int(row.label)] < target[int(row.label)]][:MAX_SIGNALS]
        if not rows:
            continue
        for row in rows:
            projected[int(row.label)] += 1
        ids.append(kic)
        signals[str(kic)] = [[row.kepoi_name, float(row.koi_period), int(row.label)] for row in rows]
    return ids, signals, index


def create_job(manager, per_class):
    if not isinstance(per_class, int) or not 2 <= per_class <= 5000:
        raise ValueError('Ejemplos por clase: entero entre 20 y 5000.')
    from kepler_detector.builder import selection
    from kepler_detector.catalog import get_catalog
    root = manager.root
    catalog = get_catalog(root)
    config = _config()
    chosen, star_ids = selection(catalog, config)
    ids, signals, index = _plan(root, per_class, {0: 0, 1: 0}, 0, star_ids, chosen)
    if not ids:
        raise ValueError('El catálogo KOI no tiene señales etiquetadas.')
    import secrets
    job = secrets.token_hex(8)
    write(manager.folder / job / 'state.json', dict(
        id=job, kind='cluster', task='build', mission='kepler',
        name=f'Entrenamiento RF repartido · {per_class} por clase',
        ids=ids, stars={str(k): 'pending' for k in ids}, status='running', current=None,
        pid=None, error=None, created=time.time(),
        build=dict(per_class=per_class, signals=signals, next_star=index, rounds=1)))
    return job


def task_for(state, kic):
    return dict(task='build', signals=state['build']['signals'][str(kic)], config=_config().to_dict())


def accept(manager, state, kic, results):
    """Valida y guarda lo que devuelve un ayudante para una estrella."""
    from kepler_detector.schema import RF_FEATURE_COLUMNS
    signals = state['build']['signals'][str(kic)]
    if not isinstance(results, list) or len(results) != len(signals):
        raise ValueError('Resultado de entrenamiento incompleto')
    clean = []
    for (name, _, label), item in zip(signals, results):
        if not isinstance(item, dict) or item.get('status') not in ('complete', 'failed'):
            raise ValueError('Resultado de entrenamiento inválido')
        if item['status'] == 'failed':
            clean.append({'status': 'failed', 'kepoi_name': name, 'error': str(item.get('error', ''))[:500]})
            continue
        record = item.get('record')
        if not isinstance(record, dict) or record.get('kepoi_name') != name or \
                record.get('kepid') != int(kic) or record.get('label') != label:
            raise ValueError('El resultado no corresponde a los KOI pedidos')
        for column in RF_FEATURE_COLUMNS:
            value = record.get(column)
            if value is not None and not isinstance(value, (int, float)):
                raise ValueError(f'Variable no numérica: {column}')
        # NaN/inf son normales (p. ej. períodos largos con pocos tránsitos): se guardan como vacío,
        # igual que en training_features.csv, y el RF los imputa.
        record = {key: (None if isinstance(value, float) and not math.isfinite(value) else value)
                  for key, value in record.items()}
        clean.append({'status': 'complete', 'record': record})
    write(manager.folder / state['id'] / 'build_results' / f'{int(kic)}.json', clean)


def _replay(manager, state):
    """Misma selección que `build`: orden de estrellas y tope por clase."""
    per_class = state['build']['per_class']
    folder = manager.folder / state['id'] / 'build_results'
    rows, progress, counts = [], {}, {0: 0, 1: 0}
    for kic in state['ids']:
        signals = state['build']['signals'][str(kic)]
        if state['stars'][str(kic)] == 'failed':
            for name, _, _ in signals:
                progress[name] = {'status': 'failed', 'kepid': int(kic), 'error': 'Descarga o preprocesado fallido'}
            continue
        results = read(folder / f'{int(kic)}.json', [])
        for (name, _, label), item in zip(signals, results):
            if counts[label] >= per_class:
                continue
            if item['status'] == 'complete':
                rows.append(item['record'])
                counts[label] += 1
                progress[name] = {'status': 'complete', 'kepid': int(kic)}
            else:
                progress[name] = {'status': 'failed', 'kepid': int(kic), 'error': item['error']}
    return rows, progress, counts


def round_done(cluster, state):
    """Llamado (con el cerrojo) cuando no quedan estrellas pendientes en la ronda."""
    manager = cluster.manager
    rows, progress, counts = _replay(manager, state)
    per_class = state['build']['per_class']
    if min(counts.values()) < per_class and state['build']['rounds'] < 5:
        from kepler_detector.builder import selection
        from kepler_detector.catalog import get_catalog
        chosen, star_ids = selection(get_catalog(manager.root), _config())
        ids, signals, index = _plan(manager.root, per_class, counts, state['build']['next_star'], star_ids, chosen)
        if ids:
            state['ids'] += ids
            state['stars'].update({str(k): 'pending' for k in ids})
            state['build']['signals'].update(signals)
            state['build']['next_star'] = index
            state['build']['rounds'] += 1
            cluster._log(state['id'], f'Faltan ejemplos {counts}: se reparten {len(ids)} estrellas más')
            return False
    _write_dataset(manager.root, rows, progress)
    state.update(status='training', current=None,
                 note=f'Conjunto listo: {counts[0]} falsos positivos y {counts[1]} candidatos. Entrenando…')
    cluster._log(state['id'], state['note'])
    threading.Thread(target=_train, args=(manager, state['id']), daemon=True).start()
    return True


def _write_dataset(root, rows, progress):
    from kepler_detector.schema import RF_FEATURE_COLUMNS, schema_metadata, timestamp
    from kepler_detector.utils import atomic_csv, atomic_json, file_hash
    config = _config()
    frame = pd.DataFrame(rows) if rows else pd.DataFrame(columns=['kepid', 'kepoi_name', 'label', *RF_FEATURE_COLUMNS])
    atomic_csv(frame, root / 'data/training_features.csv')
    atomic_json(progress, root / 'data/build_progress.json')
    manifest = {'config': config.to_dict(), **schema_metadata(),
                'catalog_sha256': file_hash(root / 'data/koi_dr25.csv'), 'label_column': 'koi_pdisposition'}
    manifest = json.loads(json.dumps(manifest))
    manifest.update(created_at=timestamp(), row_count=len(frame), distributed=True)
    atomic_json(manifest, root / 'data/build_manifest.json')


def _train(manager, job):
    """Entrena y evalúa en subprocesos (no bloquea el servidor); registro en el trabajo."""
    log_path = manager.folder / job / 'worker.log'
    status, error = 'completed', None
    for command in ('train', 'evaluate'):
        with log_path.open('ab') as log:
            log.write(f'{time.strftime("%H:%M:%S")} {command} …\n'.encode())
            log.flush()
            code = subprocess.run([sys.executable, '-u', str(CLI), command, '--root', str(manager.root)],
                                  cwd=str(CLI.parent), stdout=log, stderr=subprocess.STDOUT,
                                  creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0).returncode
        if code:
            status, error = 'error', f'{command} terminó con código {code}; mira el registro'
            break
    with manager.lock:
        path = manager.folder / job / 'state.json'
        state = read(path)
        note = ('Modelo entrenado: models/kepler_rf.joblib. Los ayudantes lo descargarán solos; '
                'los próximos análisis ya tendrán probabilidades RF.') if status == 'completed' else None
        state.update(status=status, error=error, note=note or state.get('note'))
        write(path, state)
        with log_path.open('a', encoding='utf-8') as log:
            log.write(time.strftime('%H:%M:%S ') + (note or error) + '\n')
