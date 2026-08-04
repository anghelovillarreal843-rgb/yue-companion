"""Qué se está reproduciendo AHORA (título/artista), compartido entre módulos.

Módulo ADITIVO y minúsculo. El problema: `core/youtube.py` SÍ sabe el título de
lo que el usuario pidió reproducir, pero `core/system_audio.py` solo oye rasgos
acústicos (tempo, brillo, graves) y nunca se entera de que suena "tal canción".

Este módulo es el buzón intermedio: youtube deja aquí el título al reproducir y
system_audio lo lee para que YUE comente como alguien que SABE lo que escucha, no
como un sensor de ritmo. Puro estado en memoria, sin dependencias pesadas, seguro
entre hilos. Si algo falla, todo el que lo use cae al comportamiento de siempre.

El título CADUCA solo (por defecto ~12 min): así no arrastramos "ahora suena X"
de hace una hora si el usuario cambió de contenido sin pasar por YouTube. Además,
quien detecte que ya no suena nada (silencio sostenido) puede llamar a `clear()`
para no colar un título viejo en el siguiente contenido.
"""
from __future__ import annotations

import threading
import time

try:
    import config
    _TTL = float(getattr(config, "NOW_PLAYING_TTL", 720.0))  # 12 min por defecto
except Exception:  # pragma: no cover - sin config, valor razonable
    _TTL = 720.0

_lock = threading.Lock()
_title: str = ""
_artist: str = ""
_ts: float = 0.0


def set_now_playing(title: str, artist: str = "") -> None:
    """Registra lo que se acaba de poner a sonar. Llamado por quien SÍ sabe el
    título (p. ej. youtube.resolver). Sin título válido, no hace nada."""
    global _title, _artist, _ts
    t = (title or "").strip()
    if not t:
        return
    with _lock:
        _title = t
        _artist = (artist or "").strip()
        _ts = time.time()


def get_now_playing() -> tuple[str, str, float]:
    """(title, artist, ts). Si no hay nada vigente o ya caducó, ("", "", 0.0).

    La caducidad se comprueba aquí: quien lee nunca ve un título rancio.
    """
    with _lock:
        title, artist, ts = _title, _artist, _ts
    if not title or not ts:
        return ("", "", 0.0)
    if (time.time() - ts) > _TTL:
        return ("", "", 0.0)
    return (title, artist, ts)


def clear() -> None:
    """Olvida el título actual. Lo usa system_audio cuando detecta que ya no suena
    nada (silencio sostenido), para no arrastrar el título al siguiente contenido."""
    global _title, _artist, _ts
    with _lock:
        _title = ""
        _artist = ""
        _ts = 0.0
