import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from blind_report import observed_events, binned, period_bins, depth_bins, count_bins, diagnostic
from blind_resume import inventory, verify_preserved
from local_panel.jobs import write


class BlindReportingTests(unittest.TestCase):
    def test_event_count_gaps_duplicates_and_offphase(self):
        times=[0,.001,.002,10,10.01,30,35,np.nan]
        self.assertEqual(observed_events(times,10,0,2),3)
        self.assertEqual(observed_events([],10,0,2),0)
        self.assertIsNone(observed_events(times,0,0,2))

    def test_missing_duration_cadence_fallback(self):
        self.assertEqual(observed_events([.009,.011,10],10,0,np.nan),2)

    def test_period_boundaries_one_bin_each(self):
        x=pd.Series([2.9,3,10,30,100,300,300.1])
        masks=period_bins(x)
        self.assertEqual([int(m.sum()) for _,m in masks],[1,1,1,1,2,1])
        self.assertTrue((sum(m.astype(int) for _,m in masks)==1).all())

    def test_depth_missing_not_zero(self):
        frame=pd.DataFrame({'depth':[np.nan,100,200,500,1000,3000,3001],
                            'recovered_fundamental':[False,True,False,False,True,True,False]})
        table=binned(frame,'depth',depth_bins)
        self.assertEqual(table.N.sum(),6)
        self.assertEqual(table.recovered_fundamental.sum(),3)

    def test_empty_bin_no_rate(self):
        table=binned(pd.DataFrame({'p':[1.], 'recovered_fundamental':[True]}),'p',period_bins)
        self.assertTrue(pd.isna(table.iloc[1].recovery_percent))

    def test_transit_bin_boundaries(self):
        x=pd.Series([0,2,3,4,5,10,11,30,31,100,101])
        self.assertEqual([int(m.sum()) for _,m in count_bins(x)],[2,2,2,2,2,1])

    def test_diagnosis_does_not_invent_cause(self):
        row=SimpleNamespace(recovered_fundamental=False,recovered_family=False,
                            recovered_harmonic=False,raw_assignment_conflict=False)
        self.assertEqual(diagnostic(row)[0],'unknown')
        row.recovered_harmonic=True
        self.assertEqual(diagnostic(row)[0],'harmonic only')
        row.recovered_harmonic=False; row.recovered_fundamental=True
        self.assertEqual(diagnostic(row)[0],'candidate family grouping')

    def test_original_outputs_hash_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)
            path=out/'blind_workspace/results/KIC_123/detections.csv'
            path.parent.mkdir(parents=True)
            path.write_text('immutable')
            write(out/'evaluation_manifest.json',{'preserved_output_hashes':inventory(out,[123])})
            verify_preserved(out)
            path.write_text('changed')
            with self.assertRaises(ValueError):
                verify_preserved(out)

    def test_full_report_contract_with_synthetic_results(self):
        from blind_report import generate
        from kepler_detector.blind_metrics import evaluate_star
        koi_parts=[]; star_parts=[]
        for kic in range(1,93):
            n=32 if kic==1 else 1
            known=pd.DataFrame([dict(kepid=kic,kepoi_name=f'TEST_{kic}_{i}',koi_period=2.+i,
                                    koi_epoch=100.,koi_duration=2.) for i in range(n)])
            raw=pd.DataFrame([dict(detection_order=i+1,family_id=f'F{i}',period_days=2.+i,
                                  epoch_bkjd=100.,rf_probability=.4 if i==0 else .8,
                                  rank=i+1,priority_score=100-i) for i in range(n)])
            kk,ss,_=evaluate_star(known,raw,raw,'complete',1.)
            koi_parts.append(kk); star_parts.append(ss)
        koi=pd.concat(koi_parts,ignore_index=True); stars=pd.DataFrame(star_parts)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); out=root/'results/blind_evaluation'; out.mkdir(parents=True)
            (root/'data').mkdir()
            cohort=koi[['kepid','kepoi_name','koi_period','koi_epoch','koi_duration']]
            cohort.to_csv(out/'cohort_koi.csv',index=False)
            cohort.to_csv(root/'data/koi_dr25.csv',index=False)  # no depth: must remain missing
            control=koi.iloc[:1].copy(); control['kepid']=10854555
            control[['kepid','kepoi_name','koi_period','koi_epoch','koi_duration']].to_csv(out/'control_reference.csv',index=False)
            write(out/'evaluation_manifest.json',dict(preserved_output_hashes={},completed_before_resume=[],transit_count_method='synthetic test'))
            write(out/'timing.json',{'started_at':'2026-09-21T00:00:00+00:00'})
            write(out/'pause_state.json',{'paused_at':'2026-09-21T00:01:00+00:00'})
            for name in ('blind_report.py','blind_resume.py'):
                (root/name).write_text('# synthetic fixture')
            with patch('blind_report.ev.verify_frozen',return_value={}), \
                 patch('blind_report.ev.result_tables',return_value=(koi.copy(),stars.copy())), \
                 patch('blind_report.ev.existing_attempt',return_value={'status':'complete','seconds':1.}), \
                 patch('blind_report.ev.result_tables_control',return_value=(control,{})), \
                 patch('blind_report.ev.charts'), \
                 patch('blind_report.load_curve',return_value=SimpleNamespace(time=np.arange(100.,150.,.1))), \
                 patch('builtins.print'):
                summary=generate(root)
            self.assertEqual(summary['total_koi'],123)
            self.assertEqual(summary['total_stars'],92)
            self.assertEqual(summary['fundamental_raw']['n'],123)
            self.assertEqual(summary['missing_depth_count'],123)
            self.assertEqual(summary['harmonic_family']['n'],0)
            self.assertEqual(summary['rf_positive_given_bls_fundamental']['n'],31)
            saved=pd.read_csv(out/'star_results.csv')
            self.assertEqual(saved.number_recovered.sum(),123)
            self.assertEqual(len(pd.read_csv(out/'rf_false_negatives.csv')),92)
            self.assertTrue((out/'recovery_vs_period.png').is_file())
