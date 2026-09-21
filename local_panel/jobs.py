"""Durable jobs and explicitly owned subprocesses for the local panel."""
import csv
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
        self.import_blind()
        for path in self.folder.glob('*/state.json'):
            state = read(path)
            if state['status'] in ACTIVE:
                if alive(state.get('pid')):
                    raise RuntimeError('Hay un trabajador anterior activo. No se iniciará un segundo panel.')
                state.update(status='paused', current=None, error='Sesión interrumpida; continúa explícitamente.')
                write(path, state)

    def jobdir(self, job):
        if not re.fullmatch(r'blind|[a-f0-9]{16}', job):
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
            if state['status'] in ACTIVE:
                state.update(status='error', error=f'Trabajador finalizado con código {self.process.returncode}', current=None)
                write(path, state)
            self.log_stream.close()
            self.process = self.active = self.log_stream = None

    def snapshot(self):
        with self.lock:
            self.refresh()
            return sorted([read(p) for p in self.folder.glob('*/state.json')], key=lambda s: s['created'])

    def create(self, value):
        ids = parse_kics(value)
        with self.lock:
            self.refresh()
            if self.process:
                raise ValueError('Ya hay un análisis activo; páusalo o detenlo primero.')
            job = secrets.token_hex(8)
            write(self.folder / job / 'state.json', dict(id=job, kind='scan', name=f'Análisis de {len(ids)} estrella(s)',
                  ids=ids, stars={str(k): 'pending' for k in ids}, status='paused', current=None,
                  pid=None, error=None, created=time.time()))
            self.action(job, 'resume')
            return job

    def action(self, job, action):
        with self.lock:
            self.refresh()
            folder = self.jobdir(job)
            state = read(folder / 'state.json')
            if action == 'resume':
                if self.process or state['status'] in ACTIVE or state['status'] == 'completed':
                    raise ValueError('No se puede iniciar este trabajo en su estado actual.')
                write(folder / 'command.json', {'action': 'run'})
                state.update(status='running', error=None, pid=os.getpid())
                write(folder / 'state.json', state)
                self.log_stream = (folder / 'worker.log').open('ab', buffering=0)
                try:
                    self.process = self.launcher([sys.executable, '-u', '-m', 'local_panel.worker',
                          str(self.root), job, str(os.getpid())], cwd=str(self.root), stdout=self.log_stream, stderr=subprocess.STDOUT,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                          start_new_session=os.name != 'nt')
                except Exception:
                    self.log_stream.close()
                    state.update(status='error', error='No se pudo iniciar el trabajador')
                    write(folder / 'state.json', state)
                    raise
                self.active = job
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

    def close(self):
        with self.lock:
            self.refresh()
            if self.process:
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
        return state

    def result_path(self, job, kic, filename):
        state = read(self.jobdir(job) / 'state.json')
        if str(kic) not in state['stars'] or state['stars'][str(kic)] != 'complete':
            raise ValueError('Resultados aún no disponibles')
        if filename not in {'candidates.csv', 'detections.csv', 'metadata.json'} and not re.fullmatch(r'candidate_[0-9]+_folded\.png', filename):
            raise ValueError('Archivo no permitido')
        base = self.root / 'results/blind_evaluation/blind_workspace' if job == 'blind' else self.jobdir(job) / 'workspace'
        path = base / f'results/KIC_{int(kic)}' / filename
        if not path.resolve().is_relative_to(base.resolve()):
            raise ValueError('Ruta no permitida')
        return path
