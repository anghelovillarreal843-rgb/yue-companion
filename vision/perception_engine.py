"""Motor de percepción: el cerebro del sistema de visión (puntos 3 y 14).

Une, en un solo sitio y sin que nada bloquee la interfaz ni el audio:

    CameraManager -> FrameHub -> [detectores a su propio FPS]
                              -> [trackers: personas, objetos, manos]
                              -> [analizadores: escena, gestos, acciones,
                                  emociones, atención]
                              -> EventManager -> ContextBuilder -> YUE

Cada detector corre en su propio hilo con su frecuencia (el `VisionScheduler` ya
existente), tomando SIEMPRE el último fotograma: si uno va lento, los demás no
esperan y nunca se acumulan colas.

Reglas que se respetan aquí:
  - todo pasa antes por `PrivacyManager.allows(...)`,
  - si un módulo falla, se aísla y el resto sigue,
  - si falta un modelo, ese módulo simplemente no se registra,
  - OCR y descripción global NO corren en bucle: van bajo petición o cuando hay
    texto/cambio estable.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from vision import capabilities as caps_mod
from vision.analyzers.action_analyzer import ActionAnalyzer, ActionEvent
from vision.analyzers.attention_analyzer import AttentionAnalyzer
from vision.analyzers.emotion_analyzer import EmotionAnalyzer
from vision.analyzers.gesture_analyzer import GestureAnalyzer
from vision.analyzers.room_analyzer import RoomAnalyzer
from vision.camera_manager import CameraManager
from vision.detectors.action_detector import ActionDetector
from vision.detectors.scene_detector import SceneDetector
from vision.detectors.text_detector import TextDetector
from vision.event_manager import EventManager
from vision.models.model_registry import ModelRegistry
from vision.ocr.document_scanner import DocumentScanner
from vision.ocr.ocr_engine import OCREngineChain, OCRResult
from vision.ocr.text_stabilizer import TextStabilizer
from vision.privacy_manager import PrivacyManager
from vision.tracking.hand_tracker import HandTracker
from vision.tracking.object_tracker import ObjectTracker
from vision.tracking.person_tracker import PersonTracker
from vision.vision_scheduler import VisionScheduler

log = logging.getLogger("vision.perception")


class PerceptionEngine:
    """Orquestador de percepción. No sabe nada de Qt ni de YUE."""

    def __init__(
        self,
        settings,
        *,
        privacy: PrivacyManager,
        camera: CameraManager,
        registry: ModelRegistry | None = None,
        event_manager: EventManager | None = None,
        on_event: Callable[[Any], None] | None = None,
    ) -> None:
        self.cfg = settings
        self.privacy = privacy
        self.camera = camera
        self.registry = registry or ModelRegistry(
            variant=getattr(settings, "model_variant", "full"),
            allow_download=getattr(settings, "allow_download", False),
        )
        self.events = event_manager or EventManager(
            min_duration=getattr(settings, "event_min_duration", 0.45),
            cooldown=getattr(settings, "event_cooldown", 4.0),
        )
        if on_event is not None:
            self.events.subscribe(on_event)

        self.scheduler = VisionScheduler()

        # --- trackers ---------------------------------------------------
        self.people = PersonTracker(max_people=getattr(settings, "max_people", 4))
        self.objects = ObjectTracker()
        self.hands = HandTracker()

        # --- analizadores ----------------------------------------------
        self.room = RoomAnalyzer()
        self.gestures = GestureAnalyzer(event_manager=self.events)
        self.actions = ActionAnalyzer(on_event=self._on_action)
        self.emotions = EmotionAnalyzer(
            min_confidence=getattr(settings, "emotion_min_confidence", 0.45),
            allow_voice_signals=getattr(settings, "emotion_use_voice", False),
        )
        self.attention = AttentionAnalyzer()

        # --- detectores auxiliares (sin modelo, siempre disponibles) ----
        self.scene_detector = SceneDetector()
        self.text_detector = TextDetector()
        self.action_detector = ActionDetector()
        self.scanner = DocumentScanner()
        self.ocr = OCREngineChain(
            languages=tuple(getattr(settings, "ocr_languages", ("es",))),
            preferred=getattr(settings, "ocr_engine", "auto"),
        )
        self.text_stabilizer = TextStabilizer(
            min_agreements=getattr(settings, "ocr_min_agreements", 3),
        )

        # --- detectores MediaPipe (perezosos) ---------------------------
        self._face = None
        self._landmarker = None
        self._pose = None
        self._hand_landmarker = None
        self._gesture_model = None
        self._object_model = None
        self._classifier = None

        # --- estado compartido ------------------------------------------
        self._lock = threading.RLock()
        self._started = False
        self._last_face_box: dict | None = None
        self._last_blendshapes: dict = {}
        self._last_head_pose: dict = {}
        self._last_expression = ""
        self._last_pose_landmarks: list = []
        self._last_pose_state = ""
        self._last_movement = ""
        # ADITIVO: postura serializable para el snapshot y marca del último OCR
        # automático (para no releer el mismo documento en bucle).
        self._last_pose_state_dict: dict = {
            "visible": False, "state": "unknown", "left_arm_raised": False,
            "right_arm_raised": False, "movement": "still",
        }
        self._last_auto_ocr = 0.0
        self._last_scene_hint = ("", 0.0)
        self._last_ocr: OCRResult | None = None
        self._last_stable_text = None
        self._room_snapshot: dict = {}
        self._capabilities = None
        self._last_actions: list[ActionEvent] = []

        # --- accesibilidad: control del cursor por cabeza -----------------
        # El observador clásico entregaba los landmarks crudos por fotograma a
        # `HeadCursorController`. Para poder apagarlo (y no pelear por la webcam)
        # el motor nuevo tiene que ofrecer lo mismo. Con `set_fast_mode(True)` se
        # registra un trabajo aparte, a más FPS, que SOLO saca landmarks y se los
        # pasa al consumidor. No toca la cadencia lenta de emociones ni acciones.
        self._landmark_consumer = None
        self._fast_mode = False
        self._head_landmarker = None
        self._head_job_added = False
        self._frame_shape: tuple[int, int] = (0, 0)

    # ==================================================================
    # Ciclo de vida
    # ==================================================================
    def start(self) -> bool:
        with self._lock:
            if self._started:
                return True
            self._started = True
        if not self.camera.start():
            log.warning("No pude abrir la cámara; la percepción queda inactiva.")
            self.privacy.set_camera(False)
            return False
        self.privacy.set_camera(True)
        self._build_detectors()
        self._register_jobs()
        # Si el control por cabeza ya estaba pedido antes de arrancar, se añade
        # su trabajo ANTES de lanzar el planificador (los hilos se crean ahí).
        if self._fast_mode and self._landmark_consumer is not None:
            self._ensure_head_job()
        self.scheduler.start()
        log.info("Motor de percepción iniciado. Capacidades: %s",
                 self.capabilities().as_dict())
        return True

    def stop(self) -> None:
        with self._lock:
            self._started = False
        try:
            self.scheduler.stop()
        finally:
            self.camera.stop()
        self.privacy.set_camera(False)
        for det in (self._face, self._landmarker, self._pose, self._hand_landmarker,
                    self._gesture_model, self._object_model, self._classifier,
                    self._head_landmarker):
            if det is not None:
                try:
                    det.close()
                except Exception:
                    pass
        for aux in (self.text_detector, self.scene_detector, self.action_detector):
            try:
                aux.close()
            except Exception:
                pass
        try:
            self.ocr.close()
        except Exception:
            pass
        self.registry.release()

    @property
    def running(self) -> bool:
        return self._started and self.camera.is_running()

    # ==================================================================
    # Construcción perezosa de detectores (se salta lo que falte)
    # ==================================================================
    def _build_detectors(self) -> None:
        c = self.cfg

        if getattr(c, "face_detector", True):
            path = self.registry.require("face_detector")
            if path:
                from vision.detectors.face_detector import FaceDetectorModule
                self._face = FaceDetectorModule(path, getattr(c, "face_min_conf", 0.5),
                                                getattr(c, "max_faces", 3))
        if getattr(c, "face_landmarker", True):
            path = self.registry.require("face_landmarker")
            if path:
                from vision.detectors.face_landmarker import FaceLandmarkerModule
                self._landmarker = FaceLandmarkerModule(path, getattr(c, "max_faces", 3))
        if getattr(c, "pose", True):
            path = self.registry.require("pose_landmarker")
            if path:
                from vision.detectors.pose_analyzer import PoseAnalyzerModule
                self._pose = PoseAnalyzerModule(path, getattr(c, "pose_min_conf", 0.5))
        if getattr(c, "hands_enabled", True):
            path = self.registry.require("hand_landmarker")
            if path:
                from vision.detectors.hand_landmarker import HandLandmarkerModule
                self._hand_landmarker = HandLandmarkerModule(
                    path, getattr(c, "max_hands", 2), getattr(c, "gesture_min_conf", 0.6))
            else:
                # Respaldo: el reconocedor de gestos también trae landmarks.
                path = self.registry.require("gesture_recognizer")
                if path:
                    from vision.detectors.gesture_recognizer import GestureRecognizerModule
                    self._gesture_model = GestureRecognizerModule(
                        path, getattr(c, "gesture_min_conf", 0.6), getattr(c, "max_hands", 2))
        if getattr(c, "objects", True):
            path = self.registry.require("object_detector")
            if path:
                from vision.detectors.object_detector import ObjectDetectorModule
                self._object_model = ObjectDetectorModule(path, getattr(c, "object_min_conf", 0.5))
        if getattr(c, "image_classifier", False):
            path = self.registry.require("image_classifier")
            if path:
                from vision.image_classifier import ImageClassifierModule
                self._classifier = ImageClassifierModule(path)

    def _register_jobs(self) -> None:
        c = self.cfg
        add = self.scheduler.add
        if self._face:
            add("face", getattr(c, "face_fps", 8.0), self._job_face)
        if self._landmarker:
            add("face_landmarks", getattr(c, "landmark_fps", 10.0), self._job_landmarks)
        if self._pose:
            add("pose", getattr(c, "pose_fps", 8.0), self._job_pose)
        if self._hand_landmarker or self._gesture_model:
            add("hands", getattr(c, "hand_fps", getattr(c, "gesture_fps", 12.0)), self._job_hands)
        if self._object_model:
            add("objects", getattr(c, "object_fps", 3.0), self._job_objects)
        # Estos tres son baratos y no dependen de modelos.
        add("scene", getattr(c, "scene_fps", 0.3), self._job_scene)
        add("actions", getattr(c, "action_fps", 6.0), self._job_actions)
        add("text_watch", getattr(c, "text_watch_fps", 0.5), self._job_text_watch)

    # ==================================================================
    # Trabajos periódicos (cada uno en su hilo)
    # ==================================================================
    def _job_face(self) -> None:
        if not self.privacy.allows("faces"):
            return
        rgb = self.camera.latest_rgb()
        if rgb is None:
            return
        boxes = self._face.detect(rgb) or []
        raw = [b.bounding_box for b in boxes if getattr(b, "bounding_box", None)]
        self.people.update_from_boxes(raw)
        with self._lock:
            self._last_face_box = raw[0] if raw else None
        self.attention.update(face_present=bool(raw), head_pose=self._last_head_pose or None)
        self.camera.tick_fps()

    def _job_landmarks(self) -> None:
        if not self.privacy.allows("faces"):
            return
        rgb = self.camera.latest_rgb()
        if rgb is None:
            return
        faces = self._landmarker.detect(rgb) or []
        if not faces:
            return
        face = faces[0]
        with self._lock:
            self._last_blendshapes = dict(getattr(face, "blendshapes", {}) or {})
            self._last_head_pose = dict(getattr(face, "head_pose", {}) or {})
            self._last_expression = getattr(face, "expression", "")
            if getattr(face, "bounding_box", None):
                self._last_face_box = face.bounding_box

        att = self.attention.update(
            face_present=True,
            head_pose=self._last_head_pose or None,
            looking_hint=getattr(face, "looking_at_camera", None),
        )
        if self.privacy.allows("emotions"):
            estimate = self.emotions.update(
                blendshapes=self._last_blendshapes,
                expression=self._last_expression,
                looking_at_camera=att.looking_at_camera,
                head_pose=self._last_head_pose,
                pose_state=self._last_pose_state,
                movement=self._last_movement,
            )
            # CORRECCIÓN: el umbral estaba fijo en 0.55, ignorando
            # `emotion_min_confidence`. Con la configuración del proyecto ese
            # ajuste valía 60.0 (venía en porcentaje), así que aunque se
            # respetara nunca se cumplía. Ahora se usa el valor ya normalizado
            # por `settings.get_confidence`, con un suelo prudente.
            umbral_emocion = max(0.35, float(
                getattr(self.cfg, "emotion_min_confidence", 0.45)))
            if (estimate.affective_state != "undetermined"
                    and estimate.confidence >= umbral_emocion):
                self.events.observe(
                    "affective_estimate", estimate.affective_state, estimate.confidence,
                    source="emotion_analyzer",
                    data={"certainty": estimate.certainty,
                          "safe_description": estimate.safe_description,
                          "signals": estimate.signals},
                    min_duration=1.5, cooldown=45.0,
                )

    def _job_pose(self) -> None:
        if not self.privacy.allows("pose"):
            return
        rgb = self.camera.latest_rgb()
        if rgb is None:
            return
        pose = self._pose.detect(rgb)
        if pose is None:
            return
        with self._lock:
            self._last_pose_landmarks = list(getattr(pose, "landmarks", []) or [])
            self._last_pose_state = getattr(pose, "state", "")
            self._last_movement = getattr(pose, "movement", "")
            # ADITIVO: la observación completa, para poder publicarla en el
            # snapshot. Antes la postura se calculaba y se quedaba dentro del
            # motor: `snapshot()` no la incluía, así que ni el contexto del
            # modelo ni el avatar podían enterarse de si estabas sentado.
            self._last_pose_state_dict = pose.as_state()

    def _job_hands(self) -> None:
        if not self.privacy.allows("hands"):
            return
        rgb = self.camera.latest_rgb()
        if rgb is None:
            return
        pairs: list[tuple[str, list]] = []
        if self._hand_landmarker is not None:
            pairs = self._hand_landmarker.detect(rgb) or []
        elif self._gesture_model is not None:
            # El reconocedor de gestos ya emite sus propios eventos; aquí solo
            # se aprovechan sus landmarks para el análisis fino de dedos.
            try:
                self._gesture_model.detect(rgb)
                impl = getattr(self._gesture_model, "_impl", None)
                pairs = []
                if impl is not None:
                    from vision.detectors.base import make_mp_image
                    image = make_mp_image(rgb)
                    if image is not None:
                        res = impl.recognize(image)
                        lm = getattr(res, "hand_landmarks", None) or []
                        hd = getattr(res, "handedness", None) or []
                        for i, points in enumerate(lm):
                            side = "unknown"
                            if i < len(hd) and hd[i]:
                                raw = (hd[i][0].category_name or "").lower()
                                side = {"left": "right", "right": "left"}.get(raw, "unknown")
                            pairs.append((side, [(p.x, p.y, getattr(p, "z", 0.0)) for p in points]))
            except Exception as exc:
                log.debug("no pude sacar landmarks del gesture recognizer: %s", exc)

        if not pairs:
            return
        states = self.hands.update(pairs, face_box=self._last_face_box,
                                   object_tracker=self.objects)
        self.gestures.update(states)

    def _job_objects(self) -> None:
        if not self.privacy.allows("objects"):
            return
        rgb = self.camera.latest_rgb()
        if rgb is None:
            return
        detections = self._object_model.detect(rgb) or []
        self.objects.update_from_detections(detections)
        for change in self.objects.changes_es():
            self.events.observe("scene_change", change, 0.7, source="object_tracker",
                                min_duration=0.0, cooldown=20.0)

    def _job_scene(self) -> None:
        if not self.privacy.allows("scene"):
            return
        frame = self.camera.latest_bgr()
        if frame is None:
            return
        hint, hint_conf = self._last_scene_hint
        if self._classifier is not None:
            try:
                scene = self._classifier.classify(self.camera.latest_rgb())
                hint, hint_conf = getattr(scene, "label", ""), float(getattr(scene, "confidence", 0.0))
                self._last_scene_hint = (hint, hint_conf)
            except Exception:
                pass
        reading = self.scene_detector.read(
            frame,
            object_labels=[t.label for t in self.objects.tracks()],
            hint_label=hint, hint_confidence=hint_conf,
        )
        snapshot = self.room.update(
            scene_reading=reading,
            object_tracks=self.objects.tracks(),
            people_count=self.people.count(),
        )
        with self._lock:
            self._room_snapshot = snapshot
        for change in snapshot.get("changes", []):
            self.events.observe("scene_change", change, 0.7, source="room_analyzer",
                                min_duration=0.0, cooldown=25.0)

    def _job_actions(self) -> None:
        if not self.privacy.allows("actions"):
            return
        with self._lock:
            pose_lm = list(self._last_pose_landmarks)
            face_box = dict(self._last_face_box) if self._last_face_box else None
            blendshapes = dict(self._last_blendshapes)
        evidence = self.action_detector.extract(
            pose_landmarks=pose_lm,
            hand_states=self.hands.states(),
            face_box=face_box,
            object_tracker=self.objects,
            blendshapes=blendshapes,
        )
        person_id = self.people.primary_id() or 0
        events = self.actions.update(evidence, person_id=person_id)
        if events:
            with self._lock:
                self._last_actions = events

    def _job_text_watch(self) -> None:
        """Vigila si APARECE texto delante de la cámara; no lee sin permiso.

        Punto 8 del pedido: el OCR NO corre en bucle. Solo se dispara cuando
        (a) el usuario lo pide, o (b) aparece un DOCUMENTO claro frente a la
        cámara: región de texto grande, bien puntuada y sostenida. Ese segundo
        caso estaba descrito pero no implementado: antes solo se avisaba de que
        «hay texto» y ahí se quedaba.
        """
        if not self.privacy.allows("ocr"):
            return
        if getattr(self.cfg, "ocr_only_on_request", True) and not self.privacy.request_open():
            frame = self.camera.latest_bgr()
            if frame is None:
                return
            regions = self.text_detector.detect(frame)
            if not regions:
                return
            mejor = regions[0]
            if mejor.score > 0.4:
                self.events.observe("text_visible", "hay_texto", mejor.score,
                                    source="text_detector", min_duration=1.0, cooldown=30.0)
            # --- (b) documento evidente: se lee UNA vez, con enfriamiento ---
            if not getattr(self.cfg, "ocr_auto_on_document", True):
                return
            umbral = float(getattr(self.cfg, "ocr_document_min_score", 0.55))
            es_documento = (
                mejor.score >= umbral
                and mejor.area() >= 0.08          # ocupa buena parte del encuadre
                and getattr(mejor, "lines", 1) >= 2   # varias líneas, no un logo
            )
            if not es_documento:
                return
            ahora = time.time()
            if ahora - self._last_auto_ocr < 20.0:
                return
            self._last_auto_ocr = ahora
            log.info("Documento detectado frente a la cámara; leo el texto.")
            self.read_text(auto=True)
            return
        self.read_text(auto=True)

    # ==================================================================
    # Accesibilidad: landmarks crudos para el control por cabeza
    # ==================================================================
    def set_landmark_consumer(self, consumer) -> None:
        """Registra `consumer(landmarks, (ancho, alto))`, como el clásico.

        Los landmarks son la malla facial completa (478 puntos) en coordenadas
        normalizadas 0..1, que es exactamente lo que `HeadCursorController`
        espera. Pasar None lo desconecta.
        """
        self._landmark_consumer = consumer

    def set_fast_mode(self, on: bool) -> None:
        """Activa la cadencia rápida de landmarks (solo mientras se usa el cursor)."""
        self._fast_mode = bool(on)
        if self._fast_mode:
            self._ensure_head_job()

    def _ensure_head_job(self) -> None:
        """Registra, una sola vez, el trabajo rápido de landmarks."""
        if self._head_job_added or not self._started:
            return
        path = self.registry.require("face_landmarker")
        if path is None:
            log.info("Sin face_landmarker no puedo alimentar el control por cabeza.")
            return
        from vision.detectors.face_landmarker import FaceLandmarkerModule
        # Instancia PROPIA con keep_landmarks=True: el detector de expresiones
        # no los guarda (pesan) y no queremos cambiarle el comportamiento.
        self._head_landmarker = FaceLandmarkerModule(path, max_faces=1,
                                                     keep_landmarks=True)
        fps = float(getattr(self.cfg, "head_control_fps", 18.0))
        self.scheduler.add("head_landmarks", fps, self._job_head_landmarks)
        self._head_job_added = True
        log.info("Control por cabeza: landmarks a %.0f FPS.", fps)

    def _job_head_landmarks(self) -> None:
        if not self._fast_mode or self._landmark_consumer is None:
            return
        if not self.privacy.allows("faces"):
            return
        detector = self._head_landmarker
        if detector is None:
            return
        rgb = self.camera.latest_rgb()
        if rgb is None:
            return
        try:
            shape = (int(rgb.shape[1]), int(rgb.shape[0]))
            self._frame_shape = shape
        except Exception:
            shape = self._frame_shape
        faces = detector.detect(rgb) or []
        if not faces:
            return
        landmarks = getattr(faces[0], "landmarks", None)
        if not landmarks:
            return
        try:
            self._landmark_consumer(landmarks, shape)
        except Exception:
            # Nunca dejamos que un fallo del cursor tumbe el hilo de visión.
            pass

    # ==================================================================
    # Acciones bajo petición
    # ==================================================================
    def read_text(self, *, only_title: bool = False, auto: bool = False) -> OCRResult | None:
        """Lee el texto que se muestra a la cámara. Devuelve None si no hay nada."""
        if not self.privacy.allows("ocr"):
            return None
        frame = self.camera.latest_bgr()
        if frame is None:
            return None
        regions = self.text_detector.detect(frame)
        box = regions[0].box if regions else None
        if auto and not regions:
            return None
        prepared = self.scanner.prepare(frame, box=box)
        if prepared is None:
            return None
        # Descarta fotogramas demasiado borrosos: darían basura al OCR.
        if self.scanner.sharpness(prepared) < 12.0 and not auto:
            log.debug("imagen borrosa para OCR")
        result = self.ocr.read(prepared, region=regions[0].as_list() if regions else None)
        if not result.text:
            return None

        stable = self.text_stabilizer.feed(
            result.text, result.confidence,
            region=result.region, language=result.language,
        )
        with self._lock:
            self._last_ocr = result
            if stable is not None:
                self._last_stable_text = stable
        if stable is None:
            return None
        if only_title:
            first = self.ocr.first_line(result)
            if first:
                stable.text = first
        if stable.is_new:
            self.text_stabilizer.mark_delivered(stable.text)
            self.events.observe("text_read", stable.text[:60], stable.confidence,
                                source="ocr", data={"full_text": stable.text},
                                min_duration=0.0, cooldown=8.0)
        result.text = stable.text
        result.confidence = stable.confidence
        result.stable = True
        return result

    def describe_room(self) -> str:
        """Descripción prudente de la habitación, bajo petición."""
        if not self.privacy.allows("scene"):
            return "Tengo la descripción de escena desactivada ahora mismo."
        frame = self.camera.latest_bgr()
        if frame is None:
            return "No tengo imagen de la cámara en este momento."
        hint, hint_conf = self._last_scene_hint
        reading = self.scene_detector.read(
            frame, object_labels=[t.label for t in self.objects.tracks()],
            hint_label=hint, hint_confidence=hint_conf,
        )
        snapshot = self.room.update(
            scene_reading=reading,
            object_tracks=self.objects.tracks(),
            people_count=self.people.count(),
        )
        with self._lock:
            self._room_snapshot = snapshot
        return snapshot["description"]

    def what_am_i_doing(self) -> str:
        if not self.privacy.allows("actions"):
            return "Tengo el reconocimiento de acciones desactivado."
        return self.actions.describe_current(self.people.primary_id() or 0)

    def count_fingers(self) -> str:
        if not self.privacy.allows("hands"):
            return "Tengo el seguimiento de manos desactivado."
        _n, frase = self.gestures.count_fingers(self.hands.states())
        return frase

    def what_am_i_holding(self) -> str:
        if not self.privacy.allows("hands"):
            return "Tengo el seguimiento de manos desactivado."
        from vision.tracking.object_tracker import label_es
        for hs in self.hands.states():
            if hs.holding:
                return f"Parece que sostienes {label_es(hs.holding)}."
        if self.hands.states():
            return "Veo tus manos, pero no distingo qué sostienes."
        return "No veo tus manos ahora mismo."

    def affective_estimate(self) -> dict:
        est = self.emotions.last()
        if est is None or not self.privacy.allows("emotions"):
            return {"affective_state": "undetermined", "confidence": 0.0}
        return est.as_dict()

    # ==================================================================
    def _on_action(self, event: ActionEvent) -> None:
        self.events.observe(
            "action_detected", event.action, event.confidence,
            source="action_analyzer",
            data={"person_id": event.person_id, "duration": event.duration,
                  "evidence": event.evidence, "action_es": event.action_es},
            min_duration=0.0, cooldown=2.0,
        )

    # ==================================================================
    def capabilities(self, refresh: bool = False):
        if self._capabilities is None or refresh:
            self._capabilities = caps_mod.detect(
                settings=self.cfg,
                registry=self.registry,
                privacy=self.privacy,
                camera_available=self.camera.is_running() or None,
                ocr_chain=self.ocr,
            )
        return self._capabilities

    def snapshot(self) -> dict:
        """Estado completo y serializable de la percepción."""
        with self._lock:
            room = dict(self._room_snapshot)
            stable = self._last_stable_text
            actions = list(self._last_actions)
            pose_state = dict(self._last_pose_state_dict)
        status = self.camera.status()
        att = self.attention.state()
        return {
            "camera": {
                "active": status.active,
                "index": status.index,
                "fps": round(status.real_fps, 1),
                "target_fps": status.target_fps,
            },
            "presence": self.people.as_state(),
            "attention": att.as_dict(),
            # ADITIVO: la postura ya viajaba por dentro del motor pero no salía
            # en el snapshot; sin ella `vision_state["postura"]` era siempre
            # "desconocida" y el punto 6 del pedido no se podía cumplir.
            "pose": pose_state,
            "hands": self.hands.as_state(),
            "objects": self.objects.as_state(),
            "room": room,
            "affective": self.affective_estimate(),
            "actions": [a.as_dict() for a in actions],
            "text": stable.as_dict() if stable is not None else None,
            "privacy": self.privacy.state().as_dict(),
            "capabilities": self.capabilities().as_dict(),
        }

    def metrics(self) -> dict:
        return self.scheduler.metrics_dict()

    def forget(self) -> int:
        """Olvida observaciones acumuladas ("olvida lo que viste")."""
        n = self.events.forget()
        self.text_stabilizer.reset()
        self.room.reset()
        self.actions.reset()
        self.emotions.reset()
        self.gestures.reset()
        with self._lock:
            self._last_ocr = None
            self._last_stable_text = None
            self._room_snapshot = {}
            self._last_actions = []
        return n
