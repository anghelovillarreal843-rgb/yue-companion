"""Sistema de atención (punto 7 del pedido).

A partir del flujo de presencia + posición del rostro, decide el estado:

    "atento"          -> hay rostro y está mirando de frente (centrado)
    "presente"        -> hay rostro pero no claramente atento
    "ausente"         -> no se detecta a la persona
    (evento) rostro_perdido  -> había rostro y desapareció
    (evento) ausencia_larga  -> lleva ausente más de X (p. ej. 10 min)

Es PURO y determinista: se le pasan muestras con su marca de tiempo y devuelve
qué transiciones ocurrieron. No sabe de cámara ni de IA, así que se prueba con
datos sintéticos. La "mirada" es una aproximación por la posición del rostro
(no hay estimación de gaze real): rostro centrado y de tamaño razonable = atento.
"""
from __future__ import annotations

from dataclasses import dataclass, field

try:
    import config  # type: ignore
except Exception:  # pragma: no cover
    config = None  # type: ignore


def _cfg(name: str, default):
    return getattr(config, name, default) if config is not None else default


@dataclass
class AttentionUpdate:
    state: str                          # "atento" | "presente" | "ausente"
    presence_changed: bool = False      # cambió present<->absent en esta muestra
    face_lost: bool = False             # present -> absent en esta muestra
    attention_changed: bool = False     # cambió la etiqueta de atención
    long_absence: bool = False          # se cruzó el umbral de ausencia larga
    absent_seconds: float = 0.0         # cuánto lleva ausente (0 si presente)
    events: list = field(default_factory=list)  # nombres legibles de lo ocurrido


class AttentionDetector:
    def __init__(
        self,
        absence_seconds: float | None = None,
        center_band: float | None = None,
    ) -> None:
        # Segundos de ausencia continua para avisar (10 min por defecto).
        self.absence_seconds = float(
            absence_seconds if absence_seconds is not None
            else _cfg("VISION_ABSENCE_SECONDS", 600.0)
        )
        # Semiancho de la "zona central" (0..0.5). Dentro de ella el rostro se
        # considera mirando de frente. 0.22 => banda central de ~44% del cuadro.
        self.center_band = float(
            center_band if center_band is not None
            else _cfg("VISION_ATTENTION_CENTER_BAND", 0.22)
        )

        self._present = False
        self._state = "ausente"
        self._absent_since: float | None = None
        self._absence_fired = False

    @property
    def state(self) -> str:
        return self._state

    @property
    def present(self) -> bool:
        return self._present

    def update(self, present: bool, face_position: dict | None, now: float) -> AttentionUpdate:
        present = bool(present)
        upd = AttentionUpdate(state=self._state)

        # -- cambio de presencia --
        if present != self._present:
            upd.presence_changed = True
            upd.events.append("apareció" if present else "desapareció")
            if not present:
                upd.face_lost = True
                upd.events.append("rostro_perdido")
                self._absent_since = now
                self._absence_fired = False
            else:
                self._absent_since = None
                self._absence_fired = False
            self._present = present

        # -- etiqueta de atención --
        if present:
            new_state = "atento" if self._is_centered(face_position) else "presente"
            upd.absent_seconds = 0.0
        else:
            new_state = "ausente"
            if self._absent_since is None:
                self._absent_since = now
            upd.absent_seconds = max(0.0, now - self._absent_since)
            # aviso único al cruzar el umbral de ausencia larga
            if not self._absence_fired and upd.absent_seconds >= self.absence_seconds:
                self._absence_fired = True
                upd.long_absence = True
                upd.events.append("ausencia_larga")

        if new_state != self._state:
            upd.attention_changed = True
            upd.events.append(f"atención:{new_state}")
            self._state = new_state
        upd.state = self._state
        return upd

    def _is_centered(self, face_position: dict | None) -> bool:
        if not face_position:
            return False
        try:
            x = float(face_position.get("x", 0.5))
            y = float(face_position.get("y", 0.5))
        except (TypeError, ValueError):
            return False
        return abs(x - 0.5) <= self.center_band and abs(y - 0.5) <= self.center_band
