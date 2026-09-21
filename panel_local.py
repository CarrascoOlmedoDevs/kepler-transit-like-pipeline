"""Loopback-only control panel. Starting this server never starts a scan."""
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
                if len(parts) == 3 and parts[:2] == ['api', 'jobs']:
                    return self.send(manager.details(parts[2]))
                if len(parts) == 4 and parts[:2] == ['api', 'results']:
                    target = manager.result_path(parts[2], parts[3], 'candidates.csv')
                    with target.open(encoding='utf-8', newline='') as stream:
                        return self.send(list(csv.DictReader(stream)))
                if len(parts) == 4 and parts[0] == 'files':
                    target = manager.result_path(parts[1], parts[2], parts[3])
                elif path in ('/', '/app.js', '/style.css'):
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
                    job = manager.create(data.get('kics'))
                    return self.send({'id': job}, 201)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--open', action='store_true')
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
        if args.open:
            webbrowser.open(url)
        return
    if args.open:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    print('Panel local: ' + url + ' — análisis pausados hasta acción explícita.', flush=True)
    try:
        server.serve_forever()
    finally:
        server.manager.close()
        server.server_close()


if __name__ == '__main__':
    main()
