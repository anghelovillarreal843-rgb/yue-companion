"""Seguimiento temporal de personas (punto 10 del pedido).

Da un identificador ESTABLE a cada persona vista (`person_id`) para que las
acciones y las emociones se puedan atribuir a alguien concreto y no se recuente
a la misma persona cada fotograma.

Además deduce por cada persona:
  - si se acerca o se aleja (por el tamaño de la caja del rostro/cuerpo),
  - su posición en el encuadre (izquierda/centro/derecha),
  - cuánto lleva visible,
  - si es la persona "principal" (la más grande y centrada).

No identifica a nadie: los IDs son temporales y mueren al perder la pista. No
hay reconocimiento facial ni biometría, a propósito.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from vision.tracking.base_tracker import BaseTracker, Track, center
from vision.tracking.temporal_smoother import EMA


@dataclass
class PersonSnapshot:
    """Vista serializable de una persona seguida."""

    person_id: int
    position: str                    # left | center | right
    relative_size: float             # 0..1, proporción del encuadre
    approaching: str                 # closer | farther | stable
    visible_for: float
    is_primary: bool
    bounding_box: list

    def as_dict(self) -> dict:
        return {
            "person_id": self.person_id,
            "position": self.position,
            "relative_size": round(self.relative_size, 4),
            "approaching": self.approaching,
            "visible_for": round(self.visible_for, 2),
            "is_primary": self.is_primary,
            "bounding_box": self.bounding_box,
        }


class PersonTracker(BaseTracker):
    def __init__(self, max_people: int = 4, **kwargs) -> None:
        kwargs.setdefault("iou_threshold", 0.2)
        kwargs.setdefault("max_distance", 0.22)
        kwargs.setdefault("max_missing", 15)
        kwargs.setdefault("min_hits", 2)
        kwargs.setdefault("match_label", False)   # todas las pistas son "persona"
        super().__init__(**kwargs)
        self.max_people = int(max_people)
        self._size_ema: dict[int, EMA] = {}
        self._prev_size: dict[int, float] = {}

    # ------------------------------------------------------------------
    def update_from_boxes(self, boxes: list[dict], now: float | None = None) -> list[Track]:
        """`boxes`: lista de {x,y,w,h} normalizados (rostros o cuerpos)."""
        now = time.time() if now is None else float(now)
        dets = [
            {"label": "persona", "confidence": float(b.get("score", b.get("confidence", 0.9))),
             "box": {"x": float(b["x"]), "y": float(b["y"]),
                     "w": float(b["w"]), "h": float(b["h"])}}
            for b in boxes[: self.max_people]
            if isinstance(b, dict) and {"x", "y", "w", "h"} <= set(b)
        ]
        tracks = self.update(dets, now=now)
        for t in tracks:
            area = max(0.0, t.box["w"] * t.box["h"])
            ema = self._size_ema.setdefault(t.track_id, EMA(0.25))
            prev = ema.get(area)
            self._prev_size[t.track_id] = prev
            ema.update(area)
        # Limpia memorias de pistas muertas.
        alive = {t.track_id for t in self.all_tracks()}
        for tid in list(self._size_ema):
            if tid not in alive:
                self._size_ema.pop(tid, None)
                self._prev_size.pop(tid, None)
        return tracks

    # ------------------------------------------------------------------
    def snapshots(self) -> list[PersonSnapshot]:
        out: list[PersonSnapshot] = []
        tracks = self.tracks()
        primary_id = self.primary_id()
        for t in tracks:
            cx, _cy = center(t.box)
            position = "left" if cx < 0.38 else "right" if cx > 0.62 else "center"
            area = t.box["w"] * t.box["h"]
            prev = self._prev_size.get(t.track_id, area)
            delta = area - prev
            if abs(delta) < 0.004:
                approaching = "stable"
            else:
                approaching = "closer" if delta > 0 else "farther"
            out.append(PersonSnapshot(
                person_id=t.track_id,
                position=position,
                relative_size=area,
                approaching=approaching,
                visible_for=t.age,
                is_primary=(t.track_id == primary_id),
                bounding_box=[round(t.box["x"], 4), round(t.box["y"], 4),
                              round(t.box["x"] + t.box["w"], 4),
                              round(t.box["y"] + t.box["h"], 4)],
            ))
        return out

    def primary_id(self) -> int | None:
        """Persona 'principal': la más grande y cercana al centro."""
        best, best_score = None, -1.0
        for t in self.tracks():
            cx, _ = center(t.box)
            area = t.box["w"] * t.box["h"]
            centered = 1.0 - min(1.0, abs(cx - 0.5) * 2.0)
            score = area * (0.6 + 0.4 * centered)
            if score > best_score:
                best, best_score = t.track_id, score
        return best

    def as_state(self) -> dict:
        snaps = self.snapshots()
        return {
            "count": len(snaps),
            "primary_id": self.primary_id(),
            "people": [s.as_dict() for s in snaps],
        }
