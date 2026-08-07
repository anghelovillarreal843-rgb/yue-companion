"""Detector de escena a partir del fotograma y de los objetos (punto 6).

Dos fuentes que se combinan:

  - señales de la IMAGEN (baratas, con OpenCV/numpy): brillo medio, contraste,
    saturación, "desorden visual" estimado por densidad de bordes,
  - señales SEMÁNTICAS: qué objetos hay, que es lo que de verdad permite decir
    "parece una cocina" o "parece una oficina doméstica".

Nunca afirma: devuelve el tipo de lugar con una confianza, y quien redacta usa
lenguaje prudente ("parece", "probablemente").
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger("vision.scene")

# Objetos que sugieren cada tipo de lugar, con su peso.
SCENE_HINTS: dict[str, dict[str, float]] = {
    "oficina doméstica": {"laptop": 1.0, "keyboard": 0.8, "mouse": 0.7, "monitor": 0.9,
                          "tv": 0.2, "chair": 0.4, "book": 0.3, "cell phone": 0.2,
                          "desk": 0.8},
    "dormitorio": {"bed": 1.2, "pillow": 0.8, "teddy bear": 0.4, "clock": 0.2,
                   "book": 0.2, "chair": 0.1},
    "sala": {"couch": 1.1, "tv": 0.8, "remote": 0.6, "potted plant": 0.4,
             "vase": 0.3, "chair": 0.2},
    "cocina": {"refrigerator": 1.2, "microwave": 1.0, "oven": 1.0, "sink": 0.8,
               "bowl": 0.5, "cup": 0.3, "bottle": 0.3, "knife": 0.4, "fork": 0.4,
               "spoon": 0.4, "banana": 0.3, "apple": 0.3},
    "aula": {"chair": 0.6, "book": 0.7, "backpack": 0.6, "tv": 0.3, "person": 0.3,
             "laptop": 0.2},
    "comedor": {"dining table": 1.1, "chair": 0.5, "bowl": 0.4, "cup": 0.4,
                "fork": 0.4, "wine glass": 0.5},
    "baño": {"toilet": 1.3, "sink": 0.7, "toothbrush": 0.9},
}


@dataclass
class SceneReading:
    """Lectura de escena de un fotograma."""

    scene_type: str = "desconocido"
    scene_confidence: float = 0.0
    lighting: str = "media"                     # muy_baja | baja | media | alta
    brightness: float = 0.0                     # 0..1
    contrast: float = 0.0                       # 0..1
    clutter: str = "medio"                      # ordenado | medio | desordenado
    clutter_score: float = 0.0
    candidates: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "scene_type": self.scene_type,
            "scene_confidence": round(self.scene_confidence, 3),
            "lighting": self.lighting,
            "clutter": self.clutter,
        }


class SceneDetector:
    name = "scene_detector"

    def __init__(self, min_confidence: float = 0.35) -> None:
        self.min_confidence = float(min_confidence)
        self._cv2 = None
        self._np = None
        self._checked = False

    def _ensure_libs(self):
        if self._checked:
            return
        self._checked = True
        try:
            import cv2
            self._cv2 = cv2
        except Exception:
            self._cv2 = None
        try:
            import numpy as np
            self._np = np
        except Exception:
            self._np = None

    @property
    def available(self) -> bool:
        self._ensure_libs()
        return self._np is not None

    # ------------------------------------------------------------------
    def image_signals(self, frame_bgr) -> dict:
        """Brillo, contraste y desorden visual del fotograma."""
        self._ensure_libs()
        np = self._np
        if np is None or frame_bgr is None:
            return {"brightness": 0.0, "contrast": 0.0, "clutter_score": 0.0}
        try:
            arr = frame_bgr
            if arr.ndim == 3:
                # Luminancia aproximada sin depender de OpenCV.
                gray = (0.114 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.299 * arr[:, :, 2])
            else:
                gray = arr
            gray = np.asarray(gray, dtype="float32")
            brightness = float(gray.mean()) / 255.0
            contrast = float(gray.std()) / 128.0
            clutter = 0.0
            cv2 = self._cv2
            if cv2 is not None:
                edges = cv2.Canny(gray.astype("uint8"), 80, 180)
                clutter = float((edges > 0).mean())
            else:
                dx = np.abs(np.diff(gray, axis=1)).mean() if gray.shape[1] > 1 else 0.0
                dy = np.abs(np.diff(gray, axis=0)).mean() if gray.shape[0] > 1 else 0.0
                clutter = float((dx + dy) / 60.0)
            return {
                "brightness": round(brightness, 4),
                "contrast": round(min(1.0, contrast), 4),
                "clutter_score": round(min(1.0, clutter), 4),
            }
        except Exception as exc:
            log.debug("no pude medir la imagen: %s", exc)
            return {"brightness": 0.0, "contrast": 0.0, "clutter_score": 0.0}

    # ------------------------------------------------------------------
    @staticmethod
    def lighting_label(brightness: float) -> str:
        if brightness < 0.12:
            return "muy_baja"
        if brightness < 0.30:
            return "baja"
        if brightness > 0.72:
            return "alta"
        return "media"

    @staticmethod
    def clutter_label(score: float) -> str:
        if score < 0.06:
            return "ordenado"
        if score > 0.16:
            return "desordenado"
        return "medio"

    # ------------------------------------------------------------------
    def classify(self, object_labels: list[str], hint_label: str = "",
                 hint_confidence: float = 0.0) -> tuple[str, float, list]:
        """Tipo de lugar por votación ponderada de los objetos presentes.

        `hint_label` permite inyectar la salida de un clasificador de escenas
        (por ejemplo el `image_classifier` de MediaPipe) sin acoplarse a él.
        """
        labels = {str(l).strip().lower() for l in object_labels or []}
        scores: dict[str, float] = {}
        for scene, hints in SCENE_HINTS.items():
            total = sum(weight for obj, weight in hints.items() if obj in labels)
            if total > 0:
                # Normaliza contra el máximo teórico de esa escena, suavizado.
                ceiling = sum(sorted(hints.values(), reverse=True)[:3]) or 1.0
                scores[scene] = min(1.0, total / ceiling)

        if hint_label:
            key = hint_label.strip().lower()
            for scene in SCENE_HINTS:
                if key in scene or scene in key:
                    scores[scene] = max(scores.get(scene, 0.0), float(hint_confidence))

        if not scores:
            return "desconocido", 0.0, []
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best, conf = ranked[0]
        if conf < self.min_confidence:
            return "desconocido", round(conf, 3), ranked[:3]
        return best, round(conf, 3), ranked[:3]

    # ------------------------------------------------------------------
    def read(self, frame_bgr, object_labels: list[str] | None = None,
             hint_label: str = "", hint_confidence: float = 0.0) -> SceneReading:
        signals = self.image_signals(frame_bgr)
        scene, conf, ranked = self.classify(object_labels or [], hint_label, hint_confidence)
        return SceneReading(
            scene_type=scene,
            scene_confidence=conf,
            lighting=self.lighting_label(signals["brightness"]),
            brightness=signals["brightness"],
            contrast=signals["contrast"],
            clutter=self.clutter_label(signals["clutter_score"]),
            clutter_score=signals["clutter_score"],
            candidates=[{"scene": s, "score": round(v, 3)} for s, v in ranked],
        )

    def close(self) -> None:
        self._cv2 = None
        self._np = None
