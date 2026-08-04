"""Bus de eventos interno de YUE (FASE 2 · Arquitectura).

Permite que los módulos se avisen entre sí SIN conocerse directamente (menos
acoplamiento). Es seguro entre hilos y aísla los errores: si un suscriptor
revienta, los demás siguen recibiendo el evento.

Ejemplo:
    bus = EventBus()
    off = bus.subscribe("emocion_cambiada", lambda **d: print(d))
    bus.publish("emocion_cambiada", emocion="feliz", intensidad=0.8)
    off()  # cancelar la suscripción
"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Callable


class EventBus:
    def __init__(self):
        self._lock = threading.RLock()
        self._subs: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event: str, callback: Callable) -> Callable[[], None]:
        """Suscribe un callback a un evento. Devuelve una función para cancelar."""
        with self._lock:
            self._subs[event].append(callback)

        def _unsubscribe():
            with self._lock:
                try:
                    self._subs[event].remove(callback)
                except ValueError:
                    pass

        return _unsubscribe

    def publish(self, event: str, **data) -> int:
        """Emite un evento. Devuelve a cuántos suscriptores llegó sin error."""
        with self._lock:
            listeners = list(self._subs.get(event, ()))
        ok = 0
        for cb in listeners:
            try:
                cb(**data)
                ok += 1
            except Exception:
                # Un suscriptor con fallo NUNCA debe tumbar a los demás.
                pass
        return ok

    def clear(self, event: str | None = None):
        with self._lock:
            if event is None:
                self._subs.clear()
            else:
                self._subs.pop(event, None)
