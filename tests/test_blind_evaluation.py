import json
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from config import Config
from test_pipeline import temporary_project
from kepler_detector.blind_metrics import (match_signal, evaluate_star, summarize,
                                          MIN_EPOCH_TOLERANCE_DAYS)
from evaluate_blind import scan_worker, select_cohort


def known(name='K1', period=10, epoch=100, duration=2):
    return dict(kepid=123, kepoi_name=name, koi_period=period, koi_epoch=epoch, koi_duration=duration)


def detection(order=1, family='F1', period=10, epoch=100, rf=.8, rank=1):
    return dict(detection_order=order, family_id=family, period_days=period,
                epoch_bkjd=epoch, rf_probability=rf, rank=rank, priority_score=90-rank)


class BlindEvaluationTests(unittest.TestCase):
    def test_fundamental_requires_phase(self):
        self.assertIsNotNone(match_signal(known(), detection(period=10.05)))
        self.assertIsNone(match_signal(known(), detection(epoch=105)))
        self.assertIsNone(match_signal(known(), detection(period=10.11)))
        self.assertEqual(match_signal(known(), detection(epoch=120))['epoch_error'], 0)

    def test_inclusive_boundaries_and_units(self):
        matched = match_signal(known(), detection(period=10.1, epoch=100.125))
        self.assertEqual(matched['match_type'], 'fundamental')
        self.assertAlmostEqual(matched['epoch_tolerance_days'], .125)
        self.assertIsNone(match_signal(known(), detection(epoch=100.126)))
        matched = match_signal(known(duration=np.nan), detection(epoch=100+MIN_EPOCH_TOLERANCE_DAYS))
        self.assertIsNotNone(matched)
        self.assertIsNone(match_signal(known(epoch=np.nan), detection()))

    def test_all_harmonics_require_phase(self):
        for n in range(2, 9):
            for ratio in (n, 1/n):
                with self.subTest(ratio=ratio):
                    matched = match_signal(known(), detection(period=10*ratio))
                    self.assertEqual(matched['match_type'], 'harmonic')
                    self.assertEqual(matched['harmonic_ratio'], ratio)
                    self.assertIsNone(match_signal(known(), detection(period=10*ratio, epoch=100+.5*min(10,10*ratio))))

    def test_fundamental_precedes_harmonic_and_no_double_count(self):
        truth = pd.DataFrame([known('short', 10), known('long', 20)])
        raw = pd.DataFrame([detection(period=20)])
        result, star, edges = evaluate_star(truth, raw, raw, 'complete', 1)
        self.assertEqual(result.recovered_raw.sum(), 1)
        self.assertTrue(result.set_index('kepoi_name').loc['long', 'recovered_fundamental'])
        self.assertEqual(star['multiplanet_recovery'], 'partial')
        self.assertEqual(sum(e['selected_raw'] for e in edges), 1)

    def test_family_capacity_and_raw_rf_not_primary_rf(self):
        truth = pd.DataFrame([known('short', 10), known('long', 20)])
        raw = pd.DataFrame([detection(period=10, rf=.2), detection(order=2, period=20, rf=.9)])
        families = raw.iloc[[0]]
        result, star, edges = evaluate_star(truth, raw, families, 'complete', 1)
        self.assertEqual(result.recovered_fundamental.sum(), 2)
        self.assertEqual(result.recovered_family.sum(), 1)
        self.assertEqual(result.set_index('kepoi_name').loc['long', 'rf_probability'], .9)
        self.assertEqual(star['number_unmatched_families'], 0)
        summary = summarize(result, pd.DataFrame([star]), 1)
        self.assertEqual(summary['rf_positive_given_bls_fundamental']['n'], 1)
        self.assertEqual(summary['rf_positive_given_bls_fundamental']['denominator'], 2)
        self.assertEqual(summary['rank_fundamental']['mrr_all_koi'], .5)

    def test_harmonic_separate_unmatched_burden_and_top_metrics(self):
        truth = pd.DataFrame([known()])
        raw = pd.DataFrame([detection(family='noise', period=13, rank=1),
                            detection(order=2, family='match', period=20, rank=2)])
        result, star, _ = evaluate_star(truth, raw, raw, 'complete', 1)
        summary = summarize(result, pd.DataFrame([star]), 1)
        self.assertEqual(summary['fundamental_raw']['n'], 0)
        self.assertEqual(summary['harmonic_raw']['n'], 1)
        self.assertEqual(summary['harmonic_top']['1']['n'], 0)
        self.assertEqual(summary['harmonic_top']['5']['n'], 1)
        self.assertEqual(summary['median_unmatched_above_recovered'], 1)
        self.assertEqual(summary['rank_combined']['mrr_all_koi'], .5)
        self.assertIsNone(summary['rf_positive_given_bls_fundamental']['percent'])

    def test_failed_scan_stays_in_denominator(self):
        result, star, _ = evaluate_star(pd.DataFrame([known()]), pd.DataFrame(), pd.DataFrame(), 'failed', 2, 'error')
        summary = summarize(result, pd.DataFrame([star]), 2)
        self.assertEqual(summary['not_recovered']['n'], 1)
        self.assertEqual(summary['failed_stars'], 1)
        self.assertIsNone(summary['median_families_per_star'])
        json.dumps(summary, allow_nan=False)

    def test_blind_worker_inputs_and_no_catalog(self):
        with temporary_project() as tmp:
            work = Path(tmp) / 'blind_workspace'
            (work / 'data').mkdir(parents=True)
            with patch('kepler_detector.scanner.scan') as scan:
                state = scan_worker(str(work), 123, Config().to_dict())
                self.assertEqual(state['status'], 'complete')
                scan.assert_called_once_with(work, 123, Config())
            with self.assertRaisesRegex(ValueError, 'Attempt already exists'):
                scan_worker(str(work), 123, Config().to_dict())
            (work / 'data/koi_dr25.csv').touch()
            with self.assertRaisesRegex(ValueError, 'Catalog leakage'):
                scan_worker(str(work), 124, Config().to_dict())

    def test_official_cohort_exact_split_and_all_signals(self):
        root = Path(__file__).resolve().parents[1]
        if not (root / 'models/kepler_rf.joblib').exists():
            self.skipTest('current trained artifact unavailable')
        cohort, _ = select_cohort(root)
        self.assertEqual(len(cohort), 123)
        self.assertEqual(cohort.kepid.nunique(), 92)
        self.assertNotIn(10854555, cohort.kepid.tolist())
        self.assertEqual(int((cohort.groupby('kepid').size() > 1).sum()), 22)
