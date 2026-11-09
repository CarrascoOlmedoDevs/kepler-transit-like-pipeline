"""Reparto en red local: coordinador real y ayudante real por HTTP, sin red externa."""
import io
import json
import os
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import ayudante
from kepler_detector.mast import save_curve
from kepler_detector.utils import setup, close_logging
from local_panel.cluster import Cluster
from local_panel.jobs import Manager, read, write
from test_pipeline import synthetic


class FakeProcess:
    returncode = None

    def poll(self):
        return self.returncode


class ClusterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = setup(base / 'coordinador')
        self.helper_root = setup(base / 'ayudante')
        self.env = patch.dict(os.environ, {'KEPLER_EVIDENCE_DIR': str(base / 'escritorio')})
        self.env.start()
        self.manager = Manager(self.root, launcher=lambda *a, **k: FakeProcess())
        self.cluster = Cluster(self.manager, port=0, host='127.0.0.1')
        self.cluster.start()
        self.cluster.port = self.cluster.server.server_port
        self.url = f'http://127.0.0.1:{self.cluster.port}'
        # Config rápida para las pruebas en coordinador y ayudante por igual.
        from config import Config
        fast = Config(period_max=10, coarse_points=1000, refine_peaks=3, max_planets=2)
        self.config = patch('config.Config', side_effect=lambda **kw: fast)
        self.config.start()

    def tearDown(self):
        self.config.stop()
        self.cluster.stop()
        self.env.stop()
        close_logging()
        self.tmp.cleanup()

    def helper(self, name='PC-salon', helper='aaaaaaaa11111111', **kw):
        return ayudante.run_worker(self.url, self.cluster.token, helper, name, self.helper_root,
                                   idle_exit=True, **kw)

    def test_end_to_end_distributed_job(self):
        curve, _ = synthetic()
        save_curve(curve, self.helper_root / 'cache/lightcurves/KIC_00000123.npz')
        # El catálogo del coordinador se sincroniza al ayudante (y entra en la firma).
        pd.DataFrame([{'kepid': 123, 'kepoi_name': 'K00123.01', 'koi_period': 3.2,
                       'koi_disposition': 'CONFIRMED', 'koi_pdisposition': 'CANDIDATE'}]
                     ).to_csv(self.root / 'data/koi_dr25.csv', index=False)
        job = self.manager.create('123 124', distributed=True)
        # 124 no tiene curva en el ayudante (descarga simulada falla): dos intentos y fallida.
        with patch('kepler_detector.scanner.download', side_effect=lambda root, kic, *a, **k: (
                __import__('kepler_detector.mast', fromlist=['load_curve']).load_curve(
                    root / f'cache/lightcurves/KIC_{kic:08d}.npz') if kic == 123
                else (_ for _ in ()).throw(ValueError('MAST no encontró curvas')))):
            done = self.helper()
        self.assertEqual(done, 3)  # 123 + dos intentos de 124
        self.assertTrue((self.helper_root / 'data/koi_dr25.csv').exists())
        state = read(self.root / f'results/local_panel/{job}/state.json')
        self.assertEqual(state['stars'], {'123': 'complete', '124': 'failed'})
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['done_by']['123'], 'PC-salon')
        result = self.root / f'results/local_panel/{job}/workspace/results/KIC_123'
        frame = pd.read_csv(result / 'candidates.csv')
        self.assertTrue(frame.known_koi.any())
        self.assertTrue((result / 'candidate_01_folded.png').exists())
        # El panel puede servir los resultados y el resumen de triage como siempre.
        self.assertTrue(self.manager.result_path(job, 123, 'candidates.csv').exists())
        self.assertIn('triage', self.manager.details(job))
        helpers = self.cluster.details()['helpers']
        self.assertEqual(helpers[0]['completed'], 1)
        self.assertEqual(helpers[0]['failed'], 2)

    def test_bad_token_is_refused(self):
        with self.assertRaises(SystemExit):
            ayudante.run_worker(self.url, 'mal', 'aaaaaaaa11111111', 'x', self.helper_root, idle_exit=True)

    def test_lease_expires_and_star_returns_to_queue(self):
        job = self.manager.create('123', distributed=True)
        self.cluster.lease_seconds = 0.05
        task = self.cluster.claim('aaaaaaaa11111111', 'A')
        self.assertEqual(task['kic'], 123)
        self.assertIsNone(self.cluster.claim('bbbbbbbb22222222', 'B'))
        time.sleep(0.1)
        again = self.cluster.claim('bbbbbbbb22222222', 'B')
        self.assertEqual(again['kic'], 123)
        # El primer equipo ya no puede entregar: la estrella es de otro.
        with self.assertRaises(LookupError):
            self.cluster.fail('aaaaaaaa11111111', job, 123, 'tarde')

    def zip_of(self, files):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        return buffer.getvalue()

    def test_malicious_or_mismatched_results_are_rejected(self):
        job = self.manager.create('123', distributed=True)
        task = self.cluster.claim('aaaaaaaa11111111', 'A')
        good_meta = json.dumps({'status': 'complete', 'kic': 123, 'signature': task['signature']})
        cases = {
            'ruta': {'../../evil.py': 'x'},
            'extra': {'candidates.csv': 'kepid\n', 'metadata.json': good_meta, 'run.bat': 'x'},
            'firma': {'candidates.csv': 'kepid\n', 'metadata.json': good_meta.replace(task['signature'], 'otra')},
            'estrella': {'candidates.csv': 'kepid\n999\n', 'metadata.json': good_meta},
            'imagen': {'candidates.csv': 'kepid\n', 'metadata.json': good_meta, 'lightcurve.png': 'no-png'},
        }
        for name, files in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                self.cluster.submit('aaaaaaaa11111111', job, 123, self.zip_of(files))
        with self.assertRaises(ValueError):
            self.cluster.submit('aaaaaaaa11111111', job, 123, b'no es zip')
        self.assertEqual(read(self.root / f'results/local_panel/{job}/state.json')['stars']['123'], 'running')
        # Otro equipo no puede entregar la estrella ajena.
        with self.assertRaises(LookupError):
            self.cluster.submit('bbbbbbbb22222222', job, 123,
                                self.zip_of({'candidates.csv': 'kepid\n', 'metadata.json': good_meta}))
        self.cluster.submit('aaaaaaaa11111111', job, 123,
                            self.zip_of({'candidates.csv': 'kepid\n', 'metadata.json': good_meta}))
        self.assertEqual(read(self.root / f'results/local_panel/{job}/state.json')['status'], 'completed')

    def test_pause_stop_and_restart(self):
        job = self.manager.create('123 124', distributed=True)
        self.cluster.claim('aaaaaaaa11111111', 'A')
        self.manager.action(job, 'pause')
        self.assertIsNone(self.cluster.claim('bbbbbbbb22222222', 'B'))
        self.manager.action(job, 'stop')
        state = read(self.root / f'results/local_panel/{job}/state.json')
        self.assertEqual(set(state['stars'].values()), {'pending'})
        self.manager.action(job, 'resume')
        self.cluster.claim('aaaaaaaa11111111', 'A')
        # Reinicio del panel: lo que estaba en curso vuelve a la cola y el reparto queda en pausa.
        restarted = Manager(self.root, launcher=lambda *a, **k: FakeProcess())
        state = read(self.root / f'results/local_panel/{job}/state.json')
        self.assertEqual(state['status'], 'paused')
        self.assertNotIn('running', state['stars'].values())
        with self.assertRaises(ValueError):  # Sin modo red no se puede reanudar.
            restarted.action(job, 'resume')

    def test_distributed_requires_lan_and_does_not_block_local(self):
        plain = Manager(Path(self.tmp.name) / 'otro', launcher=lambda *a, **k: FakeProcess())
        with self.assertRaises(ValueError):
            plain.create('123', distributed=True)
        self.manager.create('123', distributed=True)
        self.manager.create('456')  # Un análisis local puede convivir con un reparto.

    def test_discovery_batches_can_be_distributed(self):
        batches = self.root / 'data/discovery_batches'
        batches.mkdir(parents=True)
        for number, start in ((1, 1), (2, 11)):
            rows = 'queue_rank,kepid\n' + ''.join(f'{r},{1000 + r}\n' for r in range(start, start + 10))
            (batches / f'batch_{number:02d}.csv').write_text(rows, encoding='utf-8')
        self.cluster.set_distribute_discovery(True)
        with patch('kepler_detector.catalog.replenish_discovery_batches', side_effect=OSError('sin red')):
            self.manager.start_discovery()
            first = [s for s in self.manager.snapshot() if s.get('batch_index') == 1][0]
            self.assertEqual(first['kind'], 'cluster')
            self.assertIsNone(self.manager.process)
            # Repetir "Automatizar" no duplica el lote en curso.
            self.manager.start_discovery()
            self.assertEqual(len([s for s in self.manager.snapshot() if s.get('batch_index') == 1]), 1)
            good = lambda kic, sig: self.zip_of({'candidates.csv': 'kepid\n', 'metadata.json': json.dumps(
                {'status': 'complete', 'kic': kic, 'signature': sig})})
            while (task := self.cluster.claim('aaaaaaaa11111111', 'A')) and task['job'] == first['id']:
                self.cluster.submit('aaaaaaaa11111111', task['job'], task['kic'], good(task['kic'], task['signature']))
            second = [s for s in self.manager.snapshot() if s.get('batch_index') == 2]
            self.assertEqual(len(second), 1)
            self.assertEqual(second[0]['kind'], 'cluster')


if __name__ == '__main__':
    unittest.main()
