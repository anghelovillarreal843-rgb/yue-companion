"""Analizador de atención (punto 9, señal de apoyo).

Responde a "¿me está mirando?", "¿lleva mucho sin aparecer?", "¿está pendiente
de la pantalla o de otra cosa?". Sirve para dos cosas:

  - dar contexto al motor conversacional (YUE no interrumpe a alguien que está
    claramente concentrado en otra cosa),
  - alimentar al analizador afectivo con `looking_at_camera` ya suavizado.

Usa histéresis para no cambiar de estado por un parpadeo o un giro breve.
Estados: `attentive`, `distracted`, `absent`, `unknown`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from vision.tracking.temporal_smoother import EMA, Hysteresis


@dataclass
class AttentionState:
    state: str = "unknown"
    looking_at_camera: bool = False
    attention_ratio: float = 0.0        # 0..1 en la ventana reciente
    present: bool = False
    absent_for: float = 0.0
    description: str = ""

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "looking_at_camera": self.looking_at_camera,
            "attention_ratio": round(self.attention_ratio, 3),
            "present": self.present,
            "absent_for": round(self.absent_for, 1),
        }


class AttentionAnalyzer:
    def __init__(
        self,
        *,
        yaw_threshold: float = 0.35,
        pitch_threshold: float = 0.40,
        absence_seconds: float = 25.0,
    ) -> None:
        self.yaw_threshold = float(yaw_threshold)
        self.pitch_threshold = float(pitch_threshold)
        self.absence_seconds = float(absence_seconds)
        self._look = Hysteresis(on_threshold=0.6, off_threshold=0.35,
                                min_on=0.4, min_off=0.8)
        self._ratio = EMA(0.15)
        self._last_present = 0.0
        self._present = False
        self._state = AttentionState()

    # ------------------------------------------------------------------
    def update(
        self,
        *,
        face_present: bool,
        head_pose: dict | None = None,
        looking_hint: bool | None = None,
        now: float | None = None,
    ) -> AttentionState:
        now = time.time() if now is None else float(now)

        if face_present:
            self._last_present = now
            self._present = True
        elif self._last_present and (now - self._last_present) > 2.0:
            self._present = False

        absent_for = 0.0 if self._present else (now - self._last_present if self._last_present else 0.0)

        # Puntuación de "mira a la cámara" 0..1.
        score = 0.0
        if face_present:
            if looking_hint is not None:
                score = 0.85 if looking_hint else 0.15
            elif head_pose:
                yaw = abs(float(head_pose.get("yaw", 0.0)))
                pitch = abs(float(head_pose.get("pitch", 0.0)))
                yaw_ok = max(0.0, 1.0 - yaw / max(1e-3, self.yaw_threshold))
                pitch_ok = max(0.0, 1.0 - pitch / max(1e-3, self.pitch_threshold))
                score = max(0.0, min(1.0, 0.6 * yaw_ok + 0.4 * pitch_ok))
            else:
                score = 0.5

        looking = self._look.update(score, now=now)
        ratio = self._ratio.update(1.0 if looking else 0.0)

        if not self._present and absent_for >= self.absence_seconds:
            state = "absent"
        elif not face_present:
            state = "unknown"
        elif looking:
            state = "attentive"
        else:
            state = "distracted"

        self._state = AttentionState(
            state=state,
            looking_at_camera=looking,
            attention_ratio=ratio,
            present=self._present,
            absent_for=absent_for,
            description=self._describe(state, ratio),
        )
        return self._state

    # ------------------------------------------------------------------
    @staticmethod
    def _describe(state: str, ratio: float) -> str:
        if state == "attentive":
            return "Parece que estás pendiente de la cámara."
        if state == "distracted":
            if ratio < 0.2:
                return "Te veo, pero parece que estás mirando otra cosa."
            return "Estás por ahí, mirando a ratos."
        if state == "absent":
            return "Llevas un rato sin aparecer frente a la cámara."
        return ""

    def state(self) -> AttentionState:
        return self._state

    def reset(self) -> None:
        self._look.reset()
        self._ratio.reset()
        self._present = False
        self._last_present = 0.0
        self._state = AttentionState()
