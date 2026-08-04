"""Observación local y silenciosa de la cámara.

La cámara se abre automáticamente cuando YUE inicia, si existe y el usuario no
la desactivó en .env. Los fotogramas se procesan en memoria: no se guardan, no
se envían al motor de IA y no se usan para reconocer identidades.

MediaPipe aporta blendshapes faciales y puntos corporales. Si no está disponible,
OpenCV mantiene una detección básica de rostros para degradar con elegancia.
"""
from __future__ import annotations

import math
import platform
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import config


# NUEVO (lectura de emociones): a partir de los blendshapes que ya calcula
# MediaPipe, estima cómo se siente la persona (contenta, triste, sorprendida…).
# Aditivo y degradable: si el módulo no está, la cámara sigue dando solo señales.
try:
    from core import face_emotion
except Exception:  # pragma: no cover - respaldo si el módulo falta
    face_emotion = None


_FACE_MODEL_URL = getattr(
    config,
    "CAMERA_FACE_MODEL_URL",
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task",
)
_POSE_MODEL_URL = getattr(
    config,
    "CAMERA_POSE_MODEL_URL",
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
)


@dataclass(frozen=True)
class CameraObservation:
    timestamp: float
    camera_index: int
    faces: int = 0
    poses: int = 0
    face_cues: tuple[str, ...] = field(default_factory=tuple)
    body_cues: tuple[str, ...] = field(default_factory=tuple)
    # NUEVO (lectura de emociones): una PersonEmotion por rostro leído. Campo
    # opcional con valor por defecto: no rompe llamadas antiguas al constructor.
    person_emotions: tuple = field(default_factory=tuple)

    @property
    def people(self) -> int:
        return max(self.faces, self.poses)

    def emotion_reading_es(self) -> str:
        """NUEVO: impresión de cómo se siente la persona ("se le ve contenta")."""
        if not self.person_emotions or face_emotion is None:
            return ""
        try:
            return face_emotion.reading_es(self.person_emotions)
        except Exception:
            return ""

    def summary_es(self) -> str:
        if self.people <= 0:
            return "No detecto claramente a una persona frente a la cámara."
        who = "una persona" if self.people == 1 else f"aproximadamente {self.people} personas"
        cues = list(dict.fromkeys((*self.face_cues, *self.body_cues)))
        if not cues:
            return f"Detecto {who}, sin una señal facial o corporal clara en este momento."
        return (
            f"Detecto {who}. Señales visuales estimadas: {', '.join(cues)}. "
            "Son indicios de postura o gesto, no una lectura segura de emociones."
        )

    def rich_summary_es(self) -> str:
        """NUEVO: `summary_es` + la lectura emocional cuando la haya.

        Se usa en `describe()` y en el contexto para la IA. Mantiene intacto
        `summary_es` (y su aviso de que son estimaciones) por compatibilidad.
        """
        base = self.summary_es()
        if self.people <= 0:
            return base
        reading = self.emotion_reading_es()
        if not reading:
            return base
        return f"{base} Por su expresión, {reading} (es una impresión, no algo seguro)."


def _score_map(categories: Iterable[object]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in categories or ():
        name = str(
            getattr(item, "category_name", "")
            or getattr(item, "display_name", "")
            or getattr(item, "label", "")
        )
        if not name:
            continue
        try:
            out[name] = float(getattr(item, "score", 0.0) or 0.0)
        except Exception:
            continue
    return out


def summarize_blendshapes(scores: dict[str, float]) -> tuple[str, ...]:
    """Convierte blendshapes en señales descriptivas, sin afirmar emociones."""
    def avg(*names: str) -> float:
        values = [float(scores.get(name, 0.0)) for name in names]
        return sum(values) / max(1, len(values))

    cues: list[str] = []
    if avg("mouthSmileLeft", "mouthSmileRight") >= 0.34:
        cues.append("sonrisa visible")
    if float(scores.get("jawOpen", 0.0)) >= 0.42:
        cues.append("boca abierta")
    if avg("eyeBlinkLeft", "eyeBlinkRight") >= 0.58:
        cues.append("ojos cerrados o parpadeando")
    if float(scores.get("browInnerUp", 0.0)) >= 0.34:
        cues.append("cejas elevadas")
    if avg("mouthFrownLeft", "mouthFrownRight") >= 0.42:
        cues.append("comisuras de la boca hacia abajo")
    if avg("cheekPuff", "mouthPucker") >= 0.45:
        cues.append("labios fruncidos o mejillas infladas")
    return tuple(cues)


def _point_value(point: object) -> tuple[float, float, float]:
    if isinstance(point, dict):
        return (
            float(point.get("x", 0.0)),
            float(point.get("y", 0.0)),
            float(point.get("visibility", point.get("presence", 1.0)) or 0.0),
        )
    return (
        float(getattr(point, "x", 0.0)),
        float(getattr(point, "y", 0.0)),
        float(getattr(point, "visibility", getattr(point, "presence", 1.0)) or 0.0),
    )


def summarize_pose(landmarks: Iterable[object]) -> tuple[str, ...]:
    """Describe gestos corporales simples a partir de los 33 puntos de pose."""
    pts = list(landmarks or ())
    if len(pts) < 25:
        return ()

    def p(index: int):
        return _point_value(pts[index])

    nose = p(0)
    left_shoulder, right_shoulder = p(11), p(12)
    left_wrist, right_wrist = p(15), p(16)
    left_hip, right_hip = p(23), p(24)

    def visible(*items) -> bool:
        return all(item[2] >= 0.35 for item in items)

    cues: list[str] = []
    raised = 0
    if visible(left_shoulder, left_wrist) and left_wrist[1] < left_shoulder[1] - 0.04:
        raised += 1
    if visible(right_shoulder, right_wrist) and right_wrist[1] < right_shoulder[1] - 0.04:
        raised += 1
    if raised == 1:
        cues.append("un brazo levantado")
    elif raised >= 2:
        cues.append("ambos brazos levantados")

    def distance(a, b) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    near_face = 0
    if visible(nose, left_wrist) and distance(nose, left_wrist) < 0.18:
        near_face += 1
    if visible(nose, right_wrist) and distance(nose, right_wrist) < 0.18:
        near_face += 1
    if near_face == 1:
        cues.append("una mano cerca del rostro")
    elif near_face >= 2:
        cues.append("ambas manos cerca del rostro")

    if visible(left_shoulder, right_shoulder, left_hip, right_hip):
        shoulder_mid_x = (left_shoulder[0] + right_shoulder[0]) / 2
        hip_mid_x = (left_hip[0] + right_hip[0]) / 2
        lean = shoulder_mid_x - hip_mid_x
        if lean <= -0.075:
            cues.append("tronco inclinado hacia la izquierda")
        elif lean >= 0.075:
            cues.append("tronco inclinado hacia la derecha")

    return tuple(cues)


class _MediaPipeAnalyzer:
    def __init__(self, status: Callable[[str, bool], None]):
        self.status = status
        self.face = None
        self.pose = None
        self.mp = None
        # NUEVO (lectura de emociones): emociones estimadas del último análisis,
        # una por rostro. Lo lee el hilo de cámara al construir la observación.
        self.last_face_emotions: tuple = ()
        # NUEVO (accesibilidad): landmarks faciales del rostro principal del
        # último análisis (tuplas x,y,z normalizadas). Los usa el control por
        # cabeza; es la MISMA fuente de MediaPipe, sin pipeline aparte.
        self.last_face_landmarks: tuple = ()
        self._initialize()

    @property
    def available(self) -> bool:
        return self.face is not None or self.pose is not None

    def _initialize(self):
        try:
            import mediapipe as mp
        except Exception as exc:
            print("[camara] MediaPipe no disponible; uso detección facial básica:", exc)
            return

        self.mp = mp
        model_dir = Path(config.DATA_DIR) / "models"
        model_dir.mkdir(parents=True, exist_ok=True)
        face_path = model_dir / "face_landmarker.task"
        pose_path = model_dir / "pose_landmarker_lite.task"

        if bool(getattr(config, "CAMERA_DOWNLOAD_MODELS", True)):
            self._ensure_model(face_path, _FACE_MODEL_URL)
            self._ensure_model(pose_path, _POSE_MODEL_URL)

        try:
            if face_path.exists():
                options = mp.tasks.vision.FaceLandmarkerOptions(
                    base_options=mp.tasks.BaseOptions(model_asset_path=str(face_path)),
                    running_mode=mp.tasks.vision.RunningMode.IMAGE,
                    num_faces=max(1, int(getattr(config, "CAMERA_MAX_PEOPLE", 2))),
                    min_face_detection_confidence=0.5,
                    min_face_presence_confidence=0.5,
                    output_face_blendshapes=True,
                )
                self.face = mp.tasks.vision.FaceLandmarker.create_from_options(options)
        except Exception as exc:
            print("[camara] no pude iniciar Face Landmarker:", exc)
            self.face = None

        try:
            if pose_path.exists():
                options = mp.tasks.vision.PoseLandmarkerOptions(
                    base_options=mp.tasks.BaseOptions(model_asset_path=str(pose_path)),
                    running_mode=mp.tasks.vision.RunningMode.IMAGE,
                    num_poses=max(1, int(getattr(config, "CAMERA_MAX_PEOPLE", 2))),
                    min_pose_detection_confidence=0.5,
                    min_pose_presence_confidence=0.5,
                    min_tracking_confidence=0.5,
                )
                self.pose = mp.tasks.vision.PoseLandmarker.create_from_options(options)
        except Exception as exc:
            print("[camara] no pude iniciar Pose Landmarker:", exc)
            self.pose = None

    def _ensure_model(self, path: Path, url: str):
        if path.exists() and path.stat().st_size > 100_000:
            return
        try:
            import requests
            print(f"[camara] descargando modelo local {path.name}…")
            with requests.get(url, stream=True, timeout=(10, 90)) as response:
                response.raise_for_status()
                tmp = path.with_suffix(path.suffix + ".part")
                with tmp.open("wb") as fh:
                    for chunk in response.iter_content(1024 * 256):
                        if chunk:
                            fh.write(chunk)
                tmp.replace(path)
        except Exception as exc:
            print(f"[camara] no pude descargar {path.name}:", exc)
            try:
                path.with_suffix(path.suffix + ".part").unlink(missing_ok=True)
            except Exception:
                pass

    def analyze(self, frame_bgr) -> tuple[int, int, tuple[str, ...], tuple[str, ...]]:
        if not self.mp:
            return 0, 0, (), ()
        try:
            import cv2
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        except Exception as exc:
            print("[camara] no pude preparar el fotograma:", exc)
            return 0, 0, (), ()

        faces = poses = 0
        face_cues: list[str] = []
        body_cues: list[str] = []
        # NUEVO (lectura de emociones): reiniciamos la lista de este fotograma.
        face_emotions: list = []
        if self.face is not None:
            try:
                result = self.face.detect(image)
                faces = len(getattr(result, "face_landmarks", ()) or ())
                # NUEVO (accesibilidad): guardamos los puntos del rostro principal
                # para el control por cabeza (misma detección, sin coste extra).
                fl = getattr(result, "face_landmarks", ()) or ()
                self.last_face_landmarks = (
                    tuple((float(p.x), float(p.y), float(p.z)) for p in fl[0])
                    if fl else ()
                )
                for categories in getattr(result, "face_blendshapes", ()) or ():
                    scores = _score_map(categories)
                    face_cues.extend(summarize_blendshapes(scores))
                    # NUEVO: además de las señales, estimamos la emoción del rostro.
                    if face_emotion is not None:
                        try:
                            emo = face_emotion.infer_person_emotion(scores)
                            if emo is not None:
                                face_emotions.append(emo)
                        except Exception:
                            pass
            except Exception as exc:
                print("[camara] fallo de análisis facial:", exc)
        # Guardamos las emociones estimadas para que las lea el hilo de cámara.
        self.last_face_emotions = tuple(face_emotions)
        if self.pose is not None:
            try:
                result = self.pose.detect(image)
                pose_sets = getattr(result, "pose_landmarks", ()) or ()
                poses = len(pose_sets)
                for landmarks in pose_sets:
                    body_cues.extend(summarize_pose(landmarks))
            except Exception as exc:
                print("[camara] fallo de análisis corporal:", exc)
        return faces, poses, tuple(dict.fromkeys(face_cues)), tuple(dict.fromkeys(body_cues))

    def face_landmarks_only(self, frame_bgr):
        """Camino RÁPIDO para el control por cabeza: SOLO puntos faciales.

        Reutiliza el mismo FaceLandmarker (no hay pipeline nuevo) y NO calcula
        pose ni blendshapes, para poder correr a más FPS con poco coste.
        Devuelve (landmarks, (w, h)) o ((), None/(w,h)) si no hay rostro.
        """
        if not self.mp or self.face is None:
            return (), None
        try:
            import cv2
            h, w = frame_bgr.shape[:2]
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
            result = self.face.detect(image)
            fl = getattr(result, "face_landmarks", ()) or ()
            if not fl:
                return (), (w, h)
            pts = tuple((float(p.x), float(p.y), float(p.z)) for p in fl[0])
            return pts, (w, h)
        except Exception:
            return (), None

    def close(self):
        for task in (self.face, self.pose):
            try:
                if task is not None:
                    task.close()
            except Exception:
                pass


class CameraObserver:
    """Hilo de cámara con estado consultable y callbacks seguros para Qt signals."""

    def __init__(
        self,
        observation_callback: Callable[[CameraObservation], None] | None = None,
        status_callback: Callable[[str, bool], None] | None = None,
    ):
        self.observation_callback = observation_callback or (lambda _obs: None)
        self.status_callback = status_callback or (lambda _text, _active: None)
        self.enabled = bool(getattr(config, "CAMERA_ENABLED", True))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._latest: CameraObservation | None = None
        self._active = False
        self._camera_index = -1
        # NUEVO (riesgo por cámara): ventana móvil de "distrés" para risk_signal().
        # Guarda (ts, distress) de cada frame CON rostro leído en los últimos
        # minutos. La persistencia se evalúa sobre toda la ventana, nunca sobre
        # un frame suelto, para no dispararse por un bostezo o una mueca puntual.
        self._risk_lock = threading.Lock()
        self._distress_window: list[tuple[float, bool]] = []
        # NUEVO (accesibilidad): cuando el control por cabeza está activo, el
        # bucle corre a más FPS y entrega los landmarks a este consumidor por
        # fotograma (en el hilo de la cámara). No cambia la cadencia lenta del
        # análisis emocional/riesgo.
        self._fast_mode = threading.Event()
        self._landmark_consumer = None

    @property
    def active(self) -> bool:
        return self._active

    def set_landmark_consumer(self, consumer):
        """Registra un consumidor de landmarks por fotograma (control por cabeza).

        `consumer(landmarks, (w, h))` se invoca en el hilo de la cámara SOLO
        cuando el modo rápido está activo. Pasar None lo desconecta.
        """
        self._landmark_consumer = consumer

    def set_fast_mode(self, on: bool):
        """Activa/desactiva la cadencia rápida (para el control por cabeza)."""
        if on:
            self._fast_mode.set()
        else:
            self._fast_mode.clear()

    def start(self):
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="YueCamera", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.5)

    def latest(self) -> CameraObservation | None:
        with self._lock:
            return self._latest

    def describe(self) -> str:
        observation = self.latest()
        if not self.enabled:
            return "La cámara está desactivada en la configuración."
        if not self.active:
            return "No encontré una cámara disponible o todavía no pude abrirla."
        if observation is None:
            return "La cámara está activa, pero aún estoy preparando el análisis local."
        # NUEVO (lectura de emociones): incluye la impresión de cómo se siente la persona.
        return observation.rich_summary_es()

    def context_for_ai(self, max_age: float | None = None) -> str:
        observation = self.latest()
        if not observation:
            return ""
        limit = float(max_age or getattr(config, "CAMERA_CONTEXT_MAX_AGE", 8.0))
        if time.time() - observation.timestamp > limit:
            return ""
        # NUEVO (lectura de emociones): la IA también sabe el ánimo aparente.
        return observation.rich_summary_es()

    # ---------- riesgo emocional por señal VISUAL sostenida ----------
    def _record_emotion_sample(self, person_emotions, faces):
        """Añade un frame a la ventana móvil de distrés (uso interno, hilo cámara).

        Marca 'distrés' solo si la mejor emoción NO neutra del rostro es 'triste'
        o 'molesta' con confianza alta. Un frame sin rostro no se registra (mirar
        a otro lado no cuenta). La decisión de riesgo NO se toma aquí: esto solo
        acumula evidencia; la persistencia la evalúa risk_signal() sobre toda la
        ventana de varios minutos, evitando que un gesto puntual dispare nada.
        """
        try:
            person_emotions = person_emotions or ()
            if faces <= 0 and not person_emotions:
                return  # sin rostro leído: este frame no cuenta para el ratio
            ahora = time.time()
            keys = tuple(getattr(config, "CAMERA_RISK_KEYS", ("triste", "molesta")))
            min_conf = float(getattr(config, "CAMERA_RISK_MIN_CONFIDENCE", 0.6))
            fuertes = [
                e for e in person_emotions
                if getattr(e, "key", "neutral") != "neutral"
            ]
            distress = False
            if fuertes:
                mejor = max(fuertes, key=lambda e: getattr(e, "confidence", 0.0))
                if (getattr(mejor, "key", "") in keys
                        and float(getattr(mejor, "confidence", 0.0)) >= min_conf):
                    distress = True
            window_sec = float(getattr(config, "CAMERA_RISK_WINDOW_SEC", 180.0))
            corte = ahora - window_sec
            with self._risk_lock:
                self._distress_window.append((ahora, distress))
                self._distress_window = [
                    (t, d) for (t, d) in self._distress_window if t >= corte
                ]
        except Exception:
            pass

    def risk_signal(self) -> bool:
        """True si hay tristeza/tensión SOSTENIDA por cámara (no un gesto puntual).

        Público y seguro para llamar desde el hilo de la UI. Para evitar falsos
        positivos exige, a la vez, sobre la ventana de los últimos minutos:
          - suficientes frames en distrés (CAMERA_RISK_MIN_EVENTS),
          - que sean una fracción relevante de los rostros leídos (MIN_RATIO),
          - y que estén repartidos en el tiempo (MIN_SPAN_SEC): un bostezo o una
            mueca de unos segundos jamás cumple las tres condiciones.
        """
        try:
            window_sec = float(getattr(config, "CAMERA_RISK_WINDOW_SEC", 180.0))
            min_events = int(getattr(config, "CAMERA_RISK_MIN_EVENTS", 6))
            min_ratio = float(getattr(config, "CAMERA_RISK_MIN_RATIO", 0.45))
            min_span = float(getattr(config, "CAMERA_RISK_MIN_SPAN_SEC", 90.0))
            ahora = time.time()
            corte = ahora - window_sec
            with self._risk_lock:
                ventana = [(t, d) for (t, d) in self._distress_window if t >= corte]
                self._distress_window = ventana
            total = len(ventana)
            if total <= 0:
                return False
            distress_ts = [t for (t, d) in ventana if d]
            n = len(distress_ts)
            if n < min_events:
                return False
            if (n / total) < min_ratio:
                return False
            span = max(distress_ts) - min(distress_ts)
            return span >= min_span
        except Exception:
            return False

    def _emit_status(self, text: str, active: bool):
        self._active = bool(active)
        try:
            self.status_callback(text, bool(active))
        except Exception:
            pass

    def _run(self):
        try:
            import cv2
        except Exception as exc:
            self._emit_status("Falta opencv-python; la cámara no puede iniciarse.", False)
            print("[camara]", exc)
            return

        analyzer = None
        detector = self._haar_detector(cv2)
        retry = max(10.0, float(getattr(config, "CAMERA_RETRY_INTERVAL", 60.0)))
        # NUEVO (autoconexión de cámara): cuando NO hay cámara todavía, sondeamos
        # cada pocos segundos en vez de esperar el `retry` largo (60 s). Así, si
        # enciendes o conectas una cámara con YUE ya abierta, se engancha sola casi
        # de inmediato para poder verte, sin reiniciar la app.
        search_interval = max(0.5, float(getattr(config, "CAMERA_SEARCH_INTERVAL", 3.0)))

        while not self._stop.is_set():
            cap, index = self._open_camera(cv2)
            if cap is None:
                self._emit_status(
                    "Busco una cámara disponible… (se conectará sola en cuanto haya una).",
                    False,
                )
                self._stop.wait(search_interval)
                continue

            self._camera_index = index
            self._emit_status(
                f"Cámara {index} activa; análisis local, sin guardar ni enviar imágenes.",
                True,
            )
            print(f"[camara] activa indice={index} local_only=True")
            if analyzer is None:
                analyzer = _MediaPipeAnalyzer(self._emit_status)
            failures = 0
            interval = max(0.35, float(getattr(config, "CAMERA_ANALYSIS_INTERVAL", 1.2)))
            # NUEVO (accesibilidad): cuando el control por cabeza está activo el
            # bucle corre a más FPS para el cursor, pero la observación emocional
            # SIGUE saliendo a su ritmo lento (interval), sin alterar sus umbrales.
            fast_fps = max(1.0, float(getattr(config, "HEAD_CONTROL_FPS", 20.0)))
            fast_pause = max(0.02, 1.0 / fast_fps)
            last_obs_at = 0.0
            try:
                while not self._stop.is_set():
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        failures += 1
                        if failures >= 5:
                            break
                        self._stop.wait(0.25)
                        continue
                    failures = 0

                    head_on = (
                        self._fast_mode.is_set()
                        and self._landmark_consumer is not None
                        and analyzer is not None
                    )
                    # Camino RÁPIDO: solo landmarks para el cursor, sin tocar el
                    # pipeline lento de emociones/riesgo.
                    if head_on:
                        lms, shape = analyzer.face_landmarks_only(frame)
                        if lms:
                            try:
                                self._landmark_consumer(lms, shape)
                            except Exception:
                                pass

                    now = time.time()
                    if (now - last_obs_at) >= interval:
                        last_obs_at = now
                        faces, poses, face_cues, body_cues = (
                            analyzer.analyze(frame) if analyzer is not None else (0, 0, (), ())
                        )
                        # NUEVO (lectura de emociones): recogemos lo estimado en
                        # este fotograma (una emoción por rostro), si lo hay.
                        person_emotions = tuple(
                            getattr(analyzer, "last_face_emotions", ()) or ()
                        )
                        if faces == 0 and detector is not None:
                            try:
                                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                                # NUEVO (arreglo "me ve borroso"): ecualizamos el
                                # gris para que el rostro se detecte con distintas
                                # luces, y bajamos minSize para que también te vea
                                # aunque estés un poco más lejos de la cámara.
                                gray = cv2.equalizeHist(gray)
                                found = detector.detectMultiScale(
                                    gray, scaleFactor=1.1, minNeighbors=4, minSize=(48, 48)
                                )
                                faces = len(found)
                            except Exception:
                                pass
                        # NUEVO (riesgo por cámara): alimentamos la ventana de
                        # distrés con este frame (solo si hay rostro).
                        self._record_emotion_sample(person_emotions, faces)
                        observation = CameraObservation(
                            timestamp=time.time(),
                            camera_index=index,
                            faces=faces,
                            poses=poses,
                            face_cues=face_cues,
                            body_cues=body_cues,
                            person_emotions=person_emotions,
                        )
                        with self._lock:
                            self._latest = observation
                        try:
                            self.observation_callback(observation)
                        except Exception:
                            pass

                    # Ritmo del bucle: rápido si controlamos el cursor; si no, el
                    # ritmo lento de siempre.
                    self._stop.wait(fast_pause if head_on else interval)
            finally:
                try:
                    cap.release()
                except Exception:
                    pass
                self._emit_status("La cámara se desconectó; volveré a buscarla.", False)

            if not self._stop.is_set():
                # NUEVO (autoconexión): tras una desconexión también reintentamos
                # con el sondeo corto, para reengancharla en cuanto vuelva.
                self._stop.wait(min(retry, search_interval))

        if analyzer is not None:
            analyzer.close()
        self._emit_status("Cámara detenida.", False)

    @staticmethod
    def _haar_detector(cv2):
        """NUEVO (arreglo "me ve borroso"): el clasificador Haar es el detector de
        rostros de RESPALDO cuando MediaPipe no está (p. ej. TensorFlow no carga por
        falta de AVX). El problema era que dependía SOLO del XML que trae cv2, y en
        algunas instalaciones ese archivo no se puede abrir
        ("Can't open file ... haarcascade_frontalface_default.xml"). Resultado:
        detector=None -> rostros=0 -> YUE creía que no te distinguía y decía que te
        veía "borroso".

        Ahora probamos varias rutas EN ORDEN y nos quedamos con la primera que cargue:
          1) copia incluida en el propio proyecto (assets/haarcascades) -> nunca falla,
          2) el XML que trae cv2 (si su carpeta de datos está sana),
          3) la variante 'alt2' (más tolerante) en ambos sitios.
        Así el reconocimiento de rostro funciona aunque MediaPipe y la carpeta de
        datos de cv2 estén rotas.
        """
        base = Path(getattr(config, "RES_DIR", Path(__file__).resolve().parent.parent)) / "assets"
        try:
            cv2_data = Path(cv2.data.haarcascades)
        except Exception:
            cv2_data = None

        candidatos = []
        for nombre in ("haarcascade_frontalface_default.xml",
                       "haarcascade_frontalface_alt2.xml"):
            candidatos.append(base / "haarcascades" / nombre)  # 1) copia del proyecto
            if cv2_data is not None:
                candidatos.append(cv2_data / nombre)           # 2) copia de cv2

        for ruta in candidatos:
            try:
                if not ruta.exists():
                    continue
                detector = cv2.CascadeClassifier(str(ruta))
                if not detector.empty():
                    print(f"[camara] detector de rostros (Haar) cargado desde: {ruta}")
                    return detector
            except Exception:
                continue
        print("[camara] no encontré ningún clasificador Haar utilizable; "
              "el respaldo de detección de rostro queda desactivado.")
        return None

    @staticmethod
    def _open_camera(cv2):
        max_index = max(0, int(getattr(config, "CAMERA_MAX_INDEX", 3)))
        width = int(getattr(config, "CAMERA_WIDTH", 640))
        height = int(getattr(config, "CAMERA_HEIGHT", 480))
        backends = []
        if platform.system().lower() == "windows" and hasattr(cv2, "CAP_DSHOW"):
            backends.append(cv2.CAP_DSHOW)
        backends.append(getattr(cv2, "CAP_ANY", 0))

        for index in range(max_index + 1):
            for backend in backends:
                try:
                    cap = cv2.VideoCapture(index, backend)
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                    if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    ok, frame = cap.read()
                    if cap.isOpened() and ok and frame is not None:
                        return cap, index
                    cap.release()
                except Exception:
                    try:
                        cap.release()
                    except Exception:
                        pass
        return None, -1
