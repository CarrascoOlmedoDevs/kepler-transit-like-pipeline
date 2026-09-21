"""Export only completed first scans. Does not start or resume any search."""
import json
from pathlib import Path
import shutil
import pandas as pd
from evaluate_blind import verify_frozen, result_tables, charts, utc_now
from kepler_detector.blind_metrics import summarize
from kepler_detector.utils import atomic_csv, atomic_json


def records(frame):
    return json.loads(frame.to_json(orient='records'))


def main():
    root = Path(__file__).resolve().parent
    out = root / 'results/blind_evaluation'
    verify_frozen(root, out)
    cohort = pd.read_csv(out / 'cohort_koi.csv')
    states, paused, pending = {}, [], []
    for kic in sorted(map(int, cohort.kepid.unique())):
        attempt = out / 'attempts' / f'{kic}.json'
        state = json.loads(attempt.read_text()) if attempt.exists() else None
        meta = out / f'blind_workspace/results/KIC_{kic}/metadata.json'
        metadata = json.loads(meta.read_text()) if meta.exists() else {}
        if state and metadata.get('status') == 'complete':
            states[kic] = {**state, 'status': 'complete', 'seconds': state.get('seconds', metadata['seconds'])}
        elif state:
            paused.append(kic)
        else:
            pending.append(kic)
    pause_path = out / 'pause_state.json'
    if not pause_path.exists():
        atomic_json(dict(status='paused_by_user', paused_at=utc_now(), completed=sorted(states),
                         interrupted=paused, never_started=pending,
                         resume_note='Retain completed scans. Archive interrupted attempts before an explicitly authorized restart. '
                         'Do not run evaluate_blind.py directly while the stale lock remains.'), pause_path)
    pause = json.loads(pause_path.read_text())
    partial = out / 'partial'
    partial.mkdir(exist_ok=True)
    # result_tables expects outputs under this root; evaluate here then save
    # ONLY partial result names, never the official full-evaluation summary.
    from kepler_detector.blind_metrics import evaluate_star
    from evaluate_blind import load_table
    koi_frames, stars, edges, details = [], [], [], []
    dist = root / 'web_resultados/dist'
    assets = dist / 'assets/blind'
    assets.mkdir(parents=True, exist_ok=True)
    for kic, state in states.items():
        folder = out / f'blind_workspace/results/KIC_{kic}'
        raw, families = load_table(folder/'detections.csv'), load_table(folder/'candidates.csv')
        known = cohort[cohort.kepid == kic]
        k, s, e = evaluate_star(known, raw, families, 'complete', state['seconds'])
        koi_frames.append(k)
        stars.append(s)
        edges.extend(e)
        target = assets / str(kic)
        target.mkdir(exist_ok=True)
        for image in folder.glob('*.png'):
            shutil.copy2(image, target / image.name)
        fields = [name for name in ('rank','family_id','period_days','depth_ppm','depth_snr',
                  'duration_hours','rf_probability','priority_score','classification','vetting_flags',
                  'number_of_transits','plot_file') if name in families]
        details.append(dict(**s, koi=records(k), families=records(families[fields])))
    koi = pd.concat(koi_frames, ignore_index=True)
    star_frame = pd.DataFrame(stars)
    # Do not misclassify unscanned stars as failures or non-recoveries.
    for kic in paused + pending:
        details.append(dict(kepid=kic, scan_status='interrupted' if kic in paused else 'pending',
                            number_known_koi=int((cohort.kepid == kic).sum()),
                            koi=records(cohort[cohort.kepid == kic]), families=[]))
    summary = summarize(koi, star_frame, 0)
    summary.update(status='partial_paused', official_stars=92, official_koi=123,
                   completed_stars=len(states), interrupted_stars=len(paused), pending_stars=len(pending),
                   evaluated_koi=len(koi), paused_at=pause['paused_at'], control_status='not_run',
                   runtime_total_seconds=None)
    atomic_csv(koi, partial / 'koi_results.csv')
    atomic_csv(star_frame, partial / 'star_results_completed.csv')
    atomic_csv(pd.DataFrame([{key: value for key, value in star.items() if key not in ('koi','families')}
                             for star in sorted(details, key=lambda s:s['kepid'])]), partial/'star_results.csv')
    atomic_csv(pd.DataFrame(edges), partial / 'eligible_matches.csv')
    atomic_json(summary, partial / 'summary.json')
    charts(partial, koi, star_frame, summary)
    # The original plotting label has a fixed official denominator; these
    # partial charts are not used on the page to avoid mixing denominators.
    downloads = dist / 'downloads'
    downloads.mkdir(exist_ok=True)
    for source in (partial/'koi_results.csv', partial/'star_results.csv', out/'cohort_koi.csv', out/'protocol.txt'):
        shutil.copy2(source, downloads/source.name)
    for name in ('roc_curve.png','confusion_matrix.png','precision_recall_curve.png'):
        shutil.copy2(root/'results'/name, assets/name)
    payload = dict(summary=summary, stars=sorted(details, key=lambda s:(s['scan_status']!='complete',s['kepid'])),
                   model=json.loads((root/'models/kepler_rf_metrics.json').read_text()), feature_version='2.0')
    atomic_json(payload, dist/'blind-data.json')
    print(json.dumps({key: summary[key] for key in ('completed_stars','interrupted_stars','pending_stars',
                      'evaluated_koi','fundamental_raw','harmonic_raw','fundamental_top')}, indent=2))


if __name__ == '__main__':
    main()
