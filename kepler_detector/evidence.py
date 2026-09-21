"""Persistent, human-readable evidence report for strict follow-up candidates."""
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import secrets
import time

from .triage import assess_candidate
from .utils import atomic_json


REPORT_NAME = 'CANDIDATOS_SERIOS_KEPLER.txt'
LEDGER_NAME = 'serious_evidence.json'


def _number(row, name, default=None):
    try:
        value = float(row.get(name, ''))
    except (TypeError, ValueError):
        return default
    return value if value == value else default


def _truth(value):
    return str(value).strip().lower() in {'1', 'true', 'yes', 'si', 'sí'}


def desktop_folder():
    """Resolve the Windows Desktop, while allowing an isolated test override."""
    override = os.environ.get('KEPLER_EVIDENCE_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if os.name == 'nt':
        try:
            import winreg
            key_name = r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name) as key:
                value, _ = winreg.QueryValueEx(key, 'Desktop')
            return Path(os.path.expandvars(value)).resolve()
        except OSError:
            pass
    return (Path.home() / 'Desktop').resolve()


def _cnn_fallback(root, kic, period):
    """Use a saved CNN evaluation only when its KOI view matches this period."""
    index_path = root / 'data/cnn_view_index.csv'
    prediction_path = root / 'results/cnn_test_predictions.csv'
    if not index_path.exists() or not prediction_path.exists() or not period:
        return None
    predictions = {}
    with prediction_path.open(encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream):
            if int(float(row.get('kepid', 0))) == int(kic):
                predictions[row.get('kepoi_name')] = _number(row, 'cnn_probability')
    matches = []
    with index_path.open(encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream):
            if int(float(row.get('kepid', 0))) != int(kic):
                continue
            known_period = _number(row, 'candidate_period')
            probability = predictions.get(row.get('kepoi_name'))
            if known_period and probability is not None:
                error = abs(known_period - period) / period
                if error <= .01:
                    matches.append((error, probability, row.get('kepoi_name')))
    return min(matches) if matches else None


def _known_object(root, kic, period):
    path = root / 'data/koi_dr25.csv'
    if not path.exists() or not period:
        return None
    matches = []
    with path.open(encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream):
            if int(float(row.get('kepid', 0))) != int(kic):
                continue
            known_period = _number(row, 'koi_period')
            if known_period:
                error = abs(known_period - period) / period
                if error <= .01:
                    matches.append((error, row))
    if not matches:
        return None
    row = min(matches, key=lambda item: item[0])[1]
    return {
        'name': row.get('kepoi_name') or 'KOI sin nombre',
        'disposition': row.get('koi_disposition') or row.get('koi_pdisposition') or 'desconocida',
    }


def _entry(root, job, kic, row):
    enriched = dict(row)
    period = _number(enriched, 'period_days')
    cnn_source = 'medida sobre la detección'
    cnn = _number(enriched, 'cnn_probability')
    if cnn is None:
        fallback = _cnn_fallback(root, kic, period)
        if fallback:
            _, cnn, koi_name = fallback
            enriched['cnn_probability'] = cnn
            cnn_source = f'evaluación CNN guardada para {koi_name}'
    assessment = assess_candidate(enriched)
    if not assessment['triage_serious']:
        return None
    flags = str(enriched.get('vetting_flags') or '').strip()
    known = _known_object(root, kic, period)
    return {
        'job': str(job),
        'kic': int(kic),
        'rank': int(_number(enriched, 'rank', 0)),
        'period_days': period,
        'rf_probability': _number(enriched, 'rf_probability'),
        'cnn_probability': cnn,
        'cnn_source': cnn_source,
        'priority_score': _number(enriched, 'priority_score'),
        'snr': _number(enriched, 'depth_snr'),
        'transits': int(_number(enriched, 'number_of_transits', 0)),
        'consistency': _number(enriched, 'transit_depth_consistency'),
        'depth_ppm': _number(enriched, 'depth_ppm'),
        'duration_hours': _number(enriched, 'duration_hours'),
        'classification': enriched.get('classification') or 'sin clasificación',
        'vetting_flags': flags or 'ninguna',
        'known_object': known,
        'reason': assessment['triage_reason'],
        'recorded_at': datetime.now().astimezone().isoformat(timespec='seconds'),
    }


def _format(value, digits=3):
    return 'no disponible' if value is None else f'{value:.{digits}f}'


def _render(entries):
    entries = sorted(entries, key=lambda x: (-x['priority_score'], x['kic'], x['rank']))
    unknown = [item for item in entries if not item.get('known_object')]
    known = [item for item in entries if item.get('known_object')]
    lines = [
        'CANDIDATOS SERIOS — KEPLER TRANSIT-LIKE PIPELINE',
        '=' * 52,
        f'Actualizado: {datetime.now().astimezone().isoformat(timespec="seconds")}',
        f'Candidatos que superan el filtro estricto: {len(entries)}',
        f'Sin coincidencia KOI conocida: {len(unknown)}',
        f'Objetos conocidos usados como referencia: {len(known)}',
        '',
        'IMPORTANTE: este informe prioriza señales para seguimiento. No confirma',
        'planetas ni demuestra que una señal sea nueva.',
        '',
    ]
    sections = [('SEÑALES SIN COINCIDENCIA KOI — REVISAR PRIMERO', unknown),
                ('OBJETOS KOI CONOCIDOS — CONTROL Y REFERENCIA', known)]
    sequence = 0
    for title, group in sections:
        lines.extend([title, '=' * len(title), ''])
        if not group:
            lines.extend(['Ninguna.', ''])
            continue
        for item in group:
            sequence += 1
            catalog = item.get('known_object')
            lines.extend([
                f'{sequence}. KIC {item["kic"]} · rango {item["rank"]}',
                '-' * 52,
                f'Periodo: {_format(item["period_days"], 6)} días',
                f'RF: {_format(item["rf_probability"])}',
                f'CNN: {_format(item["cnn_probability"])} ({item["cnn_source"]})',
                f'Prioridad: {_format(item["priority_score"])}',
                f'SNR: {_format(item["snr"], 2)}',
                f'Tránsitos observados: {item["transits"]}',
                f'Consistencia de profundidad: {_format(item["consistency"])}',
                f'Profundidad: {_format(item["depth_ppm"], 1)} ppm',
                f'Duración: {_format(item["duration_hours"], 2)} horas',
                f'Clasificación: {item["classification"]}',
                f'Alertas de vetting: {item["vetting_flags"]}',
                f'Evidencia: {item["reason"]}.',
                (f'Catálogo conocido: {catalog["name"]} · {catalog["disposition"]}.'
                 if catalog else 'Catálogo conocido: sin coincidencia KOI dentro del 1 % de periodo.'),
                f'Origen interno: trabajo {item["job"]}',
                '',
            ])
    return '\n'.join(lines)


def _atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    for attempt in range(20):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.05)


def record_serious_evidence(root, job, kic, result_folder, report_folder=None):
    """Replace one star's ledger entries and regenerate the desktop TXT."""
    root = Path(root).resolve()
    result_folder = Path(result_folder)
    candidate_path = result_folder / 'candidates.csv'
    if not candidate_path.exists():
        return None
    ledger_path = root / 'results/local_panel' / LEDGER_NAME
    try:
        ledger = json.loads(ledger_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        ledger = {'version': 1, 'candidates': {}}
    prefix = f'{job}:{int(kic)}:'
    candidates = {key: value for key, value in ledger.get('candidates', {}).items()
                  if not key.startswith(prefix)}
    with candidate_path.open(encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream):
            item = _entry(root, job, kic, row)
            if item:
                candidates[f'{prefix}{item["rank"]}'] = item
    ledger = {'version': 1, 'candidates': candidates}
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(ledger, ledger_path)
    destination = Path(report_folder) if report_folder else desktop_folder()
    report = destination / REPORT_NAME
    _atomic_text(report, _render(list(candidates.values())))
    return report


def rebuild_serious_evidence(root, report_folder=None):
    """Backfill the report from every completed local-panel result."""
    root = Path(root).resolve()
    jobs = root / 'results/local_panel'
    report = None
    for state_path in sorted(jobs.glob('*/state.json')):
        if state_path.parent.name == 'cnn':
            continue
        try:
            state = json.loads(state_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        job = state_path.parent.name
        base = (root / 'results/blind_evaluation/blind_workspace' if job == 'blind'
                else state_path.parent / 'workspace')
        for kic, status in state.get('stars', {}).items():
            if status == 'complete':
                report = record_serious_evidence(
                    root, job, int(kic), base / f'results/KIC_{int(kic)}', report_folder)
    return report


def ensure_one_time_backfill(root):
    """Backfill pre-feature jobs once; new completions are recorded incrementally."""
    root = Path(root).resolve()
    marker = root / 'results/local_panel/evidence_backfill.json'
    if marker.exists():
        return
    report = rebuild_serious_evidence(root)
    atomic_json({'version': 1, 'completed_at': datetime.now().astimezone().isoformat(),
                 'report': str(report) if report else None}, marker)
