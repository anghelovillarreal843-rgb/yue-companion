"""Bus de eventos de modos (patrón observador).

Permite que otras partes del sistema (interfaz, voz, avatar) se enteren de un
cambio de modo sin acoplarse al gestor. Quien quiera reaccionar solo se suscribe
con un callback; el gestor emite un `ModeEvent` cuando algo cambia.

Puro y sin dependencias de Qt: si un suscriptor falla, no tumba a los demás.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


# Tipos de evento (cadenas simples para no acoplar con enums externos).
ENTER = "enter"     # se acaba de entrar a un modo
EXIT = "exit"       # se acaba de salir de un modo
SWITCH = "switch"   # transición completa (old -> new)


@dataclass(frozen=True)
class ModeEvent:
    type: str                 # ENTER | EXIT | SWITCH
    old_mode: str             # modo previo ("" si no aplica)
    new_mode: str             # modo resultante
    meta: dict = field(default_factory=dict)   # etiqueta, emoji, etc.


class ModeEventBus:
    def __init__(self):
        self._lock = threading.RLock()
        self._subs: list = []

    def subscribe(self, callback) -> None:
        """Registra un callback `fn(ModeEvent)`. Ignora duplicados."""
        with self._lock:
            if callback not in self._subs:
                self._subs.append(callback)

    def unsubscribe(self, callback) -> None:
        with self._lock:
            if callback in self._subs:
                self._subs.remove(callback)

    def emit(self, event: ModeEvent) -> None:
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(event)
            except Exception as exc:  # un suscriptor roto no afecta al resto
                print("[modos] suscriptor de evento falló:", exc)
