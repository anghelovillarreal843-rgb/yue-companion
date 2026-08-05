"""Orquestador del sistema de visión MediaPipe Tasks.

Une todas las piezas en una fachada simple y OPT-IN:

    webcam -> CameraService -> FrameHub -> (detectores por FPS) -> Fusion
           -> VisionState -> {contexto al LLM, gestos del avatar}

Está APAGADO por defecto (`VISION_MP_ENABLED=false`) para no pelear por la
webcam con el observador clásico ni con `core.vision`. Es aditivo y degradable:
sin cámara queda inactivo; si falta un modelo, ese módulo se salta y el resto
sigue; sin MediaPipe, todo degrada sin romper YUE.
"""
from __future__ import annotations

import logging
import threading
import time

from vision import settings as settings_mod
from vision.avatar_bridge import AvatarBridge
from vision.camera_service import CameraService
from vision.dialogue_context import build_visual_context
from vision.frame_hub import FrameHub
from vision.model_manager import ModelManager
from vision.vision_scheduler import VisionScheduler
from vision.vision_state import VisionState
from vision.vision_state_fusion import VisionStateFusion

log = logging.getLogger("vision.controller")


class VisionSystem:
    def __init__(
        self,
        on_avatar_emotion=None,        # (name, intensity, ms) -> None
        on_speak=None,                 # (text) -> None
        on_status=None,                # (text, active) -> None
        model_dir=None,
        camera_factory=None,           # para pruebas sin webcam
        settings=None,                 # inyectable en pruebas
    ) -> None:
        self.cfg = settings or settings_mod.load()
        self.models = ModelManager(base_dir=model_dir)
        self._on_status = on_status or (lambda *_: None)

        self.hub = FrameHub()
        self.state = VisionState()
        self.fusion = VisionStateFusion(self.state)
        self.scheduler = VisionScheduler()
        self.avatar = AvatarBridge(
            on_avatar_emotion or (lambda *_: None),
            on_speak or (lambda *_: None),
        )
        self.state.subscribe(self.avatar.handle_event)

        self.camera = CameraService(
            frame_hub=self.hub,
            index=self.cfg.camera_index,
            width=self.cfg.width,
            height=self.cfg.height,
            target_fps=self.cfg.target_fps,
            privacy_mode=self.cfg.privacy_mode,
            status_callback=self._status,
            capture_factory=camera_factory,
        )

        # Detectores (se construyen perezosamente al primer uso).
        self._face = self._pose = self._land = self._gesture = self._objects = None
        self._classifier = None
        self._segmenter = None
        self._interactive = None
        self._last_frame_id = -1
        self._last_emotion_key = ""
        self._last_emotion_emit = 0.0
        self._started = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def _status(self, text: str, active: bool) -> None:
        self.fusion.update_camera(active, self.cfg.target_fps)
        try:
            self._on_status(text, active)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def start(self) -> None:
        if not self.cfg.enabled:
            log.info("Sistema de visión MP desactivado (VISION_MP_ENABLED=false).")
            return
        if not self.cfg.camera_enabled:
            log.info("Cámara desactivada por configuración (CAMERA_ENABLED=false).")
            return
        if self._started:
            return
        self._started = True
        self._build_detectors()
        self._register_jobs()
        self.camera.start()
        self.scheduler.start()
        log.info("Sistema de visión MP iniciado (%s).", self.cfg.performance_mode)

    def stop(self) -> None:
        try:
            self.scheduler.stop()
        finally:
            self.camera.stop()
        for d in (self._face, self._pose, self._land, self._gesture,
                  self._objects, self._classifier):
            if d is not None:
                try:
                    d.close()
                except Exception:
                    pass
        for s in (self._segmenter, self._interactive):
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
        self._started = False

    # ------------------------------------------------------------------
    def _build_detectors(self) -> None:
        c = self.cfg
        if c.face_detector:
            from vision.detectors.face_detector import FaceDetectorModule
            self._face = FaceDetectorModule(self.models.require("face_detector"),
                                            c.face_min_conf, c.max_faces)
        if c.face_landmarker:
            from vision.detectors.face_landmarker import FaceLandmarkerModule
            self._land = FaceLandmarkerModule(self.models.require("face_landmarker"), c.max_faces)
        if c.pose:
            from vision.detectors.pose_analyzer import PoseAnalyzerModule
            self._pose = PoseAnalyzerModule(self.models.require("pose_landmarker"), c.pose_min_conf)
        if c.gesture:
            from vision.detectors.gesture_recognizer import GestureRecognizerModule
            self._gesture = GestureRecognizerModule(self.models.require("gesture_recognizer"),
                                                    c.gesture_min_conf, c.max_hands)
        if c.objects:
            from vision.detectors.object_detector import ObjectDetectorModule
            self._objects = ObjectDetectorModule(self.models.require("object_detector"),
                                                 c.object_min_conf)
        if c.image_classifier:
            from vision.image_classifier import ImageClassifierModule
            self._classifier = ImageClassifierModule(self.models.require("image_classifier"))

    def _register_jobs(self) -> None:
        c = self.cfg
        if self._face:
            self.scheduler.add("face_detector", c.face_fps, self._job_face)
        if self._land:
            self.scheduler.add("face_landmarker", c.landmark_fps, self._job_landmarks)
        if self._pose:
            self.scheduler.add("pose", c.pose_fps, self._job_pose)
        if self._gesture:
            self.scheduler.add("gesture", c.gesture_fps, self._job_gesture)
        if self._objects:
            self.scheduler.add("objects", c.object_fps, self._job_objects)
        if self._classifier and c.classifier_fps > 0:
            self.scheduler.add("classifier", c.classifier_fps, self._job_classifier)

    # -- jobs (corren cada uno en su hilo, a su FPS) -------------------
    def _job_face(self) -> None:
        rgb = self.hub.latest_rgb()
        boxes = self._face.detect(rgb) if rgb is not None else []
        # El landmarker se ejecuta en su propio job; aquí solo presencia rápida.
        faces = self.state.get("faces", []) or []
        self.fusion.update_faces(boxes, faces)

    def _job_landmarks(self) -> None:
        rgb = self.hub.latest_rgb()
        faces = self._land.detect(rgb) if rgb is not None else []
        boxes = self.state.get("face_boxes", []) or []
        self.fusion.update_faces(boxes, faces)
        if faces:
            self._maybe_emit_emotion(faces[0])

    def _job_pose(self) -> None:
        rgb = self.hub.latest_rgb()
        if rgb is not None:
            self.fusion.update_pose(self._pose.detect(rgb))

    def _job_gesture(self) -> None:
        rgb = self.hub.latest_rgb()
        if rgb is not None:
            self.fusion.update_gestures(self._gesture.detect(rgb))

    def _job_objects(self) -> None:
        rgb = self.hub.latest_rgb()
        if rgb is not None:
            self.fusion.update_objects(self._objects.detect(rgb))

    def _job_classifier(self) -> None:
        rgb = self.hub.latest_rgb()
        if rgb is not None:
            self.fusion.update_scene(self._classifier.classify(rgb))

    def _maybe_emit_emotion(self, face) -> None:
        key = face.emotion_estimate
        now = time.time()
        if key == "neutral" or face.confidence < 0.45:
            return
        if key == self._last_emotion_key and (now - self._last_emotion_emit) < 15:
            return
        self._last_emotion_key = key
        self._last_emotion_emit = now
        self.state.emit_event({"type": "emotion", "value": key, "confidence": face.confidence})

    # ------------------------------------------------------------------
    # API para el resto de YUE
    def snapshot(self) -> dict:
        return self.fusion.snapshot()

    def context_for_ai(self) -> str:
        return build_visual_context(self.snapshot())

    def describe(self) -> str:
        s = self.snapshot()
        if not s["camera"]["active"]:
            return "La cámara de visión no está activa ahora mismo."
        if not s["presence"]["present"]:
            return "La cámara está activa, pero no veo a nadie frente a ella."
        parts = [f"Veo a {s['presence']['person_count']} persona(s)"]
        if s["faces"]:
            parts.append(f"expresión aparente: {s['faces'][0]['expression']}")
        if s["pose"]["visible"]:
            parts.append(f"postura: {s['pose']['state']}")
        return "; ".join(parts) + "."

    def classify_now(self) -> str:
        """Clasifica la escena bajo petición (paso 12)."""
        if self._classifier is None:
            from vision.image_classifier import ImageClassifierModule
            self._classifier = ImageClassifierModule(self.models.require("image_classifier"))
        rgb = self.hub.latest_rgb()
        if rgb is None:
            return "No tengo imagen de la cámara ahora mismo."
        scene = self._classifier.classify(rgb)
        if scene.label == "unknown":
            return "No consigo clasificar la escena (¿falta el modelo image_classifier?)."
        self.fusion.update_scene(scene)
        return f"La escena parece: {scene.label} ({int(scene.confidence * 100)}%)."

    def metrics(self) -> dict:
        return self.scheduler.metrics_dict()

    # -- segmentación bajo demanda ------------------------------------
    def segmenter(self):
        if self._segmenter is None:
            from vision.segmentation.image_segmenter import ImageSegmenter
            self._segmenter = ImageSegmenter(self.models.require("image_segmenter"))
        return self._segmenter

    def interactive_segmenter(self):
        if self._interactive is None:
            from vision.segmentation.interactive_segmenter import InteractiveSegmenter
            self._interactive = InteractiveSegmenter(self.models.require("interactive_segmenter"))
        return self._interactive

    # -- privacidad / toggles -----------------------------------------
    def status_report(self) -> dict:
        c = self.cfg
        m = self.snapshot()
        return {
            "camara": "activa" if m["camera"]["active"] else "inactiva",
            "rostro": "activo" if c.face_detector else "desactivado",
            "landmarks": "activo" if c.face_landmarker else "desactivado",
            "postura": "activa" if c.pose else "desactivada",
            "gestos": "activos" if c.gesture else "desactivados",
            "objetos": "activos" if c.objects else "desactivados",
            "clasificador": "activo" if c.image_classifier else "desactivado",
            "segmentacion": "bajo demanda",
            "fps_captura": m["camera"]["fps"],
            "privacidad": "procesamiento local" if c.privacy_mode else "local",
            "modelos": self.models.status(),
        }
