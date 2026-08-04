"""Mini servidor HTTP local para servir el avatar y el .vrm.

Chromium bloquea fetch() sobre file://, asi que servimos los archivos por
http://127.0.0.1 (mismo origen, sin CORS) y el modelo 3D carga sin problemas.
"""
import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import config

_port = None


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass  # sin spam en consola


def start():
    """Inicia el servidor (una sola vez) y devuelve el puerto."""
    global _port
    if _port:
        return _port
    handler = functools.partial(_Quiet, directory=str(config.BASE_DIR))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)  # puerto libre al azar
    _port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"[avatar] servidor local en http://127.0.0.1:{_port}")
    return _port
