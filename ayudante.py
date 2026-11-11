"""Equipo ayudante de Sakanet: pide estrellas al panel coordinador, las analiza y
devuelve los resultados. Usa el mismo código, configuración y modelos que el
coordinador (se sincronizan solos). Detener: Ctrl+C (la estrella en curso vuelve a la cola).

    python ayudante.py --server http://192.168.1.20:8769 --token XXXX [--workers 2]

La primera vez guarda servidor, token y nombre en ayudante.json."""
import argparse
import io
import json
import logging
import multiprocessing
import os
from pathlib import Path
import secrets
import socket
import threading
import time
import traceback
import urllib.error
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
SETTINGS = HERE / 'ayudante.json'
log = logging.getLogger('ayudante')


def _plain(value):
    """Números de numpy (int64, float32…) a tipos JSON."""
    if hasattr(value, 'item'):
        return value.item()
    raise TypeError(f'No serializable: {type(value).__name__}')


RESTART = 75  # Código de salida: "me he actualizado, vuelve a lanzarme" (lo entiende AYUDAR.bat)


class Restart(Exception):
    """El coordinador tiene código más nuevo: actualizar y relanzar."""


class Rejected(Exception):
    """El coordinador rechazó la operación (409/400): no tiene sentido reintentar."""


class Client:
    def __init__(self, server, token, helper, name):
        self.server = server.rstrip('/')
        self.token = token
        self.helper = helper
        self.name = name

    def request(self, path, data=None, raw=None, headers=None, timeout=60):
        body = raw if raw is not None else (json.dumps(data, default=_plain).encode() if data is not None else None)
        request = urllib.request.Request(self.server + path, data=body, method='POST' if body else 'GET')
        request.add_header('Authorization', f'Bearer {self.token}')
        request.add_header('Content-Type', 'application/zip' if raw is not None else 'application/json')
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content = response.read()
                kind = response.headers.get('Content-Type', '')
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read()).get('error', str(error))
            except Exception:
                message = str(error)
            if error.code == 401:
                raise SystemExit('Token incorrecto: cópialo de nuevo desde el panel coordinador.')
            if error.code in (400, 404, 409):
                raise Rejected(message) from None
            raise ConnectionError(message) from None
        return json.loads(content) if kind.startswith('application/json') else content

    def post(self, path, **data):
        return self.request(path, {'helper': self.helper, 'name': self.name, **data})


def retrying(operation, what, attempts=6):
    for attempt in range(attempts):
        try:
            return operation()
        except (ConnectionError, OSError, urllib.error.URLError) as error:
            if attempt == attempts - 1:
                raise
            wait = min(60, 2 ** (attempt + 1))
            log.warning('%s: sin conexión con el coordinador (%s); reintento en %ss', what, error, wait)
            time.sleep(wait)


_local_version = None


def local_code_version():
    """Versión del código con el que arrancó este proceso (misma cuenta que el coordinador)."""
    global _local_version
    if _local_version is None:
        import hashlib
        from local_panel.cluster import code_snapshot, code_version
        _local_version = code_version({name: hashlib.sha256(content).hexdigest()
                                       for name, content in code_snapshot(HERE).items()})
    return _local_version


def sync_code(client, code_dir=HERE):
    """Descarga del coordinador los archivos de código que difieren. Devuelve los cambiados.
    Sólo escribe rutas de la lista permitida, verifica cada sha256 y no deja nada a medias."""
    import hashlib
    from local_panel.cluster import CODE_FILE
    manifest = retrying(lambda: client.request('/cluster/v1/code'), 'código')['files']
    code_dir = Path(code_dir)
    pending = {}
    for name, wanted in manifest.items():
        if not CODE_FILE.fullmatch(name):
            raise ConnectionError(f'El coordinador ofrece un archivo no permitido: {name[:80]}')
        path = code_dir / name
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == wanted:
            continue
        content = retrying(lambda: client.request('/cluster/v1/code/' + name, timeout=300), name)
        if hashlib.sha256(content).hexdigest() != wanted:
            raise ConnectionError(f'{name} llegó corrupto')
        pending[name] = content
    for name, content in pending.items():  # Todo descargado y verificado: ahora sí se escribe.
        path = code_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.nuevo')
        temporary.write_bytes(content)
        os.replace(temporary, path)
    return sorted(pending)


def update_from_coordinator(client):
    """Si el coordinador ejecuta otra versión, la instala aquí. True si hay que relanzar."""
    info = retrying(lambda: client.request('/cluster/v1/info'), 'info')
    wanted = info.get('code_version')
    if not wanted or wanted == local_code_version():
        return False
    print(f'El coordinador tiene una versión nueva de Sakanet ({wanted}). Actualizando…', flush=True)
    changed = sync_code(client)
    if not changed:
        return False
    print('Actualizados: ' + ', '.join(changed[:8]) + (f' y {len(changed) - 8} más' if len(changed) > 8 else ''), flush=True)
    if any(name.startswith('requirements') for name in changed):
        import subprocess
        import sys
        print('Instalando dependencias nuevas…', flush=True)
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-r', str(HERE / 'requirements.txt')], check=False)
    return True


def check_and_sync(client, root):
    """Comprueba versión y sincroniza modelos/catálogos con los del coordinador."""
    from kepler_detector import PIPELINE_VERSION, FEATURE_VERSION
    from kepler_detector.schema import FEATURE_SCHEMA_HASH
    from kepler_detector.utils import file_hash
    from local_panel.cluster import ASSETS, PROTOCOL
    info = retrying(lambda: client.request('/cluster/v1/info'), 'info')
    if info.get('application') != 'sakanet-cluster' or info.get('protocol') != PROTOCOL:
        raise SystemExit('El servidor no es un coordinador Sakanet compatible.')
    mine = dict(pipeline_version=PIPELINE_VERSION, feature_version=FEATURE_VERSION,
                feature_schema_hash=FEATURE_SCHEMA_HASH)
    for key, value in mine.items():
        if info.get(key) != value:
            raise SystemExit(f'Versión distinta al coordinador ({key}: {value} frente a {info.get(key)}). '
                             'Cierra y vuelve a abrir AYUDAR.bat para que se actualice solo.')
    for name in ASSETS:
        path = root / name
        wanted = info['assets'].get(name)
        if wanted is None:
            if path.exists():
                path.unlink()  # El coordinador no lo usa: tampoco aquí, o la firma no coincidiría.
            continue
        if path.exists() and file_hash(path) == wanted:
            continue
        log.info('Descargando %s del coordinador', name)
        content = retrying(lambda: client.request('/cluster/v1/assets/' + name, timeout=600), name)
        import hashlib
        if hashlib.sha256(content).hexdigest() != wanted:
            raise ConnectionError(f'{name} llegó corrupto')
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.tmp')
        temporary.write_bytes(content)
        os.replace(temporary, path)
    return info


def pack(folder):
    from local_panel.cluster import RESULT_FILES
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.iterdir()):
            if path.is_file() and RESULT_FILES.fullmatch(path.name):
                archive.write(path, path.name)
    return buffer.getvalue()


def process(client, root, task):
    from config import Config
    from kepler_detector.scanner import scan
    job, kic = task['job'], int(task['kic'])
    stop = threading.Event()

    def heartbeat():
        every = max(5, min(60, task.get('lease_seconds', 600) / 4))
        while not stop.wait(every):
            try:
                if not client.post('/cluster/v1/heartbeat', job=job, kic=kic).get('keep'):
                    log.warning('KIC %s: el coordinador la reasignó; se terminará igualmente', kic)
                    return
            except Exception as error:
                log.warning('Latido fallido: %s', error)

    beat = threading.Thread(target=heartbeat, daemon=True)
    beat.start()
    started = time.monotonic()
    if task.get('task') == 'build':
        return process_build(client, root, task, stop, started)
    try:
        from kepler_detector import missions
        info = missions.get(task.get('mission', 'kepler'))
        log.info('%s: analizando…', info.label(kic))
        scan(root, kic, Config(), no_model=task.get('no_model', False), mission=info.key)
        folder = root / 'results' / info.folder(kic)
        meta = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
        if meta.get('signature') != task['signature']:
            raise RuntimeError('La firma del análisis no coincide con la del coordinador '
                               '(modelos o versión distintos)')
        payload = pack(folder)
    except Exception as error:
        stop.set()
        traceback.print_exc()
        try:
            retrying(lambda: client.post('/cluster/v1/fail', job=job, kic=kic,
                                         error=f'{type(error).__name__}: {error}'), 'fallo')
        except Rejected as rejected:
            log.warning('KIC %s: %s', kic, rejected)
        return 'failed'
    stop.set()
    try:
        retrying(lambda: client.request('/cluster/v1/result', raw=payload, timeout=600, headers={
            'X-Helper': client.helper, 'X-Name': client.name.encode('ascii', 'replace').decode(),
            'X-Job': job, 'X-Kic': str(kic)}), 'envío')
    except Rejected as rejected:
        log.warning('KIC %s: resultado no aceptado: %s', kic, rejected)
        return 'rejected'
    log.info('KIC %s: enviado (%.0f s)', kic, time.monotonic() - started)
    return 'complete'


def watch_parent(pid):
    """Ayudante lanzado por el panel: termina si el panel se cierra."""
    from local_panel.jobs import alive
    while alive(pid):
        time.sleep(3)
    os._exit(0)


def process_build(client, root, task, stop, started):
    """Entrenamiento repartido: medir los KOI de una estrella y devolver sus variables."""
    from config import Config
    from kepler_detector.builder import star_records
    job, kic = task['job'], int(task['kic'])
    try:
        config = Config(**{**task['config'], 'durations': tuple(task['config']['durations'])})
        if config.to_dict() != Config().to_dict():
            raise RuntimeError('Configuración distinta a la de este equipo; actualiza Sakanet')
        log.info('KIC %s: midiendo %d KOI para entrenar…', kic, len(task['signals']))
        results = star_records(root, kic, [tuple(s) for s in task['signals']], config)
    except Exception as error:
        stop.set()
        traceback.print_exc()
        try:
            retrying(lambda: client.post('/cluster/v1/fail', job=job, kic=kic,
                                         error=f'{type(error).__name__}: {error}'), 'fallo')
        except Rejected as rejected:
            log.warning('KIC %s: %s', kic, rejected)
        return 'failed'
    stop.set()
    try:
        retrying(lambda: client.post('/cluster/v1/build_result', job=job, kic=kic, results=results), 'envío')
    except Rejected as rejected:
        log.warning('KIC %s: resultado no aceptado: %s', kic, rejected)
        return 'rejected'
    log.info('KIC %s: %d KOI enviados (%.0f s)', kic, len(results), time.monotonic() - started)
    return 'complete'


def run_worker(server, token, helper, name, root, idle_exit=False, max_tasks=None, parent=None):
    if parent:
        threading.Thread(target=watch_parent, args=(parent,), daemon=True).start()
    from kepler_detector.utils import setup
    root = setup(Path(root))
    logging.basicConfig(level=logging.INFO, format=f'%(asctime)s [{name}] %(message)s', datefmt='%H:%M:%S')
    client = Client(server, token, helper, name)
    # Versión del coordinador con la que arranca este proceso (main() ya se ha sincronizado con ella).
    accepted = check_and_sync(client, root).get('code_version')
    log.info('Conectado a %s', server)
    done, idle, current = 0, 0, None
    try:
        while max_tasks is None or done < max_tasks:
            task = retrying(lambda: client.post('/cluster/v1/claim', caps=['build', 'missions']), 'pedir trabajo', attempts=1000)
            if accepted and task.get('code_version') and task['code_version'] != accepted:
                # El coordinador se ha reiniciado con código nuevo: soltar la estrella y actualizarse.
                if task.get('job'):
                    try:
                        client.post('/cluster/v1/release', job=task['job'], kic=task['kic'])
                    except Exception:
                        pass
                raise Restart()
            if not task.get('job'):
                if idle_exit:
                    break
                idle = min(idle + 1, 6)
                if idle == 1:
                    log.info('Sin estrellas pendientes; esperando trabajo…')
                time.sleep(5 * idle)
                continue
            if idle:
                check_and_sync(client, root)  # Tras una espera, el coordinador pudo cambiar de modelo.
            idle, current = 0, task
            if process(client, root, task) == 'rejected':
                check_and_sync(client, root)  # p. ej. el coordinador acaba de entrenar un modelo nuevo
            current = None
            done += 1
    except KeyboardInterrupt:
        if current:
            try:
                client.post('/cluster/v1/release', job=current['job'], kic=current['kic'])
                log.info('KIC %s devuelta a la cola', current['kic'])
            except Exception:
                pass
    return done


def _worker_process(*args):
    try:
        run_worker(*args)
    except Restart:
        raise SystemExit(RESTART)


def settings(args):
    path = args.settings
    saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    server = args.server or saved.get('server')
    token = args.token or saved.get('token')
    if not server:
        server = input('Dirección del coordinador (ej. http://192.168.1.20:8769): ').strip()
    if not token:
        token = input('Token (lo muestra el panel coordinador): ').strip()
    if not server.startswith('http'):
        server = 'http://' + server
    if server.count(':') == 1:
        server += ':8769'
    name = args.name or saved.get('name') or socket.gethostname()
    helper = saved.get('helper') or secrets.token_hex(8)
    if args.workers is None:
        args.workers = saved.get('workers')
    if args.workers is None:
        cores = os.cpu_count() or 2
        suggested = max(1, min(8, cores // 2))
        answer = input(f'¿Cuántas estrellas a la vez? Este PC tiene {cores} hilos [{suggested}]: ').strip()
        args.workers = int(answer) if answer.isdigit() else suggested
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(server=server, token=token, name=name, helper=helper,
                                    workers=args.workers), indent=2), encoding='utf-8')
    return server, token, name, helper


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--server')
    parser.add_argument('--token')
    parser.add_argument('--name', help='Nombre visible en el panel; por defecto el del PC')
    parser.add_argument('--workers', type=int, help='Estrellas en paralelo (1-8); se recuerda')
    parser.add_argument('--root', type=Path, default=HERE / 'ayudante_datos',
                        help='Carpeta de trabajo y caché de este ayudante')
    parser.add_argument('--idle-exit', action='store_true', help='Salir cuando no quede trabajo')
    parser.add_argument('--settings', type=Path, default=SETTINGS, help=argparse.SUPPRESS)
    parser.add_argument('--parent', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    server, token, name, helper = settings(args)
    if not 1 <= args.workers <= 8:
        parser.error('--workers debe estar entre 1 y 8')
    try:
        if update_from_coordinator(Client(server, token, helper, name)):
            print('Sakanet actualizado. Reiniciando el ayudante…', flush=True)
            return RESTART
    except (ConnectionError, OSError, urllib.error.URLError) as error:
        print(f'No se pudo comprobar si hay actualización ({error}); se sigue con la versión actual.', flush=True)
    print(f'Ayudante "{name}" -> {server} - {args.workers} proceso(s). Ctrl+C para parar.', flush=True)
    if args.workers == 1:
        try:
            run_worker(server, token, helper, name, args.root, args.idle_exit, parent=args.parent)
        except Restart:
            return RESTART  # Al relanzarse, main() descarga la versión nueva.
        return 0
    processes = [multiprocessing.Process(target=_worker_process, args=(
        server, token, f'{helper}-{n}', f'{name} #{n}', args.root, args.idle_exit, None, args.parent))
        for n in range(1, args.workers + 1)]
    for process_ in processes:
        process_.start()
        time.sleep(2)  # Escalonar la sincronización inicial.
    try:
        for process_ in processes:
            process_.join()
    except KeyboardInterrupt:
        for process_ in processes:
            process_.join(timeout=30)
    return RESTART if any(process_.exitcode == RESTART for process_ in processes) else 0


if __name__ == '__main__':
    raise SystemExit(main())
