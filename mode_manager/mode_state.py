"""Estado del modo actual de YUE.

Guarda cuál es el modo activo (companion, teacher, …) de forma segura entre hilos.
Por diseño, YUE SIEMPRE arranca en el modo por defecto (companion): el modo se
mantiene durante toda la sesión y solo cambia cuando el usuario lo pide, pero no
se "hereda" un modo especial de una ejecución anterior.

Es un módulo puro (sin Qt, sin IA): solo maneja una cadena y avisa por callback.
"""
from __future__ import annotations

import threading

# Identificador del modo por defecto. El resto del sistema NO debe asumir otro.
DEFAULT_MODE = "companion"


class ModeState:
    """Contenedor seguro del modo actual.

    No decide transiciones (eso es del router/manager); solo custodia el valor y
    garantiza el arranque en el modo por defecto.
    """

    def __init__(self, default_mode: str = DEFAULT_MODE):
        self._lock = threading.RLock()
        self._default = default_mode
        self._current = default_mode
        # Marca de tiempo del último cambio, útil para depurar/telemetría ligera.
        self._changed_at = 0.0

    @property
    def default(self) -> str:
        return self._default

    def get(self) -> str:
        with self._lock:
            return self._current

    def is_default(self) -> bool:
        with self._lock:
            return self._current == self._default

    def set(self, mode: str) -> str:
        """Fija el modo actual. Devuelve el valor final (por si se normaliza)."""
        import time
        with self._lock:
            self._current = str(mode)
            self._changed_at = time.time()
            return self._current

    def reset_to_default(self) -> str:
        return self.set(self._default)
