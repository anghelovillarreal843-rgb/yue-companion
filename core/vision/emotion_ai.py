"""Motor de emociones con DeepFace (punto 3 del pedido).

Analiza el recorte del rostro y devuelve una de las emociones del documento:

    happy · sad · angry · fear · surprise · neutral · disgust

con un nivel de confianza (0..100):

    {"emotion": "happy", "confidence": 92}

CLAVE DE RENDIMIENTO: DeepFace NO se ejecuta en cada fotograma. Se llama, como
mucho, una vez cada `emotion_interval` segundos (8/5/2 s según LOW/MEDIUM/HIGH).
Entre análisis se devuelve el último resultado en caché. La cadencia la marca el
perfil; aquí solo se respeta.

Degradable: si DeepFace (o TensorFlow) no está instalado, `available` es False y
`analyze` devuelve None. El resto del sistema sigue funcionando (presencia,
atención, avatar) sin emociones.

Inyectable: se puede pasar un `analyzer` propio (por ejemplo en las pruebas) que
reciba el ROI y devuelva {"emotion","confidence"}, evitando cargar DeepFace.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional


# Emociones válidas EXACTAS del documento (las mismas que produce DeepFace).
VALID_EMOTIONS = ("happy", "sad", "angry", "fear", "surprise", "neutral", "disgust")


@dataclass(frozen=True)
class EmotionReading:
    emotion: str        # una de VALID_EMOTIONS
    confidence: int     # 0..100
    at: float           # timestamp del análisis

    def as_dict(self) -> dict:
        return {"emotion": self.emotion, "confidence": int(self.confidence)}


Analyzer = Callable[[object], Optional[dict]]


class EmotionAI:
    def __init__(self, emotion_interval: float = 5.0, analyzer: Analyzer | None = None) -> None:
        self.emotion_interval = max(0.5, float(emotion_interval))
        self._analyzer = analyzer
        self._deepface_ready: bool | None = None if analyzer is None else True
        self._last: EmotionReading | None = None
        # Sentinela muy negativo: la PRIMERA llamada siempre corre (no se salta
        # por creer que "acaba de analizar" en t=0).
        self._last_run_at = float("-inf")

    @property
    def available(self) -> bool:
        if self._analyzer is not None:
            return True
        if self._deepface_ready is None:
            self._deepface_ready = self._probe_deepface()
        return bool(self._deepface_ready)

    def set_interval(self, seconds: float) -> None:
        self.emotion_interval = max(0.5, float(seconds))

    def latest(self) -> EmotionReading | None:
        return self._last

    def analyze(self, roi, now: float | None = None, force: bool = False) -> EmotionReading | None:
        """Devuelve una lectura si toca analizar; si no, la última en caché.

        `roi` es el recorte BGR del rostro (ndarray). `force=True` ignora la
        cadencia (útil para pruebas o para un análisis puntual bajo demanda).
        """
        now = time.time() if now is None else now
        if not force and (now - self._last_run_at) < self.emotion_interval:
            return self._last
        if roi is None:
            return self._last

        self._last_run_at = now
        reading = self._run(roi, now)
        if reading is not None:
            self._last = reading
        return reading

    # -------------------------------------------------------------------
    def _run(self, roi, now: float) -> EmotionReading | None:
        if self._analyzer is not None:
            try:
                data = self._analyzer(roi) or None
            except Exception as exc:
                print("[vision.emotion] analizador inyectado falló:", exc)
                return None
            return self._from_dict(data, now)

        if not self.available:
            return None
        return self._from_dict(self._run_deepface(roi), now)

    @staticmethod
    def _from_dict(data: dict | None, now: float) -> EmotionReading | None:
        if not data:
            return None
        emo = str(data.get("emotion", "")).strip().lower()
        if emo not in VALID_EMOTIONS:
            return None
        try:
            conf = int(round(float(data.get("confidence", 0))))
        except (TypeError, ValueError):
            conf = 0
        conf = max(0, min(100, conf))
        return EmotionReading(emotion=emo, confidence=conf, at=now)

    # -- DeepFace real (perezoso) --
    def _probe_deepface(self) -> bool:
        try:
            import deepface  # noqa: F401
            return True
        except Exception as exc:
            print("[vision.emotion] DeepFace no disponible (emociones desactivadas):", exc)
            return False

    def _run_deepface(self, roi) -> dict | None:
        try:
            from deepface import DeepFace

            # Ya recibimos SOLO la cara -> saltamos la re-detección para ir rápido
            # y no fallar si el recorte es ajustado (enforce_detection=False).
            result = DeepFace.analyze(
                roi,
                actions=["emotion"],
                enforce_detection=False,
                detector_backend="skip",
                silent=True,
            )
            if isinstance(result, list):
                result = result[0] if result else {}
            dominant = str(result.get("dominant_emotion", "")).strip().lower()
            scores = result.get("emotion", {}) or {}
            conf = float(scores.get(dominant, 0.0))
            if dominant:
                return {"emotion": dominant, "confidence": conf}
        except Exception as exc:
            print("[vision.emotion] DeepFace.analyze falló:", exc)
        return None
