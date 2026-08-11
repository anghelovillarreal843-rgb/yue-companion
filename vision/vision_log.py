"""Logs de percepción visual con el prefijo `[vision]`.

El problema de registrar visión es que corre a 10-15 FPS: un `print` por
detección llena la consola en segundos y deja de servir para depurar. Aquí se
registra por CAMBIO, no por fotograma:

    log_vision("rostro", "1 rostro · centro · cerca")   -> solo si cambió
    log_evento("Gesto: saludo")                          -> siempre (es puntual)

Salida:

    [vision] Rostro detectado: 1 rostro · centro · cerca
    [vision] Emoción: feliz (0.72)
    [vision] Objeto: botella
    [vision] Gesto: saludo (mano derecha)
    [vision] Texto leído: "Reunión viernes 7"

Escribe por `logging` (canal `vision.live`) y, si `VISION_DEBUG=true`, también
por consola con `print`, que es lo que se ve al lanzar YUE desde la terminal.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("vision.live")

_lock = threading.Lock()
_ultimo: dict[str, tuple[str, float]] = {}
_consola = False
_min_gap = 1.0          # segundos mínimos entre repeticiones del MISMO canal


def configurar(*, consola: bool = False, min_gap: float = 1.0) -> None:
    """Ajusta la salida. `consola=True` imprime además por stdout."""
    global _consola, _min_gap
    _consola = bool(consola)
    _min_gap = float(min_gap)


def _emitir(texto: str) -> None:
    log.info(texto)
    if _consola:
        try:
            print(f"[vision] {texto}", flush=True)
        except Exception:
            pass


def log_vision(canal: str, texto: str, *, force: bool = False) -> bool:
    """Registra SOLO si el mensaje de ese canal cambió. True si se registró.

    `canal` es la categoría estable ("rostro", "emocion", "postura"…). Así una
    persona quieta frente a la cámara no genera una línea por fotograma, pero en
    cuanto cambia algo se ve inmediatamente.
    """
    if not texto:
        return False
    now = time.time()
    with _lock:
        previo, ts = _ultimo.get(canal, ("", 0.0))
        if not force and texto == previo and (now - ts) < _min_gap:
            return False
        if not force and texto == previo:
            # Mismo texto pero ya pasó el hueco mínimo: refresca la marca sin
            # volver a escribir. Evita el goteo de líneas idénticas.
            _ultimo[canal] = (texto, now)
            return False
        _ultimo[canal] = (texto, now)
    _emitir(texto)
    return True


def log_evento(texto: str) -> None:
    """Registra un evento PUNTUAL (gesto, texto leído, acción). Siempre sale."""
    if texto:
        _emitir(texto)


def reset() -> None:
    with _lock:
        _ultimo.clear()
