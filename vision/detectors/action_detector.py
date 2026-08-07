"""Evidencias por fotograma para el reconocimiento de acciones (punto 8).

Este módulo NO decide ninguna acción. Solo mira UN fotograma y responde a
preguntas atómicas y comprobables:

    ¿la mano está cerca de la boca?  ¿hay una botella detectada?
    ¿los brazos están cruzados?      ¿las muñecas están sobre una mesa?

La decisión ("está bebiendo") la toma `analyzers/action_analyzer.py` combinando
estas evidencias A LO LARGO DEL TIEMPO. Separarlo así es lo que evita el error
clásico de deducir una acción compleja de una sola postura.

Trabaja con landmarks de MediaPipe Pose (33 puntos) y con el estado de manos del
`HandTracker`. Todos los puntos se normalizan con `_xyz`, así que se prueba con
tuplas simples y sin MediaPipe.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

# Índices de MediaPipe Pose.
NOSE = 0
LEFT_EYE, RIGHT_EYE = 2, 5
LEFT_EAR, RIGHT_EAR = 7, 8
MOUTH_LEFT, MOUTH_RIGHT = 9, 10
LEFT_SHOULDER, RIGHT_SHOULDER = 11, 12
LEFT_ELBOW, RIGHT_ELBOW = 13, 14
LEFT_WRIST, RIGHT_WRIST = 15, 16
LEFT_HIP, RIGHT_HIP = 23, 24
LEFT_KNEE, RIGHT_KNEE = 25, 26
LEFT_ANKLE, RIGHT_ANKLE = 27, 28

DRINK_OBJECTS = ("bottle", "cup", "wine glass", "mug")
FOOD_OBJECTS = ("banana", "apple", "sandwich", "pizza", "donut", "cake", "bowl",
                "hot dog", "orange", "carrot")
PHONE_OBJECTS = ("cell phone", "cell_phone", "mobile phone", "remote")
READ_OBJECTS = ("book", "laptop", "tv", "monitor")
WRITE_OBJECTS = ("pen", "pencil", "book", "notebook")
TYPE_OBJECTS = ("keyboard", "laptop")


def _xyz(point: Any) -> tuple[float, float, float]:
    if point is None:
        return 0.0, 0.0, 0.0
    if hasattr(point, "x"):
        return (float(getattr(point, "x", 0.0)), float(getattr(point, "y", 0.0)),
                float(getattr(point, "z", 0.0) or 0.0))
    if isinstance(point, dict):
        return float(point.get("x", 0.0)), float(point.get("y", 0.0)), float(point.get("z", 0.0))
    try:
        vals = list(point)
        while len(vals) < 3:
            vals.append(0.0)
        return float(vals[0]), float(vals[1]), float(vals[2])
    except Exception:
        return 0.0, 0.0, 0.0


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _mid(a, b):
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0, (a[2] + b[2]) / 2.0)


@dataclass
class FrameEvidence:
    """Todo lo comprobable de un fotograma, en booleanos y números simples."""

    has_pose: bool = False
    torso_height: float = 0.0            # hombro->cadera, escala del cuerpo
    hip_y: float = 0.0
    shoulder_y: float = 0.0
    nose_y: float = 0.0
    body_center: tuple = (0.0, 0.0)
    body_size: float = 0.0               # tamaño aparente (acercarse/alejarse)
    knees_visible: bool = False
    knee_angle_closed: bool = False      # rodillas dobladas (sentado)
    horizontal_body: bool = False        # tronco casi horizontal (posible caída)
    arms_crossed: bool = False
    hand_near_mouth: bool = False
    hand_near_ear: bool = False
    hand_near_face: bool = False
    both_hands_up: bool = False
    hands_together: bool = False
    wrists_low_and_close: bool = False   # postura de teclear
    holding: tuple = ()                  # etiquetas cerca de las manos
    drink_object_near_mouth: bool = False
    food_object_near_mouth: bool = False
    phone_near_ear: bool = False
    phone_in_hand: bool = False
    looking_down: bool = False
    mouth_open: float = 0.0
    eyes_closed: float = 0.0
    gaze_at_object: bool = False
    object_extended_to_camera: bool = False
    pointing: bool = False
    signals: list = field(default_factory=list)

    def add(self, name: str) -> None:
        if name not in self.signals:
            self.signals.append(name)


class ActionDetector:
    """Extrae evidencias de un fotograma. Sin estado temporal a propósito."""

    name = "action_detector"

    def __init__(self, near_factor: float = 0.45) -> None:
        self.near_factor = float(near_factor)

    # ------------------------------------------------------------------
    def extract(
        self,
        *,
        pose_landmarks: Sequence[Any] | None = None,
        hand_states: Sequence[Any] | None = None,
        face_box: dict | None = None,
        object_tracker=None,
        blendshapes: dict | None = None,
    ) -> FrameEvidence:
        ev = FrameEvidence()
        bs = {k.lower(): float(v) for k, v in (blendshapes or {}).items()}
        ev.mouth_open = max(bs.get("jawopen", 0.0), 0.0)
        ev.eyes_closed = max(bs.get("eyeblinkleft", 0.0), bs.get("eyeblinkright", 0.0))

        pts = [_xyz(p) for p in (pose_landmarks or [])]
        if len(pts) >= 29:
            self._from_pose(ev, pts)

        self._from_hands(ev, hand_states, face_box, pts, object_tracker)
        self._from_objects(ev, object_tracker, pts, face_box)
        return ev

    # ------------------------------------------------------------------
    def _from_pose(self, ev: FrameEvidence, pts) -> None:
        ev.has_pose = True
        ls, rs = pts[LEFT_SHOULDER], pts[RIGHT_SHOULDER]
        lh, rh = pts[LEFT_HIP], pts[RIGHT_HIP]
        shoulder = _mid(ls, rs)
        hip = _mid(lh, rh)
        ev.shoulder_y = shoulder[1]
        ev.hip_y = hip[1]
        ev.nose_y = pts[NOSE][1]
        ev.torso_height = max(1e-4, abs(hip[1] - shoulder[1]))
        ev.body_center = (shoulder[0], shoulder[1])
        ev.body_size = max(1e-4, _dist(ls, rs))

        # Tronco horizontal: hombros y caderas casi a la misma altura, y el
        # ancho del tronco mayor que su alto. Solo es una EVIDENCIA de caída.
        torso_dx = abs(shoulder[0] - hip[0])
        if torso_dx > ev.torso_height * 1.3:
            ev.horizontal_body = True
            ev.add("horizontal_body")

        lk, rk = pts[LEFT_KNEE], pts[RIGHT_KNEE]
        ev.knees_visible = bool(lk[1] > 0 and rk[1] > 0)
        if ev.knees_visible:
            # Sentado: la rodilla queda a la altura de la cadera o por encima.
            knee_y = (lk[1] + rk[1]) / 2.0
            if knee_y <= hip[1] + ev.torso_height * 0.25:
                ev.knee_angle_closed = True
                ev.add("knees_bent")

        # Brazos cruzados: cada muñeca al otro lado del pecho y a media altura.
        lw, rw = pts[LEFT_WRIST], pts[RIGHT_WRIST]
        chest_y = shoulder[1] + ev.torso_height * 0.45
        crossed = (lw[0] > shoulder[0] and rw[0] < shoulder[0]
                   and abs(lw[1] - chest_y) < ev.torso_height * 0.6
                   and abs(rw[1] - chest_y) < ev.torso_height * 0.6)
        if crossed:
            ev.arms_crossed = True
            ev.add("arms_crossed")

        if lw[1] < shoulder[1] and rw[1] < shoulder[1]:
            ev.both_hands_up = True
            ev.add("both_hands_up")
        if _dist(lw, rw) < ev.torso_height * 0.35:
            ev.hands_together = True
            ev.add("hands_together")

        # Teclear: ambas muñecas bajas, cercanas entre sí y estables.
        if (lw[1] > shoulder[1] + ev.torso_height * 0.5
                and rw[1] > shoulder[1] + ev.torso_height * 0.5
                and _dist(lw, rw) < ev.torso_height * 1.6):
            ev.wrists_low_and_close = True
            ev.add("wrists_over_desk")

        if pts[NOSE][1] > shoulder[1] - ev.torso_height * 0.15:
            ev.looking_down = True
            ev.add("head_down")

    # ------------------------------------------------------------------
    def _from_hands(self, ev: FrameEvidence, hand_states, face_box, pts, object_tracker) -> None:
        holding: list[str] = []
        for hs in hand_states or []:
            if getattr(hs, "near_face", False):
                ev.hand_near_face = True
                ev.add("hand_near_face")
            if getattr(hs, "gesture", "") == "pointing":
                ev.pointing = True
                ev.add("pointing")
            label = getattr(hs, "holding", "")
            if label:
                holding.append(label)
        ev.holding = tuple(holding)

        # Cerca de la boca / de la oreja: se calcula con la pose si la hay, y si
        # no, con la caja del rostro.
        mouth = None
        ear_left = ear_right = None
        scale = 0.12
        if len(pts) >= 11:
            mouth = _mid(pts[MOUTH_LEFT], pts[MOUTH_RIGHT])
            ear_left, ear_right = pts[LEFT_EAR], pts[RIGHT_EAR]
            scale = max(0.05, ev.torso_height * self.near_factor)
        elif face_box:
            try:
                mouth = (float(face_box["x"]) + float(face_box["w"]) / 2.0,
                         float(face_box["y"]) + float(face_box["h"]) * 0.75, 0.0)
                scale = float(face_box["w"]) * 0.55
                ear_left = (float(face_box["x"]), mouth[1] - float(face_box["h"]) * 0.2, 0.0)
                ear_right = (float(face_box["x"]) + float(face_box["w"]),
                             mouth[1] - float(face_box["h"]) * 0.2, 0.0)
            except Exception:
                mouth = None

        if mouth is None:
            return
        wrists = []
        if len(pts) >= 17:
            wrists.extend([pts[LEFT_WRIST], pts[RIGHT_WRIST]])
        for hs in hand_states or []:
            w = getattr(hs, "wrist", None)
            if w:
                wrists.append(tuple(w))
        for w in wrists:
            if _dist(w, mouth) < scale:
                ev.hand_near_mouth = True
                ev.add("hand_near_mouth")
            for ear in (ear_left, ear_right):
                if ear and _dist(w, ear) < scale * 0.9:
                    ev.hand_near_ear = True
                    ev.add("hand_near_ear")

    # ------------------------------------------------------------------
    def _from_objects(self, ev: FrameEvidence, object_tracker, pts, face_box) -> None:
        if object_tracker is None:
            return
        try:
            tracks = object_tracker.tracks()
        except Exception:
            return

        mouth = None
        if len(pts) >= 11:
            mouth = _mid(pts[MOUTH_LEFT], pts[MOUTH_RIGHT])
            radius = max(0.06, ev.torso_height * 0.6)
        elif face_box:
            mouth = (float(face_box["x"]) + float(face_box["w"]) / 2.0,
                     float(face_box["y"]) + float(face_box["h"]) * 0.75, 0.0)
            radius = float(face_box["w"]) * 0.7
        else:
            radius = 0.15

        holding_lower = {h.lower() for h in ev.holding}
        for t in tracks:
            label = (t.label or "").strip().lower()
            cx = t.box["x"] + t.box["w"] / 2.0
            cy = t.box["y"] + t.box["h"] / 2.0
            near_mouth = mouth is not None and _dist((cx, cy, 0.0), mouth) < radius

            if label in DRINK_OBJECTS:
                if near_mouth:
                    ev.drink_object_near_mouth = True
                    ev.add("bottle_detected")
                    ev.add("object_near_mouth")
                elif label in holding_lower:
                    ev.add("bottle_detected")
            if label in FOOD_OBJECTS and near_mouth:
                ev.food_object_near_mouth = True
                ev.add("food_near_mouth")
            if label in PHONE_OBJECTS:
                if label in holding_lower:
                    ev.phone_in_hand = True
                    ev.add("phone_in_hand")
                if ev.hand_near_ear and near_mouth is False and t.box["y"] < 0.55:
                    ev.phone_near_ear = True
                    ev.add("phone_near_ear")
            if label in READ_OBJECTS and ev.looking_down:
                ev.gaze_at_object = True
                ev.add("looking_at_object")
            # Objeto grande y muy centrado: se lo están mostrando a la cámara.
            if t.box["w"] * t.box["h"] > 0.10 and 0.25 < cx < 0.75 and label in holding_lower:
                ev.object_extended_to_camera = True
                ev.add("object_shown_to_camera")

    def close(self) -> None:
        return None
