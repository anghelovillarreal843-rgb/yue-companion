"""Estado de visión combinado y seguro entre hilos (paso 14).

`VisionState` guarda el último resultado de cada módulo con su marca de tiempo
monotónica, sabe cuándo un resultado está VENCIDO (para no mezclar información
vieja con nueva) y permite suscribirse a EVENTOS importantes (gestos nuevos,
aparición/desaparición de personas, etc.).

Es la única fuente de verdad que consulta el motor de diálogo y el avatar.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


class VisionState:
    def __init__(self, default_ttl: float = 2.0) -> None:
        self._lock = threading.RLock()
        self._sections: dict[str, dict] = {}   # nombre -> {"data":..., "ts":...}
        self._ttl: dict[str, float] = {}
        self._default_ttl = float(default_ttl)
        self._subs: list[Callable[[dict], None]] = []

    # -- escritura por módulo -----------------------------------------
    def set(self, section: str, data: Any, ttl: float | None = None) -> None:
        with self._lock:
            self._sections[section] = {"data": data, "ts": time.monotonic()}
            self._ttl[section] = self._default_ttl if ttl is None else float(ttl)

    def get(self, section: str, default: Any = None) -> Any:
        with self._lock:
            entry = self._sections.get(section)
            if entry is None:
                return default
            ttl = self._ttl.get(section, self._default_ttl)
            if ttl > 0 and (time.monotonic() - entry["ts"]) > ttl:
                return default  # vencido: no se mezcla con lo actual
            return entry["data"]

    def age(self, section: str) -> float:
        with self._lock:
            entry = self._sections.get(section)
            if entry is None:
                return float("inf")
            return time.monotonic() - entry["ts"]

    def is_stale(self, section: str) -> bool:
        with self._lock:
            entry = self._sections.get(section)
            if entry is None:
                return True
            ttl = self._ttl.get(section, self._default_ttl)
            return ttl > 0 and (time.monotonic() - entry["ts"]) > ttl

    # -- eventos -------------------------------------------------------
    def subscribe(self, callback: Callable[[dict], None]) -> Callable:
        with self._lock:
            self._subs.append(callback)
        return callback

    def emit_event(self, event: dict) -> None:
        with self._lock:
            targets = list(self._subs)
        for cb in targets:
            try:
                cb(event)
            except Exception:
                pass
