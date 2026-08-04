"""Sumidero desacoplado para la bitácora de actividades.

Los módulos de acción (music_gen, music_emotion, video_gen, youtube, image_gen,
pc_control, autonomy…) llaman a `activity.log(...)` en el punto donde HACEN algo.
main.py registra `memory.add_activity` como sumidero con `set_sink()`. Si no hay
sumidero (p. ej. en tests), `log()` es un no-op y nunca lanza.

Ventajas de este diseño:
- Los módulos hoja NO necesitan una referencia a Memory ni conocerla (sin
  acoplar ni arriesgar imports circulares: este módulo solo usa la stdlib).
- El contexto "con_usuario" (si fue pedido por el usuario o iniciativa de YUE)
  puede fijarse por bloque con `autonomy_window()`, de modo que lo que YUE haga
  sola durante inactividad quede marcado como con_usuario=False sin tener que
  pasar el dato por toda la cadena de llamadas.

Regla de oro: aquí solo va una DESCRIPCIÓN CORTA de la actividad, nunca
contenido sensible ni archivos completos.
"""
import threading

_sink = None
_local = threading.local()


def set_sink(fn):
    """Registra el destino real de la bitácora (normalmente memory.add_activity)."""
    global _sink
    _sink = fn


def _in_autonomy() -> bool:
    return bool(getattr(_local, "autonomy", False))


def user_default() -> bool:
    """con_usuario por defecto: True, salvo dentro de una ventana de autonomía."""
    return not _in_autonomy()


class autonomy_window:
    """Marca un bloque como iniciativa de YUE (por defecto con_usuario=False).

    Uso: `with activity.autonomy_window(): ...` alrededor del trabajo que YUE
    hace sola. Las actividades registradas dentro (sin con_usuario explícito) se
    marcarán como no compartidas.
    """
    def __enter__(self):
        _local.autonomy = True
        return self

    def __exit__(self, *_exc):
        _local.autonomy = False
        return False


def log(tipo, detalle, con_usuario=None, origen=""):
    """Registra una actividad breve. Seguro de llamar siempre (nunca lanza).

    con_usuario=None -> se decide por el contexto (True salvo en autonomy_window).
    """
    if con_usuario is None:
        con_usuario = user_default()
    sink = _sink
    if sink is None:
        return
    try:
        sink(str(tipo or ""), str(detalle or "")[:300], bool(con_usuario), str(origen or ""))
    except Exception as exc:
        print("[actividad] no pude registrar:", exc)
