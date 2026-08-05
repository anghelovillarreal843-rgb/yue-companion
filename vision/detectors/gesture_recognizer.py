"""Gesture Recognizer + gestos dinámicos y antirrebote (paso 8).

Usa MediaPipe Gesture Recognizer (que ya trae los 21 landmarks por mano, así que
NO corremos Hand Landmarker por separado). Reconoce los gestos estáticos del
catálogo y, sobre los landmarks, deriva gestos dinámicos por análisis temporal:

  - Wave: palma abierta moviéndose de lado a lado.
  - SwipeLeft / SwipeRight: desplazamiento horizontal marcado y rápido.

ANTIRREBOTE: un gesto sostenido NO dispara la misma acción cada fotograma. Se
emite `is_new=True` solo cuando el gesto aparece; hasta que desaparezca y vuelva,
no se vuelve a marcar como nuevo.
"""
from __future__ import annotations

import logging
import time
from collections import deque

from vision.detectors.base import BaseDetector, make_mp_image, mp_tasks
from vision.observations import GestureEvent

log = logging.getLogger("vision.gesture")


class GestureRecognizerModule(BaseDetector):
    name = "gesture_recognizer"

    def __init__(self, model_path, min_confidence: float = 0.6, max_hands: int = 2,
                 min_gap: float = 0.6) -> None:
        super().__init__()
        self.model_path = str(model_path) if model_path else None
        self.min_confidence = float(min_confidence)
        self.max_hands = int(max_hands)
        self.min_gap = float(min_gap)  # antirrebote: separación mínima entre "nuevos"
        # Estado por mano para antirrebote y gestos dinámicos.
        self._active: dict[str, dict] = {}
        self._wrist_hist: dict[str, deque] = {}

    def _build(self):
        if not self.model_path:
            return None
        mp, mp_vision = mp_tasks()
        if mp is None:
            return None
        base_options = mp.tasks.BaseOptions(model_asset_path=self.model_path)
        options = mp_vision.GestureRecognizerOptions(
            base_options=base_options,
            running_mode=mp_vision.RunningMode.IMAGE,
            num_hands=self.max_hands,
            min_hand_detection_confidence=self.min_confidence,
        )
        return mp_vision.GestureRecognizer.create_from_options(options)

    def detect(self, rgb_frame, now: float | None = None) -> list[GestureEvent]:
        now = time.time() if now is None else now
        if rgb_frame is None or not self.ensure_loaded():
            return []
        image = make_mp_image(rgb_frame)
        if image is None:
            return []
        try:
            result = self._impl.recognize(image)
        except Exception as exc:
            log.debug("fallo en recognize(): %s", exc)
            return []

        gestures_all = getattr(result, "gestures", None) or []
        handed_all = getattr(result, "handedness", None) or []
        landmarks_all = getattr(result, "hand_landmarks", None) or []

        seen_hands: set[str] = set()
        events: list[GestureEvent] = []

        for i, glist in enumerate(gestures_all):
            if not glist:
                continue
            top = glist[0]
            name = top.category_name or "None"
            conf = float(top.score)
            side = "unknown"
            if i < len(handed_all) and handed_all[i]:
                side = (handed_all[i][0].category_name or "unknown").lower()

            # Gesto dinámico a partir del movimiento de la muñeca (landmark 0).
            dyn = None
            if i < len(landmarks_all) and landmarks_all[i]:
                dyn = self._dynamic(side, landmarks_all[i][0].x, name, now)

            final = dyn or (name if conf >= self.min_confidence else None)
            if not final or final == "None":
                continue
            seen_hands.add(side)
            ev = self._emit(side, final, conf, now)
            if ev is not None:
                events.append(ev)

        # Limpia manos que desaparecieron (para permitir re-disparo futuro).
        for side in list(self._active.keys()):
            if side not in seen_hands:
                self._active.pop(side, None)
                self._wrist_hist.pop(side, None)
        return events

    def _dynamic(self, side: str, wrist_x: float, static_name: str, now: float):
        hist = self._wrist_hist.setdefault(side, deque(maxlen=10))
        hist.append((now, wrist_x))
        if len(hist) < 5:
            return None
        xs = [p[1] for p in hist]
        span = max(xs) - min(xs)
        # Cambios de dirección (oscilación) => saludo si la palma está abierta.
        direction_changes = 0
        for a, b, c in zip(xs, xs[1:], xs[2:]):
            if (b - a) * (c - b) < 0:
                direction_changes += 1
        if static_name == "Open_Palm" and span > 0.12 and direction_changes >= 2:
            return "Wave"
        # Desplazamiento neto rápido => swipe.
        net = xs[-1] - xs[0]
        if abs(net) > 0.2 and direction_changes <= 1:
            return "SwipeRight" if net > 0 else "SwipeLeft"
        return None

    def _emit(self, side: str, gesture: str, conf: float, now: float):
        state = self._active.get(side)
        if state and state["gesture"] == gesture:
            # Mismo gesto sostenido: no es nuevo; solo actualiza duración.
            state["duration"] = now - state["started_at"]
            return GestureEvent(gesture=gesture, hand=side, confidence=conf,
                                started_at=state["started_at"],
                                duration=round(state["duration"], 3), is_new=False)
        # Gesto distinto o mano nueva: respeta el antirrebote temporal.
        if state and (now - state["started_at"]) < self.min_gap and state["gesture"] == gesture:
            return None
        self._active[side] = {"gesture": gesture, "started_at": now, "duration": 0.0}
        log.info("Gesto detectado: %s (%s)", gesture, side)
        return GestureEvent(gesture=gesture, hand=side, confidence=conf,
                            started_at=now, duration=0.0, is_new=True)
