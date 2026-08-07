"""Rastreador genérico por IoU + centroide (base de personas y objetos).

Asigna un identificador ESTABLE a cada caja detectada de un fotograma al
siguiente, para poder decir "la misma persona sigue ahí" o "apareció un objeto
nuevo" en vez de recontarlo todo cada vez.

Algoritmo (deliberadamente simple y barato, sin dependencias):
  1. empareja cada detección nueva con la pista existente más parecida
     (IoU alto, o centroides cercanos si las cajas son pequeñas),
  2. las pistas sin pareja envejecen y se dan por perdidas tras `max_missing`,
  3. las detecciones sin pareja crean una pista nueva con ID incremental.

Las cajas son diccionarios normalizados {x, y, w, h} en 0..1.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from vision.tracking.temporal_smoother import EMA, MotionTracker


def iou(a: dict, b: dict) -> float:
    """Intersección sobre unión de dos cajas {x,y,w,h}."""
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


def center(box: dict) -> tuple[float, float]:
    return box["x"] + box["w"] / 2.0, box["y"] + box["h"] / 2.0


def distance(a: dict, b: dict) -> float:
    ax, ay = center(a)
    bx, by = center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


@dataclass
class Track:
    """Una pista viva: identidad + caja + historia mínima."""

    track_id: int
    label: str
    box: dict
    confidence: float
    first_seen: float
    last_seen: float
    hits: int = 1
    missing: int = 0
    state: str = "visible"                      # visible | perdido
    motion: MotionTracker = field(default_factory=MotionTracker, repr=False)
    conf_ema: EMA = field(default_factory=lambda: EMA(0.3), repr=False)
    extra: dict = field(default_factory=dict)

    @property
    def age(self) -> float:
        return self.last_seen - self.first_seen

    def as_dict(self) -> dict:
        bx = self.box
        return {
            "id": self.track_id,
            "label": self.label,
            "confidence": round(float(self.confidence), 3),
            "bounding_box": [round(bx["x"], 4), round(bx["y"], 4),
                             round(bx["x"] + bx["w"], 4), round(bx["y"] + bx["h"], 4)],
            "state": self.state,
            "age": round(self.age, 2),
            "hits": self.hits,
        }


class BaseTracker:
    """Rastreador por emparejamiento voraz. Subclasificado por persona/objeto."""

    def __init__(
        self,
        *,
        iou_threshold: float = 0.25,
        max_distance: float = 0.18,
        max_missing: int = 12,
        min_hits: int = 2,
        match_label: bool = True,
    ) -> None:
        self.iou_threshold = float(iou_threshold)
        self.max_distance = float(max_distance)
        self.max_missing = int(max_missing)
        self.min_hits = int(min_hits)
        self.match_label = bool(match_label)
        self._tracks: dict[int, Track] = {}
        self._next_id = 1
        self._appeared: list[Track] = []
        self._disappeared: list[Track] = []

    # ------------------------------------------------------------------
    def update(self, detections: list[dict], now: float | None = None) -> list[Track]:
        """`detections`: [{'label', 'confidence', 'box': {x,y,w,h}, ...}].

        Devuelve las pistas confirmadas (con al menos `min_hits` aciertos).
        """
        now = time.time() if now is None else float(now)
        self._appeared = []
        self._disappeared = []

        unmatched = list(range(len(detections)))
        used_tracks: set[int] = set()

        # 1) Emparejamiento voraz por mejor puntuación.
        pairs: list[tuple[float, int, int]] = []
        for det_idx, det in enumerate(detections):
            box = det.get("box") or det.get("bounding_box")
            if not isinstance(box, dict):
                continue
            for tid, track in self._tracks.items():
                if self.match_label and track.label != det.get("label", track.label):
                    continue
                score = iou(box, track.box)
                if score < self.iou_threshold:
                    # Cajas pequeñas: la IoU es cruel; miramos el centroide.
                    if distance(box, track.box) > self.max_distance:
                        continue
                    score = max(score, 0.05)
                pairs.append((score, det_idx, tid))

        for score, det_idx, tid in sorted(pairs, key=lambda p: p[0], reverse=True):
            if det_idx not in unmatched or tid in used_tracks:
                continue
            unmatched.remove(det_idx)
            used_tracks.add(tid)
            self._absorb(self._tracks[tid], detections[det_idx], now)

        # 2) Pistas sin pareja: envejecen.
        for tid, track in list(self._tracks.items()):
            if tid in used_tracks:
                continue
            track.missing += 1
            if track.missing > self.max_missing:
                track.state = "perdido"
                self._disappeared.append(track)
                self._tracks.pop(tid, None)

        # 3) Detecciones sin pareja: pistas nuevas.
        for det_idx in unmatched:
            det = detections[det_idx]
            box = det.get("box") or det.get("bounding_box")
            if not isinstance(box, dict):
                continue
            track = Track(
                track_id=self._next_id,
                label=str(det.get("label", "objeto")),
                box=dict(box),
                confidence=float(det.get("confidence", 0.0)),
                first_seen=now,
                last_seen=now,
            )
            track.conf_ema.update(track.confidence)
            track.motion.add(*center(box), now=now)
            if det.get("extra"):
                track.extra.update(det["extra"])
            self._tracks[self._next_id] = track
            self._next_id += 1
            self._appeared.append(track)

        return self.tracks()

    def _absorb(self, track: Track, det: dict, now: float) -> None:
        box = det.get("box") or det.get("bounding_box")
        track.box = dict(box)
        track.confidence = track.conf_ema.update(float(det.get("confidence", track.confidence)))
        track.last_seen = now
        track.hits += 1
        track.missing = 0
        track.state = "visible"
        track.motion.add(*center(box), now=now)
        if det.get("extra"):
            track.extra.update(det["extra"])

    # ------------------------------------------------------------------
    def tracks(self) -> list[Track]:
        """Pistas confirmadas y actualmente visibles."""
        return [t for t in self._tracks.values() if t.hits >= self.min_hits]

    def all_tracks(self) -> list[Track]:
        return list(self._tracks.values())

    def appeared(self) -> list[Track]:
        """Pistas nacidas en la última llamada a `update`."""
        return [t for t in self._appeared]

    def disappeared(self) -> list[Track]:
        return list(self._disappeared)

    def count(self) -> int:
        return len(self.tracks())

    def reset(self) -> None:
        self._tracks.clear()
        self._appeared.clear()
        self._disappeared.clear()
