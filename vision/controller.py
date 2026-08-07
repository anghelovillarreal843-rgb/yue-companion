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

# --- ADITIVO (visión avanzada) ------------------------------------------
# Estos módulos son nuevos y NO reemplazan nada de arriba: el sistema clásico
# de MediaPipe Tasks sigue funcionando igual. El motor de percepción se monta
# encima y se puede apagar con VISION_PERCEPTION_ENABLED=false.
from vision.camera_manager import CameraManager
from vision.context_builder import VisionContextBuilder
from vision.event_manager import EventManager
from vision.models.model_registry import ModelRegistry
from vision.privacy_manager import from_settings as privacy_from_settings

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

        # ------------------------------------------------------------------
        # ADITIVO (visión avanzada): privacidad, cerrojo de cámara, eventos y
        # motor de percepción. Todo comparte el MISMO FrameHub, así que nunca
        # se abre una segunda webcam. Si algo falla aquí, el sistema clásico
        # sigue funcionando exactamente igual que antes.
        # ------------------------------------------------------------------
        self.privacy = privacy_from_settings(self.cfg)
        self.registry = ModelRegistry(
            base_dir=model_dir,
            variant=getattr(self.cfg, "model_variant", "full"),
            allow_download=getattr(self.cfg, "allow_download", False),
        )
        self.events = EventManager(
            min_duration=getattr(self.cfg, "event_min_duration", 0.45),
            cooldown=getattr(self.cfg, "event_cooldown", 4.0),
        )
        self.context_builder = VisionContextBuilder()
        self.camera_manager = CameraManager(
            frame_hub=self.hub,
            index=self.cfg.camera_index,
            width=self.cfg.width,
            height=self.cfg.height,
            target_fps=self.cfg.target_fps,
            privacy_mode=self.cfg.privacy_mode,
            owner="vision.perception",
            auto_search=getattr(self.cfg, "auto_search_camera", True),
            max_index=getattr(self.cfg, "max_camera_index", 4),
            status_callback=self._status,
            capture_factory=camera_factory,
        )
        self.perception = None
        if getattr(self.cfg, "perception_enabled", True):
            try:
                from vision.perception_engine import PerceptionEngine
                self.perception = PerceptionEngine(
                    self.cfg,
                    privacy=self.privacy,
                    camera=self.camera_manager,
                    registry=self.registry,
                    event_manager=self.events,
                )
            except Exception as exc:
                log.warning("No pude montar el motor de percepción: %s", exc)
                self.perception = None

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

        # ADITIVO: si el motor de percepción está activo, ÉL es el dueño de la
        # cámara (a través del CameraManager, que además toma el cerrojo global).
        # Nunca se arrancan los dos caminos: eso es justo lo que provocaba que
        # dos módulos pelearan por la misma webcam.
        if self.perception is not None:
            ok = self.perception.start()
            if ok:
                log.info("Sistema de visión iniciado con motor de percepción (%s).",
                         self.cfg.performance_mode)
                return
            log.warning("El motor de percepción no arrancó; uso el camino clásico.")
            self.perception = None

        self._build_detectors()
        self._register_jobs()
        self.camera.start()
        self.scheduler.start()
        log.info("Sistema de visión MP iniciado (%s).", self.cfg.performance_mode)

    def stop(self) -> None:
        if self.perception is not None:
            try:
                self.perception.stop()
            except Exception as exc:
                log.debug("fallo deteniendo la percepción: %s", exc)
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
        if self.perception is not None:
            return self.perception.snapshot()
        return self.fusion.snapshot()

    def context_for_ai(self) -> str:
        if self.perception is not None:
            ctx = self.context_builder.build(self.perception.snapshot(), events=self.events)
            return ctx.text
        return build_visual_context(self.snapshot())

    # --- ADITIVO: funciones de la visión avanzada ---------------------
    def read_text(self, only_title: bool = False):
        """Lee el texto mostrado a la cámara. None si no hay nada estable."""
        if self.perception is None:
            return None
        self.privacy.request_analysis()
        return self.perception.read_text(only_title=only_title)

    def describe_room(self) -> str:
        if self.perception is None:
            return self.describe()
        self.privacy.request_analysis()
        return self.perception.describe_room()

    def what_am_i_doing(self) -> str:
        if self.perception is None:
            return "El reconocimiento de acciones no está activo."
        return self.perception.what_am_i_doing()

    def count_fingers(self) -> str:
        if self.perception is None:
            return "El seguimiento de manos no está activo."
        return self.perception.count_fingers()

    def what_am_i_holding(self) -> str:
        if self.perception is None:
            return "El seguimiento de manos no está activo."
        return self.perception.what_am_i_holding()

    def affective_estimate(self) -> dict:
        if self.perception is None:
            return {"affective_state": "undetermined", "confidence": 0.0}
        return self.perception.affective_estimate()

    def capabilities(self, refresh: bool = False) -> dict:
        """Mapa de capacidades reales (punto 18). Nunca lanza excepción."""
        try:
            if self.perception is not None:
                return self.perception.capabilities(refresh=refresh).as_dict()
            from vision import capabilities as caps_mod
            return caps_mod.detect(settings=self.cfg, registry=self.registry,
                                   privacy=self.privacy).as_dict()
        except Exception:
            return {}

    def capabilities_report(self) -> str:
        try:
            if self.perception is not None:
                return self.perception.capabilities(refresh=True).describe_es()
            from vision import capabilities as caps_mod
            return caps_mod.detect(settings=self.cfg, registry=self.registry,
                                   privacy=self.privacy).describe_es()
        except Exception as exc:
            return f"No pude calcular las capacidades: {exc}"

    def handle_voice(self, text: str) -> str | None:
        """Procesa una orden hablada o escrita sobre la visión. None si no aplica."""
        from vision import voice_intents
        return voice_intents.handle(self.perception, text)

    def forget_observations(self) -> int:
        """Olvida todo lo observado ("olvida lo que viste")."""
        if self.perception is not None:
            return self.perception.forget()
        return self.events.forget()

    def set_privacy_mode(self, on: bool) -> str:
        self.privacy.set_privacy_mode(bool(on))
        self.events.set_muted(bool(on))
        return ("Modo privacidad activado: no analizo nada de la cámara."
                if on else "Modo privacidad desactivado.")

    def privacy_report(self) -> str:
        return self.privacy.describe_es()

    def models_report(self) -> str:
        return self.registry.describe()

    def should_react(self, user_asked: bool = False) -> tuple[bool, str]:
        """¿Debe YUE comentar algo por su cuenta? Por defecto, no."""
        if self.perception is None:
            return False, ""
        return self.context_builder.should_react(
            self.perception.snapshot(), self.events, user_asked=user_asked)

    def describe(self) -> str:
        # Con el motor de percepción, la descripción prudente de la habitación
        # es mucho mejor que el resumen clásico.
        if self.perception is not None:
            if not self.perception.running:
                return "La cámara de visión no está activa ahora mismo."
            return self.perception.describe_room()
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
        if self.perception is not None:
            return self.perception.metrics()
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
        cam = m.get("camera", {}) or {}
        base = {
            "camara": "activa" if cam.get("active") else "inactiva",
            "rostro": "activo" if c.face_detector else "desactivado",
            "landmarks": "activo" if c.face_landmarker else "desactivado",
            "postura": "activa" if c.pose else "desactivada",
            "gestos": "activos" if c.gesture else "desactivados",
            "objetos": "activos" if c.objects else "desactivados",
            "clasificador": "activo" if c.image_classifier else "desactivado",
            "segmentacion": "bajo demanda",
            "fps_captura": cam.get("fps", 0.0),
            "privacidad": "procesamiento local" if c.privacy_mode else "local",
            "modelos": self.models.status(),
        }
        # ADITIVO: lo nuevo se añade sin romper a quien lea las claves de arriba.
        if self.perception is not None:
            base.update({
                "motor": "percepción avanzada",
                "ocr": "activo" if self.privacy.allows("ocr") else "desactivado",
                "acciones": "activas" if self.privacy.allows("actions") else "desactivadas",
                "emociones": "estimación activa" if self.privacy.allows("emotions") else "desactivadas",
                "escena": "activa" if self.privacy.allows("scene") else "desactivada",
                "modo_privacidad": self.privacy.is_private(),
                "capacidades": self.capabilities(),
            })
        return base
