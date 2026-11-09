"""Loopback-only control panel. Starting this server never starts a scan.

Con --lan se abre además un servidor aparte para equipos ayudantes de la red local
(local_panel/cluster.py, protegido por token). El panel en sí sigue siendo sólo local."""
import argparse
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit
import webbrowser
from local_panel.jobs import Manager

ROOT = Path(__file__).resolve().parent


def make_server(root=ROOT, port=8768, manager=None):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, content, status=200, mime='application/json; charset=utf-8'):
            if not isinstance(content, bytes):
                content = json.dumps(content, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(content)

        def allowed(self, mutation=False):
            port = self.server.server_port
            hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
            if self.headers.get('Host') not in hosts:
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in {f'http://{host}' for host in hosts}:
                return False
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return False
            return not mutation or (self.headers.get('Content-Type', '').split(';')[0] == 'application/json'
                     and secrets.compare_digest(self.headers.get('X-Panel-Token', ''), token))

        def do_GET(self):
            if not self.allowed():
                return self.send({'error': 'Acceso solo local / origen no permitido'}, 403)
            path = urlsplit(self.path).path
            parts = path.strip('/').split('/')
            try:
                if path == '/api/state':
                    return self.send(dict(application='kepler-local-panel', token=token, jobs=manager.snapshot()))
                if path == '/api/cnn':
                    return self.send(manager.cnn_details())
                if path == '/api/discovery':
                    return self.send(manager.discovery_details())
                if path == '/api/cluster':
                    return self.send(manager.cluster.details() if manager.cluster else {'enabled': False})
                if len(parts) == 3 and parts[:2] == ['api', 'jobs']:
                    return self.send(manager.details(parts[2]))
                if len(parts) == 4 and parts[:2] == ['api', 'results']:
                    target = manager.result_path(parts[2], parts[3], 'candidates.csv')
                    with target.open(encoding='utf-8', newline='') as stream:
                        from kepler_detector.triage import assess_candidate
                        rows = list(csv.DictReader(stream))
                        for row in rows:
                            row.update(assess_candidate(row))
                        return self.send(rows)
                if len(parts) == 4 and parts[0] == 'files':
                    target = manager.result_path(parts[1], parts[2], parts[3])
                elif path in ('/', '/app.js', '/style.css', '/triage.css'):
                    target = Path(root) / 'local_panel/static' / ('index.html' if path == '/' else path[1:])
                else:
                    return self.send({'error': 'No encontrado'}, 404)
                return self.send(target.read_bytes(), mime=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
            except (ValueError, FileNotFoundError, OSError) as exc:
                return self.send({'error': str(exc)}, 404)

        def do_POST(self):
            if not self.allowed(True):
                return self.send({'error': 'Solicitud no autorizada'}, 403)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384:
                    raise ValueError('Tamaño de solicitud inválido')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('Se esperaba un objeto JSON')
                path = urlsplit(self.path).path
                if path == '/api/jobs':
                    job = manager.create(data.get('kics'), data.get('distributed') is True)
                    return self.send({'id': job}, 201)
                if path == '/api/cnn/start':
                    manager.start_cnn(data.get('operation'), data.get('options'))
                    return self.send({'ok': True}, 202)
                if path == '/api/cnn/stop':
                    manager.stop_cnn()
                    return self.send({'ok': True})
                if path == '/api/discovery/start':
                    return self.send(manager.start_discovery(), 202)
                if path == '/api/discovery/replenish':
                    return self.send(manager.replenish_discovery(), 201)
                if path == '/api/cluster/settings':
                    if not manager.cluster:
                        raise ValueError('Modo red desactivado')
                    return self.send(manager.cluster.set_distribute_discovery(data.get('distribute_discovery') is True))
                if path == '/api/cluster/token':
                    if not manager.cluster:
                        raise ValueError('Modo red desactivado')
                    return self.send(manager.cluster.new_token())
                if path == '/api/discovery/stop':
                    manager.stop_discovery()
                    return self.send({'ok': True})
                parts = path.strip('/').split('/')
                if len(parts) == 4 and parts[:2] == ['api', 'jobs']:
                    manager.action(parts[2], parts[3])
                    return self.send({'ok': True})
                return self.send({'error': 'Acción inexistente'}, 404)
            except (ValueError, KeyError, RuntimeError, OSError) as exc:
                return self.send({'error': str(exc)}, 400)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    try:
        manager = manager or Manager(root)
    except Exception:
        server.server_close()
        raise
    server.manager = manager
    return server


def start_local_helper(cluster, workers):
    """Ayudante en este mismo PC, con su propia carpeta de trabajo."""
    import os
    import socket
    import subprocess
    import sys
    log = (ROOT / 'results/local_panel/ayudante_local.log').open('ab', buffering=0)
    return subprocess.Popen(
        [sys.executable, '-u', str(ROOT / 'ayudante.py'), '--server', f'http://127.0.0.1:{cluster.port}',
         '--token', cluster.token, '--name', f'{socket.gethostname()} (este PC)', '--workers', str(workers),
         '--root', str(ROOT / 'ayudante_local'), '--settings', str(ROOT / 'results/local_panel/ayudante_local.json'),
         '--parent', str(os.getpid())],
        cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        start_new_session=os.name != 'nt')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--lan', action='store_true', help='Aceptar equipos ayudantes de la red local')
    parser.add_argument('--lan-port', type=int, default=8769)
    parser.add_argument('--local-workers', type=int, default=1,
                        help='Con --lan: estrellas que analiza también este PC (0 = sólo coordinar)')
    args = parser.parse_args()
    url = f'http://127.0.0.1:{args.port}'
    try:
        server = make_server(port=args.port)
    except OSError:
        # Only reuse a confirmed instance of this app, never an arbitrary port owner.
        from urllib.request import urlopen
        with urlopen(url + '/api/state', timeout=3) as response:
            if json.load(response).get('application') != 'kepler-local-panel':
                raise RuntimeError('El puerto está ocupado por otra aplicación')
        if args.lan:
            from urllib.request import urlopen as _open
            try:
                with _open(url + '/api/cluster', timeout=3) as response:
                    enabled = json.load(response).get('enabled')
            except OSError:
                enabled = False  # Panel de una versión anterior, sin modo red.
            if not enabled:
                raise SystemExit('El panel ya está abierto sin modo red. Ciérralo y vuelve a abrir '
                                 'ABRIR_PANEL_RED.bat (que lo cierra solo).')
        if args.open:
            webbrowser.open(url)
        return
    if args.open:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    print('Panel local: ' + url + ' — análisis pausados hasta acción explícita.', flush=True)
    if args.lan:
        from local_panel.cluster import Cluster, lan_addresses
        cluster = Cluster(server.manager, args.lan_port)
        cluster.start()
        for address in lan_addresses() or ['<IP de este PC>']:
            print(f'Ayudantes: http://{address}:{args.lan_port}  token: {cluster.token}', flush=True)
        if args.local_workers > 0:
            local_helper = start_local_helper(cluster, args.local_workers)
            server.local_helper = local_helper
            print(f'Este PC también analiza ({args.local_workers} a la vez).', flush=True)
    try:
        server.serve_forever()
    finally:
        helper = getattr(server, 'local_helper', None)
        if helper and helper.poll() is None:
            from local_panel.jobs import stop_process
            try:
                stop_process(helper)
            except Exception:
                pass
        if server.manager.cluster:
            server.manager.cluster.stop()
        server.manager.close()
        server.server_close()


if __name__ == '__main__':
    main()
