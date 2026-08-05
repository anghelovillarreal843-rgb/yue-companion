"""Motor de expresiones y emociones (paso 6 del pedido).

Interpreta los BLENDSHAPES del Face Landmarker (movimientos faciales medibles)
para estimar una expresión observable y, con prudencia, una emoción aproximada.

IMPORTANTE (queda documentado a propósito):
  - Los blendshapes representan MOVIMIENTOS de la cara, no sentimientos.
  - La emoción es solo una ESTIMACIÓN; nunca se afirma con certeza.
  - YUE debe hablar con frases prudentes ("parece que…", "tal vez…").

Suavizado temporal para evitar cambios bruscos:
  - ventana de varias observaciones (mediana/voto mayoritario),
  - histéresis: exige varias lecturas seguidas antes de cambiar de estado,
  - tiempo mínimo antes de volver a cambiar,
  - confianza mínima configurable.

Es Python puro (sin MediaPipe): se le pasan blendshapes como dict {nombre: valor}
y se prueba sin cámara.
"""
from __future__ import annotations

import time
from collections import Counter, deque


def _g(bs: dict, *names: str) -> float:
    """Máximo de los blendshapes pedidos (0 si no están)."""
    return max((float(bs.get(n, 0.0)) for n in names), default=0.0)


def classify_expression(bs: dict) -> tuple[str, float]:
    """Traduce blendshapes a una expresión observable + una confianza 0..1.

    Nombres de blendshape según el catálogo estándar de MediaPipe Face
    Landmarker (ARKit-like): mouthSmileLeft/Right, jawOpen, browInnerUp, etc.
    """
    smile = _g(bs, "mouthSmileLeft", "mouthSmileRight")
    frown = _g(bs, "mouthFrownLeft", "mouthFrownRight")
    jaw_open = _g(bs, "jawOpen")
    brow_up = _g(bs, "browInnerUp")
    brow_down = _g(bs, "browDownLeft", "browDownRight")
    eye_wide = _g(bs, "eyeWideLeft", "eyeWideRight")
    eye_blink = _g(bs, "eyeBlinkLeft", "eyeBlinkRight")
    eye_squint = _g(bs, "eyeSquintLeft", "eyeSquintRight")
    mouth_press = _g(bs, "mouthPressLeft", "mouthPressRight")

    # Reglas ordenadas por especificidad. Cada una da (expresión, confianza).
    candidates: list[tuple[str, float]] = []

    if smile > 0.35:
        candidates.append(("smiling", min(1.0, smile)))
    if jaw_open > 0.45 and (brow_up > 0.3 or eye_wide > 0.3):
        candidates.append(("surprised", min(1.0, 0.5 * jaw_open + 0.5 * max(brow_up, eye_wide))))
    if frown > 0.25 and brow_up > 0.25:
        candidates.append(("sad_expression", min(1.0, 0.5 * frown + 0.5 * brow_up)))
    if brow_down > 0.35 and (eye_squint > 0.25 or mouth_press > 0.3):
        candidates.append(("angry_expression", min(1.0, 0.5 * brow_down + 0.5 * max(eye_squint, mouth_press))))
    if mouth_press > 0.4 and brow_down > 0.2 and smile < 0.2:
        candidates.append(("tense", min(1.0, 0.5 * mouth_press + 0.5 * brow_down)))
    if eye_blink > 0.6 and smile < 0.2 and jaw_open < 0.2:
        candidates.append(("sleepy", min(1.0, eye_blink)))
    if brow_up > 0.4 and eye_squint > 0.25 and smile < 0.2:
        candidates.append(("confused", min(1.0, 0.5 * brow_up + 0.5 * eye_squint)))

    if not candidates:
        # Neutralidad: confianza moderada según lo "quieta" que esté la cara.
        movement = max(smile, frown, jaw_open, brow_up, brow_down, eye_wide)
        return "neutral", round(max(0.3, 1.0 - movement), 3)

    candidates.sort(key=lambda c: c[1], reverse=True)
    expr, conf = candidates[0]
    return expr, round(conf, 3)


# Expresión observable -> emoción estimada (prudente).
_EXPRESSION_TO_EMOTION = {
    "smiling": "happy",
    "surprised": "surprised",
    "sad_expression": "sad",
    "angry_expression": "angry",
    "tense": "worried",
    "sleepy": "tired",
    "confused": "worried",
    "thinking": "neutral",
    "neutral": "neutral",
}


def expression_to_emotion(expression: str) -> str:
    return _EXPRESSION_TO_EMOTION.get(expression, "neutral")


class ExpressionSmoother:
    """Suavizado temporal con voto mayoritario, histéresis y confianza mínima.

    Se mantiene UNO por rostro rastreado. `update()` recibe la expresión cruda de
    un fotograma y devuelve la expresión ESTABLE que debe reportarse.
    """

    def __init__(
        self,
        window: int = 6,
        min_confidence: float = 0.35,
        min_hold: float = 0.8,      # segundos mínimos antes de cambiar de estado
        streak_to_change: int = 3,  # lecturas seguidas necesarias para cambiar
    ) -> None:
        self.window = max(1, int(window))
        self.min_confidence = float(min_confidence)
        self.min_hold = float(min_hold)
        self.streak_to_change = max(1, int(streak_to_change))

        self._buf: deque[str] = deque(maxlen=self.window)
        self._stable = "neutral"
        self._stable_conf = 0.3
        self._changed_at = 0.0
        self._pending = None
        self._pending_streak = 0

    def update(self, expression: str, confidence: float, now: float | None = None) -> tuple[str, float]:
        now = time.time() if now is None else now
        if confidence < self.min_confidence:
            expression = "neutral"
        self._buf.append(expression)

        # Voto mayoritario de la ventana.
        winner, _count = Counter(self._buf).most_common(1)[0]

        if winner == self._stable:
            self._pending = None
            self._pending_streak = 0
            self._stable_conf = max(self._stable_conf, confidence)
            return self._stable, round(self._stable_conf, 3)

        # Candidato distinto: exige racha + tiempo mínimo mantenido.
        if winner == self._pending:
            self._pending_streak += 1
        else:
            self._pending = winner
            self._pending_streak = 1

        enough_streak = self._pending_streak >= self.streak_to_change
        enough_time = (now - self._changed_at) >= self.min_hold
        if enough_streak and enough_time:
            self._stable = winner
            self._stable_conf = confidence
            self._changed_at = now
            self._pending = None
            self._pending_streak = 0
        return self._stable, round(self._stable_conf, 3)
