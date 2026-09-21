import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from local_panel.jobs import Manager, alive, parse_kics, read, stop_process, write
from local_panel.worker import run, prepare_scan, claim_evaluation
from panel_local import make_server


class FakeProcess:
    pid = 99999999
    returncode = None
    def poll(self):
        return self.returncode


class LocalPanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.proc = FakeProcess()
        self.manager = Manager(self.root, launcher=lambda *a, **k: self.proc)

    def tearDown(self):
        if self.manager.log_stream:
            self.manager.log_stream.close()
        self.tmp.cleanup()

    def create(self):
        return self.manager.create('123,456')

    def test_kic_validation_and_dedup(self):
        self.assertEqual(parse_kics('123, 123;456'), [123, 456])
        for bad in ['', '123 & whoami', '../1', '0', None, '1.2']:
            with self.assertRaises(ValueError):
                parse_kics(bad)

    def test_single_active_job_pause_request_and_stop(self):
        job = self.create()
        with self.assertRaises(ValueError):
            self.create()
        self.manager.action(job, 'pause')
        self.assertEqual(read(self.manager.jobdir(job) / 'command.json')['action'], 'pause')
        def fake_stop(proc):
            proc.returncode = 1
        with patch('local_panel.jobs.stop_process', side_effect=fake_stop):
            self.manager.action(job, 'stop')
        self.assertEqual(self.manager.snapshot()[0]['status'], 'stopped')

    def test_worker_pause_at_boundary_resume_skips_complete(self):
        job = self.create()
        folder = self.manager.jobdir(job)
        seen = []
        def scan(work, kic, config):
            seen.append(kic)
            write(folder / 'command.json', {'action': 'pause'})
        with patch('kepler_detector.scanner.scan', side_effect=scan):
            run(self.root, job)
        self.assertEqual(seen, [123])
        self.assertEqual(read(folder / 'state.json')['status'], 'paused')
        write(folder / 'command.json', {'action': 'run'})
        with patch('kepler_detector.scanner.scan', side_effect=lambda w,k,c: seen.append(k)):
            run(self.root, job)
        self.assertEqual(seen, [123, 456])
        self.assertEqual(read(folder / 'state.json')['status'], 'completed')

    def test_interrupted_output_archived_before_resume(self):
        job = self.create()
        folder = self.manager.jobdir(job)
        state = read(folder / 'state.json')
        state['stars']['123'] = 'interrupted'
        write(folder / 'state.json', state)
        partial = folder / 'workspace/results/KIC_123/partial.txt'
        partial.parent.mkdir(parents=True)
        partial.write_text('preserved')
        with patch('kepler_detector.scanner.scan'):
            run(self.root, job)
        self.assertEqual(len(list((folder / 'history').glob('*/KIC_123/partial.txt'))), 1)

    def test_restart_paused_not_automatic(self):
        job = self.create()
        path = self.manager.jobdir(job) / 'state.json'
        state = read(path)
        state['pid'] = 99999999
        write(path, state)
        manager = Manager(self.root)
        self.assertEqual(manager.snapshot()[0]['status'], 'paused')
        self.assertIsNone(manager.process)

    def test_cache_is_copied_not_removed(self):
        path = self.root / 'cache/lightcurves/KIC_00000123.npz'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'cached curve')
        cnn_model = self.root / 'models/kepler_exominer_lite.keras'
        cnn_model.parent.mkdir(parents=True)
        cnn_model.write_bytes(b'cnn')
        cnn_metadata = self.root / 'models/kepler_exominer_lite_metadata.json'
        cnn_metadata.write_text('{}')
        prepare_scan(self.root, self.root / 'isolated', [123])
        self.assertEqual(path.read_bytes(), b'cached curve')
        self.assertEqual((self.root / 'isolated/cache/lightcurves/KIC_00000123.npz').read_bytes(), path.read_bytes())
        self.assertEqual((self.root / 'isolated/models/kepler_exominer_lite.keras').read_bytes(), b'cnn')
        self.assertTrue((self.root / 'isolated/models/kepler_exominer_lite_metadata.json').exists())

    def test_blind_import_preserves_attempt(self):
        out = self.root / 'results/blind_evaluation'
        out.mkdir()
        (out / 'cohort_stars.csv').write_text('kepid,number_known_koi\n123,1\n456,1\n')
        write(out / 'attempts/123.json', {'status': 'complete'})
        write(out / 'attempts/456.json', {'status': 'running'})
        before = (out / 'attempts/456.json').read_bytes()
        manager = Manager(self.root)
        state = manager.snapshot()[0]
        self.assertEqual(state['status'], 'paused')
        self.assertEqual(state['stars']['123'], 'complete')
        self.assertEqual(state['stars']['456'], 'interrupted')
        self.assertEqual((out / 'attempts/456.json').read_bytes(), before)

    def test_live_evaluation_lock_rejected(self):
        (self.root / 'evaluation.lock').write_text(str(os.getpid()))
        with self.assertRaises(ValueError):
            claim_evaluation(self.root, self.root / 'job')

    def test_path_traversal_and_running_results_rejected(self):
        job = self.create()
        with self.assertRaises(ValueError):
            self.manager.jobdir('../models')
        with self.assertRaises(ValueError):
            self.manager.result_path(job, '123', 'candidates.csv')

    def test_cnn_state_launch_and_successful_completion(self):
        details = self.manager.cnn_details()
        self.assertEqual(details['status'], 'ready')
        self.assertIn('tensorflow', details['artifacts'])
        captured = {}
        def launch(args, **kwargs):
            captured['args'] = args
            captured['kwargs'] = kwargs
            return self.proc
        self.manager.launcher = launch
        self.manager.start_cnn('train', {'epochs': 3, 'batch_size': 8, 'test_size': 0.2, 'seed': 7})
        self.assertIn('cnn-train', captured['args'])
        self.assertEqual(captured['args'][captured['args'].index('--epochs') + 1], '3')
        with self.assertRaises(ValueError):
            self.manager.create('789')
        self.proc.returncode = 0
        self.assertEqual(self.manager.cnn_details()['status'], 'completed')

    def test_discovery_automation_advances_in_ten_star_batches(self):
        batches = self.root / 'data/discovery_batches'
        batches.mkdir(parents=True)
        for number, start in ((1, 1), (2, 11)):
            rows = 'queue_rank,kepid\n' + ''.join(
                f'{rank},{1000 + rank}\n' for rank in range(start, start + 10))
            (batches / f'batch_{number:02d}.csv').write_text(rows, encoding='utf-8')

        automation = self.manager.start_discovery()
        first = self.manager.active
        first_state = read(self.manager.jobdir(first) / 'state.json')
        self.assertTrue(automation['enabled'])
        self.assertEqual(first_state['batch_index'], 1)
        self.assertEqual(len(first_state['ids']), 10)

        first_state['status'] = 'completed'
        write(self.manager.jobdir(first) / 'state.json', first_state)
        self.proc.returncode = 0
        self.manager.snapshot()
        second_state = read(self.manager.jobdir(self.manager.active) / 'state.json')
        self.assertNotEqual(self.manager.active, first)
        self.assertEqual(second_state['batch_index'], 2)

    def test_http_csrf_host_origin_and_static(self):
        server = make_server(self.root, 0, self.manager)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port
        def request(method, path, data=None, headers=None):
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
            conn.request(method, path, json.dumps(data) if data is not None else None, headers or {})
            response = conn.getresponse()
            value = response.read()
            status = response.status
            conn.close()
            return status, json.loads(value)
        try:
            status, state = request('GET', '/api/state')
            self.assertEqual(status, 200)
            self.assertEqual(request('GET', '/api/cnn')[0], 200)
            self.assertEqual(request('GET','/api/state',headers={'Host':'evil.example'})[0],403)
            self.assertEqual(request('POST','/api/jobs',{'kics':'123'})[0],403)
            headers = {'Content-Type':'application/json', 'X-Panel-Token':state['token']}
            self.assertEqual(request('POST','/api/jobs',{'kics':'123'}, {**headers,'Origin':'https://evil.example'})[0],403)
            self.assertEqual(request('POST','/api/cnn/start',{'operation':'invalid'},headers)[0],400)
            self.assertEqual(request('POST','/api/jobs',{'kics':'123'},headers)[0],201)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_stop_owned_process_tree(self):
        # Only synthetic sleeping processes; no scientific scanner is invoked.
        code = 'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);print(p.pid,flush=True);time.sleep(60)'
        proc = subprocess.Popen([sys.executable, '-u', '-c', code], stdout=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            start_new_session=os.name != 'nt')
        child = int(proc.stdout.readline())
        try:
            stop_process(proc)
            for _ in range(30):
                if not alive(child):
                    break
                time.sleep(.1)
            self.assertFalse(alive(child))
        finally:
            if proc.poll() is None:
                stop_process(proc)
            proc.stdout.close()


if __name__ == '__main__':
    unittest.main()
