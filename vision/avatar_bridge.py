"""Puente visión -> avatar (paso 17).

Traduce eventos visuales (gestos del usuario, presencia, emoción) a gestos del
avatar VRM de YUE `set_emotion(nombre, intensidad, duracion_ms)`, con COOLDOWNS
para que no reaccione constantemente:

  - no saludar más de una vez cada 30 s,
  - no comentar la misma emoción sin dejar pasar tiempo,
  - no reaccionar al mismo gesto hasta que desaparezca y vuelva.

Filosofía = ESPEJO EMPÁTICO: si te ve triste/tenso, YUE se preocupa (no imita).

Es Python puro (no importa Qt ni el avatar): se le pasan callbacks y un reloj.
Se prueba sin avatar.
"""
from __future__ import annotations

import time
from typing import Callable

# Emoción estimada del usuario -> gesto empático del avatar (nombre, intensidad, ms).
_EMPATHIC = {
    "happy": ("happy", 0.85, 5000),
    "sad": ("worried", 0.8, 6000),
    "worried": ("worried", 0.8, 5500),
    "angry": ("worried", 0.7, 5000),
    "surprised": ("surprised", 0.85, 4200),
    "tired": ("relaxed", 0.5, 4500),
    "neutral": None,
}

# Gesto del usuario -> reacción del avatar.
_GESTURE_REACTION = {
    "Thumb_Up": ("happy", 0.9, 4000),
    "Thumb_Down": ("confused", 0.6, 4000),
    "Victory": ("happy", 0.95, 4500),
    "ILoveYou": ("shy", 0.85, 5000),
    "Wave": ("happy", 0.8, 4000),
    "Open_Palm": ("happy", 0.7, 3500),
    "Pointing_Up": ("focused", 0.7, 3500),
}


class AvatarBridge:
    def __init__(
        self,
        on_avatar_emotion: Callable[[str, float, int], None],
        on_speak: Callable[[str], None] | None = None,
        greet_cooldown: float = 30.0,
        emotion_cooldown: float = 20.0,
    ) -> None:
        self._avatar = on_avatar_emotion or (lambda *_: None)
        self._speak = on_speak or (lambda *_: None)
        self.greet_cooldown = float(greet_cooldown)
        self.emotion_cooldown = float(emotion_cooldown)
        self._last_greet = 0.0
        self._last_emotion_at = 0.0
        self._last_emotion_key = ""

    def _apply(self, gesto, now: float) -> None:
        if not gesto:
            return
        try:
            self._avatar(gesto[0], float(gesto[1]), int(gesto[2]))
        except Exception:
            pass

    def on_gesture(self, gesture: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        # Los gestos ya llegan solo cuando son NUEVOS (antirrebote del recognizer),
        # pero el saludo tiene su propio enfriamiento adicional.
        if gesture in ("Wave", "Open_Palm"):
            if now - self._last_greet < self.greet_cooldown:
                return
            self._last_greet = now
        self._apply(_GESTURE_REACTION.get(gesture), now)

    def on_emotion(self, emotion: str, confidence: float, now: float | None = None) -> None:
        now = time.time() if now is None else now
        gesto = _EMPATHIC.get(emotion)
        if gesto is None:
            return
        # No repetir la misma emoción sin dejar pasar tiempo.
        if emotion == self._last_emotion_key and (now - self._last_emotion_at) < self.emotion_cooldown:
            return
        self._last_emotion_key = emotion
        self._last_emotion_at = now
        self._apply(gesto, now)

    def on_presence(self, present: bool, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if present:
            if now - self._last_greet >= self.greet_cooldown:
                self._last_greet = now
                self._apply(("happy", 0.7, 3500), now)
        else:
            self._apply(("relaxed", 0.4, 4000), now)  # modo espera tranquilo

    def handle_event(self, event: dict, now: float | None = None) -> None:
        """Punto de entrada único desde `VisionState.subscribe`."""
        etype = event.get("type")
        if etype == "gesture" and event.get("is_new"):
            self.on_gesture(event.get("value"), now)
        elif etype == "presence":
            self.on_presence(bool(event.get("present")), now)
        elif etype == "emotion":
            self.on_emotion(event.get("value"), event.get("confidence", 0.0), now)
