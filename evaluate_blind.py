"""Frozen blind evaluation. No changes to the detector or its parameters.

Run: .venv/Scripts/python.exe evaluate_blind.py --workers 4
An existing run is resumed only for never-started stars; finished/failed
attempts are never overwritten or silently retried.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import time
import traceback

import numpy as np
import pandas as pd
from config import Config
from kepler_detector.blind_metrics import evaluate_star, summarize
from kepler_detector.ml import load_model
from kepler_detector.schema import validate_dataset, schema_metadata
from kepler_detector.utils import atomic_csv, atomic_json, file_hash

PROTOCOL = """BLIND END-TO-END EVALUATION — frozen first run

Cohort: current model TEST KepIDs intersected with training rows label=1 and
catalog koi_pdisposition=CANDIDATE. Exactly 92 stars / 123 unique KOIs.
All CANDIDATE catalog KOIs on those stars are present. No train stars.
KIC 10854555 is excluded and evaluated separately after the official cohort.

Blindness: workers receive only KepID, a catalog-free isolated root and the
unchanged Config(). That root contains the current model and cached curves,
but NO KOI/TCE catalog and NO training CSV. Normal scanner crossmatch therefore
has no ephemerides available. Evaluation truth is outside the worker root.
No detection, family, RF score, ranking or threshold is changed.
Normal default max_planets=5 is retained: top 10 cannot exceed top 5 here.

All periods/epochs are days BKJD (TDB); catalog koi_duration is hours.
Fundamental period error = 100*abs(Pdet-Pkoi)/Pkoi <= 1.0 percent.
Epoch window W = max(1.5*koi_duration/24, 2*1765.5/86400) days.
The minimum W is 0.0408680555556 days (58.85 min, two long cadences).
Cadence source: https://archive.stsci.edu/kepler/manuals/Data_Characteristics.pdf
If KOI duration is missing/nonpositive, only the minimum window is used.
Fundamental epoch error = abs(((Edet-Ekoi+Pkoi/2) % Pkoi)-Pkoi/2) <= W.
Missing period/epoch is never counted as a match. All 123 have ephemerides.
No epoch fitting/translation or detector tuning with the known ephemeris.
This is an anchor-epoch match, not a full-baseline ephemeris/overlap validation;
period drift/TTVs can cause misses, and phase agreement does not validate a signal.

Harmonic ratios r = 1/8,1/7,1/6,1/5,1/4,1/3,1/2,2,3,4,5,6,7,8.
Harmonic period error = 100*abs(Pdet-r*Pkoi)/(r*Pkoi) <= 1.0 percent.
Epoch error uses the same circular formula with modulus Pkoi*min(1,r),
and must be <= W. This aligns one subset of events for integer harmonics.
Harmonic matches never count as fundamental recoveries.
period_error_percent always reports the error relative to Pkoi;
harmonic_period_error_percent reports error relative to the nominal harmonic.

One-to-one assignment, independently at raw-detection and family stages:
deterministic greedy order fundamental first, then harmonic-adjusted period
error, normalized epoch error, KOI name, detection_order. Each resource can
serve one KOI only. A KOI with an eligible fundamental cannot be relabeled as
harmonic when it loses an assignment conflict. All eligible edges and conflicts
are saved, including harmonic ambiguities; none are counted twice.
Family matching considers every raw member, not only the primary, but each
family has capacity one. It is restricted to raw-recovered KOIs and the same
match category. A recovered member can be hidden behind a harmonic primary:
the separate family_primary_period column records this explicitly.
The main detected_period/epoch/rf_probability describe the selected raw match;
rank/priority_score describe the assigned family's unchanged primary.

Top K refers to candidate-family rank, separately for fundamental and harmonic.
The compact console TOP K uses fundamental ONLY; combined metrics are also saved.
Per-star top K and multiplanet all/partial/none use fundamental OR harmonic.
Multiplanet denominator = cohort stars with >1 known CANDIDATE (22 systems).
RF conditional recall = raw fundamental matches with RF>=0.5 / raw fundamental
matches. It uses the assigned raw detection, never max RF over aliases.
Missing RF remains missing and is reported, never imputed to a probability.
MRR uses all 123 KOIs; unrecovered/unassigned families contribute zero.
Median/mean rank and rank bins use only KOIs with an assigned family rank.
Both fundamental-only and combined rank statistics are saved.

Unmatched family = no eligible period+phase match to ANY of the 123 cohort KOIs
on that star, before assignment conflicts. This is relative to this candidate
reference set, NOT evidence of a false positive or a globally uncatalogued signal.
No unmatched family is labeled as a physical false positive.
Failed scans stay in denominators, have missing burden counts, and are reported.
Runtime total is wall time for the official cohort; mean/star is mean measured
task runtime (not parallel wall time divided by 92). Control time is separate.

Frozen source/model/catalog/dataset/curve hashes are recorded before scanning.
Successful, failed and interrupted first attempts are retained. Resume runs
only never-started stars; it does not reselect/tune/rerun based on performance.
"""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_table(path):
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def select_cohort(root):
    model = load_model(root)
    validate_dataset(root)
    data_path = root / 'data/training_features.csv'
    if file_hash(data_path) != model['dataset_sha256']:
        raise ValueError('Dataset changed since training')
    data = pd.read_csv(data_path)
    catalog = pd.read_csv(root / 'data/koi_dr25.csv')
    selected = data[data.kepid.isin(model['test_kepids']) & data.label.eq(1)][['kepid', 'kepoi_name']]
    cohort = selected.merge(catalog, on=['kepid', 'kepoi_name'], validate='one_to_one')
    if not cohort.koi_pdisposition.eq('CANDIDATE').all():
        raise ValueError('Disposition mismatch')
    cohort = cohort[['kepid', 'kepoi_name', 'koi_period', 'koi_time0bk', 'koi_duration']].rename(
        columns={'koi_time0bk': 'koi_epoch'}).sort_values(['kepid', 'kepoi_name']).reset_index(drop=True)
    if len(cohort) != 123 or cohort.kepid.nunique() != 92 or cohort.kepoi_name.duplicated().any():
        raise ValueError('Expected exactly 92 TEST stars and 123 unique CANDIDATE KOIs')
    if set(cohort.kepid) & (set(model['train_kepids']) | {10854555}):
        raise ValueError('Train/control leakage')
    full = catalog[catalog.kepid.isin(cohort.kepid) & catalog.koi_pdisposition.eq('CANDIDATE')]
    if set(full.kepoi_name) != set(cohort.kepoi_name):
        raise ValueError('Cohort omits known CANDIDATE on selected stars')
    if not np.isfinite(cohort[['koi_period', 'koi_epoch']]).all().all():
        raise ValueError('Missing ephemerides')
    return cohort, catalog


def prepare(root, out):
    if (out / 'frozen_manifest.json').exists():
        verify_frozen(root, out)
        return
    if out.exists() and any(out.iterdir()):
        raise ValueError('Nonempty evaluation directory without manifest; will not overwrite it')
    cohort, catalog = select_cohort(root)
    out.mkdir(parents=True, exist_ok=True)
    work = out / 'blind_workspace'
    for folder in ('data', 'models', 'cache/lightcurves', 'logs', 'results'):
        (work / folder).mkdir(parents=True, exist_ok=True)
    atomic_csv(cohort, out / 'cohort_koi.csv')
    atomic_csv(cohort.groupby('kepid').size().rename('number_known_koi').reset_index(), out / 'cohort_stars.csv')
    control = catalog[(catalog.kepid == 10854555) & (catalog.kepoi_name == 'K00755.01')]
    if len(control) != 1:
        raise ValueError('Control reference is missing/ambiguous')
    control = control[['kepid', 'kepoi_name', 'koi_period', 'koi_time0bk', 'koi_duration']].rename(
        columns={'koi_time0bk': 'koi_epoch'})
    atomic_csv(control, out / 'control_reference.csv')
    (out / 'protocol.txt').write_text(PROTOCOL, encoding='utf-8')
    source_files = [*sorted((root / 'kepler_detector').glob('*.py')), root / 'config.py', root / 'evaluate_blind.py']
    source_hashes = {str(p.relative_to(root)): file_hash(p) for p in source_files}
    for source in source_files:
        target = out / 'source_snapshot' / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    model = root / 'models/kepler_rf.joblib'
    shutil.copy2(model, work / 'models/kepler_rf.joblib')
    cache_hashes = {}
    for kic in [*sorted(cohort.kepid.unique()), 10854555]:
        name = f'KIC_{int(kic):08d}.npz'
        source = root / 'cache/lightcurves' / name
        cache_hashes[name] = file_hash(source)
        shutil.copy2(source, work / 'cache/lightcurves' / name)
    manifest = dict(created_at=utc_now(), evaluation_version='1.0', **schema_metadata(),
        config=Config().to_dict(), official_stars=92, official_koi=123,
        model_sha256=file_hash(model), dataset_sha256=file_hash(root / 'data/training_features.csv'),
        catalog_sha256=file_hash(root / 'data/koi_dr25.csv'), source_hashes=source_hashes,
        curve_hashes=cache_hashes, protocol_sha256=file_hash(out / 'protocol.txt'),
        cohort_sha256=file_hash(out / 'cohort_koi.csv'),
        versions={name: importlib.metadata.version(name) for name in ('numpy', 'pandas', 'scipy', 'astropy', 'scikit-learn')})
    atomic_json(manifest, out / 'frozen_manifest.json')
    print('FROZEN: 92 TEST stars, 123 CANDIDATE KOIs; defaults unchanged; no catalog in scanner workspace.', flush=True)


def verify_frozen(root, out):
    manifest = json.loads((out / 'frozen_manifest.json').read_text(encoding='utf-8'))
    for relative, expected in manifest['source_hashes'].items():
        if file_hash(root / relative) != expected:
            raise ValueError(f'Frozen source changed: {relative}')
    checks = [(root / 'models/kepler_rf.joblib', manifest['model_sha256']),
              (out / 'blind_workspace/models/kepler_rf.joblib', manifest['model_sha256']),
              (root / 'data/training_features.csv', manifest['dataset_sha256']),
              (root / 'data/koi_dr25.csv', manifest['catalog_sha256']),
              (out / 'protocol.txt', manifest['protocol_sha256']),
              (out / 'cohort_koi.csv', manifest['cohort_sha256'])]
    checks += [(out / 'blind_workspace/cache/lightcurves' / name, value) for name, value in manifest['curve_hashes'].items()]
    for path, expected in checks:
        if file_hash(path) != expected:
            raise ValueError(f'Frozen input changed: {path}')
    if any((out / 'blind_workspace/data' / name).exists() for name in ('koi_dr25.csv', 'tce_dr25.csv')):
        raise ValueError('Catalog leakage into blind workspace')
    return manifest


def scan_worker(work_string, kic, config_values):
    """Worker inputs have NO KOI period, epoch, duration or evaluation labels."""
    from kepler_detector.scanner import scan
    from kepler_detector.utils import close_logging
    work = Path(work_string)
    if any((work / 'data' / name).exists() for name in ('koi_dr25.csv', 'tce_dr25.csv')):
        raise ValueError('Catalog leakage')
    status_path = work.parent / 'attempts' / f'{kic}.json'
    status_path.parent.mkdir(exist_ok=True)
    # Never overwrite the first attempt. Resume is coordinated by the parent.
    if status_path.exists():
        raise ValueError(f'Attempt already exists for {kic}')
    start = time.monotonic()
    state = dict(kepid=int(kic), status='running', started_at=utc_now())
    atomic_json(state, status_path)
    try:
        scan(work, int(kic), Config(**config_values))
        state.update(status='complete')
    except Exception:
        state.update(status='failed', error=traceback.format_exc())
    finally:
        state.update(seconds=time.monotonic() - start, ended_at=utc_now())
        atomic_json(state, status_path)
        close_logging()
    return state


def existing_attempt(out, kic):
    path = out / 'attempts' / f'{kic}.json'
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding='utf-8'))
    if state['status'] == 'running':
        # Preserve the original attempt record. Do not rerun an interrupted scan.
        meta = out / f'blind_workspace/results/KIC_{kic}/metadata.json'
        metadata = json.loads(meta.read_text(encoding='utf-8')) if meta.exists() else {}
        if metadata.get('status') == 'complete':
            return {**state, 'status': 'complete', 'seconds': metadata['seconds']}
        return {**state, 'status': 'interrupted', 'seconds': 0,
                'error': 'Interrupted first attempt retained; not retried'}
    return state


def result_tables(out, cohort, states):
    koi_frames, star_rows, all_edges = [], [], []
    for kic, known in cohort.groupby('kepid', sort=True):
        state = states[int(kic)]
        target = out / f'blind_workspace/results/KIC_{int(kic)}'
        raw = load_table(target / 'detections.csv') if state['status'] == 'complete' else pd.DataFrame()
        families = load_table(target / 'candidates.csv') if state['status'] == 'complete' else pd.DataFrame()
        rows, star, edges = evaluate_star(known, raw, families, state['status'], state['seconds'], state.get('error', ''))
        koi_frames.append(rows)
        star_rows.append(star)
        all_edges.extend(edges)
    koi, stars = pd.concat(koi_frames, ignore_index=True), pd.DataFrame(star_rows)
    atomic_csv(koi, out / 'koi_results.csv')
    atomic_csv(stars, out / 'star_results.csv')
    atomic_csv(pd.DataFrame(all_edges), out / 'eligible_matches.csv')
    return koi, stars


def charts(out, koi, stars, summary):
    from kepler_detector.plotting import plt, save
    fig, ax = plt.subplots(figsize=(8, 4.5))
    x = np.arange(4)
    ax.bar(x - .18, [summary['fundamental_top'][str(k)]['n'] for k in (1, 3, 5, 10)], .36, label='Fundamental')
    ax.bar(x + .18, [summary['combined_top'][str(k)]['n'] for k in (1, 3, 5, 10)], .36, label='Fundamental + harmonic')
    ax.set(xticks=x, xticklabels=['Top 1', 'Top 3', 'Top 5', 'Top 10'], ylabel='Recovered KOIs / 123',
           title='Blind recovery by family rank (scanner limit: 5 detections)')
    ax.legend()
    save(fig, out / 'recovery_by_rank.png')
    for name, values, xlabel, title in [
        ('period_error_histogram', koi.loc[koi.recovered_fundamental, 'period_error_percent'],
         'Absolute period error (%)', 'Fundamental recoveries'),
        ('rf_probability_recovered', koi.loc[koi.recovered_fundamental, 'rf_probability'],
         'RF score (not calibrated planet probability)', 'RF on BLS fundamental recoveries'),
        ('candidate_families_per_star', stars.number_candidate_families,
         'Candidate families', 'Family burden per TEST star')]:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        values = values.dropna()
        ax.hist(values, bins=np.arange(-.5, 6.5, 1) if 'families' in name else 15, color='#376b9b', edgecolor='white')
        if name == 'rf_probability_recovered':
            ax.axvline(.5, color='#b34f38', linestyle='--', label='RF threshold 0.5')
            ax.set_xlim(0, 1)
            ax.legend()
        if len(values) == 0:
            ax.text(.5, .5, 'No recovered observations', transform=ax.transAxes, ha='center')
        ax.set(xlabel=xlabel, ylabel='Stars' if 'families' in name else 'KOIs', title=title)
        save(fig, out / f'{name}.png')


def console_summary(s):
    def show(metric):
        percent = metric['percent']
        return f"{metric['n']}/{metric['denominator']} ({percent:.1f}%)" if percent is not None else 'n/a (zero denominator)'
    rank = s['rank_fundamental']
    median = f"{rank['median']:.1f}" if rank['median'] is not None else 'n/a'
    def number(value):
        return 'n/a' if value is None else f'{value:.1f}'
    control = s.get('control', {})
    return '\n'.join([
        'BLIND END-TO-END EVALUATION', '----------------------------',
        f"Stars:                         {s['total_stars']}",
        f"Known CANDIDATE KOIs:           {s['total_koi']}", '',
        f"Fundamental recovered:         {show(s['fundamental_raw'])}",
        f"Fundamental+harmonic:           {show(s['fundamental_plus_harmonic_raw'])}", '',
        *[f"Top {k} (fundamental):           {show(s['fundamental_top'][str(k)])}" for k in (1, 3, 5, 10)], '',
        f"Stars >=1 recovered:           {show(s['per_star']['any_recovered'])}",
        f"Multiplanet systems all:       {show(s['multiplanet']['all'])}", '',
        f"Median rank (fundamental):     {median}",
        f"MRR (fundamental, all KOIs):   {rank['mrr_all_koi']:.3f}", '',
        f"RF positive | BLS recovered:   {show(s['rf_positive_given_bls_fundamental'])}", '',
        f"Median families/star:          {number(s['median_families_per_star'])}",
        f"Median unmatched/star:         {number(s['median_unmatched_families_per_star'])}",
        f"Failed/interrupted stars:      {s['failed_stars']}", '',
        f"Runtime (official wall):       {s['runtime_total_seconds']:.1f} s",
        f"Mean task runtime/star:        {s['runtime_mean_per_star_seconds']:.1f} s", '',
        'KIC 10854555 control (excluded):',
        f"P detected = {control.get('detected_period')}",
        f"status = {control.get('status', 'PENDING')}", '',
        'Full fundamental/harmonic, per-star, rank and burden metrics: summary.json',
        'Unmatched means no match to the frozen CANDIDATE reference set.',
        'Default 5-detection limit retained; top 10 equals top 5 by construction.',
    ]) + '\n'


def detailed_summary(s):
    lines = [console_summary(s), 'ADDITIONAL METRICS (fixed denominators)']
    for key in ('fundamental_family', 'harmonic_raw', 'not_recovered'):
        m = s[key]
        lines.append(f"{key}: {m['n']}/{m['denominator']} ({m['percent']:.1f}%)")
    for group in ('harmonic_top', 'combined_top', 'per_star', 'multiplanet'):
        lines.append(group)
        for name, m in s[group].items():
            percent = f"{m['percent']:.1f}%" if m['percent'] is not None else 'n/a'
            lines.append(f"  {name}: {m['n']}/{m['denominator']} ({percent})")
    for group in ('rank_fundamental', 'rank_combined'):
        lines.append(f'{group}: {json.dumps(s[group])}')
    lines.append(f"Median unmatched families above recovered KOI: {s['median_unmatched_above_recovered']}")
    lines.append(f"Median all families above recovered KOI: {s['median_families_above_recovered']}")
    return '\n'.join(lines) + '\n'


def run(root, out, workers):
    prepare(root, out)
    manifest = verify_frozen(root, out)
    if (out / 'summary.json').exists():
        print('Existing first evaluation retained; no rerun.')
        print((out / 'summary.txt').read_text(encoding='utf-8'))
        return
    lock = out / 'evaluation.lock'
    with lock.open('x', encoding='utf-8') as stream:
        stream.write(str(os.getpid()))
    try:
        cohort = pd.read_csv(out / 'cohort_koi.csv')
        ids = sorted(map(int, cohort.kepid.unique()))
        work = str(out / 'blind_workspace')
        states = {kic: existing_attempt(out, kic) for kic in ids}
        pending = [kic for kic, state in states.items() if state is None]
        timing_path = out / 'timing.json'
        timing = json.loads(timing_path.read_text()) if timing_path.exists() else dict(started_at=utc_now())
        atomic_json(timing, timing_path)
        print(f'RUN: {len(pending)} never-started stars; {workers} workers; unchanged Config().', flush=True)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            jobs = {pool.submit(scan_worker, work, kic, manifest['config']): kic for kic in pending}
            for future in as_completed(jobs):
                kic = jobs[future]
                states[kic] = future.result()
                done = sum(state is not None for state in states.values())
                print(f'EVALUATION_PROGRESS {done}/92 KIC {kic} {states[kic]["status"]}', flush=True)
        runtime = (datetime.now(timezone.utc) - datetime.fromisoformat(timing['started_at'])).total_seconds()
        koi, stars = result_tables(out, cohort, states)
        summary = summarize(koi, stars, runtime)
        summary.update(evaluation_version=manifest['evaluation_version'], config=manifest['config'],
                       unmatched_reference='frozen CANDIDATE cohort, not all catalogs',
                       protocol_sha256=manifest['protocol_sha256'])
        charts(out, koi, stars, summary)
        print('Official scans completed; running independent control now.', flush=True)
        control_state = existing_attempt(out, 10854555)
        if control_state is None:
            control_state = scan_worker(work, 10854555, manifest['config'])
        control_ref = pd.read_csv(out / 'control_reference.csv')
        ck, cs = result_tables_control(out, control_ref, control_state)
        first = ck.iloc[0]
        summary['control'] = dict(kepid=10854555,
            status='PASS' if first.recovered_fundamental else 'FAIL',
            detected_period=float(first.detected_period) if pd.notna(first.detected_period) else None,
            rank=int(first['rank']) if pd.notna(first['rank']) else None,
            seconds=control_state['seconds'], scan_status=control_state['status'])
        verify_frozen(root, out)
        summary['frozen_inputs_unchanged'] = True
        atomic_json(summary, out / 'summary.json')
        text = console_summary(summary)
        (out / 'summary.txt').write_text(detailed_summary(summary), encoding='utf-8')
        print(text, flush=True)
    finally:
        lock.unlink()


def result_tables_control(out, known, state):
    target = out / 'blind_workspace/results/KIC_10854555'
    raw = load_table(target / 'detections.csv') if state['status'] == 'complete' else pd.DataFrame()
    families = load_table(target / 'candidates.csv') if state['status'] == 'complete' else pd.DataFrame()
    koi, star, edges = evaluate_star(known, raw, families, state['status'], state['seconds'], state.get('error', ''))
    atomic_csv(koi, out / 'control_results.csv')
    atomic_json(star, out / 'control_star.json')
    return koi, star


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--workers', type=int, default=4, choices=range(1, 5))
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    out = root / 'results/blind_evaluation'
    if args.prepare_only:
        prepare(root, out)
    else:
        run(root, out, args.workers)


if __name__ == '__main__':
    main()
