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
from local_panel.cluster import Cluster, lan_addresses
from local_panel.jobs import Manager, read, write
from test_pipeline import synthetic


class FakeProcess:
    returncode = None

    def poll(self):
        return self.returncode


class ClusterBase(unittest.TestCase):
    def zip_of(self, files):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        return buffer.getvalue()

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


class ClusterTests(ClusterBase):

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
        self.assertEqual(state['done_by']['123'], f'{lan_addresses()[0] if lan_addresses() else "127.0.0.1"} (este PC)')
        self.assertEqual(self.cluster.details()['helpers'][0]['host'], 'PC-salon')
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

    def test_malicious_or_mismatched_results_are_rejected(self):
        job = self.manager.create('123', distributed=True)
        task = self.cluster.claim('aaaaaaaa11111111', 'A')
        good_meta = json.dumps({'status': 'complete', 'kic': 123, 'signature': task['signature']})
        cases = {
            'ruta': {'../../evil.py': 'x'},
            'extra': {'candidates.csv': 'kepid\n', 'metadata.json': good_meta, 'run.bat': 'x'},
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


class DistributedTrainingTests(ClusterBase):
    def test_distributed_training_builds_dataset_and_model(self):
        from local_panel import training
        from test_pipeline import synthetic as make
        rows = []
        for n in range(8):
            rows.append({'kepid': 1001 + n, 'kepoi_name': f'K0{1001 + n}.01', 'koi_period': 3.2,
                         'koi_disposition': 'CANDIDATE', 'koi_pdisposition': 'CANDIDATE',
                         'koi_time0bk': 120.7, 'koi_duration': 3.8})
            rows.append({'kepid': 2001 + n, 'kepoi_name': f'K0{2001 + n}.01', 'koi_period': 3.2,
                         'koi_disposition': 'FALSE POSITIVE', 'koi_pdisposition': 'FALSE POSITIVE',
                         'koi_time0bk': 120.7, 'koi_duration': 3.8})
        pd.DataFrame(rows).to_csv(self.root / 'data/koi_dr25.csv', index=False)
        for n in range(8):
            curve, _ = make(depth=0.003 + 0.0002 * n)
            save_curve(curve, self.helper_root / f'cache/lightcurves/KIC_{1001 + n:08d}.npz')
            curve, _ = make(depth=0.006, secondary=0.002 + 0.0001 * n)
            save_curve(curve, self.helper_root / f'cache/lightcurves/KIC_{2001 + n:08d}.npz')
        with self.manager.lock:
            job = training.create_job(self.manager, 5)
        state = read(self.root / f'results/local_panel/{job}/state.json')
        self.assertEqual(len(state['ids']), 12)  # 5 por clase + 15 % de margen
        self.assertIsNone(self.cluster.claim('cccccccc33333333', 'viejo'))  # sin 'build': nada
        missing = state['ids'][0]
        (self.helper_root / f'cache/lightcurves/KIC_{missing:08d}.npz').unlink()
        real_download = __import__('kepler_detector.mast', fromlist=['download']).download

        def offline(root, kic, *a, **k):
            path = root / f'cache/lightcurves/KIC_{int(kic):08d}.npz'
            if not path.exists():
                raise ValueError('MAST no encontró curvas')
            return real_download(root, kic, *a, **k)
        with patch('kepler_detector.builder.download', side_effect=offline):
            self.helper()
        for _ in range(240):
            state = read(self.root / f'results/local_panel/{job}/state.json')
            if state['status'] in ('completed', 'error'):
                break
            time.sleep(0.5)
        log = (self.root / f'results/local_panel/{job}/worker.log').read_text(encoding='utf-8')
        self.assertEqual(state['status'], 'completed', log[-3000:])
        self.assertEqual(state['stars'][str(missing)], 'failed')
        data = pd.read_csv(self.root / 'data/training_features.csv')
        self.assertEqual(sorted(data.label.value_counts().to_dict().items()), [(0, 5), (1, 5)])
        self.assertTrue((self.root / 'models/kepler_rf.joblib').exists())
        self.assertTrue((self.root / 'models/kepler_rf_metrics.json').exists())
        manifest = json.loads((self.root / 'data/build_manifest.json').read_text())
        self.assertEqual(manifest['row_count'], 10)
        # El modelo nuevo se ofrece a los ayudantes y cambia la firma esperada.
        self.assertIn('models/kepler_rf.joblib', self.cluster.info()['assets'])
        self.assertFalse(self.cluster.no_model())

    def test_result_with_old_models_goes_back_to_queue(self):
        job = self.manager.create('123', distributed=True)
        task = self.cluster.claim('aaaaaaaa11111111', 'A')
        stale = json.dumps({'status': 'complete', 'kic': 123, 'signature': 'firma-antigua'})
        with self.assertRaises(LookupError):
            self.cluster.submit('aaaaaaaa11111111', job, 123,
                                self.zip_of({'candidates.csv': 'kepid\n', 'metadata.json': stale}))
        self.assertEqual(read(self.root / f'results/local_panel/{job}/state.json')['stars']['123'], 'pending')


class TrainingResultTests(ClusterBase):
    def build_job(self):
        state = dict(id='abcdef0123456789', kind='cluster', task='build', mission='kepler', name='t', ids=[5],
                     stars={'5': 'pending'}, status='running', current=None, pid=None, error=None,
                     created=time.time(), build=dict(per_class=1, signals={'5': [['K1.01', 300.0, 0]]},
                                                     next_star=1, rounds=5))
        from local_panel.jobs import write as save
        save(self.root / 'results/local_panel/abcdef0123456789/state.json', state)
        return self.cluster.claim('aaaaaaaa11111111', 'A', caps=['build'])

    def test_nan_features_are_accepted(self):
        from kepler_detector.schema import RF_FEATURE_COLUMNS
        self.build_job()
        record = {'kepid': 5, 'kepoi_name': 'K1.01', 'label': 0,
                  **{c: float('nan') for c in RF_FEATURE_COLUMNS}, 'period_days': 300.0}
        with patch('local_panel.training.round_done', return_value=True):
            self.cluster.submit_build('aaaaaaaa11111111', 'abcdef0123456789', 5,
                                      [{'status': 'complete', 'record': record}])
        saved = read(self.root / 'results/local_panel/abcdef0123456789/build_results/5.json')
        self.assertIsNone(saved[0]['record']['odd_even_sigma'])
        self.assertEqual(saved[0]['record']['period_days'], 300.0)

    def test_invalid_result_counts_as_failure_not_loop(self):
        self.build_job()
        with self.assertRaises(LookupError):
            self.cluster.submit_build('aaaaaaaa11111111', 'abcdef0123456789', 5, [{'status': 'raro'}])
        state = read(self.root / 'results/local_panel/abcdef0123456789/state.json')
        self.assertEqual(state['stars']['5'], 'pending')
        self.assertEqual(state['attempts']['5'], 1)


class SelfUpdateTests(ClusterBase):
    def client(self):
        return ayudante.Client(self.url, self.cluster.token, 'aaaaaaaa11111111', 'PC')

    def test_helper_downloads_only_what_differs(self):
        import hashlib
        target = Path(self.tmp.name) / 'ayudante_codigo'
        changed = ayudante.sync_code(self.client(), target)
        self.assertEqual(set(changed), set(self.cluster.code_manifest))
        self.assertIn('ayudante.py', changed)
        self.assertNotIn('AYUDAR.bat', changed)          # Nunca se reescribe el .bat en ejecución
        self.assertFalse(any(name.startswith(('results', 'data', 'models')) for name in changed))
        for name, digest in self.cluster.code_manifest.items():
            self.assertEqual(hashlib.sha256((target / name).read_bytes()).hexdigest(), digest)
        self.assertEqual(ayudante.sync_code(self.client(), target), [])
        (target / 'config.py').write_text('# antiguo', encoding='utf-8')
        self.assertEqual(ayudante.sync_code(self.client(), target), ['config.py'])
        self.assertFalse(list(target.rglob('*.nuevo')))

    def test_unexpected_paths_and_corrupt_files_are_refused(self):
        target = Path(self.tmp.name) / 'ayudante_codigo'
        self.cluster.code_manifest['../fuera.py'] = 'x'
        with self.assertRaises(ConnectionError):
            ayudante.sync_code(self.client(), target)
        del self.cluster.code_manifest['../fuera.py']
        self.cluster.code_manifest['config.py'] = '0' * 64   # hash que no coincide con el contenido
        with self.assertRaises(ConnectionError):
            ayudante.sync_code(self.client(), target)
        self.assertFalse(target.exists() and any(target.rglob('*.py')))  # nada a medias

    def test_worker_asks_to_restart_when_coordinator_version_changes(self):
        job = self.manager.create('123', distributed=True)
        real_claim = self.cluster.claim

        def claim_then_upgrade(*args, **kwargs):
            task = real_claim(*args, **kwargs)
            self.cluster.code_version = 'versionnueva0000'  # el panel se reinició con código nuevo
            return task
        with patch.object(self.cluster, 'claim', side_effect=claim_then_upgrade):
            with self.assertRaises(ayudante.Restart):
                self.helper()
        # La estrella que le tocó vuelve a la cola, no se pierde.
        self.assertEqual(read(self.root / f'results/local_panel/{job}/state.json')['stars']['123'], 'pending')


class HelperNameTests(ClusterBase):
    def test_panel_shows_helper_ip_instead_of_hostname(self):
        self.manager.create('1 2 3', distributed=True)
        self.cluster.claim('aaaaaaaa11111111', 'DESKTOP-AILPCVR', '192.168.1.44')
        self.cluster.claim('bbbbbbbb22222222', 'DESKTOP-8CI7A80 #2', '192.168.1.51')
        names = {h['host']: h['name'] for h in self.cluster.details()['helpers']}
        self.assertEqual(names, {'DESKTOP-AILPCVR': '192.168.1.44', 'DESKTOP-8CI7A80 #2': '192.168.1.51 #2'})
        log = (self.root / 'results/local_panel').glob('*/worker.log')
        self.assertIn('192.168.1.44: INICIO KIC', next(log).read_text(encoding='utf-8'))
