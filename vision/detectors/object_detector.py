"""Object Detector (paso 9): objetos comunes del entorno.

Consolida por categoría, evita duplicados por cajas muy solapadas (NMS simple) y
mantiene los objetos unos instantes para evitar parpadeo. NO afirma clases que el
modelo no conoce.
"""
from __future__ import annotations

import logging
import time

from vision.detectors.base import BaseDetector, make_mp_image, mp_tasks
from vision.observations import ObjectObservation

log = logging.getLogger("vision.objects")


def _iou(a: dict, b: dict) -> float:
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    ix1, iy1 = max(a["x"], b["x"]), max(a["y"], b["y"])
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


class ObjectDetectorModule(BaseDetector):
    name = "object_detector"

    def __init__(self, model_path, min_confidence: float = 0.5, max_results: int = 12,
                 persist: float = 1.5) -> None:
        super().__init__()
        self.model_path = str(model_path) if model_path else None
        self.min_confidence = float(min_confidence)
        self.max_results = int(max_results)
        self.persist = float(persist)  # segundos que un objeto "sobrevive" sin verse
        self._memory: dict[str, dict] = {}  # label -> {last_seen, box, conf}

    def _build(self):
        if not self.model_path:
            return None
        mp, mp_vision = mp_tasks()
        if mp is None:
            return None
        base_options = mp.tasks.BaseOptions(model_asset_path=self.model_path)
        options = mp_vision.ObjectDetectorOptions(
            base_options=base_options,
            running_mode=mp_vision.RunningMode.IMAGE,
            score_threshold=self.min_confidence,
            max_results=self.max_results,
        )
        return mp_vision.ObjectDetector.create_from_options(options)

    def detect(self, rgb_frame, now: float | None = None) -> list[ObjectObservation]:
        now = time.time() if now is None else now
        detections: list[ObjectObservation] = []
        if rgb_frame is not None and self.ensure_loaded():
            image = make_mp_image(rgb_frame)
            if image is not None:
                try:
                    result = self._impl.detect(image)
                    detections = self._parse(result, rgb_frame, now)
                except Exception as exc:
                    log.debug("fallo en detect(): %s", exc)

        return self._consolidate(detections, now)

    def _parse(self, result, rgb_frame, now) -> list[ObjectObservation]:
        h, w = rgb_frame.shape[0], rgb_frame.shape[1]
        out: list[ObjectObservation] = []
        for det in (getattr(result, "detections", None) or []):
            if not det.categories:
                continue
            cat = det.categories[0]
            score = float(cat.score)
            if score < self.min_confidence:
                continue
            bb = det.bounding_box
            box = {"x": round(bb.origin_x / max(1, w), 4),
                   "y": round(bb.origin_y / max(1, h), 4),
                   "w": round(bb.width / max(1, w), 4),
                   "h": round(bb.height / max(1, h), 4)}
            out.append(ObjectObservation(label=cat.category_name or "object",
                                         confidence=round(score, 4),
                                         bounding_box=box, timestamp=now))
        return self._nms(out)

    @staticmethod
    def _nms(objs: list[ObjectObservation], thr: float = 0.6) -> list[ObjectObservation]:
        objs = sorted(objs, key=lambda o: o.confidence, reverse=True)
        kept: list[ObjectObservation] = []
        for o in objs:
            if any(o.label == k.label and _iou(o.bounding_box, k.bounding_box) > thr for k in kept):
                continue
            kept.append(o)
        return kept

    def _consolidate(self, fresh: list[ObjectObservation], now: float) -> list[ObjectObservation]:
        """Combina detecciones frescas con la memoria corta (anti-parpadeo)."""
        for o in fresh:
            self._memory[o.label] = {"last_seen": now, "box": o.bounding_box, "conf": o.confidence}
        # Expira lo viejo.
        alive: list[ObjectObservation] = []
        for label, info in list(self._memory.items()):
            if now - info["last_seen"] > self.persist:
                self._memory.pop(label, None)
                continue
            alive.append(ObjectObservation(label=label, confidence=info["conf"],
                                           bounding_box=info["box"], timestamp=info["last_seen"]))
        return alive

    def counts(self) -> dict[str, int]:
        """Conteo por categoría de lo que sigue vivo en memoria."""
        c: dict[str, int] = {}
        for label in self._memory:
            c[label] = c.get(label, 0) + 1
        return c
