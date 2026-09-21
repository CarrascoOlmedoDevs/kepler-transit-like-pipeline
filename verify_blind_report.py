"""Read-only consistency checks on frozen scans; writes an audit report only."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from PIL import Image
import evaluate_blind as ev
from blind_resume import verify_preserved
from kepler_detector.blind_metrics import match_signal
from kepler_detector.utils import file_hash
from local_panel.jobs import write


def verify(root):
    root=Path(root).resolve(); out=root/'results/blind_evaluation'
    frozen=ev.verify_frozen(root,out); verify_preserved(out)
    cohort,_=ev.select_cohort(root)
    koi=pd.read_csv(out/'koi_results.csv'); stars=pd.read_csv(out/'star_results.csv')
    summary=json.loads((out/'summary.json').read_text())
    assert len(koi)==123 and len(stars)==92
    assert koi.kepoi_name.is_unique and stars.kepid.is_unique
    assert set(koi.kepoi_name)==set(cohort.kepoi_name)
    assert set(stars.kepid)==set(cohort.kepid)
    assert 10854555 not in set(stars.kepid)
    assert stars.scan_status.eq('complete').all()
    assert not koi[koi.recovered_raw].duplicated(['kepid','detection_order']).any()
    assert not koi[koi.recovered_family].duplicated(['kepid','family_id']).any()
    assert int(koi.recovered_fundamental.sum())==summary['fundamental_raw']['n']
    assert int((koi.recovered_fundamental & koi.recovered_family).sum())==summary['fundamental_family']['n']
    assert int((koi.recovered_harmonic & koi.recovered_family).sum())==summary['harmonic_family']['n']
    assert int((~koi.recovered_raw).sum())==summary['not_recovered']['n']
    assert int(stars.number_recovered.sum())==int(koi.recovered_raw.sum())
    for k in (1,3,5,10):
        assert int((koi.recovered_fundamental & (koi['rank']<=k)).sum())==summary['fundamental_top'][str(k)]['n']
    raw_count=family_count=0
    for kic,rows in koi.groupby('kepid'):
        target=out/f'blind_workspace/results/KIC_{int(kic)}'
        meta=json.loads((target/'metadata.json').read_text())
        assert meta['config']==frozen['config']
        raw=ev.load_table(target/'detections.csv'); families=ev.load_table(target/'candidates.csv')
        raw_count+=len(raw); family_count+=len(families)
        for _,row in rows.iterrows():
            if row.recovered_raw:
                detection=raw[raw.detection_order==row.detection_order].iloc[0]
                assert match_signal(row.to_dict(),detection.to_dict())['match_type']==row.match_type
                assert np.isclose(row.rf_probability,detection.rf_probability,equal_nan=True)
                assert np.isclose(row.detected_period,detection.period_days)
            if row.recovered_family:
                family=families[families.family_id==row.family_id].iloc[0]
                assert row['rank']==family['rank']
                assert np.isclose(row.priority_score,family.priority_score)
    files=['koi_results.csv','star_results.csv','summary.json','summary.txt','evaluation_manifest.json',
           'not_recovered.csv','rf_false_negatives.csv','control_results.csv']
    for name in ('recovery_by_rank','recovery_vs_period','recovery_vs_depth','recovery_vs_transit_count',
                 'period_error_histogram','rf_probability_recovered','candidate_families_per_star'):
        path=out/(name+'.png')
        with Image.open(path) as picture:
            assert min(picture.size)>=400
            picture.verify()
        files.append(path.name)
    audit=dict(verified_at=ev.utc_now(),status='PASS',stars=92,koi=123,
        scientific_source_model_inputs_unchanged=True,original_22_outputs_unchanged=True,
        one_to_one_assignment_verified=True,rf_and_priority_unmodified=True,
        raw_detections=raw_count,candidate_families=family_count,
        output_hashes={name:file_hash(out/name) for name in files})
    write(out/'verification.json',audit)
    print(json.dumps({k:v for k,v in audit.items() if k!='output_hashes'},indent=2))


if __name__=='__main__':
    verify(Path(__file__).resolve().parent)
