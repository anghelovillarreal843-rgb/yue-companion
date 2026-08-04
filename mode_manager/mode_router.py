"""Router central de modos.

Guarda el registro de modos y es el ÚNICO punto por el que se decide a qué motor
va cada mensaje. Añadir un modo nuevo = registrar una clase/spec con su id; no
hay que tocar la lógica de despacho.

El router no conoce Qt ni la IA: trata a cada modo como un objeto con `handler`
(un llamable `process(text, ctx)`). Quien registra el modo (main.py) decide qué
hace ese handler.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteDecision:
    """Resultado de resolver un mensaje: ¿es un cambio de modo o va al modo actual?"""
    switched: bool
    mode: str                 # modo resultante (nuevo si switched, actual si no)
    intent_kind: str = ""     # "activate" | "deactivate" | ""
    target_mode: str = ""     # modo objetivo del intent, si lo hubo


class ModeRouter:
    def __init__(self, state, default_mode: str):
        self._state = state
        self._default = default_mode
        self._registry: dict = {}

    # ---- registro ----
    def register(self, spec) -> None:
        mode_id = getattr(spec, "id", None)
        if not mode_id:
            raise ValueError("Un modo debe tener 'id'.")
        self._registry[mode_id] = spec

    @property
    def registry(self) -> dict:
        return self._registry

    def get(self, mode_id: str):
        return self._registry.get(mode_id)

    def has(self, mode_id: str) -> bool:
        return mode_id in self._registry

    def meta(self, mode_id: str) -> dict:
        spec = self._registry.get(mode_id)
        if spec is None:
            return {"label": mode_id, "emoji": ""}
        return {"label": getattr(spec, "label", mode_id), "emoji": getattr(spec, "emoji", "")}

    # ---- despacho al motor del modo actual (patrón "router -> engine") ----
    def dispatch(self, text: str, ctx=None):
        """Envía el mensaje al handler del modo ACTUAL, si tiene uno.

        Devuelve lo que devuelva el handler (normalmente None: el trabajo suele
        ser asíncrono). Si el modo no tiene handler, no hace nada.
        """
        spec = self._registry.get(self._state.get())
        handler = getattr(spec, "handler", None) if spec else None
        if callable(handler):
            return handler(text, ctx)
        return None
