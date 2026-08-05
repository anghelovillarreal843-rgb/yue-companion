"""Fusión del estado de visión (paso 14).

Toma lo que cada módulo depositó en `VisionState` (respetando su vencimiento) y
arma la estructura única `vision_state` con la forma EXACTA del pedido. También
detecta transiciones (aparición/desaparición de personas, gestos nuevos) y las
publica como eventos, con anti-parpadeo de presencia.
"""
from __future__ import annotations

import time

from vision.observations import (
    FaceBox,
    FaceObservation,
    GestureEvent,
    ObjectObservation,
    PoseObservation,
    SceneObservation,
)
from vision.vision_state import VisionState


class VisionStateFusion:
    def __init__(self, state: VisionState) -> None:
        self.state = state
        self._present = False
        self._absent_since = 0.0
        self._present_grace = 1.5  # s de gracia antes de declarar "se fue"

    # -- ingestión desde cada módulo (guarda con su propio TTL) --------
    def update_camera(self, active: bool, fps: float) -> None:
        self.state.set("camera", {"active": bool(active), "fps": round(float(fps), 1),
                                  "last_frame_time": time.monotonic()}, ttl=0)

    def update_faces(self, boxes: list[FaceBox], faces: list[FaceObservation]) -> None:
        self.state.set("face_boxes", boxes, ttl=1.0)
        self.state.set("faces", faces, ttl=1.5)
        self._track_presence(len(boxes) or len(faces))

    def update_pose(self, pose: PoseObservation) -> None:
        self.state.set("pose", pose, ttl=1.5)

    def update_gestures(self, events: list[GestureEvent]) -> None:
        self.state.set("hands", events, ttl=1.0)
        for ev in events:
            if ev.is_new:
                self.state.emit_event({"type": "gesture", "value": ev.gesture,
                                       "hand": ev.hand, "is_new": True})

    def update_objects(self, objects: list[ObjectObservation]) -> None:
        self.state.set("objects", objects, ttl=3.0)

    def update_scene(self, scene: SceneObservation) -> None:
        self.state.set("scene", scene, ttl=15.0)

    # -- presencia con anti-parpadeo ----------------------------------
    def _track_presence(self, count: int) -> None:
        now = time.monotonic()
        if count > 0:
            if not self._present:
                self._present = True
                self.state.emit_event({"type": "presence", "present": True})
            self._absent_since = 0.0
        else:
            if self._present:
                if self._absent_since == 0.0:
                    self._absent_since = now
                elif now - self._absent_since >= self._present_grace:
                    self._present = False
                    self.state.emit_event({"type": "presence", "present": False})

    # -- lectura combinada --------------------------------------------
    def snapshot(self) -> dict:
        camera = self.state.get("camera", {"active": False, "fps": 0.0, "last_frame_time": 0.0})
        boxes: list[FaceBox] = self.state.get("face_boxes", []) or []
        faces: list[FaceObservation] = self.state.get("faces", []) or []
        pose: PoseObservation | None = self.state.get("pose")
        hands: list[GestureEvent] = self.state.get("hands", []) or []
        objects: list[ObjectObservation] = self.state.get("objects", []) or []
        scene: SceneObservation | None = self.state.get("scene")

        person_count = max(len(boxes), len(faces))
        out = {
            "camera": {
                "active": bool(camera.get("active", False)),
                "fps": camera.get("fps", 0.0),
                "last_frame_time": camera.get("last_frame_time", 0.0),
            },
            "presence": {
                "present": self._present,
                "person_count": person_count,
                "face_count": len(faces) or len(boxes),
            },
            "faces": [f.as_state() for f in faces],
            "pose": pose.as_state() if pose else {
                "visible": False, "state": "unknown",
                "left_arm_raised": False, "right_arm_raised": False, "movement": "still",
            },
            "hands": [h.as_state() for h in hands],
            "objects": self._object_counts(objects),
            "scene": {
                "label": scene.label if scene else "unknown",
                "confidence": round(scene.confidence, 3) if scene else 0.0,
            },
            "events": [{"type": "gesture", "value": h.gesture, "is_new": h.is_new}
                       for h in hands if h.is_new],
        }
        return out

    @staticmethod
    def _object_counts(objects: list[ObjectObservation]) -> dict:
        counts: dict[str, int] = {}
        for o in objects:
            key = o.label.replace(" ", "_")
            counts[key] = counts.get(key, 0) + 1
        return counts
