"""Reparto de estrellas entre equipos de la red local (protocolo v1).

El panel normal sigue siendo sólo local. Este módulo abre un servidor aparte, sólo si se
pide (--lan), protegido por token. Los ayudantes piden una estrella, la analizan con el
mismo código, configuración y modelos, y devuelven sus resultados. El coordinador valida
cada envío antes de aceptarlo. Protocolo pensado para crecer después a internet
(HTTPS + un token por voluntario + validación cruzada)."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
from pathlib import Path
import json
import re
import secrets
import shutil
import socket
import threading
import time
import zipfile
from urllib.parse import urlsplit

from local_panel.jobs import read, write

PROTOCOL = 1
DEFAULT_PORT = 8769
LEASE_SECONDS = 600          # Sin latido en 10 min: la estrella vuelve a la cola.
MAX_ATTEMPTS = 2             # Fallos en ayudantes distintos antes de marcarla fallida.
MAX_UPLOAD = 30 * 1024 * 1024
MAX_UNPACKED = 80 * 1024 * 1024
HELPER_TTL = 120             # Ayudante "conectado" si habló en los últimos 2 min.
ASSETS = ('models/kepler_rf.joblib', 'models/tess_rf.joblib', 'models/kepler_exominer_lite.keras',
          'models/kepler_exominer_lite_metadata.json', 'data/koi_dr25.csv', 'data/tce_dr25.csv',
          'data/tess_toi.csv')
RESULT_FILES = re.compile(r'(candidates|detections)\.csv|metadata\.json|'
                          r'(lightcurve|detrended_lightcurve|bls_periodogram|candidate_[0-9]{2}_folded)\.png')
HELPER_ID = re.compile(r'[A-Za-z0-9_-]{8,64}')


def lan_addresses():
    found = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 9))  # No envía nada; sólo elige la interfaz de salida.
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in found if not a.startswith('127.'))


# Código que el coordinador reparte a los ayudantes para que se actualicen solos.
# AYUDAR.bat queda fuera: Windows no tolera reescribir un .bat mientras se ejecuta.
CODE_FILE = re.compile(r'(?!AYUDAR\.bat$)(?:[A-Za-z0-9_]+\.(?:py|bat)|requirements[a-z-]*\.txt|'
                       r'(?:kepler_detector|local_panel)/[A-Za-z0-9_]+\.py|'
                       r'local_panel/static/[A-Za-z0-9_.-]+\.(?:html|js|css|svg)|'
                       r'local_panel/static/fonts/[A-Za-z0-9_.-]+\.(?:woff2|txt))')
CODE_ROOT = Path(__file__).resolve().parent.parent


def code_snapshot(root=CODE_ROOT):
    """{ruta: bytes} del código repartible, tal como está en disco ahora."""
    files = {}
    for pattern in ('*.py', '*.bat', 'requirements*.txt', 'kepler_detector/*.py', 'local_panel/*.py',
                    'local_panel/static/*', 'local_panel/static/fonts/*'):
        for path in sorted(Path(root).glob(pattern)):
            name = path.relative_to(root).as_posix()
            if path.is_file() and CODE_FILE.fullmatch(name):
                files[name] = path.read_bytes()
    return files


def code_version(manifest):
    import hashlib
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:16]


class SignatureChanged(ValueError):
    pass


class Cluster:
    def __init__(self, manager, port=DEFAULT_PORT, host='0.0.0.0', lease_seconds=LEASE_SECONDS):
        self.manager = manager
        self.root = manager.root
        self.lock = manager.lock          # Un único cerrojo con el gestor: sin carreras ni interbloqueos.
        self.port = port
        self.host = host
        self.lease_seconds = lease_seconds
        self.settings_path = manager.folder / 'cluster.json'
        settings = read(self.settings_path, {}) or {}
        if not settings.get('token'):
            settings['token'] = secrets.token_urlsafe(18)
        settings.setdefault('distribute_discovery', False)
        write(self.settings_path, settings)
        self.settings = settings
        # Foto del código al arrancar: lo que se reparte es lo que este proceso ejecuta,
        # aunque los archivos del disco cambien después (hasta el próximo reinicio).
        import hashlib
        self.code = code_snapshot()
        self.code_manifest = {name: hashlib.sha256(content).hexdigest() for name, content in self.code.items()}
        self.code_version = code_version(self.code_manifest)
        self.leases = {}                  # (job, kic) -> {helper, expires}
        self.helpers = {}                 # helper_id -> estado visible en el panel
        self._hashes = {}
        self.server = None
        manager.cluster = self

    # ------------------------------------------------------------------ utilidades
    @property
    def token(self):
        return self.settings['token']

    def set_distribute_discovery(self, value):
        with self.lock:
            self.settings['distribute_discovery'] = bool(value)
            write(self.settings_path, self.settings)
            return self.details()

    def new_token(self):
        with self.lock:
            self.settings['token'] = secrets.token_urlsafe(18)
            write(self.settings_path, self.settings)
            return self.details()

    def _asset_hash(self, name):
        path = self.root / name
        if not path.is_file():
            return None
        stat = path.stat()
        key = (name, stat.st_mtime_ns, stat.st_size)
        if key not in self._hashes:
            from kepler_detector.utils import file_hash
            self._hashes[key] = file_hash(path)
        return self._hashes[key]

    def assets(self):
        return {name: digest for name in ASSETS if (digest := self._asset_hash(name))}

    def no_model(self):
        return not (self.root / 'models/kepler_rf.joblib').is_file()

    def expected_signature(self, mission='kepler'):
        from config import Config
        from kepler_detector.scanner import signature
        return signature(self.root, Config(), self.no_model(), mission)

    def info(self):
        from kepler_detector import FEATURE_VERSION, PIPELINE_VERSION, __version__
        from kepler_detector.schema import FEATURE_SCHEMA_HASH
        from config import Config
        return dict(application='sakanet-cluster', protocol=PROTOCOL, version=__version__,
                    pipeline_version=PIPELINE_VERSION, feature_version=FEATURE_VERSION,
                    feature_schema_hash=FEATURE_SCHEMA_HASH, config=Config().to_dict(),
                    no_model=self.no_model(), assets=self.assets(),
                    lease_seconds=self.lease_seconds, code_version=self.code_version)

    def _name(self, state, kic):
        return self._label(state.get('mission', 'kepler'), kic)

    @staticmethod
    def _label(mission, kic):
        from kepler_detector import missions
        return missions.get(mission).label(kic)

    def _log(self, job, text):
        with (self.manager.folder / job / 'worker.log').open('a', encoding='utf-8') as stream:
            stream.write(time.strftime('%H:%M:%S ') + text + '\n')

    def _touch(self, helper, name=None, address=None, **changes):
        entry = self.helpers.setdefault(helper, dict(id=helper, name=helper[:8], host='', completed=0,
                                                     failed=0, current=None, address=address))
        if name:
            entry['host'] = str(name)[:40]   # Nombre del equipo: se conserva como dato secundario
        if address:
            entry['address'] = address
        entry['name'] = self._display_name(entry)
        entry['last_seen'] = time.time()
        entry.update(changes)
        return entry

    @staticmethod
    def _display_name(entry):
        """Lo que se ve en el panel y en los registros: la IP del ayudante, no su nombre de equipo."""
        host, address = entry.get('host') or '', entry.get('address')
        if not address:
            return host or entry['id'][:8]
        worker = re.search(r' #[0-9]+$', host.replace(' (este PC)', ''))  # Varios procesos en un mismo PC
        suffix = worker.group(0) if worker else ''
        if address.startswith('127.') or address == '::1':
            local = lan_addresses()
            return f"{local[0] if local else address}{suffix} (este PC)"
        return address + suffix

    def _jobs(self):
        states = []
        for path in self.manager.folder.glob('*/state.json'):
            state = read(path, {})
            if state.get('kind') == 'cluster':
                states.append(state)
        return sorted(states, key=lambda s: s['created'])

    def _save(self, state):
        write(self.manager.folder / state['id'] / 'state.json', state)

    def _expire(self):
        now = time.time()
        for (job, kic), lease in list(self.leases.items()):
            if lease['expires'] > now:
                continue
            del self.leases[(job, kic)]
            path = self.manager.folder / job / 'state.json'
            state = read(path, {})
            if state.get('stars', {}).get(kic) == 'running':
                state['stars'][kic] = 'pending'
                state.get('assigned', {}).pop(kic, None)
                self._save(state)
                name = self.helpers.get(lease['helper'], {}).get('name', lease['helper'][:8])
                self._log(job, f'{name}: sin respuesta, {self._name(state, kic)} vuelve a la cola')
            helper = self.helpers.get(lease['helper'])
            if helper and helper.get('current') == f'{job}:{kic}':
                helper['current'] = None

    def _refresh_current(self, state):
        running = [k for k, v in state['stars'].items() if v == 'running']
        state['current'] = int(running[0]) if len(running) == 1 else (len(running) or None)

    # ------------------------------------------------------------------ protocolo
    def claim(self, helper, name, address=None, caps=(), _retry=True):
        with self.lock:
            self._expire()
            self._touch(helper, name, address)
            for state in self._jobs():
                if state['status'] != 'running':
                    continue
                if state.get('task') == 'build' and 'build' not in caps:
                    continue  # Ayudante de una versión anterior: sólo recibe análisis normales.
                if state.get('mission', 'kepler') != 'kepler' and 'missions' not in caps:
                    continue  # Ayudantes antiguos sólo saben analizar Kepler.
                if state.get('task') == 'build' and state.get('mission', 'kepler') != 'kepler' \
                        and 'build-missions' not in caps:
                    continue  # Medirían el TIC como si fuera un KIC.
                for kic in map(str, state['ids']):
                    if state['stars'][kic] != 'pending':
                        continue
                    state['stars'][kic] = 'running'
                    state.setdefault('assigned', {})[kic] = self.helpers[helper]['name']
                    self._refresh_current(state)
                    self._save(state)
                    self.leases[(state['id'], kic)] = dict(helper=helper,
                                                           expires=time.time() + self.lease_seconds)
                    self.helpers[helper]['current'] = f"{state['id']}:{kic}"
                    mission = state.get('mission', 'kepler')
                    self._log(state['id'], f"{self.helpers[helper]['name']}: INICIO {self._label(mission, kic)}")
                    if state.get('task') == 'build':
                        from local_panel import training
                        return dict(job=state['id'], kic=int(kic), mission=mission,
                                    lease_seconds=self.lease_seconds, **training.task_for(state, kic))
                    return dict(job=state['id'], kic=int(kic), mission=mission,
                                no_model=self.no_model(), signature=self.expected_signature(mission),
                                lease_seconds=self.lease_seconds)
            self.helpers[helper]['current'] = None
            # Nada que repartir: si el lote de descubrimiento sólo tiene estrellas en curso,
            # esto lanza el siguiente y el ayudante lo recibe en esta misma petición.
            # Un solo reintento: si el lote nuevo tampoco tiene nada para este ayudante, se le dice
            # que espere (antes se reintentaba sin fin y la petición moría por recursión).
            if _retry and any(s.get('batch_index') and s['status'] == 'running' for s in self._jobs()):
                self.manager._advance_discovery()
                return self.claim(helper, name, address, caps, _retry=False)
            return None

    def heartbeat(self, helper, job, kic):
        with self.lock:
            self._expire()
            self._touch(helper)
            lease = self.leases.get((job, str(kic)))
            if not lease or lease['helper'] != helper:
                return dict(keep=False)
            lease['expires'] = time.time() + self.lease_seconds
            return dict(keep=True)

    def release(self, helper, job, kic):
        with self.lock:
            lease = self.leases.get((job, str(kic)))
            if lease and lease['helper'] == helper:
                lease['expires'] = 0
                self._expire()
            self._touch(helper, current=None)
            return dict(ok=True)

    def _owned_state(self, helper, job, kic):
        state = read(self.manager.jobdir(job) / 'state.json')
        if state.get('kind') != 'cluster' or kic not in state['stars']:
            raise ValueError('Estrella desconocida')
        lease = self.leases.get((job, kic))
        if state['stars'][kic] in ('complete', 'failed'):
            raise LookupError('La estrella ya estaba resuelta')
        if lease and lease['helper'] != helper:
            raise LookupError('La estrella está asignada a otro equipo')
        return state

    def fail(self, helper, job, kic, error):
        kic = str(kic)
        with self.lock:
            self._expire()
            state = self._owned_state(helper, job, kic)
            self.leases.pop((job, kic), None)
            attempts = state.setdefault('attempts', {})
            attempts[kic] = attempts.get(kic, 0) + 1
            final = attempts[kic] >= MAX_ATTEMPTS
            state['stars'][kic] = 'failed' if final else 'pending'
            state.get('assigned', {}).pop(kic, None)
            entry = self._touch(helper, current=None)
            entry['failed'] += 1
            self._log(job, f"{entry['name']}: FALLO {self._name(state, kic)} ({str(error)[:200]})"
                           + ('' if final else ' · se reintentará en otro equipo'))
            self._finish_if_done(state)
            return dict(ok=True, final=final)

    def submit(self, helper, job, kic, payload, name=None):
        kic = str(kic)
        if len(payload) > MAX_UPLOAD:
            raise ValueError('Resultado demasiado grande')
        with self.lock:
            self._expire()
            state = self._owned_state(helper, job, kic)
            mission = state.get('mission', 'kepler')
            files = self._unpack(payload)
            try:
                self._validate(files, int(kic), self.expected_signature(mission), mission)
            except SignatureChanged as error:
                # Modelo nuevo durante el análisis: la estrella vuelve a la cola y el ayudante se resincroniza.
                self.leases.pop((job, kic), None)
                state['stars'][kic] = 'pending'
                state.get('assigned', {}).pop(kic, None)
                self._refresh_current(state)
                self._save(state)
                self._log(job, f'{self._name(state, kic)}: resultado con modelos antiguos, vuelve a la cola')
                raise LookupError(str(error)) from None
            folder = self.manager.star_folder(state, int(kic))
            if folder.exists():
                history = self.manager.folder / job / 'history' / f'{kic}-{time.time_ns()}'
                history.mkdir(parents=True, exist_ok=True)
                shutil.move(str(folder), str(history / folder.name))
            temporary = folder.with_name(folder.name + '.' + secrets.token_hex(4) + '.tmp')
            temporary.mkdir(parents=True)
            for filename, content in files.items():
                (temporary / filename).write_bytes(content)
            temporary.replace(folder)
            self.leases.pop((job, kic), None)
            state['stars'][kic] = 'complete'
            entry = self._touch(helper, name, current=None)
            entry['completed'] += 1
            state.setdefault('done_by', {})[kic] = entry['name']
            state.get('assigned', {}).pop(kic, None)
            self._refresh_current(state)
            self._save(state)
            self._log(job, f"{entry['name']}: FIN {self._name(state, kic)}: complete")
            try:
                if mission == 'kepler':
                    from kepler_detector.evidence import record_serious_evidence
                    record_serious_evidence(self.root, job, int(kic), folder)
            except Exception as error:  # El resultado científico sigue siendo válido.
                self._log(job, f'AVISO informe de evidencias {self._name(state, kic)}: {error}')
            self._finish_if_done(state)
            return dict(ok=True)

    def submit_build(self, helper, job, kic, results, name=None):
        kic = str(kic)
        with self.lock:
            self._expire()
            state = self._owned_state(helper, job, kic)
            if state.get('task') != 'build':
                raise ValueError('Este trabajo no es de entrenamiento')
            from local_panel import training
            try:
                training.accept(self.manager, state, kic, results)
            except ValueError as error:
                # Un resultado inválido cuenta como intento fallido: nunca un bucle de reasignaciones.
                self._log(job, f'{self._name(state, kic)}: resultado de entrenamiento rechazado ({error})')
                self.fail(helper, job, kic, f'Resultado rechazado: {error}')
                raise LookupError(str(error)) from None
            self.leases.pop((job, kic), None)
            state['stars'][kic] = 'complete'
            entry = self._touch(helper, name, current=None)
            entry['completed'] += 1
            state.setdefault('done_by', {})[kic] = entry['name']
            state.get('assigned', {}).pop(kic, None)
            good = sum(item['status'] == 'complete' for item in results)
            self._log(job, f"{entry['name']}: FIN {self._name(state, kic)}: {good}/{len(results)} KOI medidos")
            self._finish_if_done(state)
            return dict(ok=True)

    @staticmethod
    def _unpack(payload):
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile:
            raise ValueError('El resultado no es un ZIP válido') from None
        files, total = {}, 0
        with archive:
            for item in archive.infolist():
                if item.is_dir() or not RESULT_FILES.fullmatch(item.filename):
                    raise ValueError(f'Archivo no permitido en el resultado: {item.filename[:80]}')
                total += item.file_size
                if total > MAX_UNPACKED:
                    raise ValueError('Resultado descomprimido demasiado grande')
                content = archive.read(item)
                if len(content) != item.file_size:
                    raise ValueError('ZIP inconsistente')
                files[item.filename] = content
        return files

    @staticmethod
    def _validate(files, kic, signature, mission='kepler'):
        import csv
        for name in ('candidates.csv', 'metadata.json'):
            if name not in files:
                raise ValueError(f'Falta {name} en el resultado')
        try:
            meta = json.loads(files['metadata.json'].decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('metadata.json ilegible') from None
        if meta.get('status') != 'complete' or meta.get('kic') != kic:
            raise ValueError('metadata.json no corresponde a un análisis completo de esta estrella')
        if meta.get('signature') != signature:
            raise SignatureChanged('Versión, configuración o modelos distintos a los del coordinador; '
                             'actualiza Sakanet en el equipo ayudante')
        rows = list(csv.DictReader(io.StringIO(files['candidates.csv'].decode('utf-8'))))
        column = 'kepid' if mission == 'kepler' else 'target_id'
        if any(int(float(row.get(column, -1))) != kic for row in rows):
            raise ValueError('candidates.csv contiene otra estrella')
        if any(name.endswith('.png') and not content.startswith(b'\x89PNG') for name, content in files.items()):
            raise ValueError('Imagen inválida en el resultado')

    def _finish_if_done(self, state):
        self._refresh_current(state)
        if state['status'] in ('running', 'paused') and all(
                v in ('complete', 'failed') for v in state['stars'].values()):
            if state.get('task') == 'build':
                from local_panel import training
                training.round_done(self, state)
                self._save(state)
                return
            state.update(status='completed', current=None)
            self._save(state)
            self._log(state['id'], 'Trabajo repartido terminado')
            self.manager.cluster_job_finished(state)
        else:
            self._save(state)

    # ------------------------------------------------------------------ control desde el panel
    def action(self, job, action):
        with self.lock:
            self._expire()
            state = read(self.manager.jobdir(job) / 'state.json')
            if action == 'resume':
                if state['status'] == 'completed':
                    raise ValueError('Este trabajo ya terminó.')
                for kic, value in state['stars'].items():
                    if value in ('interrupted', 'failed'):  # Reanudar reintenta también las fallidas.
                        state['stars'][kic] = 'pending'
                        state.get('attempts', {}).pop(kic, None)
                state.update(status='running', error=None)
                self._log(job, 'Reparto reanudado')
                self._finish_if_done(state)  # Entrenamiento interrumpido con todo medido: rehacer el final.
                return
            elif action == 'pause':
                if state['status'] != 'running':
                    raise ValueError('Este reparto no está en ejecución.')
                state['status'] = 'paused'
                self._log(job, 'Pausa: no se reparten más estrellas; las que están en curso terminan')
            elif action == 'stop':
                if state['status'] not in ('running', 'paused'):
                    raise ValueError('Este reparto no está activo.')
                for (lease_job, kic) in [k for k in self.leases if k[0] == job]:
                    del self.leases[(lease_job, kic)]
                    state['stars'][kic] = 'pending'
                    state.get('assigned', {}).pop(kic, None)
                state.update(status='stopped', current=None)
                self._log(job, 'Reparto detenido; las estrellas en curso vuelven a la cola')
            else:
                raise ValueError('Acción desconocida')
            self._save(state)

    def details(self):
        with self.lock:
            self._expire()
            now = time.time()
            helpers = [dict(h, online=now - h['last_seen'] < HELPER_TTL) for h in self.helpers.values()]
            return dict(enabled=self.server is not None, port=self.port, token=self.token,
                        addresses=lan_addresses(), distribute_discovery=self.settings['distribute_discovery'],
                        helpers=sorted(helpers, key=lambda h: (not h['online'], h['name'])),
                        leases=len(self.leases))

    # ------------------------------------------------------------------ servidor HTTP
    def start(self):
        cluster = self

        class Handler(BaseHTTPRequestHandler):
            server_version = 'SakanetCluster/1'

            def log_message(self, *args):
                pass

            def reply(self, value, status=200):
                body = json.dumps(value, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)

            def authorized(self):
                header = self.headers.get('Authorization', '')
                return header.startswith('Bearer ') and secrets.compare_digest(
                    header[7:].encode(), cluster.token.encode())

            def body(self, limit):
                length = int(self.headers.get('Content-Length') or 0)
                if not 0 < length <= limit:
                    raise ValueError('Tamaño de solicitud inválido')
                return self.rfile.read(length)

            def helper(self, value):
                if not isinstance(value, str) or not HELPER_ID.fullmatch(value):
                    raise ValueError('Identificador de ayudante inválido')
                return value

            def do_GET(self):
                if not self.authorized():
                    return self.reply({'error': 'Token incorrecto'}, 401)
                path = urlsplit(self.path).path
                try:
                    if path == '/cluster/v1/info':
                        return self.reply(cluster.info())
                    if path == '/cluster/v1/code':
                        return self.reply(dict(version=cluster.code_version, files=cluster.code_manifest))
                    if path.startswith('/cluster/v1/code/'):
                        content = cluster.code.get(path[len('/cluster/v1/code/'):])
                        if content is None:
                            return self.reply({'error': 'No encontrado'}, 404)
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/octet-stream')
                        self.send_header('Content-Length', str(len(content)))
                        self.end_headers()
                        self.wfile.write(content)
                        return
                    if path.startswith('/cluster/v1/assets/'):
                        name = path[len('/cluster/v1/assets/'):]
                        if name not in ASSETS or not (cluster.root / name).is_file():
                            return self.reply({'error': 'No encontrado'}, 404)
                        content = (cluster.root / name).read_bytes()
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/octet-stream')
                        self.send_header('Content-Length', str(len(content)))
                        self.end_headers()
                        self.wfile.write(content)
                        return
                    return self.reply({'error': 'No encontrado'}, 404)
                except (OSError, ValueError) as error:
                    return self.reply({'error': str(error)}, 500)

            def do_POST(self):
                if not self.authorized():
                    return self.reply({'error': 'Token incorrecto'}, 401)
                path = urlsplit(self.path).path
                address = self.client_address[0]
                try:
                    if path == '/cluster/v1/result':
                        helper = self.helper(self.headers.get('X-Helper'))
                        result = cluster.submit(helper, self.headers.get('X-Job', ''),
                                                self.headers.get('X-Kic', ''), self.body(MAX_UPLOAD),
                                                self.headers.get('X-Name'))
                        return self.reply(result)
                    data = json.loads(self.body(2 * 1024 * 1024 if path == '/cluster/v1/build_result' else 16384))
                    if not isinstance(data, dict):
                        raise ValueError('Se esperaba un objeto JSON')
                    helper = self.helper(data.get('helper'))
                    if path == '/cluster/v1/claim':
                        caps = data.get('caps') if isinstance(data.get('caps'), list) else []
                        task = cluster.claim(helper, data.get('name'), address, caps)
                        return self.reply({**(task or {'task': None}), 'code_version': cluster.code_version})
                    if path == '/cluster/v1/build_result':
                        return self.reply(cluster.submit_build(helper, str(data.get('job')), data.get('kic'),
                                                               data.get('results'), data.get('name')))
                    if path == '/cluster/v1/heartbeat':
                        return self.reply(cluster.heartbeat(helper, str(data.get('job')), data.get('kic')))
                    if path == '/cluster/v1/fail':
                        return self.reply(cluster.fail(helper, str(data.get('job')), data.get('kic'),
                                                       data.get('error', '')))
                    if path == '/cluster/v1/release':
                        return self.reply(cluster.release(helper, str(data.get('job')), data.get('kic')))
                    return self.reply({'error': 'No encontrado'}, 404)
                except LookupError as error:
                    return self.reply({'error': str(error)}, 409)
                except (ValueError, KeyError, json.JSONDecodeError) as error:
                    return self.reply({'error': str(error)}, 400)

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.server

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
