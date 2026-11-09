"""K2 y TESS: descarga, escaneo y CLI sin red (Lightkurve simulado)."""
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from config import Config
from kepler_detector import missions
from kepler_detector.mast import cache_path, download, save_curve
from kepler_detector.utils import setup
from test_pipeline import synthetic, temporary_project


class FakeQuantity:
    def __init__(self, value):
        self.value = value


class FakeTime:
    def __init__(self, jd):
        self.tdb = types.SimpleNamespace(jd=jd)


class FakeLightCurve:
    def __init__(self, sector):
        t = np.arange(0, 27, 0.02)
        self.time = FakeTime(t + 2459000.0)
        self.flux = FakeQuantity(1000 + np.random.default_rng(sector).normal(0, 0.1, len(t)))
        self.flux_err = FakeQuantity(np.full(len(t), 0.1))
        self.meta = {'SECTOR': sector, 'FILENAME': f'tess_s{sector:04d}.fits'}


class FakeProduct:
    def __init__(self, sector):
        self.sector = sector

    def download(self, **options):
        assert options['flux_column'] == 'pdcsap_flux'
        return FakeLightCurve(self.sector)


def fake_lightkurve(responses, calls):
    def search_lightcurve(target, **options):
        calls.append((target, options))
        return responses.get(options['author'], [])
    return types.SimpleNamespace(search_lightcurve=search_lightcurve)


class MissionDownloadTests(unittest.TestCase):
    def test_kepler_cache_path_unchanged(self):
        root = Path('/x')
        self.assertEqual(cache_path(root, 123), root / 'cache/lightcurves/KIC_00000123.npz')
        self.assertEqual(cache_path(root, 123, 4), root / 'cache/lightcurves/KIC_00000123_Q04.npz')
        self.assertEqual(cache_path(root, 25155310, 14, 'tess'),
                         root / 'cache/lightcurves/TIC_0025155310_S014.npz')
        self.assertEqual(cache_path(root, 201367065, None, 'k2'),
                         root / 'cache/lightcurves/EPIC_0201367065.npz')

    def test_unknown_mission(self):
        with self.assertRaises(ValueError):
            missions.get('hubble')

    def test_tess_falls_back_to_ffi_and_records_metadata(self):
        calls = []
        lk = fake_lightkurve({'TESS-SPOC': [FakeProduct(14), FakeProduct(15)]}, calls)
        with temporary_project() as tmp, patch.dict(sys.modules, {'lightkurve': lk}):
            root = setup(Path(tmp))
            curve = download(root, 25155310, mission='tess')
            self.assertEqual([c[1]['author'] for c in calls], ['SPOC', 'TESS-SPOC'])
            self.assertEqual(calls[0][0], 'TIC 25155310')
            self.assertEqual(calls[0][1]['mission'], 'TESS')
            self.assertEqual(calls[0][1]['exptime'], 120)
            self.assertEqual(curve.metadata['mission'], 'tess')
            self.assertEqual(curve.metadata['author'], 'TESS-SPOC')
            self.assertEqual([p['sector'] for p in curve.metadata['products']], [14, 15])
            self.assertEqual(set(curve.segment), {0, 1})
            # Tiempos en la misma escala que Kepler (BJD - 2454833).
            self.assertAlmostEqual(curve.time.min(), 2459000.0 - 2454833.0, places=3)
            self.assertTrue(cache_path(root, 25155310, None, 'tess').exists())

    def test_segment_is_passed_as_sector_or_campaign(self):
        calls = []
        lk = fake_lightkurve({'SPOC': [FakeProduct(7)], 'K2': [FakeProduct(5)]}, calls)
        with temporary_project() as tmp, patch.dict(sys.modules, {'lightkurve': lk}):
            root = setup(Path(tmp))
            download(root, 1, 7, mission='tess')
            self.assertEqual(calls[-1][1]['sector'], 7)
            self.assertNotIn('quarter', calls[-1][1])
            download(root, 2, 5, mission='k2')
            self.assertEqual(calls[-1][1]['campaign'], 5)
            self.assertEqual(calls[-1][1]['cadence'], 'long')

    def test_no_products_is_a_clear_error(self):
        lk = fake_lightkurve({}, [])
        with temporary_project() as tmp, patch.dict(sys.modules, {'lightkurve': lk}):
            root = setup(Path(tmp))
            with self.assertRaisesRegex(ValueError, 'EPIC 99'):
                download(root, 99, mission='k2')


class MissionScanTests(unittest.TestCase):
    def config(self):
        return Config(period_max=10, coarse_points=1000, refine_peaks=3, max_planets=2)

    def test_tess_scan_uses_own_folder_and_skips_koi(self):
        from kepler_detector.scanner import scan
        curve, _ = synthetic()
        with temporary_project() as tmp:
            root = setup(Path(tmp))
            save_curve(curve, cache_path(root, 777, None, 'tess'))
            # Un KOI con el mismo número y período no debe cruzarse con una estrella TESS.
            pd.DataFrame([{'kepid': 777, 'kepoi_name': 'K00777.01', 'koi_period': 3.2,
                           'koi_disposition': 'CONFIRMED', 'koi_pdisposition': 'CANDIDATE'}]
                         ).to_csv(root / 'data/koi_dr25.csv', index=False)
            result = scan(root, 777, self.config(), no_model=True, mission='tess')
            self.assertGreaterEqual(len(result), 1)
            self.assertEqual(result.iloc[0]['target'], 'TIC 777')
            self.assertEqual(result.iloc[0]['mission'], 'tess')
            self.assertNotIn('kepid', result.columns)
            self.assertFalse(result.known_koi.any())
            meta = json.loads((root / 'results/TIC_777/metadata.json').read_text())
            self.assertEqual(meta['status'], 'complete')
            self.assertFalse(meta['model_calibrated'])
            self.assertTrue((root / 'results/TIC_777/candidate_01_folded.png').exists())
            self.assertFalse((root / 'results/KIC_777').exists())

    def test_kepler_signature_unchanged_by_mission_support(self):
        from kepler_detector.scanner import signature
        with temporary_project() as tmp:
            root = setup(Path(tmp))
            cfg = self.config()
            self.assertEqual(signature(root, cfg, True), signature(root, cfg, True, 'kepler'))
            self.assertNotEqual(signature(root, cfg, True), signature(root, cfg, True, 'tess'))

    def test_k2_scan_list_reads_epic_column(self):
        from kepler_detector.scanner import scan_list
        curve, _ = synthetic()
        with temporary_project() as tmp:
            root = setup(Path(tmp))
            save_curve(curve, cache_path(root, 201, None, 'k2'))
            targets = root / 'data/k2_targets.csv'
            pd.DataFrame({'epic': [201]}).to_csv(targets, index=False)
            progress = scan_list(root, targets, self.config(), no_model=True, mission='k2')
            self.assertEqual(progress['201']['status'], 'complete')
            self.assertTrue((root / 'results/batch_progress_k2.json').exists())
            summary = pd.read_csv(root / 'results/batch_summary_k2.csv')
            self.assertEqual(set(summary.target), {'EPIC 201'})
            bad = root / 'data/bad.csv'
            pd.DataFrame({'kepid': [1]}).to_csv(bad, index=False)
            with self.assertRaisesRegex(ValueError, 'epic'):
                scan_list(root, bad, self.config(), no_model=True, mission='k2')


class MissionCliTests(unittest.TestCase):
    def run_cli(self, argv):
        import kepler_rf_detector as cli
        seen = {}

        def fake_scan(root, target, config, force, no_model, mission='kepler'):
            seen.update(target=target, mission=mission, segment=config.quarter)
            return pd.DataFrame()
        with temporary_project() as tmp, patch('kepler_detector.scanner.scan', side_effect=fake_scan):
            code = cli.main(['scan', '--root', tmp, '--no-model', *argv])
        return code, seen

    def test_tic_with_sector(self):
        code, seen = self.run_cli(['--tic', '25155310', '--sector', '14'])
        self.assertEqual(code, 0)
        self.assertEqual(seen, {'target': 25155310, 'mission': 'tess', 'segment': 14})

    def test_epic_and_kic(self):
        self.assertEqual(self.run_cli(['--epic', '201'])[1]['mission'], 'k2')
        self.assertEqual(self.run_cli(['--kic', '123'])[1]['mission'], 'kepler')

    def test_kepler_quarter_range_still_validated(self):
        code, seen = self.run_cli(['--kic', '123', '--quarter', '40'])
        self.assertEqual(code, 1)
        self.assertEqual(seen, {})


if __name__ == '__main__':
    unittest.main()
