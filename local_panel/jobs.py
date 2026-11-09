"""Durable jobs and explicitly owned subprocesses for the local panel."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time


ACTIVE = {'running', 'pausing', 'stopping'}


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    for attempt in range(20):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.05)


def alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # fail closed on access denied
        try:
            code = wintypes.DWORD()
            return not api.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def stop_process(process):
    """Only terminate a subprocess created and retained by this manager."""
    if process.poll() is not None:
        return
    if os.name == 'nt':
        result = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode and process.poll() is None:
            raise RuntimeError('No se pudo detener el proceso: ' + result.stderr.decode(errors='replace'))
    else:
        import signal
        os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=20)


def parse_kics(value):
    if not isinstance(value, str) or len(value) > 10000:
        raise ValueError('Introduce una lista de KIC.')
    items = re.split(r'[\s,;]+', value.strip())
    if not items or any(not re.fullmatch(r'[0-9]{1,9}', item) or int(item) <= 0 for item in items):
        raise ValueError('Los KIC deben ser enteros positivos separados por comas o espacios.')
    ids = list(dict.fromkeys(map(int, items)))
    if len(ids) > 200:
        raise ValueError('Máximo 200 estrellas por cola.')
    return ids


class Manager:
    def __init__(self, root, launcher=None):
        self.root = Path(root).resolve()
        self.folder = self.root / 'results/local_panel'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.process = None
        self.active = None
        self.log_stream = None
        self.launcher = launcher or subprocess.Popen
        self.cluster = None  # local_panel.cluster.Cluster cuando el panel se abre con --lan
        self.import_blind()
        self.ensure_cnn_state()
        for path in self.folder.glob('*/state.json'):
            state = read(path)
            if state.get('kind') == 'cluster':
                # Los alquileres de ayudantes no sobreviven al reinicio: devolver a la cola.
                changed = False
                for kic, value in state['stars'].items():
                    if value == 'running':
                        state['stars'][kic] = 'pending'
                        changed = True
                if state['status'] in ('running', 'training'):
                    state.update(status='paused', error='Panel reiniciado; reanuda el reparto explícitamente.')
                    changed = True
                state.pop('assigned', None)
                state['current'] = None
                if changed:
                    write(path, state)
                continue
            if state['status'] in ACTIVE:
                if alive(state.get('pid')):
                    raise RuntimeError('Hay un trabajador anterior activo. No se iniciará un segundo panel.')
                state.update(status='paused', current=None, error='Sesión interrumpida; continúa explícitamente.')
                write(path, state)

        automation = read(self.folder / 'discovery_automation.json')
        if automation and automation.get('enabled'):
            automation.update(enabled=False, status='paused',
                              note='Panel reiniciado; reactiva la automatizacion explicitamente.')
            write(self.folder / 'discovery_automation.json', automation)

    def ensure_cnn_state(self):
        path = self.folder / 'cnn/state.json'
        if not path.exists():
            write(path, dict(
                id='cnn', kind='cnn', name='ExoMiner-lite', status='ready',
                operation=None, pid=None, error=None, created=time.time(),
                started=None, finished=None,
            ))

    def jobdir(self, job):
        if not re.fullmatch(r'blind|cnn|[a-f0-9]{16}', job):
            raise ValueError('Trabajo inválido')
        path = self.folder / job
        if not (path / 'state.json').is_file():
            raise ValueError('Trabajo inexistente')
        return path

    def import_blind(self):
        out = self.root / 'results/blind_evaluation'
        target = self.folder / 'blind/state.json'
        if target.exists() or not (out / 'cohort_stars.csv').exists():
            return
        with (out / 'cohort_stars.csv').open(encoding='utf-8', newline='') as f:
            ids = [int(row['kepid']) for row in csv.DictReader(f)]
        statuses = {}
        for kic in ids + [10854555]:
            attempt = read(out / f'attempts/{kic}.json', {})
            status = attempt.get('status', 'pending')
            if status == 'running':
                meta = read(out / f'blind_workspace/results/KIC_{kic}/metadata.json', {})
                status = 'complete' if meta.get('status') == 'complete' else 'interrupted'
            statuses[str(kic)] = status
        write(target, dict(id='blind', kind='blind', name='Evaluación ciega · 92 estrellas / 123 KOI',
              ids=ids, control=10854555, stars=statuses, status='completed' if (out / 'summary.json').exists() else 'paused',
              current=None, pid=None, error=None, created=time.time()))

    def refresh(self):
        if self.process is not None and self.process.poll() is not None:
            path = self.jobdir(self.active) / 'state.json'
            state = read(path)
            if state['status'] in ACTIVE and state.get('kind') == 'cnn' and self.process.returncode == 0:
                state.update(status='completed', error=None, finished=time.time(), pid=None)
                write(path, state)
            elif state['status'] in ACTIVE:
                state['pid'] = None
                state.update(status='error', error=f'Trabajador finalizado con código {self.process.returncode}', current=None)
                write(path, state)
            if state.get('batch_index'):
                from kepler_detector.catalog import update_discovery_batch_status
                completed = sum(value == 'complete' for value in state.get('stars', {}).values())
                update_discovery_batch_status(self.root, state['batch_index'], state['status'], completed)
            self.log_stream.close()
            self.process = self.active = self.log_stream = None

        self._advance_discovery()

    def discovery_details(self):
        state = read(self.folder / 'discovery_automation.json', {})
        return {
            'enabled': bool(state.get('enabled')),
            'status': state.get('status', 'ready'),
            'next_batch': state.get('next_batch', 1),
            'total_batches': state.get('total_batches', 25),
            'waiting_job': state.get('waiting_job'),
            'note': state.get('note'),
            'prepared_targets': self._csv_rows(self.root / 'data/kepler_discovery_targets.csv'),
        }

    def _batch_files(self):
        files = sorted((self.root / 'data/discovery_batches').glob('batch_*.csv'))
        if not files:
            raise ValueError('No existen lotes de descubrimiento. Genera discovery-batches primero.')
        return files

    @staticmethod
    def _batch_ids(path):
        with Path(path).open(encoding='utf-8', newline='') as stream:
            rows = list(csv.DictReader(stream))
        ids = [int(row['kepid']) for row in rows]
        if not ids or len(ids) > 10 or len(ids) != len(set(ids)):
            raise ValueError(f'Lote invalido: {Path(path).name}')
        return ids

    def start_discovery(self):
        with self.lock:
            self.refresh()
            files = self._batch_files()
            waiting = self.active if self.process and self.active != 'cnn' else None
            if waiting is None:  # Un lote repartido en curso cuenta como análisis activo.
                for path in self.folder.glob('*/state.json'):
                    other = read(path, {})
                    if other.get('kind') == 'cluster' and other.get('batch_index') and \
                            other.get('status') in ('running', 'paused'):
                        waiting = other['id']
            next_batch = 1
            if waiting:
                current = read(self.jobdir(waiting) / 'state.json')
                for index, path in enumerate(files, 1):
                    if current.get('ids') == self._batch_ids(path):
                        next_batch = index + 1
                        break
                else:
                    raise ValueError('El analisis activo no coincide con un lote de descubrimiento.')
            else:
                handled = set()
                for path in self.folder.glob('*/state.json'):
                    state = read(path, {})
                    for kic, status in state.get('stars', {}).items():
                        if status in ('complete', 'failed'):
                            handled.add(int(kic))
                for index, path in enumerate(files, 1):
                    if not set(self._batch_ids(path)).issubset(handled):
                        next_batch = index
                        break
                else:
                    next_batch = len(files) + 1
            automation = dict(enabled=True, status='scheduled' if waiting else 'running',
                              next_batch=next_batch, total_batches=len(files),
                              waiting_job=waiting, created=time.time(), note=None)
            write(self.folder / 'discovery_automation.json', automation)
            self._advance_discovery()
            return self.discovery_details()

    def stop_discovery(self):
        with self.lock:
            state = read(self.folder / 'discovery_automation.json')
            if not state or not state.get('enabled'):
                raise ValueError('La automatizacion no esta activa.')
            state.update(enabled=False, status='paused',
                         note='Secuencia automatica desactivada; el lote actual no se interrumpe.')
            write(self.folder / 'discovery_automation.json', state)

    def replenish_discovery(self):
        with self.lock:
            from kepler_detector.catalog import replenish_discovery_batches
            result = replenish_discovery_batches(self.root, block_size=250, batch_size=10)
            state = read(self.folder / 'discovery_automation.json', {})
            state.update(total_batches=result['total_batches'],
                         note=f"Se añadieron 25 lotes; {result['total_targets']} estrellas preparadas.")
            write(self.folder / 'discovery_automation.json', state)
            return {**result, **self.discovery_details()}

    def _advance_discovery(self):
        automation = read(self.folder / 'discovery_automation.json')
        if not automation or not automation.get('enabled') or automation.get('status') == 'launching':
            return
        waiting = automation.get('waiting_job')
        if waiting:
            state = read(self.folder / waiting / 'state.json', {})
            if state.get('status') not in ('completed', 'error'):
                automation['status'] = 'running' if state.get('status') == 'running' else 'paused'
                write(self.folder / 'discovery_automation.json', automation)
                return
            automation['waiting_job'] = None
            write(self.folder / 'discovery_automation.json', automation)
        if self.process:
            return
        files = self._batch_files()
        number = int(automation.get('next_batch', 1))
        if len(files) - number + 1 <= 25:
            from kepler_detector.catalog import replenish_discovery_batches
            try:
                replenish_discovery_batches(self.root, block_size=250, batch_size=10)
                files = self._batch_files()
                automation['total_batches'] = len(files)
                automation['note'] = 'Reserva ampliada automaticamente con 25 lotes nuevos.'
            except Exception as error:  # red caida o archivo NASA no disponible: seguir con los lotes que ya hay
                automation['note'] = f'No se pudo ampliar la reserva: {error}'
            write(self.folder / 'discovery_automation.json', automation)
        if number > len(files):
            automation.update(enabled=False, status='completed', waiting_job=None,
                              note='Todos los lotes de descubrimiento han terminado.')
            write(self.folder / 'discovery_automation.json', automation)
            return
        ids = self._batch_ids(files[number - 1])
        job = secrets.token_hex(8)
        state = dict(id=job, kind='discovery',
                     name=f'Descubrimiento - lote {number:02d}/{len(files):02d}',
                     ids=ids, stars={str(k): 'pending' for k in ids}, status='paused',
                     current=None, pid=None, error=None, created=time.time(),
                     batch_index=number, batch_total=len(files))
        distributed = bool(self.cluster and self.cluster.server and
                           self.cluster.settings.get('distribute_discovery'))
        if distributed:
            state.update(kind='cluster', mission='kepler', status='running',
                         name=f'Descubrimiento repartido - lote {number:02d}/{len(files):02d}')
        write(self.folder / job / 'state.json', state)
        automation.update(status='launching', waiting_job=job, next_batch=number + 1)
        write(self.folder / 'discovery_automation.json', automation)
        if not distributed:
            self._launch(job, state)
        from kepler_detector.catalog import update_discovery_batch_status
        update_discovery_batch_status(self.root, number, 'running', 0)
        automation['status'] = 'running'
        write(self.folder / 'discovery_automation.json', automation)

    def snapshot(self):
        with self.lock:
            self.refresh()
            return sorted(
                [read(p) for p in self.folder.glob('*/state.json') if p.parent.name != 'cnn'],
                key=lambda s: s['created'],
            )

    def cnn_details(self):
        with self.lock:
            self.refresh()
            state = read(self.folder / 'cnn/state.json')
            training = self.root / 'data/training_features.csv'
            index = self.root / 'data/cnn_view_index.csv'
            model = self.root / 'models/kepler_exominer_lite.keras'
            metrics_path = self.root / 'models/kepler_exominer_lite_metrics.json'
            state['artifacts'] = {
                'training_features': training.exists(),
                'view_index': index.exists(),
                'model': model.exists(),
                'tensorflow': importlib.util.find_spec('tensorflow') is not None,
            }
            state['training_rows'] = self._csv_rows(training)
            state['view_rows'] = self._csv_rows(index)
            state['metrics'] = read(metrics_path)
            log = self.folder / 'cnn/worker.log'
            if log.exists():
                with log.open('rb') as stream:
                    stream.seek(max(0, log.stat().st_size - 16000))
                    state['log'] = stream.read(16000).decode('utf-8', errors='replace')
            else:
                state['log'] = ''
            return state

    @staticmethod
    def _csv_rows(path):
        if not path.exists():
            return 0
        with path.open('rb') as stream:
            return max(0, sum(1 for _ in stream) - 1)

    def start_cnn(self, operation, options=None):
        commands = {'build': 'cnn-build', 'train': 'cnn-train', 'evaluate': 'cnn-evaluate'}
        if operation not in commands:
            raise ValueError('Operación CNN desconocida')
        options = options or {}
        if not isinstance(options, dict):
            raise ValueError('Opciones CNN inválidas')
        with self.lock:
            self.refresh()
            if self.process:
                raise ValueError('Ya hay un trabajo activo; páusalo o detenlo primero.')
            args = [sys.executable, '-u', '-m', 'local_panel.cnn_worker', str(os.getpid()),
                    commands[operation], '--root', str(self.root)]
            if operation == 'build':
                limit = options.get('limit')
                if limit not in (None, ''):
                    if not isinstance(limit, int) or not 1 <= limit <= 100000:
                        raise ValueError('El límite debe ser un entero entre 1 y 100000.')
                    args += ['--limit', str(limit)]
                if options.get('force') is True:
                    args.append('--force')
            elif operation == 'train':
                seed = options.get('seed', 42)
                epochs = options.get('epochs', 60)
                batch = options.get('batch_size', 32)
                test_size = options.get('test_size', 0.25)
                if not isinstance(seed, int) or not isinstance(epochs, int) or not 1 <= epochs <= 1000:
                    raise ValueError('Seed o número de epochs inválido.')
                if not isinstance(batch, int) or not 1 <= batch <= 4096:
                    raise ValueError('Batch size inválido.')
                if not isinstance(test_size, (int, float)) or not 0.05 <= test_size <= 0.5:
                    raise ValueError('Test size debe estar entre 0.05 y 0.5.')
                args += ['--seed', str(seed), '--epochs', str(epochs),
                         '--batch-size', str(batch), '--test-size', str(test_size)]
            folder = self.folder / 'cnn'
            state = read(folder / 'state.json')
            state.update(status='running', operation=operation, error=None,
                         started=time.time(), finished=None)
            write(folder / 'state.json', state)
            self.log_stream = (folder / 'worker.log').open('ab', buffering=0)
            try:
                self.process = self.launcher(
                    args, cwd=str(self.root), stdout=self.log_stream, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                    start_new_session=os.name != 'nt',
                )
            except Exception:
                self.log_stream.close()
                self.log_stream = None
                state.update(status='error', error='No se pudo iniciar ExoMiner-lite')
                write(folder / 'state.json', state)
                raise
            self.active = 'cnn'
            state['pid'] = self.process.pid
            write(folder / 'state.json', state)

    def stop_cnn(self):
        with self.lock:
            self.refresh()
            if self.active != 'cnn' or not self.process:
                raise ValueError('ExoMiner-lite no está en ejecución.')
            stop_process(self.process)
            state = read(self.folder / 'cnn/state.json')
            state.update(status='stopped', pid=None, finished=time.time())
            write(self.folder / 'cnn/state.json', state)
            self.refresh()

    def create(self, value, distributed=False):
        ids = parse_kics(value)
        with self.lock:
            self.refresh()
            if distributed:
                if not self.cluster or not self.cluster.server:
                    raise ValueError('Abre el panel en modo red (ABRIR_PANEL_RED.bat) para repartir.')
                job = secrets.token_hex(8)
                write(self.folder / job / 'state.json', dict(
                    id=job, kind='cluster', name=f'Reparto de {len(ids)} estrella(s)', mission='kepler',
                    ids=ids, stars={str(k): 'pending' for k in ids}, status='running', current=None,
                    pid=None, error=None, created=time.time()))
                return job
            if self.process:
                raise ValueError('Ya hay un análisis activo; páusalo o detenlo primero.')
            job = secrets.token_hex(8)
            write(self.folder / job / 'state.json', dict(id=job, kind='scan', name=f'Análisis de {len(ids)} estrella(s)',
                  ids=ids, stars={str(k): 'pending' for k in ids}, status='paused', current=None,
                  pid=None, error=None, created=time.time()))
            self.action(job, 'resume')
            return job

    def _launch(self, job, state):
        write(self.folder / job / 'command.json', {'action': 'run'})
        state.update(status='running', error=None, pid=os.getpid())
        write(self.folder / job / 'state.json', state)
        self.log_stream = (self.folder / job / 'worker.log').open('ab', buffering=0)
        try:
            self.process = self.launcher([sys.executable, '-u', '-m', 'local_panel.worker',
                  str(self.root), job, str(os.getpid())], cwd=str(self.root), stdout=self.log_stream, stderr=subprocess.STDOUT,
                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                  start_new_session=os.name != 'nt')
        except Exception:
            self.log_stream.close()
            self.log_stream = None
            state.update(status='error', error='No se pudo iniciar el trabajador')
            write(self.folder / job / 'state.json', state)
            raise
        self.active = job

    def action(self, job, action):
        with self.lock:
            self.refresh()
            folder = self.jobdir(job)
            state = read(folder / 'state.json')
            if state.get('kind') == 'cluster':
                if not self.cluster or (action == 'resume' and not self.cluster.server):
                    raise ValueError('Abre el panel en modo red (ABRIR_PANEL_RED.bat) para gestionar repartos.')
                return self.cluster.action(job, action)
            if action == 'resume':
                if self.process or state['status'] in ACTIVE or state['status'] == 'completed':
                    raise ValueError('No se puede iniciar este trabajo en su estado actual.')
                self._launch(job, state)
            elif action == 'pause':
                if self.active != job or not self.process:
                    raise ValueError('Este análisis no está en ejecución.')
                write(folder / 'command.json', {'action': 'pause'})
                # Worker is the sole state writer while running, avoiding lost updates.
            elif action == 'stop':
                if self.active != job or not self.process:
                    raise ValueError('Este análisis no está en ejecución.')
                write(folder / 'command.json', {'action': 'stop'})
                stop_process(self.process)
                state = read(folder / 'state.json')
                if state.get('current'):
                    state['stars'][str(state['current'])] = 'interrupted'
                state.update(status='stopped', current=None, pid=None)
                write(folder / 'state.json', state)
                self.refresh()
            else:
                raise ValueError('Acción desconocida')

    def cluster_job_finished(self, state):
        """Llamado por el coordinador (con el cerrojo tomado) al terminar un reparto."""
        if state.get('batch_index'):
            from kepler_detector.catalog import update_discovery_batch_status
            completed = sum(value == 'complete' for value in state.get('stars', {}).values())
            update_discovery_batch_status(self.root, state['batch_index'], state['status'], completed)
        self._advance_discovery()

    def close(self):
        with self.lock:
            self.refresh()
            if self.process:
                if self.active == 'cnn':
                    self.stop_cnn()
                else:
                    self.action(self.active, 'stop')

    def details(self, job):
        folder = self.jobdir(job)
        state = read(folder / 'state.json')
        state['requested_action'] = read(folder / 'command.json', {}).get('action')
        log = folder / 'worker.log'
        if log.exists():
            with log.open('rb') as f:
                f.seek(max(0, log.stat().st_size - 16000))
                state['log'] = f.read(16000).decode('utf-8', errors='replace')
        else:
            state['log'] = ''
        state['triage'] = self._triage_summary(job, state)
        return state

    def _triage_summary(self, job, state):
        from kepler_detector.triage import assess_candidate
        base = (self.root / 'results/blind_evaluation/blind_workspace' if job == 'blind'
                else self.folder / job / 'workspace')
        serious = []
        review = []
        for kic, status in state.get('stars', {}).items():
            if status != 'complete':
                continue
            path = base / f'results/KIC_{int(kic)}' / 'candidates.csv'
            if not path.exists():
                continue
            with path.open(encoding='utf-8', newline='') as stream:
                for row in csv.DictReader(stream):
                    assessment = assess_candidate(row)
                    item = {'kic': int(kic), 'rank': int(float(row.get('rank', 0))),
                            'period_days': float(row.get('period_days', 0)),
                            'reason': assessment['triage_reason']}
                    if assessment['triage_serious']:
                        serious.append(item)
                    elif assessment['triage_review']:
                        review.append(item)
        return {'serious_count': len(serious), 'review_count': len(review),
                'serious': serious, 'review': review}

    def result_path(self, job, kic, filename):
        state = read(self.jobdir(job) / 'state.json')
        if str(kic) not in state['stars'] or state['stars'][str(kic)] != 'complete':
            raise ValueError('Resultados aún no disponibles')
        if filename not in {'candidates.csv', 'detections.csv', 'metadata.json', 'lightcurve.png',
                            'detrended_lightcurve.png', 'bls_periodogram.png'} and \
                not re.fullmatch(r'candidate_[0-9]+_folded\.png', filename):
            raise ValueError('Archivo no permitido')
        base = self.root / 'results/blind_evaluation/blind_workspace' if job == 'blind' else self.jobdir(job) / 'workspace'
        path = base / f'results/KIC_{int(kic)}' / filename
        if not path.resolve().is_relative_to(base.resolve()):
            raise ValueError('Ruta no permitida')
        return path
