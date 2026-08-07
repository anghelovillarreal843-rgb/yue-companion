"""Analizador de gestos con antirrebote (punto 7 del pedido).

Convierte el flujo continuo del `HandTracker` en EVENTOS discretos, aplicando:
  - tiempo mínimo del gesto (no cuenta un fotograma suelto),
  - histéresis (para dejar de estar activo hace falta ausencia sostenida),
  - enfriamiento entre eventos del mismo gesto,
  - identificador estable de mano.

Salida (forma exacta del documento):

    {"event": "gesture_detected", "gesture": "thumbs_up",
     "hand": "right", "confidence": 0.93, "duration": 1.2}

Reconoce al menos: mano abierta, puño, señalar, pulgar arriba, pulgar abajo,
victoria, OK, pinza, saludo, llamada, y el NÚMERO mostrado con los dedos (0-5
por mano, 0-10 con las dos). Son 11 gestos + conteo, por encima de los ocho que
exige el criterio de aceptación.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from vision.tracking.hand_tracker import GESTURES_ES

# Gestos que merecen un evento (el resto es ruido).
REPORTABLE = ("thumbs_up", "thumbs_down", "victory", "open_palm", "fist",
              "pointing", "ok", "pinch", "wave", "call_me")


@dataclass
class GestureReading:
    event: str = "gesture_detected"
    gesture: str = ""
    hand: str = "unknown"
    confidence: float = 0.0
    duration: float = 0.0
    finger_count: int = 0
    gesture_es: str = ""

    def as_dict(self) -> dict:
        return {
            "event": self.event,
            "gesture": self.gesture,
            "hand": self.hand,
            "confidence": round(float(self.confidence), 3),
            "duration": round(float(self.duration), 2),
        }


class GestureAnalyzer:
    def __init__(
        self,
        *,
        min_duration: float = 0.5,
        cooldown: float = 4.0,
        release_after: float = 0.6,
        min_confidence: float = 0.6,
        event_manager=None,
    ) -> None:
        self.min_duration = float(min_duration)
        self.cooldown = float(cooldown)
        self.release_after = float(release_after)
        self.min_confidence = float(min_confidence)
        self._events = event_manager
        self._active: dict[str, dict] = {}
        self._last_emit: dict[str, float] = {}
        self._last_reading: GestureReading | None = None

    # ------------------------------------------------------------------
    def update(self, hand_states, now: float | None = None) -> list[GestureReading]:
        now = time.time() if now is None else float(now)
        out: list[GestureReading] = []
        seen: set[str] = set()

        for hs in hand_states or []:
            gesture = getattr(hs, "gesture", "none")
            side = getattr(hs, "side", "unknown")
            conf = float(getattr(hs, "gesture_confidence", 0.0))
            slot = f"{side}:{gesture}"
            seen.add(side)

            if gesture not in REPORTABLE or conf < self.min_confidence:
                continue

            state = self._active.get(side)
            if state is None or state["gesture"] != gesture:
                self._active[side] = {"gesture": gesture, "started_at": now,
                                      "last_seen": now, "emitted": False, "conf": conf}
                continue

            state["last_seen"] = now
            state["conf"] = 0.7 * state["conf"] + 0.3 * conf
            duration = now - state["started_at"]
            if state["emitted"] or duration < self.min_duration:
                continue

            last = self._last_emit.get(slot, 0.0)
            if last and (now - last) < self.cooldown:
                continue

            state["emitted"] = True
            self._last_emit[slot] = now
            reading = GestureReading(
                gesture=gesture,
                hand=side,
                confidence=round(state["conf"], 3),
                duration=round(duration, 2),
                finger_count=int(getattr(hs, "finger_count", 0)),
                gesture_es=GESTURES_ES.get(gesture, gesture),
            )
            self._last_reading = reading
            out.append(reading)
            if self._events is not None:
                self._events.observe(
                    "gesture_detected", gesture, reading.confidence,
                    source="gesture_analyzer",
                    data={"hand": side, "gesture_es": reading.gesture_es},
                    min_duration=0.0, cooldown=self.cooldown, now=now,
                )

        # Histéresis: una mano que desaparece libera su gesto tras un tiempo.
        for side in list(self._active):
            if side not in seen and (now - self._active[side]["last_seen"]) > self.release_after:
                self._active.pop(side, None)
        return out

    # ------------------------------------------------------------------
    @staticmethod
    def count_fingers(hand_states) -> tuple[int, str]:
        """Número mostrado con los dedos y una frase para decirlo."""
        states = list(hand_states or [])
        if not states:
            return -1, "No veo tus manos ahora mismo."
        total = sum(int(getattr(s, "finger_count", 0)) for s in states)
        if len(states) == 1:
            side = getattr(states[0], "side", "unknown")
            lado = {"left": "izquierda", "right": "derecha"}.get(side, "")
            sufijo = f" con la mano {lado}" if lado else ""
            return total, f"Cuento {total} dedo{'s' if total != 1 else ''}{sufijo}."
        return total, f"Cuento {total} dedos entre las dos manos."

    def last(self) -> GestureReading | None:
        return self._last_reading

    def describe_last(self) -> str:
        r = self._last_reading
        if r is None:
            return "No he visto ningún gesto claro todavía."
        lado = {"left": "izquierda", "right": "derecha"}.get(r.hand, "")
        nombre = r.gesture_es or GESTURES_ES.get(r.gesture, r.gesture)
        return f"Vi un gesto de {nombre}" + (f" con la mano {lado}." if lado else ".")

    def reset(self) -> None:
        self._active.clear()
        self._last_emit.clear()
        self._last_reading = None
