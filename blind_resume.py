"""Operational continuation only. Frozen detector and matching are imported unchanged."""
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import time
import traceback
import pandas as pd
import evaluate_blind as ev
from local_panel.jobs import read, write
from local_panel.worker import archive, claim_evaluation
from kepler_detector.utils import file_hash

INTERRUPTED = [5812960, 6043562, 6070337, 6263593]
ISSUE = 'KNOWN ISSUE PIPELINE 2.0: possible resonance/harmonic family collision (KIC 5219234). Not corrected in this evaluation.'


def inventory(out, ids):
    paths = []
    for k in ids:
        paths += list((out / f'blind_workspace/results/KIC_{k}').rglob('*'))
        paths.append(out / f'attempts/{k}.json')
    return {str(p.relative_to(out)): file_hash(p) for p in paths if p.is_file()}


def prepare(root):
    root = Path(root).resolve()
    out = root / 'results/blind_evaluation'
    frozen = ev.verify_frozen(root, out)
    cohort, _ = ev.select_cohort(root)
    saved = pd.read_csv(out / 'cohort_koi.csv')
    pd.testing.assert_frame_equal(cohort, saved, check_dtype=False)
    ids = list(map(int, saved.kepid.unique()))
    states = {k: ev.existing_attempt(out, k) for k in ids}
    paused = read(out / 'pause_state.json')
    completed = paused['completed']
    interrupted = paused['interrupted']
    pending = [k for k in ids if k not in completed + interrupted]
    if len(completed) != 22 or interrupted != INTERRUPTED or len(pending) != 66:
        raise ValueError('Recorded 22/4/66 state differs; audit before continuation')
    if any(not states[k] or states[k]['status'] != 'complete' for k in completed):
        raise ValueError('Previously completed star is no longer complete')
    manifest_path = out / 'evaluation_manifest.json'
    if manifest_path.exists():
        raise ValueError('Continuation manifest already exists; do not overwrite')
    manifest = dict(PIPELINE_VERSION=frozen['pipeline_version'], FEATURE_VERSION=frozen['feature_version'],
        FEATURE_SCHEMA_HASH=frozen['feature_schema_hash'], created_at=ev.utc_now(),
        kepids=ids, koi_names=list(saved.kepoi_name), official_stars=92, official_koi=123,
        model_used='blind_workspace/models/kepler_rf.joblib', model_sha256=frozen['model_sha256'],
        scan_parameters=frozen['config'], matching_criterion=ev.PROTOCOL, rf_threshold=.5,
        frozen_manifest_sha256=file_hash(out / 'frozen_manifest.json'),
        scientific_source_hashes=frozen['source_hashes'], cohort_sha256=frozen['cohort_sha256'],
        completed_before_resume=completed, preserved_output_hashes=inventory(out, completed),
        operational_retries=INTERRUPTED, pending_at_resume=pending, workers=4,
        observed_attempt_status_at_manifest={str(k):s['status'] if s else 'pending' for k,s in states.items()},
        prior_retry_archive='results/local_panel/blind/history (panel continuation began before this manifest)',
        retry_reason='External operational interruption. Not performance-based. Exactly the same pipeline, model and configuration.',
        known_issues=[ISSUE], report_status='prepared',
        transit_count_method='Count unique nearest KOI event indices with >=1 finite cached timestamp within max(KOI duration/2, 1765.5/86400/2) days. Before preprocessing; coverage proxy, not independently detected transits.',
        bin_boundaries='Period: <3, [3,10), [10,30), [30,100), [100,300], >300 days. Depth: <200, [200,500), [500,1000), [1000,3000], >3000 ppm. Transit counts: <3, 3-4, 5-10, 11-30, 31-100, >100; missing shown separately.')
    write(manifest_path, manifest)
    (out / 'KNOWN_ISSUES_PIPELINE_2_0.txt').write_text(ISSUE + '\nNo detector changes or tuning authorized in this run.\n', encoding='utf-8')
    print('PREPARED: 22 preserved; 4 operational retries; 66 never started; model/source hashes verified.')


def verify_preserved(out):
    manifest = read(out / 'evaluation_manifest.json')
    for relative, expected in manifest['preserved_output_hashes'].items():
        if file_hash(out / relative) != expected:
            raise ValueError('Completed output changed: ' + relative)


def run(root, job='blind'):
    root = Path(root).resolve()
    out = root / 'results/blind_evaluation'
    folder = root / 'results/local_panel' / job
    state = read(folder / 'state.json')
    lock = None
    start = time.monotonic()
    session = dict(started_at=ev.utc_now(), pid=os.getpid(), completed=[], status='running')
    session_file = out / 'resume_sessions' / f'{time.time_ns()}.json'
    def save():
        state['pid'] = os.getpid()
        state['current'] = next(iter(state.get('active_kics', [])), None)
        write(folder / 'state.json', state)
        write(session_file, session)
    try:
        frozen = ev.verify_frozen(root, out)
        verify_preserved(out)
        manifest = read(out / 'evaluation_manifest.json')
        if state['ids'] != manifest['kepids']:
            raise ValueError('Cohort mismatch')
        lock = claim_evaluation(out, folder)
        state.update(status='running', error=None, active_kics=[])
        save()
        for group in (INTERRUPTED, [k for k in state['ids'] if k not in INTERRUPTED], [10854555]):
            queue = []
            for kic in group:
                existing = ev.existing_attempt(out, kic)
                if existing and existing['status'] in ('complete','failed'):
                    state['stars'][str(kic)] = existing['status']
                    continue
                if existing:
                    if kic not in INTERRUPTED:
                        raise ValueError(f'Unapproved interrupted retry: {kic}')
                    history = out / 'operational_retry_archive' / f'{kic}-{time.time_ns()}'
                    archive(out / f'blind_workspace/results/KIC_{kic}', history)
                    archive(out / f'attempts/{kic}.json', history)
                    write(history / 'reason.json', dict(kepid=kic, reason=manifest['retry_reason'],
                          model_sha256=frozen['model_sha256'], scan_parameters=frozen['config'], time=ev.utc_now()))
                queue.append(kic)
            with ProcessPoolExecutor(max_workers=4) as pool:
                active = {}
                while queue or active:
                    command = read(folder / 'command.json', {}).get('action')
                    while queue and len(active) < 4 and command not in ('pause','stop'):
                        kic = queue.pop(0)
                        active[pool.submit(ev.scan_worker, str(out / 'blind_workspace'), kic, frozen['config'])] = kic
                        state['stars'][str(kic)] = 'running'
                        print(f'INICIO KIC {kic}', flush=True)
                    state['active_kics'] = list(active.values())
                    save()
                    if not active:
                        if queue:
                            state['status'] = 'paused'
                            return
                        break
                    finished, _ = wait(active, timeout=3, return_when=FIRST_COMPLETED)
                    for future in finished:
                        kic = active.pop(future)
                        result = future.result()
                        state['stars'][str(kic)] = result['status']
                        session['completed'].append(kic)
                        print(f"FIN KIC {kic}: {result['status']} ({result['seconds']:.1f}s)", flush=True)
                state['active_kics'] = []
                save()
            if group != [10854555]:
                session['official_wall_seconds'] = time.monotonic() - start
                save()
        ev.verify_frozen(root, out)
        verify_preserved(out)
        from blind_report import generate
        session.update(status='complete', seconds=time.monotonic()-start, ended_at=ev.utc_now())
        save()
        generate(root)
        state.update(status='completed', active_kics=[])
    except Exception:
        state.update(status='error', error=traceback.format_exc())
        session['error'] = state['error']
        traceback.print_exc()
    finally:
        session.update(status=state['status'], seconds=time.monotonic()-start, ended_at=ev.utc_now())
        write(session_file, session)
        state.update(pid=None, current=None)
        write(folder / 'state.json', state)
        if lock and lock.exists() and lock.read_text().strip() == str(os.getpid()):
            lock.unlink()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    (prepare if args.prepare else run)(Path(__file__).resolve().parent)
