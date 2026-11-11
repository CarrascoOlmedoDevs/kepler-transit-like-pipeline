import importlib
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from config import Config
from kepler_detector.mast import Curve, save_curve, load_curve
from kepler_detector.bls import (Candidate, search, phase, harmonic_relation, harmonic_info,
                                 deduplicate)
from kepler_detector.features import extract, transit_consistency, FEATURES, binned_views
from kepler_detector.preprocessing import preprocess
from kepler_detector.vetting import vet, priority_score
from kepler_detector.crossmatch import match
from kepler_detector.utils import setup, close_logging


@contextmanager
def temporary_project():
    with tempfile.TemporaryDirectory() as tmp:
        try:
            yield tmp
        finally:
            close_logging()


def synthetic(period=3.2, duration=0.16, depth=0.003, secondary=0, alternating=False, days=45):
    t = np.arange(0, days, 0.0204) + 120.0
    epoch = 120.7
    rng = np.random.default_rng(42)
    y = 1 + rng.normal(0, 0.00015, len(t))
    mask = abs(phase(t, period, epoch)) < duration / 2
    events = np.floor((t - epoch) / period + 0.5).astype(int)
    dep = depth * np.where(events % 2 == 0, 1, 0.4) if alternating else depth
    y -= dep * mask
    y -= secondary * (abs(phase(t, period, epoch + period / 2)) < duration / 2)
    curve = Curve(t, y, np.full(len(t), 0.00015), np.zeros(len(t)), {'time_format': 'BKJD', 'time_scale': 'TDB'})
    candidate = Candidate(period, duration, epoch, 1000, 1000, depth, 50)
    return curve, candidate


class ScienceTests(unittest.TestCase):
    def test_features(self):
        curve, candidate = synthetic()
        f = extract(curve, candidate)
        self.assertEqual(set(f), set(FEATURES))
        self.assertAlmostEqual(f['depth_ppm'], 3000, delta=80)
        self.assertGreater(f['number_of_transits'], 12)
        self.assertGreater(f['depth_snr'], 30)
        self.assertTrue(all(np.isfinite(list(f.values()))))

    def test_odd_even(self):
        curve, candidate = synthetic(alternating=True)
        f = extract(curve, candidate)
        self.assertGreater(f['odd_even_sigma'], 10)
        self.assertIn('odd-even', vet(f)['vetting_flags'])
        clean, c = synthetic()
        self.assertLess(extract(clean, c)['odd_even_sigma'], 3)

    def test_secondary_eclipse(self):
        curve, candidate = synthetic(secondary=0.0015)
        f = extract(curve, candidate)
        self.assertAlmostEqual(f['secondary_depth_ratio'], 0.5, delta=0.05)
        self.assertIn('secondary-eclipse', vet(f)['vetting_flags'])

    def test_secondary_brightening_is_flagged_by_absolute_snr(self):
        curve, candidate = synthetic(secondary=-0.0015)
        f = extract(curve, candidate)
        result = vet(f)
        self.assertGreater(f['secondary_abs_snr'], 3)
        self.assertLess(f['secondary_snr'], -3)
        self.assertTrue(result['secondary_brightening'])
        self.assertTrue(result['phase_0_5_anomaly'])
        self.assertIn('phase-0.5-anomaly', result['vetting_flags'])

    def test_harmonics(self):
        self.assertTrue(harmonic_relation(4, 8))
        self.assertTrue(harmonic_relation(4, 2))
        self.assertFalse(harmonic_relation(4, 6))
        curve, c = synthetic(alternating=True)
        f = extract(curve, c)
        self.assertGreater(f['double_period_score'], 0)
        self.assertLess(f['half_period_depth'], f['depth_ppm'])
        self.assertIn('double-period-preferred', vet({**f, 'double_period_score': 1.5})['vetting_flags'])
        self.assertIn('half-period-consistent', vet({**f, 'half_period_depth': f['depth_ppm'],
                                                   'secondary_snr': 10})['vetting_flags'])
        self.assertEqual(harmonic_info(20, 5)[0], 4)
        self.assertEqual(harmonic_info(5, 20)[0], 0.25)
        self.assertIsNotNone(harmonic_info(8 * 5, 5))
        self.assertIsNone(harmonic_info(9 * 5, 5))

    def test_deduplication(self):
        curve, c = synthetic()
        same = Candidate(c.period * 1.00001, c.duration, c.epoch, 900, 900, c.depth, 40)
        harmonic = Candidate(c.period * 2, c.duration, c.epoch, 800, 800, c.depth, 30)
        other = Candidate(5.7, c.duration, c.epoch + 1, 500, 500, c.depth, 25)
        # El alias se elimina; el armónico permanece para agruparlo explícitamente.
        self.assertEqual(len(deduplicate([c, same, harmonic, other], curve.time)), 3)

    def test_adaptive_duration_expands_upper_boundary(self):
        # Planeta de período largo con tránsito de ~14 h: la malla inicial (<= 0.45 d) se amplía
        # y, al ser físicamente plausible, ya no se marca como borde (2.0 sí lo marcaba).
        curve, _ = synthetic(period=40.0, duration=0.6, depth=0.004, days=400)
        cfg = Config(period_max=60, coarse_points=1000, refine_peaks=3,
                     durations=(0.05, 0.1, 0.2, 0.3, 0.45))
        candidates, _ = search(curve, cfg, known_period=40.0)
        self.assertGreater(candidates[0].duration, 0.45)
        self.assertFalse(candidates[0].duration_at_boundary)
        self.assertAlmostEqual(candidates[0].duration, 0.6, delta=0.1)

    def test_unphysical_duration_is_capped_and_flagged(self):
        # Caja de ~17 h con período de 5 d: imposible para un planeta (máximo ~7.8 h con factor 2.5).
        from kepler_detector.bls import max_duration
        curve, _ = synthetic(period=5.0, duration=0.7, depth=0.004, days=80)
        cfg = Config(period_max=10, coarse_points=1000, refine_peaks=3)
        candidates, _ = search(curve, cfg, known_period=5.0)
        self.assertLessEqual(candidates[0].duration, max_duration(5.0, cfg) * 1.001)
        self.assertTrue(candidates[0].duration_at_boundary)

    def test_transit_consistency_and_jackknife(self):
        curve, candidate = synthetic(days=45)
        result = transit_consistency(curve, candidate)
        self.assertGreater(len(json.loads(result['individual_transit_depths'])), 10)
        self.assertGreater(result['median_transit_depth'], 2500)
        self.assertLess(result['max_single_transit_contribution'], 0.2)
        self.assertGreater(result['jackknife_min_snr_fraction'], 0.8)
        self.assertEqual(result['number_of_independent_quarters'], 1)

    def test_crossmatch(self):
        cat = pd.DataFrame([{'kepid': 123, 'kepoi_name': 'K00123.01', 'koi_period': 3.2, 'koi_disposition': 'CANDIDATE'}])
        self.assertTrue(match(123, 3.21, cat)['known_koi'])
        self.assertFalse(match(456, 3.2, cat)['known_koi'])
        self.assertFalse(match(123, 6.4, cat)['known_koi'])
        self.assertEqual(match(123, 4, cat)['catalog_match_status'], 'completely unmatched signal')
        tce = pd.DataFrame([{'kepid': 456, 'tce_id': 'TCE-1', 'tce_period': 3.2,
                             'tce_disposition': 'FALSE ALARM'}])
        matched = match(456, 3.2, cat, tce_catalog=tce)
        self.assertTrue(matched['known_tce'])
        self.assertTrue(matched['previously_known_false_alarm'])
        self.assertEqual(matched['catalog_match_status'], 'previously known false alarm')

    def test_priority(self):
        curve, c = synthetic()
        f = extract(curve, c)
        score = priority_score(f, 0.8)
        self.assertTrue(0 <= score <= 100)
        self.assertGreater(score, priority_score({**f, 'odd_even_sigma': 12}, 0.8))
        self.assertGreater(score, priority_score({**f, 'secondary_snr': 10, 'secondary_depth_ratio': 0.5}, 0.8))
        self.assertGreater(score, priority_score({**f, 'duration_at_boundary': True}, 0.8))
        false_positive = priority_score({**f, 'odd_even_sigma': 12}, 0.99, 'odd-even')
        clean = priority_score(f, 0.55, '')
        self.assertLess(false_positive, clean)
        self.assertTrue(np.isfinite(priority_score({key: np.nan for key in FEATURES}, np.nan)))

    def test_preprocessing_preserves_transit(self):
        curve, c = synthetic(duration=0.4)
        curve.flux *= 1 + 0.003 * np.sin(curve.time / 10)
        curve.flux[30] = np.nan
        curve.flux[50] = 2
        flat = preprocess(curve, Config())
        self.assertLess(len(flat.time), len(curve.time))
        self.assertGreater(extract(flat, c)['depth_ppm'], 2700)
        self.assertTrue(np.isfinite(flat.flux).all())

    def test_bls_blind_and_guided(self):
        curve, c = synthetic()
        cfg = Config(period_max=10, coarse_points=1500, refine_peaks=4, durations=(0.1, 0.16, 0.25))
        candidates, _ = search(curve, cfg)
        self.assertAlmostEqual(candidates[0].period, c.period, delta=0.005)
        guided, _ = search(curve, cfg, known_period=c.period)
        self.assertAlmostEqual(guided[0].period, c.period, delta=0.005)

    def test_cnn_views(self):
        curve, c = synthetic()
        views = binned_views(curve, c)
        self.assertEqual(len(views['global']['flux']), 2001)
        self.assertEqual(len(views['local']['flux']), 201)

    def test_cache(self):
        curve, _ = synthetic()
        with temporary_project() as tmp:
            path = Path(tmp) / 'curve.npz'
            save_curve(curve, path)
            loaded = load_curve(path)
            np.testing.assert_array_equal(loaded.time, curve.time)
            self.assertEqual(loaded.metadata, curve.metadata)

    def test_imports(self):
        for name in ('catalog', 'mast', 'preprocessing', 'bls', 'features', 'vetting', 'ml',
                     'scanner', 'plotting', 'crossmatch', 'utils', 'builder', 'anomaly'):
            self.assertIsNotNone(importlib.import_module('kepler_detector.' + name))

    def test_invalid_config(self):
        with self.assertRaises(ValueError):
            Config(flatten_window=1)
        with self.assertRaises(ValueError):
            Config(period_min=0)


class IntegrationTests(unittest.TestCase):
    def test_global_families_aliases_and_harmonics(self):
        from kepler_detector.scanner import build_families
        curve, primary = synthetic(period=5.0, days=100)
        alias = Candidate(5.01, primary.duration, primary.epoch, 900, 900, primary.depth, 40)
        harmonic = Candidate(20.0, primary.duration, primary.epoch, 800, 800, primary.depth, 30)
        rows = [
            {'period_days': c.period, 'detection_order': i + 1, 'priority_score': 80 - i,
             'rf_probability': 0.7, 'vetting_flags': '', **extract(curve, c)}
            for i, c in enumerate((primary, alias, harmonic))
        ]
        families = build_families(rows, [primary, alias, harmonic], curve.time)
        self.assertEqual(len(families), 1)
        self.assertEqual(families[0].n_detections, 3)
        self.assertEqual(sum(row['family_relation'] == 'alias' for row in rows), 1)
        self.assertEqual(sum(row['family_relation'] == 'harmonic' for row in rows), 1)
        self.assertIn('harmonic-duplicate', rows[2]['vetting_flags'])

    def test_adaptive_mask_reduces_local_power_without_excessive_loss(self):
        from kepler_detector.scanner import adaptive_mask
        curve, candidate = synthetic(period=3.2, duration=0.16, depth=0.003, days=45)
        available = np.ones(len(curve.time), dtype=bool)
        remaining, effectiveness, width, removed = adaptive_mask(curve, available, candidate)
        self.assertGreater(effectiveness, 0.5)
        self.assertLessEqual(width, 3.0)
        self.assertLess(removed, 0.15)
        self.assertLess(remaining.sum(), available.sum())

    def test_unmeasurable_residual_keeps_first_detection(self):
        from kepler_detector.scanner import scan
        curve, _ = synthetic()
        with temporary_project() as tmp:
            root = setup(Path(tmp))
            save_curve(curve, root / 'cache/lightcurves/KIC_00000123.npz')
            cfg = Config(period_max=10, coarse_points=1000, refine_peaks=3, max_planets=2)
            calls = []

            def measurement(curve, candidate):
                if calls:
                    raise ValueError('Residual sin cobertura suficiente')
                calls.append(True)
                return extract(curve, candidate)

            with patch('kepler_detector.scanner.extract', side_effect=measurement):
                result = scan(root, 123, cfg, no_model=True)
            self.assertEqual(len(result), 1)
            meta = json.loads((root / 'results/KIC_123/metadata.json').read_text())
            self.assertEqual(meta['status'], 'complete')

    def test_build_resume_and_failure(self):
        from kepler_detector.builder import build
        with temporary_project() as tmp:
            root = setup(Path(tmp))
            catalog = pd.DataFrame([
                {'kepid': kic, 'kepoi_name': f'K{kic}.01', 'koi_pdisposition': disposition,
                 'koi_disposition': disposition, 'koi_period': 3.2}
                for kic, disposition in [(123, 'CANDIDATE'), (124, 'FALSE POSITIVE'), (-1, 'CANDIDATE')]])
            catalog.to_csv(root / 'data/koi_dr25.csv', index=False)
            curve, _ = synthetic()
            for kic in (123, 124):
                save_curve(curve, root / f'cache/lightcurves/KIC_{kic:08d}.npz')
            cfg = Config()
            counts = build(root, cfg, per_class=2)
            self.assertEqual(counts, {0: 1, 1: 1})
            progress = json.loads((root / 'data/build_progress.json').read_text())
            self.assertEqual(progress['K-1.01']['status'], 'failed')
            with patch('kepler_detector.builder.download', side_effect=AssertionError('Reprocesamiento inesperado')):
                self.assertEqual(build(root, cfg, per_class=2), counts)
            self.assertEqual(len(pd.read_csv(root / 'data/training_features.csv')), 2)

    def test_scan_batch_resume_and_failure(self):
        from kepler_detector.scanner import scan_list
        with temporary_project() as tmp:
            root = setup(Path(tmp))
            cfg = Config(period_max=10, coarse_points=1000, refine_peaks=3, max_planets=2,
                         durations=(0.1, 0.16, 0.25))
            curve, _ = synthetic()
            # Dos señales no armónicas: debe recuperar ambas iterativamente.
            curve.flux -= 0.002 * (abs(phase(curve.time, 7.13, 121.1)) < 0.1)
            save_curve(curve, root / 'cache/lightcurves/KIC_00000123.npz')
            targets = root / 'data/test_targets.csv'
            pd.DataFrame({'kepid': [-1, 123]}).to_csv(targets, index=False)
            progress = scan_list(root, targets, cfg, no_model=True)
            self.assertEqual(progress['-1']['status'], 'failed')
            self.assertEqual(progress['123']['status'], 'complete')
            result = pd.read_csv(root / 'results/KIC_123/candidates.csv')
            self.assertEqual(len(result), 2)
            self.assertTrue(any(abs(result.period_days - 7.13) < 0.02))
            self.assertTrue((root / 'results/KIC_123/candidate_02_folded.png').exists())
            stamp = (root / 'results/KIC_123/metadata.json').stat().st_mtime_ns
            with patch('kepler_detector.scanner._worker', side_effect=AssertionError('Reprocesamiento inesperado')):
                scan_list(root, targets, cfg, no_model=True)
            self.assertEqual(stamp, (root / 'results/KIC_123/metadata.json').stat().st_mtime_ns)

    def test_ml_group_split_and_evaluate(self):
        from kepler_detector.ml import train, evaluate, load_model
        with temporary_project() as tmp:
            root = setup(Path(tmp))
            records = []
            for star in range(24):
                for koi in range(2):
                    curve, c = synthetic(alternating=bool(star % 2), days=30)
                    records.append({'kepid': star + 1, 'kepoi_name': f'K{star}.{koi}',
                                    'label': 1 - star % 2, **extract(curve, c)})
            pd.DataFrame(records).to_csv(root / 'data/training_features.csv', index=False)
            from kepler_detector.schema import schema_metadata, timestamp
            from kepler_detector.utils import atomic_json
            atomic_json({**schema_metadata(), 'created_at': timestamp(), 'row_count': len(records)},
                        root / 'data/build_manifest.json')
            metrics = train(root)
            artifact = load_model(root)
            self.assertEqual(artifact['model_type'], 'RandomForestClassifier')
            self.assertIn('training_timestamp', artifact)
            self.assertTrue(all(artifact[key] == value for key, value in schema_metadata().items()))
            self.assertIn('model', artifact)
            self.assertFalse(set(artifact['train_kepids']) & set(artifact['test_kepids']))
            self.assertEqual(metrics, evaluate(root))
            self.assertTrue((root / 'results/roc_curve.png').exists())
            # Datos sintéticos exclusivamente en temporal, nunca un modelo científico entregado.

    @unittest.skipUnless(os.environ.get('KEPLER_REAL_REGRESSION') == '1',
                         'regresión real opcional; requiere la curva KIC 10854555 en caché')
    def test_real_kic_10854555_regression(self):
        from kepler_detector.scanner import scan
        source = Path(__file__).resolve().parents[1]
        cached = source / 'cache/lightcurves/KIC_10854555.npz'
        if not cached.exists():
            self.skipTest('curva real no disponible')
        with temporary_project() as tmp:
            root = setup(Path(tmp))
            save_curve(load_curve(cached), root / 'cache/lightcurves/KIC_10854555.npz')
            pd.read_csv(source / 'data/koi_dr25.csv').to_csv(root / 'data/koi_dr25.csv', index=False)
            cfg = Config(max_planets=20, coarse_points=10000, refine_peaks=12)
            result = scan(root, 10854555, cfg, no_model=True)
            detections = pd.read_csv(root / 'results/KIC_10854555/detections.csv')
            first = detections.sort_values('detection_order').iloc[0]
            self.assertAlmostEqual(first.period_days, 2.5256, delta=0.002)
            self.assertLess(len(result), len(detections))
            self.assertLess(np.mean(np.isclose(detections.duration_hours, 10.8)), 0.5)
            self.assertGreater(detections.duration_at_boundary.sum(), 0)
            self.assertGreater((detections.family_relation == 'harmonic').sum(), 0)
            negative = detections[detections.secondary_snr < -3]
            self.assertTrue(negative.phase_0_5_anomaly.all())


if __name__ == '__main__':
    unittest.main()
