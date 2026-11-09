"""Queue wrapper around unchanged scanner and frozen evaluation functions."""
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import traceback
from local_panel.jobs import alive, read, write


def watch_parent(pid):
    """Do not leave an unmanaged scan running if the local server crashes."""
    while alive(pid):
        time.sleep(2)
    os._exit(115)  # Incomplete outputs retained; next launch requires explicit resume.


def archive(path, folder):
    if path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / path.name
        if destination.exists():
            raise ValueError('Archivo de intento ya existe; no se sobrescribe')
        shutil.move(str(path), str(destination))


def claim_evaluation(out, jobdir):
    lock = out / 'evaluation.lock'
    if lock.exists():
        pid = int(lock.read_text().strip())
        if alive(pid):
            raise ValueError('La evaluación ya tiene un proceso activo')
        archive(lock, jobdir / 'history' / str(time.time_ns()))
    with lock.open('x', encoding='utf-8') as f:
        f.write(str(os.getpid()))
    return lock


def prepare_scan(root, work, ids):
    for name in ('data', 'models', 'cache/lightcurves', 'results'):
        (work / name).mkdir(parents=True, exist_ok=True)
    for name in ('models/kepler_rf.joblib',
                 'models/kepler_exominer_lite.keras',
                 'models/kepler_exominer_lite_metadata.json',
                 'data/koi_dr25.csv', 'data/tce_dr25.csv'):
        source = root / name
        if source.exists() and not (work / name).exists():
            shutil.copy2(source, work / name)
    for kic in ids:
        name = f'cache/lightcurves/KIC_{kic:08d}.npz'
        if (root / name).exists() and not (work / name).exists():
            shutil.copy2(root / name, work / name)


def finish_blind(root, out, state, runtime):
    import pandas as pd
    import evaluate_blind as evaluation
    from kepler_detector.blind_metrics import summarize
    evaluation.verify_frozen(root, out)
    states = {k: evaluation.existing_attempt(out, k) for k in state['ids']}
    koi, stars = evaluation.result_tables(out, pd.read_csv(out / 'cohort_koi.csv'), states)
    summary = summarize(koi, stars, runtime)
    control_state = evaluation.existing_attempt(out, 10854555)
    ck, _ = evaluation.result_tables_control(out, pd.read_csv(out / 'control_reference.csv'), control_state)
    first = ck.iloc[0]
    summary.update(control=dict(status='PASS' if first.recovered_fundamental else 'FAIL',
        detected_period=float(first.detected_period) if pd.notna(first.detected_period) else None,
        rank=int(first['rank']) if pd.notna(first['rank']) else None), frozen_inputs_unchanged=True,
        execution_note='User-requested continuation; interrupted attempts archived. No detector tuning.',
        runtime_note='Sum of completed official task times; pauses excluded (sequential continuation).')
    evaluation.charts(out, koi, stars, summary)
    write(out / 'summary.json', summary)
    (out / 'summary.txt').write_text(evaluation.detailed_summary(summary), encoding='utf-8')
    print(evaluation.console_summary(summary), flush=True)


def run(root, job):
    root = Path(root)
    from kepler_detector.evidence import ensure_one_time_backfill
    ensure_one_time_backfill(root)
    if job == 'blind' and (root / 'results/blind_evaluation/evaluation_manifest.json').exists():
        from blind_resume import run as resume_frozen
        return resume_frozen(root, job)
    folder = root / 'results/local_panel' / job
    state = read(folder / 'state.json')
    state.update(pid=os.getpid(), status='running', current=None)
    write(folder / 'state.json', state)
    lock = None
    try:
        blind = state['kind'] == 'blind'
        if blind:
            import evaluate_blind as evaluation
            out = root / 'results/blind_evaluation'
            config = evaluation.verify_frozen(root, out)['config']
            lock = claim_evaluation(out, folder)
            work = out / 'blind_workspace'
            ids = state['ids'] + [state['control']]
            # Read authoritative completed attempts before every continuation.
            for kic in ids:
                existing = evaluation.existing_attempt(out, kic)
                state['stars'][str(kic)] = existing['status'] if existing else 'pending'
        else:
            ids = state['ids']
            work = folder / 'workspace'
            prepare_scan(root, work, ids)
        for kic in ids:
            if state['stars'][str(kic)] in ('complete', 'failed'):
                continue
            command = read(folder / 'command.json', {}).get('action')
            if command in ('pause', 'stop'):
                state.update(status='paused' if command == 'pause' else 'stopped', current=None)
                break
            archive_dir = folder / 'history' / f'{kic}-{time.time_ns()}'
            if state['stars'][str(kic)] in ('interrupted', 'running'):
                archive(work / f'results/KIC_{kic}', archive_dir)
                if blind:
                    archive(out / f'attempts/{kic}.json', archive_dir)
                write(archive_dir / 'resume.json', dict(kepid=kic, reason='Explicit user continuation', time=time.time()))
            state['stars'][str(kic)] = 'running'
            state.update(current=kic, status='running')
            write(folder / 'state.json', state)
            print(f'INICIO KIC {kic}', flush=True)
            if blind:
                result = evaluation.scan_worker(str(work), kic, config)
                status = result['status']
            else:
                from config import Config
                from kepler_detector.scanner import scan
                no_model = not (work / 'models/kepler_rf.joblib').exists()
                if no_model and not state.get('no_model_warned'):
                    print('AVISO: no hay modelo RF entrenado (models/kepler_rf.joblib). Se buscan señales '
                          'con BLS y vetting, sin probabilidades RF/CNN ni marca de "prioritario".', flush=True)
                    state['no_model_warned'] = True
                try:
                    scan(work, kic, Config(), no_model=no_model)
                    status = 'complete'
                except Exception:
                    traceback.print_exc()
                    status = 'failed'
            state['stars'][str(kic)] = status
            state.update(current=None)
            write(folder / 'state.json', state)
            if status == 'complete':
                try:
                    from kepler_detector.evidence import record_serious_evidence
                    record_serious_evidence(root, job, kic, work / f'results/KIC_{kic}')
                except Exception as error:
                    # The scientific result remains valid if the convenience report cannot be written.
                    print(f'AVISO informe de evidencias KIC {kic}: {error}', flush=True)
            print(f'FIN KIC {kic}: {status}', flush=True)
        else:
            if blind:
                seconds = sum((evaluation.existing_attempt(out, k) or {}).get('seconds', 0) for k in state['ids'])
                finish_blind(root, out, state, seconds)
            state.update(status='completed', current=None)
    except Exception:
        state.update(status='error', error=traceback.format_exc())
        traceback.print_exc()
    finally:
        state['pid'] = None
        write(folder / 'state.json', state)
        if lock and lock.exists() and lock.read_text().strip() == str(os.getpid()):
            lock.unlink()


if __name__ == '__main__':
    if len(sys.argv) > 3:
        threading.Thread(target=watch_parent, args=(int(sys.argv[3]),), daemon=True).start()
    run(sys.argv[1], sys.argv[2])
