"""Seguimiento preciso de manos y dedos (punto 7 del pedido).

Trabaja sobre los 21 puntos de referencia por mano de MediaPipe y deduce, sin
depender de ningún modelo extra:

  - mano izquierda / derecha con identificador estable,
  - dedos extendidos o flexionados (los cinco, uno a uno),
  - orientación de la palma (hacia la cámara / hacia afuera / de canto),
  - distancia entre dedos y PINZA pulgar-índice,
  - gestos estáticos: mano abierta, puño, señalar, pulgar arriba, pulgar abajo,
    victoria, "ok", número mostrado con los dedos (0..5),
  - gestos dinámicos: saludo, acercamiento y alejamiento,
  - mano cerca del rostro,
  - mano sosteniendo un objeto (si el rastreador de objetos lo confirma).

Acepta landmarks en cualquiera de estas formas: objetos con `.x/.y/.z`, tuplas
`(x, y, z)` o dicts `{"x":..., "y":..., "z":...}`. Así se prueba sin MediaPipe.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from vision.tracking.temporal_smoother import EMA, MotionTracker

# Índices de MediaPipe Hands.
WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP = 17, 18, 19, 20

FINGER_NAMES = ("pulgar", "índice", "medio", "anular", "meñique")
_TIPS = (THUMB_TIP, INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)
_PIPS = (THUMB_IP, INDEX_PIP, MIDDLE_PIP, RING_PIP, PINKY_PIP)
_MCPS = (THUMB_MCP, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP)

GESTURES_ES = {
    "open_palm": "mano abierta",
    "fist": "puño cerrado",
    "pointing": "señalando",
    "thumbs_up": "pulgar arriba",
    "thumbs_down": "pulgar abajo",
    "victory": "señal de victoria",
    "ok": "gesto de OK",
    "pinch": "pinza con los dedos",
    "wave": "saludo con la mano",
    "call_me": "gesto de llamada",
    "none": "sin gesto claro",
}


def _xyz(point: Any) -> tuple[float, float, float]:
    """Normaliza un punto a (x, y, z) sea cual sea su formato de entrada."""
    if point is None:
        return 0.0, 0.0, 0.0
    if hasattr(point, "x"):
        return float(getattr(point, "x", 0.0)), float(getattr(point, "y", 0.0)), float(getattr(point, "z", 0.0) or 0.0)
    if isinstance(point, dict):
        return float(point.get("x", 0.0)), float(point.get("y", 0.0)), float(point.get("z", 0.0))
    try:
        vals = list(point)
        while len(vals) < 3:
            vals.append(0.0)
        return float(vals[0]), float(vals[1]), float(vals[2])
    except Exception:
        return 0.0, 0.0, 0.0


def _dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


@dataclass
class HandState:
    """Estado completo de una mano en un instante."""

    hand_id: int
    side: str                                   # left | right | unknown
    fingers_extended: list[bool] = field(default_factory=lambda: [False] * 5)
    finger_count: int = 0
    palm_orientation: str = "unknown"           # hacia_camara | hacia_afuera | de_canto
    pinch: bool = False
    pinch_distance: float = 1.0
    gesture: str = "none"
    gesture_confidence: float = 0.0
    near_face: bool = False
    motion: str = "still"                       # still | closer | farther | wave | swipe_*
    holding: str = ""                           # etiqueta del objeto sostenido, si la hay
    wrist: tuple[float, float, float] = (0.0, 0.0, 0.0)
    index_tip: tuple[float, float, float] = (0.0, 0.0, 0.0)
    hand_size: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "hand_id": self.hand_id,
            "hand": self.side,
            "fingers_extended": list(self.fingers_extended),
            "finger_count": self.finger_count,
            "palm_orientation": self.palm_orientation,
            "pinch": self.pinch,
            "pinch_distance": round(self.pinch_distance, 4),
            "gesture": self.gesture,
            "gesture_es": GESTURES_ES.get(self.gesture, self.gesture),
            "confidence": round(self.gesture_confidence, 3),
            "near_face": self.near_face,
            "motion": self.motion,
            "holding": self.holding,
        }


class HandTracker:
    """Analiza landmarks de manos y mantiene identidad y movimiento por mano."""

    def __init__(self, *, pinch_ratio: float = 0.35, wave_span: float = 0.10) -> None:
        self.pinch_ratio = float(pinch_ratio)
        self.wave_span = float(wave_span)
        self._motion: dict[str, MotionTracker] = {}
        self._size_ema: dict[str, EMA] = {}
        self._ids: dict[str, int] = {}
        self._next_id = 1
        self._last_seen: dict[str, float] = {}
        self._states: dict[str, HandState] = {}

    # ------------------------------------------------------------------
    def update(
        self,
        hands: Sequence[tuple[str, Sequence[Any]]],
        *,
        face_box: dict | None = None,
        object_tracker=None,
        now: float | None = None,
    ) -> list[HandState]:
        """`hands`: [(lado, landmarks), ...]. Devuelve el estado de cada mano."""
        now = time.time() if now is None else float(now)
        seen: set[str] = set()
        out: list[HandState] = []

        for side, landmarks in hands or []:
            side = (side or "unknown").strip().lower()
            if side not in {"left", "right"}:
                side = "unknown"
            pts = [_xyz(p) for p in (landmarks or [])]
            if len(pts) < 21:
                continue
            seen.add(side)
            state = self._analyze(side, pts, now, face_box, object_tracker)
            self._states[side] = state
            self._last_seen[side] = now
            out.append(state)

        # Manos que ya no se ven: se olvidan (permiten re-disparo limpio).
        for side in list(self._states):
            if side not in seen and (now - self._last_seen.get(side, 0.0)) > 1.0:
                self._states.pop(side, None)
                self._motion.pop(side, None)
                self._size_ema.pop(side, None)
                self._ids.pop(side, None)
        return out

    # ------------------------------------------------------------------
    def _analyze(self, side: str, pts: list[tuple[float, float, float]], now: float,
                 face_box: dict | None, object_tracker) -> HandState:
        hand_id = self._ids.get(side)
        if hand_id is None:
            hand_id = self._next_id
            self._ids[side] = hand_id
            self._next_id += 1

        wrist = pts[WRIST]
        # Escala de la mano: muñeca -> nudillo del medio. Normaliza todas las
        # distancias, de modo que funcione igual de cerca que de lejos.
        scale = max(1e-4, _dist(wrist, pts[MIDDLE_MCP]))

        extended = self._fingers_extended(pts, scale)
        finger_count = sum(1 for i, e in enumerate(extended) if e)

        pinch_d = _dist(pts[THUMB_TIP], pts[INDEX_TIP]) / scale
        pinch = pinch_d < self.pinch_ratio

        palm = self._palm_orientation(pts, side)
        gesture, conf = self._static_gesture(pts, extended, pinch, pinch_d, scale, palm)

        # Movimiento de la muñeca (saludo, acercarse, alejarse).
        mt = self._motion.setdefault(side, MotionTracker(window=1.2))
        mt.add(wrist[0], wrist[1], now=now)
        size_ema = self._size_ema.setdefault(side, EMA(0.25))
        prev_size = size_ema.get(scale)
        size_ema.update(scale)
        motion = self._motion_label(mt, scale, prev_size, extended)
        if motion == "wave":
            gesture, conf = "wave", max(conf, 0.75)

        near_face = self._near_face(pts, face_box, scale)
        holding = self._holding(pts, object_tracker)

        return HandState(
            hand_id=hand_id,
            side=side,
            fingers_extended=extended,
            finger_count=finger_count,
            palm_orientation=palm,
            pinch=pinch,
            pinch_distance=pinch_d,
            gesture=gesture,
            gesture_confidence=conf,
            near_face=near_face,
            motion=motion,
            holding=holding,
            wrist=wrist,
            index_tip=pts[INDEX_TIP],
            hand_size=scale,
            timestamp=now,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _fingers_extended(pts, scale: float) -> list[bool]:
        """Un dedo está extendido si su punta queda lejos de la muñeca.

        Para los cuatro dedos largos basta comparar punta vs articulación PIP en
        distancia a la muñeca. El PULGAR se mide aparte, porque se dobla en otro
        plano: se comprueba si su PUNTA está más lejos del nudillo del índice que
        su propia base. Al recogerse sobre la palma, la punta se acerca a ese
        nudillo; al extenderse, se aleja. Es una relación entre dos distancias de
        la propia mano, así que funciona igual de cerca que de lejos.
        """
        wrist = pts[WRIST]
        out: list[bool] = []
        base_gap = _dist(pts[THUMB_MCP], pts[INDEX_MCP])
        tip_gap = _dist(pts[THUMB_TIP], pts[INDEX_MCP])
        out.append(base_gap > 1e-6 and tip_gap > base_gap * 1.10)
        for tip, pip in zip(_TIPS[1:], _PIPS[1:]):
            out.append(_dist(pts[tip], wrist) > _dist(pts[pip], wrist) * 1.08)
        return out

    @staticmethod
    def _palm_orientation(pts, side: str) -> str:
        """Orientación aproximada por el orden de los nudillos en horizontal."""
        index_x = pts[INDEX_MCP][0]
        pinky_x = pts[PINKY_MCP][0]
        spread = abs(index_x - pinky_x)
        if spread < 0.03:
            return "de_canto"
        if side == "right":
            return "hacia_camara" if index_x < pinky_x else "hacia_afuera"
        if side == "left":
            return "hacia_camara" if index_x > pinky_x else "hacia_afuera"
        return "unknown"

    def _static_gesture(self, pts, extended: list[bool], pinch: bool,
                        pinch_d: float, scale: float, palm: str) -> tuple[str, float]:
        thumb, index, middle, ring, pinky = extended
        count = sum(extended)
        wrist = pts[WRIST]

        # Pulgar arriba / abajo: solo el pulgar fuera y apuntando en vertical.
        if thumb and not any((index, middle, ring, pinky)):
            dy = (pts[THUMB_TIP][1] - wrist[1]) / scale
            if dy < -0.5:
                return "thumbs_up", 0.9
            if dy > 0.5:
                return "thumbs_down", 0.9
            return "fist", 0.6

        if count == 0:
            return "fist", 0.88
        if index and middle and not ring and not pinky:
            gap = _dist(pts[INDEX_TIP], pts[MIDDLE_TIP]) / scale
            if gap > 0.35:
                return "victory", 0.9
        if index and not middle and not ring and not pinky:
            return "pointing", 0.88
        if pinch and middle and ring and pinky:
            return "ok", 0.82
        if pinch:
            return "pinch", 0.8
        if count >= 4:
            return "open_palm", 0.9
        if pinky and thumb and not index and not middle and not ring:
            return "call_me", 0.75
        return "none", 0.3

    def _motion_label(self, mt: MotionTracker, scale: float,
                      prev_scale: float, extended: list[bool]) -> str:
        span_x = mt.span("x")
        osc = mt.oscillations("x")
        if span_x > self.wave_span and osc >= 2 and sum(extended) >= 4:
            return "wave"
        growth = (scale - prev_scale) / max(1e-4, prev_scale)
        if growth > 0.18:
            return "closer"
        if growth < -0.18:
            return "farther"
        direction = mt.direction(min_delta=0.12)
        if direction in {"left", "right"} and osc <= 1:
            return f"swipe_{direction}"
        return "still"

    @staticmethod
    def _near_face(pts, face_box: dict | None, scale: float) -> bool:
        if not face_box:
            return False
        try:
            fx = float(face_box["x"]) + float(face_box["w"]) / 2.0
            fy = float(face_box["y"]) + float(face_box["h"]) / 2.0
            radius = max(float(face_box["w"]), float(face_box["h"])) * 0.85
        except Exception:
            return False
        for idx in (INDEX_TIP, MIDDLE_TIP, THUMB_TIP, WRIST):
            x, y, _ = pts[idx]
            if ((x - fx) ** 2 + (y - fy) ** 2) ** 0.5 <= radius:
                return True
        return False

    @staticmethod
    def _holding(pts, object_tracker) -> str:
        """Etiqueta del objeto más cercano a la palma, si hay uno pegado."""
        if object_tracker is None:
            return ""
        palm_x = (pts[WRIST][0] + pts[MIDDLE_MCP][0]) / 2.0
        palm_y = (pts[WRIST][1] + pts[MIDDLE_MCP][1]) / 2.0
        try:
            track, dist = object_tracker.nearest_to(palm_x, palm_y)
        except Exception:
            return ""
        if track is None or dist > 0.12:
            return ""
        return track.label

    # ------------------------------------------------------------------
    def states(self) -> list[HandState]:
        return list(self._states.values())

    def by_side(self, side: str) -> HandState | None:
        return self._states.get(side)

    def total_fingers(self) -> int:
        """Suma de dedos extendidos de todas las manos visibles."""
        return sum(s.finger_count for s in self._states.values())

    def as_state(self) -> list[dict]:
        return [s.as_dict() for s in self._states.values()]

    def reset(self) -> None:
        self._states.clear()
        self._motion.clear()
        self._size_ema.clear()
        self._ids.clear()
        self._last_seen.clear()
