"""Modo TESS: catálogo TOI, cola de descubrimiento, triage y reparto (sin red)."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import ayudante
from config import Config
from kepler_detector import tess
from kepler_detector.mast import cache_path, save_curve
from kepler_detector.triage import assess_candidate
from kepler_detector.utils import setup, close_logging
from local_panel.cluster import Cluster
from local_panel.jobs import Manager, read
from test_pipeline import synthetic


def toi_frame(root):
    pd.DataFrame([{'tic': 777, 'toi': 101.01, 'disposition': 'PC', 'period': 3.2},
                  {'tic': 5, 'toi': 102.01, 'disposition': 'FP', 'period': 1.0}]).to_csv(
        root / tess.TOI_PATH, index=False)


class TessCatalogTests(unittest.TestCase):
    def test_match_toi(self):
        catalog = pd.DataFrame([{'tic': 777, 'toi': 101.01, 'disposition': 'PC', 'period': 3.2}])
        hit = tess.match_toi(777, 3.201, catalog)
        self.assertTrue(hit['known_toi'])
        self.assertEqual(hit['matched_toi_name'], 'TOI 101.01')
        self.assertEqual(hit['catalog_match_status'], 'known TOI')
        self.assertFalse(tess.match_toi(777, 5.0, catalog)['known_toi'])
        self.assertFalse(tess.match_toi(778, 3.2, catalog)['known_toi'])

    def test_replenish_walks_sectors_excludes_toi_and_is_reproducible(self):
        sectors = {1: list(range(1, 8)), 2: list(range(5, 30)), 3: list(range(100, 140))}
        with tempfile.TemporaryDirectory() as tmp:
            root = setup(Path(tmp))
            toi_frame(root)
            fetch = lambda sector: sectors.get(sector, [])
            first = tess.replenish(root, block_size=20, batch_size=10, max_sectors=5, fetch=fetch)
            queue = pd.read_csv(root / tess.TARGETS_PATH)
            self.assertEqual(first['new_targets'], 20)
            self.assertNotIn(5, set(queue.tic))  # TIC con TOI excluido
            self.assertEqual(len(set(queue.tic)), 20)
            self.assertEqual(sorted(queue.queue_rank), list(range(1, 21)))
            second = tess.replenish(root, block_size=20, batch_size=10, max_sectors=5, fetch=fetch)
            queue2 = pd.read_csv(root / tess.TARGETS_PATH)
            self.assertEqual(queue2.tic.iloc[:20].tolist(), queue.tic.tolist())  # El prefijo no cambia
            self.assertEqual(len(set(queue2.tic)), 40)
            batches = sorted((root / tess.BATCH_DIR).glob('batch_*.csv'))
            self.assertEqual(len(batches), 4)
            self.assertEqual(list(pd.read_csv(batches[0]).columns), ['queue_rank', 'tic', 'sector'])
            self.assertEqual(second['total_batches'], 4)

    def test_uncalibrated_model_never_prioritario(self):
        row = dict(rf_probability=0.95, cnn_probability=0.9, priority_score=70, depth_snr=40,
                   number_of_transits=10, transit_depth_consistency=0.9, vetting_flags='')
        self.assertEqual(assess_candidate(row)['triage_label'], 'prioritario')
        capped = assess_candidate(dict(row, model_calibrated='False'))
        self.assertEqual(capped['triage_label'], 'revisar')
        self.assertIn('no calibrado', capped['triage_reason'])


class TessScanTests(unittest.TestCase):
    def test_scan_marks_known_toi_and_signature_tracks_catalog(self):
        from kepler_detector.scanner import scan, signature
        curve, _ = synthetic()
        cfg = Config(period_max=10, coarse_points=1000, refine_peaks=3, max_planets=2)
        with tempfile.TemporaryDirectory() as tmp:
            root = setup(Path(tmp))
            save_curve(curve, cache_path(root, 777, None, 'tess'))
            before = signature(root, cfg, True, 'tess')
            toi_frame(root)
            self.assertNotEqual(before, signature(root, cfg, True, 'tess'))
            self.assertEqual(signature(root, cfg, True), signature(root, cfg, True, 'kepler'))
            result = scan(root, 777, cfg, no_model=True, mission='tess')
            self.assertTrue(result.known_toi.iloc[0])
            self.assertEqual(result.matched_toi_name.iloc[0], 'TOI 101.01')
            self.assertEqual(result.catalog_match_status.iloc[0], 'known TOI')
            close_logging()


class TessPanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = setup(base / 'coordinador')
        self.helper_root = setup(base / 'ayudante')
        self.env = patch.dict(os.environ, {'KEPLER_EVIDENCE_DIR': str(base / 'escritorio')})
        self.env.start()
        self.manager = Manager(self.root, launcher=lambda *a, **k: None)
        self.cluster = Cluster(self.manager, port=0, host='127.0.0.1')
        self.cluster.start()
        self.url = f'http://127.0.0.1:{self.cluster.server.server_port}'
        fast = Config(period_max=10, coarse_points=1000, refine_peaks=3, max_planets=2)
        self.config = patch('config.Config', side_effect=lambda **kw: fast)
        self.config.start()

    def tearDown(self):
        self.config.stop()
        self.cluster.stop()
        self.env.stop()
        close_logging()
        self.tmp.cleanup()

    def test_distributed_tess_job_end_to_end(self):
        toi_frame(self.root)
        curve, _ = synthetic()
        save_curve(curve, cache_path(self.helper_root, 777, None, 'tess'))
        self.manager.set_mode('tess')
        job = self.manager.create('777', distributed=True)
        state = read(self.root / f'results/local_panel/{job}/state.json')
        self.assertEqual(state['mission'], 'tess')
        # Un ayudante antiguo (sin capacidad 'missions') no recibe estrellas TESS.
        self.assertIsNone(self.cluster.claim('cccccccc33333333', 'viejo', caps=['build']))
        done = ayudante.run_worker(self.url, self.cluster.token, 'aaaaaaaa11111111', 'PC-TESS',
                                   self.helper_root, idle_exit=True)
        self.assertEqual(done, 1)
        self.assertTrue((self.helper_root / 'data/tess_toi.csv').exists())  # catálogo sincronizado
        state = read(self.root / f'results/local_panel/{job}/state.json')
        self.assertEqual(state['stars'], {'777': 'complete'})
        folder = self.root / f'results/local_panel/{job}/workspace/results/TIC_777'
        self.assertTrue((folder / 'candidates.csv').exists())
        self.assertEqual(self.manager.result_path(job, 777, 'candidates.csv'), folder / 'candidates.csv')
        tess_view = self.manager.overview('tess')
        self.assertEqual(tess_view['stars'], 1)
        self.assertEqual(self.manager.overview('kepler')['stars'], 0)
        if tess_view['follow']:
            self.assertEqual(tess_view['follow'][0]['matched_toi_name'], 'TOI 101.01')
            self.assertNotEqual(tess_view['follow'][0]['label'], 'prioritario')
        self.manager.watch(777, 'add', 'TESS', mission='tess')
        self.assertEqual(len(self.manager.overview('tess')['watchlist']), 1)
        self.assertEqual(len(self.manager.overview('kepler')['watchlist']), 0)

    def test_tess_discovery_batches_are_distributed_with_mission(self):
        batches = self.root / tess.BATCH_DIR
        batches.mkdir(parents=True)
        for number in range(1, 31):  # Más de 25 lotes: sin ampliación automática
            (batches / f'batch_{number:02d}.csv').write_text(
                'queue_rank,tic,sector\n' + ''.join(f'{r},{9000 + r},1\n' for r in range(number * 10 - 9, number * 10 + 1)),
                encoding='utf-8')
        self.manager.set_mode('tess')
        self.cluster.set_distribute_discovery(True)
        self.manager.start_discovery()
        jobs = [s for s in self.manager.snapshot() if s.get('batch_index')]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['mission'], 'tess')
        self.assertEqual(jobs[0]['ids'][0], 9001)
        self.assertTrue(read(self.root / 'results/local_panel/discovery_automation_tess.json')['enabled'])
        self.assertEqual(self.manager.discovery_details()['mission'], 'tess')
        self.assertEqual(self.manager.discovery_details('kepler')['enabled'], False)


if __name__ == '__main__':
    unittest.main()
