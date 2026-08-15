"""YUE · avatar 3D, voz interrumpible, control visual e iniciativa local."""
from __future__ import annotations

import os

from platforms import get_platform_controller

# Se ejecuta antes de importar Qt/pyautogui para que coordenadas y capturas usen
# la escala física real en Windows 125 %, 150 %, etc. (degradado no-op en Linux).
get_platform_controller().set_dpi_awareness()

os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--enable-transparent-visuals --disable-gpu-compositing --ignore-gpu-blocklist",
)

import threading
import time
import re
import unicodedata

from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication

import config
from core.ai_engine import AIEngine
from core.memory import Memory
from core import bonding, personality, safety, screen_capture as vision, emotion, commands, activity
from core import memory_consolidation
from core.camera_observer import CameraObserver, CameraObservation
from contracts.vision_host import VisionHost
from engine.controller_ctx import ControllerContext
from engine.media_director import MediaCompanionDirector
from engine.voice_director import VoiceDirector

# NUEVO (arbitraje del avatar): prioridades con las que cada subsistema pide la
# cara de YUE. Se resuelven contra core.state.Priority; si el gestor de estado
# no estuviera disponible, se usan estos números sueltos y nada se rompe.
#
#   SEGURIDAD > APOYO AL USUARIO > REACCIÓN DIRECTA > MULTIMEDIA > IDLE
#
# La consecuencia práctica que buscábamos: la música (MEDIA) ya NO puede pisar
# la expresión de un momento delicado de la conversación (APOYO).
try:
    from core.state import Priority as _StatePriority
    _PRIO_SEGURIDAD = int(_StatePriority.EMERGENCY)     # 100
    _PRIO_APOYO = int(_StatePriority.USER)              # 90
    _PRIO_CONVERSACION = int(_StatePriority.CONVERSATION)  # 70
    _PRIO_MEDIA = int(_StatePriority.MEDIA)             # 40
    _PRIO_PROFESORA = int(_StatePriority.TEACHER)       # 80
    _PRIO_AMBIENTE = int(_StatePriority.AMBIENT)        # 20
except Exception:  # pragma: no cover - degradación elegante
    _PRIO_SEGURIDAD, _PRIO_APOYO, _PRIO_CONVERSACION, _PRIO_MEDIA = 100, 90, 70, 40
    _PRIO_PROFESORA, _PRIO_AMBIENTE = 80, 20


#: Valencia y activación aproximadas de lo que lee la cámara facial. La fusión
#: las necesita para saber si dos fuentes apuntan en la misma dirección
#: ("tristeza" y "cansancio" sí; "tristeza" y "alegría" no). Son valores
#: gruesos a propósito: la cara da una pista, no una medida.
_CAMARA_VA = {
    "happy": (0.7, 0.6), "sad": (-0.7, 0.25), "angry": (-0.6, 0.85),
    "fear": (-0.6, 0.8), "surprise": (0.15, 0.8), "disgust": (-0.5, 0.5),
    "tired": (-0.35, 0.15), "neutral": (0.0, 0.3),
}


class VisionHostAdapter(VisionHost):
    """Adapta la app (Controller) al contrato VisionHost para vision/integration.

    PR 4: implementa formalmente los 4 miembros del contrato delegando en el
    gestor de estado y reenvía por __getattr__ el RESTO de la superficie
    duck-typed que vision/integration.py sigue usando (can_speak/can_animate y
    callbacks): chat, teacher, pet, _yue_say, _on_vision_status,
    observe_emotion, flags _*_busy, _yue_may_take_initiative.

    NOTA (REFACTOR_SPEC §4.1 fila 5): cuando VisionDirector se extraiga en
    PR 5, esas dependencias deben llegarle vía controller_ctx (patrón
    WorkerRegistry), NO heredando este __getattr__ hacia el Controller.
    """

    def __init__(self, controller):
        self._controller = controller

    def request_emotion(self, name: str, intensity: float, duration_ms: int,
                        priority: int, source: str) -> None:
        gestor = getattr(self._controller, "state_manager", None)
        if gestor is not None:
            gestor.request_emotion(str(name), float(intensity), int(duration_ms),
                                   priority=int(priority), source=str(source or "vision"))

    def emotion_would_win(self, priority: int) -> bool:
        gestor = getattr(self._controller, "state_manager", None)
        if gestor is None:
            return False  # prudente: sin gestor la visión no propone
        try:
            return bool(gestor.would_win(int(priority), "vision"))
        except Exception:
            return False

    @property
    def media_playing(self) -> bool:
        audio = getattr(self._controller, "audio", None)
        try:
            return bool(getattr(audio, "media_playing", False))
        except Exception:
            return False

    @property
    def is_speaking(self) -> bool:
        speaker = getattr(self._controller, "speaker", None)
        try:
            return bool(getattr(speaker, "is_speaking", False))
        except Exception:
            return False

    def __getattr__(self, nombre):
        # Puente formal: el resto del duck-typing de vision/integration.py se
        # resuelve contra el Controller real (getattr(..., None) lo absorbe).
        return getattr(self._controller, nombre)


def _valencia_activacion_camara(clave):
    """(valencia, activación) para una etiqueta facial. Neutro si no se conoce."""
    return _CAMARA_VA.get(str(clave or "").strip().lower(), (0.0, 0.3))

# NUEVO (conciencia de cámara): detecta "¿puedes verme?" y responde según el
# estado real de la cámara, para que YUE nunca niegue verte si la cámara está.
try:
    from core import vision_awareness
except Exception:  # pragma: no cover - degradación elegante
    vision_awareness = None
from core.system_audio import SystemAudioReactor, Reaction
from core.media_companion import MediaCompanion, CompanionReaction, AvatarMediaState
from core.voice import Speaker
from core.listener import VoiceListener
from core.pc_control import PCController, looks_like_pc_command
from core.head_control import HeadCursorController
from core.autonomy import Autonomy
from ui.desktop_pet import DesktopPet
from ui.foot_chat import FootChat
from ui.floating_text import FloatingText

# NUEVO: arquitectura de modos inteligentes (companion por defecto, teacher bajo demanda).
from mode_manager import ModeManager, ModeSpec, DEFAULT_MODE
from teacher import TeacherEngine, ProgressStore, ClassLog, PedagogyModel
from teacher import assistant as teacher_assistant
from teacher import participation as teacher_participation

# Identificadores de modo (evitan cadenas sueltas por el código).
MODE_COMPANION = DEFAULT_MODE          # "companion"
MODE_TEACHER = "teacher"


class CameraBridge(QObject):
    observation = pyqtSignal(object)
    status = pyqtSignal(str, bool)


class AudioBridge(QObject):
    # Las reacciones nacen en el hilo de audio; se llevan al hilo de la UI.
    reaction = pyqtSignal(object)
    status = pyqtSignal(str, bool)
    # NUEVO (petición 2): estado "hay vídeo/música sonando", del hilo de audio
    # al de la UI, para avisar al micrófono.
    media_state = pyqtSignal(bool)


class MediaCompanionBridge(QObject):
    """Cruza los callbacks de los hilos multimedia al hilo principal de Qt."""
    reaction = pyqtSignal(object)
    avatar = pyqtSignal(object)
    status = pyqtSignal(str, bool)


class AiWorker(QThread):
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, engine, messages):
        super().__init__()
        self.engine = engine
        self.messages = messages

    def run(self):
        try:
            self.done.emit(self.engine.chat(self.messages))
        except Exception as exc:
            self.failed.emit(str(exc))


class PdfPageVisionWorker(QThread):
    """NUEVO: hace que YUE MIRE una página de PDF rasterizada (PNG) y la explique.

    A diferencia de VisionWorker (que captura la pantalla), aquí la imagen viene de
    un archivo: la codificamos a base64 y la enviamos al modelo de visión. Se usa
    para páginas de puras imágenes/diagramas de un PDF escaneado.
    """
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, engine, system, instruction, image_path):
        super().__init__()
        self.engine = engine
        self.system = system
        self.instruction = instruction
        self.image_path = image_path

    def run(self):
        try:
            import base64
            with open(self.image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            self.done.emit(self.engine.look(self.system, b64, self.instruction))
        except Exception as exc:
            self.failed.emit(str(exc))


class VisionWorker(QThread):
    """MIRA la pantalla con el flujo completo: captura -> clasificación ->
    OCR y/o VisionRouter -> respuesta.

    ADITIVO: antes llamaba directo a `engine.look()` con un único proveedor y,
    si ese fallaba, YUE decía "no pude ver". Ahora la observación estructurada
    trae texto OCR aunque toda la visión multimodal se caiga, así que YUE puede
    seguir contando qué hay en pantalla.

    Emite `observed(object)` con la ScreenObservation para quien la quiera.
    """
    done = pyqtSignal(str)
    failed = pyqtSignal(str)
    observed = pyqtSignal(object)

    def __init__(self, engine, system, instruction, question="", force_fresh=True,
                 monitor=None):
        super().__init__()
        self.engine = engine
        self.system = system
        self.instruction = instruction
        self.question = question or instruction
        self.force_fresh = bool(force_fresh)
        self.monitor = monitor

    def run(self):
        try:
            obs = self.engine.look_screen(
                question=self.question, force_fresh=self.force_fresh,
                monitor=self.monitor,
            )
            try:
                self.observed.emit(obs)
            except Exception:
                pass
            if not obs:
                # Ni descripción visual ni texto: informamos del motivo real.
                motivo = " | ".join(str(e)[:160] for e in (obs.errors or []))
                self.failed.emit(motivo or "no obtuve nada de la pantalla")
                return
            respuesta = self.engine.answer_from_observation(
                obs, self.system, question=self.question)
            if not (respuesta or "").strip():
                # El router de texto tampoco respondió: al menos devolvemos lo
                # que se observó, en crudo, antes que un "no puedo ver".
                respuesta = (obs.visual_description
                             or ("Esto es lo que alcanzo a leer en tu pantalla:\n\n"
                                 + obs.ocr_text[:900]))
            self.done.emit(respuesta)
        except Exception as exc:
            self.failed.emit(str(exc))


class VisionLegacyWorker(QThread):
    """Camino antiguo (una sola llamada a `engine.look()` con la captura).

    Se conserva para el diagnóstico y para cualquier flujo que ya dependiera de
    él; el flujo normal usa VisionWorker.
    """
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, engine, system, instruction):
        super().__init__()
        self.engine = engine
        self.system = system
        self.instruction = instruction

    def run(self):
        try:
            self.done.emit(self.engine.look(self.system, vision.capture_b64(), self.instruction))
        except Exception as exc:
            self.failed.emit(str(exc))


class VisionDiagWorker(QThread):
    """Autodiagnóstico de visión: prueba CAPTURA y MODELO por separado."""
    done = pyqtSignal(object)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def run(self):
        report = []
        # 0) FILA de proveedores visuales (sin exponer ninguna clave).
        try:
            fila = self.engine.vision_router_report()
            if fila:
                disponibles = [p for p in fila if p.get("disponible")]
                detalle = " · ".join(
                    f"{p['nombre']}/{p['modelo'][:34]}"
                    f"{'' if p.get('disponible') else ' (' + (p.get('ultimo_error') or 'sin clave')[:28] + ')'}"
                    for p in fila[:5]
                )
                report.append((
                    f"Fila visual ({len(disponibles)}/{len(fila)} disponibles)",
                    bool(disponibles), detalle,
                ))
            else:
                report.append(("Fila visual", False,
                               "vacía (VISION_PROVIDER=none o sin claves multimodales)"))
        except Exception as exc:
            report.append(("Fila visual", False, str(exc)[:200]))
        # 0b) Endpoint único de compatibilidad.
        try:
            info = self.engine.vision_diag_info()
            report.append((
                "Endpoint de compatibilidad",
                bool(info.get("ok")),
                f"host={info.get('host','?')} · modelo={info.get('modelo','?')} · clave={info.get('clave','?')}",
            ))
        except Exception as exc:
            report.append(("Endpoint de compatibilidad", False, str(exc)[:200]))
        # 0c) Motor OCR (es el respaldo final: importa saber si existe).
        try:
            from core import screen_ocr
            hay_ocr = screen_ocr.available()
            report.append(("Motor OCR (respaldo)", bool(hay_ocr),
                           f"motor={screen_ocr.engine_name()}"))
        except Exception as exc:
            report.append(("Motor OCR (respaldo)", False, str(exc)[:200]))
        # 1) Captura de pantalla (con monitores y antigüedad del frame).
        info_cap = vision.capture_info()
        if info_cap["ok"]:
            report.append((
                "Captura de pantalla", True,
                f"{info_cap['width']}x{info_cap['height']}px · monitor "
                f"{info_cap.get('monitor', 1)}/{info_cap.get('monitors', 1)} · "
                f"{info_cap['bytes']} bytes · frame_age {info_cap.get('frame_age', 0)}s · "
                f"método {info_cap.get('method', '?')}",
            ))
        else:
            report.append(("Captura de pantalla", False, info_cap["note"]))
            self.done.emit(report)
            return
        # 2) Mirada REAL de principio a fin (clasificación + visión + OCR).
        try:
            obs = self.engine.look_screen(
                question="¿Qué se ve en la pantalla? Una sola frase.",
                force_fresh=True,
            )
            report.append((
                "Clasificación de contenido", True,
                f"tipo={obs.detected_content_type} · confianza={obs.confidence:.2f} · "
                f"estrategia={obs.strategy}",
            ))
            report.append((
                "Visión multimodal", obs.has_vision(),
                (f"{obs.provider_used}/{obs.model_used}: {obs.visual_description[:150]}"
                 if obs.has_vision()
                 else " | ".join(str(e)[:120] for e in obs.errors) or "sin respuesta"),
            ))
            report.append((
                "OCR de pantalla", obs.has_text(),
                f"{obs.ocr_chars} caracteres leídos" if obs.has_text() else "sin texto legible",
            ))
        except Exception as exc:
            report.append(("Mirada completa", False, str(exc)[:300]))
        self.done.emit(report)


class OcrVisionWorker(QThread):
    """Visión por OCR: lee el texto de la pantalla y responde con el modelo de
    chat (no necesita clave de visión multimodal). Corre fuera del hilo de UI."""
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, engine, system, instruction):
        super().__init__()
        self.engine = engine
        self.system = system
        self.instruction = instruction

    def run(self):
        try:
            self.done.emit(self.engine.look_ocr(self.system, self.instruction))
        except Exception as exc:
            self.failed.emit(str(exc))


class VisionPreflightWorker(QThread):
    """Valida al arrancar la clave/proveedor de visión SIN gastar tokens.

    Corre en segundo plano para no bloquear la interfaz; emite el dict que
    devuelve engine.preflight_vision().
    """
    done = pyqtSignal(object)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def run(self):
        try:
            self.done.emit(self.engine.preflight_vision())
        except Exception as exc:
            self.done.emit({"ok": False, "reason": "error", "detail": str(exc)[:200]})


class AppScanWorker(QThread):
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def run(self):
        try:
            self.done.emit(self.controller.rescan_apps())
        except Exception as exc:
            self.failed.emit(str(exc))


class PCWorker(QThread):
    progress = pyqtSignal(str)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, controller, engine, instruction):
        super().__init__()
        self.controller = controller
        self.engine = engine
        self.instruction = instruction

    def run(self):
        # --- aprendizaje: si ya sé hacer esto, lo repito sin gastar visión ---
        try:
            from core.learning import integration as learning
            replay = learning.try_replay(
                self.controller, self.instruction, progress=self.progress.emit
            )
            if replay is not None:
                self.done.emit(replay)
                return
        except Exception as exc:
            if "cancelada" in str(exc).lower():
                self.failed.emit(str(exc))
                return
            print("[aprendizaje] no pude repetir la receta:", exc)

        try:
            self.done.emit(self.controller.execute(
                self.instruction,
                engine=self.engine,
                progress=self.progress.emit,
            ))
        except Exception as exc:
            self.failed.emit(str(exc))


class PCRecoveryWorker(QThread):
    """Ejecuta 'deshacer' o 'repetir' fuera del hilo de la interfaz.

    Ambos actúan sobre teclado/ratón (Ctrl+Z o reejecutar un bloque), así que
    conviene no bloquear la UI. Los métodos del controlador ya devuelven un dict
    con el resultado (no lanzan), por eso 'failed' casi nunca se usa.
    """
    progress = pyqtSignal(str)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, controller, mode, actions=None, label=""):
        super().__init__()
        self.controller = controller
        self.mode = mode
        self.actions = actions          # solo para mode == "routine"
        self.label = label

    def run(self):
        try:
            if self.mode == "undo":
                self.done.emit(self.controller.undo_last())
            elif self.mode == "routine":
                self.done.emit(self.controller.run_actions(
                    self.actions, progress=self.progress.emit, label=self.label
                ))
            else:
                self.done.emit(self.controller.repeat_last(progress=self.progress.emit))
        except Exception as exc:
            self.failed.emit(str(exc))


class AutonomyWorker(QThread):
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, engine, messages):
        super().__init__()
        self.engine = engine
        self.messages = messages

    def run(self):
        try:
            self.done.emit(self.engine.chat(self.messages, timeout=55))
        except Exception as exc:
            self.failed.emit(str(exc))


class Controller(QObject):
    # NUEVO (accesibilidad): señales para pedir confirmación por voz desde el
    # hilo del PCWorker sin tocar la UI/voz fuera del hilo principal.
    confirm_request = pyqtSignal(str)   # pregunta -> hablar en el hilo principal
    confirm_notify = pyqtSignal(str)    # aviso (p. ej. timeout) en hilo principal

    def __init__(self):
        super().__init__()
        self.memory = Memory(config.DB_PATH)
        # NUEVO (bitácora): los módulos de acción registran en la bitácora vía el
        # sumidero desacoplado; aquí lo conectamos a memory.add_activity.
        activity.set_sink(self.memory.add_activity)
        self.engine = AIEngine()
        # NUEVO (comprensión emocional v2): cerebro de acompañamiento. Une el
        # intérprete afectivo, la detección de necesidades, la evaluación de
        # seguridad graduada (safety_ext) y la política de apoyo. Es ADITIVO: si
        # fallara, self.brain queda en None y todo el flujo antiguo sigue vivo.
        self.brain = None
        self.state_manager = None
        self._last_companion = None
        try:
            from core.companion_brain import CompanionBrain
            self.brain = CompanionBrain(
                engine=self.engine,
                use_semantic=bool(getattr(config, "AFFECT_SEMANTIC_ENABLED", True)),
            )
        except Exception as exc:
            print("[affect] sistema afectivo no disponible, sigo con el clásico:", exc)
        # NUEVO (memoria episódica emocional): capa ADITIVA sobre la memoria.
        # Recuerda ACONTECIMIENTOS concretos ligados a una emoción («mañana
        # tiene una entrevista y está nervioso porque la anterior salió mal»),
        # su seguimiento y cómo terminaron. No sustituye nada: facts, goals,
        # mood_log y affect_log siguen exactamente igual.
        #
        # Si fallara, queda en None y todo el flujo anterior sigue vivo.
        self.episodic = None
        self._episodic_lock = threading.Lock()
        try:
            from core.episodic_memory import EpisodicMemory
            if bool(getattr(config, "EPISODIC_MEMORY_ENABLED", True)):
                self.episodic = EpisodicMemory(memory=self.memory, engine=self.engine)
                # Caducidad y purga al arrancar: barato y evita que la base
                # crezca sin fin si YUE lleva meses funcionando.
                try:
                    self.episodic.maintenance()
                except Exception:
                    pass
        except Exception as exc:
            print("[episodic] memoria episódica no disponible:", exc)
        # NUEVO (memoria histórica relevante): TERCERA capa de memoria, también
        # ADITIVA. Busca en el historial COMPLETO lo que tiene que ver con el
        # mensaje de ahora mismo, para que YUE pueda recordar algo de hace meses
        # cuando vuelve a venir a cuento («Andrea volvió a escribirme»).
        #
        # No duplica nada: lee la tabla `messages` de core/memory.py, que ya es
        # la única fuente del historial. Si el usuario borra su historial, aquí
        # no queda ninguna copia.
        #
        # Reparto de papeles, para que no se pisen:
        #   recent_messages(12) -> continuidad inmediata de la charla
        #   episodic            -> acontecimientos importantes y su seguimiento
        #   memory_ext          -> pasado relevante para ESTE mensaje
        #
        # Si fallara, queda en None y todo el flujo anterior sigue vivo.
        self.memory_ext = None
        try:
            if bool(getattr(config, "MEMORY_RELEVANCE_ENABLED", True)):
                from core.memory_ext import MemoryExtension
                self.memory_ext = MemoryExtension(config.DB_PATH)
        except Exception as exc:
            print("[memory-ext] memoria histórica no disponible:", exc)
        # NUEVO (memoria narrativa): CUARTA capa, también ADITIVA. Las tres de
        # arriba recuerdan HECHOS; esta recuerda HISTORIAS: hilos de su vida
        # que evolucionan durante semanas o meses (una amistad con algo
        # pendiente, una meta, un proyecto) y que se componen de varios
        # episodios. Es lo que permite que «Andrea volvió a escribirme»
        # signifique algo quince días después de aquella discusión.
        #
        # No duplica nada: cuando puede, sus acontecimientos APUNTAN al episodio
        # o al mensaje original en vez de copiar su texto. Y no necesita modelo:
        # la vía principal es determinista y funciona igual sin conexión.
        #
        # Si fallara, queda en None y todo el flujo anterior sigue vivo.
        self.story_memory = None
        self._story_lock = threading.Lock()
        try:
            if bool(getattr(config, "STORY_MEMORY_ENABLED", True)):
                from core.story_memory import StoryMemory
                self.story_memory = StoryMemory(
                    memory=self.memory, engine=self.engine)
                # Las metas activas de siempre estrenan su hilo narrativo. Es
                # barato (una consulta) y no toca la tabla `goals`.
                try:
                    self.story_memory.sync_goals()
                except Exception:
                    pass
        except Exception as exc:
            print("[story-memory] memoria narrativa no disponible:", exc)
        # NUEVO: gestor central de estado. Arbitra la emoción del avatar por
        # prioridad para que la música o la cámara no pisen un momento delicado
        # de la conversación. Si no está, _set_avatar_emotion cae al modo directo.
        # NUEVO (cerebro central): el gestor ya no arbitra solo la emoción, sino
        # el COMPORTAMIENTO COMPLETO de YUE (cara, voz, iniciativa, animación) a
        # partir de propuestas con prioridad. Los tres estados (usuario, YUE,
        # sistema) viven separados dentro de él.
        self.avatar_renderer = None
        self._state_timer = None
        try:
            from core.state import YueStateManager
            self.state_manager = YueStateManager()
            try:
                self.state_manager.set_decision_logging(
                    bool(getattr(config, "STATE_LOG_DECISIONS", True)))
            except Exception:
                pass
        except Exception as exc:
            print("[estado] gestor central no disponible:", exc)
        # MIGRACIÓN de privacidad: mood_log guardaba una copia del mensaje del
        # usuario que ya estaba en 'messages'. Se limpia una sola vez y se deja
        # marca para no repetir el trabajo en cada arranque.
        try:
            if self.memory.get_state("mood_text_purged", "") != "1":
                limpiadas = self.memory.purge_mood_texts()
                self.memory.set_state("mood_text_purged", "1")
                if limpiadas:
                    print(f"[privacidad] limpiados {limpiadas} textos duplicados de mood_log.")
        except Exception as exc:
            print("[privacidad] no pude migrar mood_log:", exc)
        self.speaker = Speaker()
        self.listener = VoiceListener()
        self.pc = PCController()
        # NUEVO (accesibilidad): canal de confirmación verbal para acciones
        # irreversibles. pc_control llamará a esto (desde el hilo del PCWorker)
        # y YUE preguntará por voz esperando un "sí/no".
        self._pc_confirm = None  # {"event", "holder"} mientras hay una pendiente
        self.pc.set_confirm_callback(self._pc_confirm_by_voice)
        self.autonomy = Autonomy()
        self._camera_bridge = CameraBridge()
        self.camera = CameraObserver(
            observation_callback=self._camera_bridge.observation.emit,
            status_callback=self._camera_bridge.status.emit,
        )
        # NUEVO (accesibilidad): control del cursor con la cabeza. Reutiliza los
        # landmarks de la MISMA cámara y clica por el camino seguro de pc_control
        # (solo clic izquierdo; nunca puede disparar acciones bloqueadas). Se
        # aparta si hay una orden de PC en curso, para no pelear por el ratón.
        self.head_control = HeadCursorController(
            click_fn=self.pc.safe_click,
            is_blocked=lambda: self._pc_busy,
        )
        if bool(getattr(config, "HEAD_CONTROL_ENABLED", True)):
            self.camera.set_landmark_consumer(self.head_control.process_landmarks)
        self._audio_bridge = AudioBridge()
        self.audio = SystemAudioReactor(
            reaction_callback=self._audio_bridge.reaction.emit,
            status_callback=self._audio_bridge.status.emit,
            media_state_callback=self._audio_bridge.media_state.emit,
            # Reutiliza el análisis acústico existente; no abre una segunda
            # captura loopback ni duplica CPU/RAM.
            observation_callback=lambda obs, profile: (
                getattr(self, "media_companion", None)
                and self.media_companion.on_audio_observation(obs, profile)
            ),
        )

        self.pet = DesktopPet()
        self.chat = FootChat()

        # Puente al hilo de la interfaz. Sin esto, el control del PC no puede
        # apartar las ventanas de Yue del mouse: cambiar el estilo de una
        # ventana desde PCWorker (otro hilo) cuelga la app entera.
        try:
            from core import ui_bridge
            ui_bridge.install(self.pet)
        except Exception as exc:
            print("[ui-bridge] no disponible:", exc)

        # NUEVO (punto único del avatar): a partir de aquí, la ÚNICA llamada a
        # pet.set_emotion() del proyecto vive en core/state/renderer.py. Todos
        # los módulos mandan propuestas al gestor; el renderer pinta al ganador
        # y siempre desde el hilo de la interfaz (vía ui_bridge).
        try:
            if self.state_manager is not None:
                from core.state import attach_renderer
                self.avatar_renderer = attach_renderer(
                    self.state_manager, self.pet, speaker=self.speaker)
        except Exception as exc:
            print("[estado] no pude enganchar el renderer del avatar:", exc)

        self.pc.set_action_log_callback(self._on_pc_action_log)

        self.controller_ctx = ControllerContext()  # PR 5.1: workers viven en ctx
        self._floaters = []
        self._pc_busy = False
        self._pc_instruction = ""       # última orden de PC, para el aprendizaje
        # El módulo de lecciones necesita el LLM para reflexionar sobre los fallos.
        try:
            from core.learning import integration as learning
            learning.bind_engine(self.engine)
        except Exception as exc:
            print("[aprendizaje] paquete no disponible:", exc)
        self._vision_on = bool(config.VISION_ENABLED)
        self._vision_busy = False
        self._autonomy_busy = False
        self._autonomy_started_at = 0.0
        self._last_user_activity = time.time()
        self._last_user_text = ""
        self._chat_request_id = 0

        # Coordinador aditivo audio + pantalla + emoción + avatar + memoria.
        # Sus callbacks siempre pasan por señales Qt antes de tocar la interfaz.
        self._media_companion_bridge = MediaCompanionBridge()
        self.media_companion = MediaCompanion(
            ai_engine=self.engine,
            memory=self.memory,
            reaction_callback=self._media_companion_bridge.reaction.emit,
            avatar_callback=self._media_companion_bridge.avatar.emit,
            status_callback=self._media_companion_bridge.status.emit,
            context_callback=self._media_companion_context,
        )

        # La visión de pantalla está disponible desde el inicio, pero no hace
        # comentarios periódicos. Solo se usa para órdenes o peticiones explícitas.

        # NUEVO (latido del cerebro): `state_manager.tick()` estaba definido
        # pero NADIE lo llamaba, así que los TTL solo caducaban de rebote cuando
        # otro módulo pedía algo. Resultado: el modo profesora podía quedarse
        # gobernando la cara de YUE mucho después de terminar la explicación.
        # Con este timer, cada 250 ms se caducan las propuestas vencidas y se
        # RECALCULA el ganador (no se cae a neutral: gana lo que siguiera vivo).
        # Corre en el hilo de la interfaz, que es donde debe correr.
        if self.state_manager is not None:
            self._state_timer = QTimer(self)
            self._state_timer.setInterval(
                max(100, int(getattr(config, "STATE_TICK_MS", 250))))
            self._state_timer.timeout.connect(self._state_tick)
            self._state_timer.start()

        self._autonomy_timer = QTimer(self)
        self._autonomy_timer.setInterval(max(60, config.AUTONOMY_INTERVAL) * 1000)
        self._autonomy_timer.timeout.connect(self._autonomous_create)

        # NUEVO (check-in de ánimo): estado de una pregunta "del 1 al 5" a la
        # espera de respuesta, y un timer barato que evalúa si toca un check-in
        # proactivo (no intrusivo). Se apaga por completo con CHECKIN_ENABLED.
        self._pending_checkin = False
        # NUEVO (palabra de activación): YUE solo responde a frases que empiezan por
        # «Yue» (voz o texto). Configurable desde el .env. No afecta a la escucha de
        # música/vídeo del sistema, que es un subsistema aparte.
        self._wake_word_enabled = bool(getattr(config, "WAKE_WORD_ENABLED", True))
        # PR 5.2: el estado de entrada (voz/texto) y el wake-re viven en el
        # VoiceDirector/ctx; aquí solo se publican los componentes del host.
        self.controller_ctx.input_source = "texto"
        self.controller_ctx.speaker = self.speaker
        self.controller_ctx.listener = self.listener
        self.controller_ctx.chat = self.chat
        self.controller_ctx.pet = self.pet
        self.controller_ctx.state_manager = self.state_manager
        self.controller_ctx.say = self._yue_say
        self.controller_ctx.user_message = self.on_user_message
        self.controller_ctx.avatar_emotion = self._set_avatar_emotion
        self.voice = VoiceDirector(self.controller_ctx)
        self._checkin_timer = QTimer(self)
        self._checkin_timer.setInterval(
            max(30, int(getattr(config, "CHECKIN_CHECK_INTERVAL", 90))) * 1000
        )
        self._checkin_timer.timeout.connect(self._maybe_checkin)

        self.chat.send_message.connect(self.voice.on_text_message)
        # NUEVO: arrastrar un PDF/documento al chat -> YUE lo lee en Modo Profesora.
        self.chat.files_dropped.connect(self._on_files_dropped)
        self.pet.clicked.connect(self.toggle_chat)
        self.pet.moved.connect(lambda: self.chat.reposition(self.pet))
        self.pet.request_quit.connect(QApplication.instance().quit)
        self.pet.toggle_voice.connect(self.voice.toggle_voice)
        self.pet.toggle_mic.connect(self.voice.toggle_mic)
        self.pet.toggle_autonomy.connect(self._toggle_autonomy)
        # Última observación estructurada de pantalla (ScreenObservation).
        self._last_screen_observation = None
        self.pet.look_screen.connect(self._glance)
        self.speaker.speaking.connect(self.pet.set_talking)
        # NUEVO (lip-sync real): el envelope de amplitud del audio llega al
        # avatar para mover la boca con la voz real (con seno de respaldo).
        self.speaker.lipsync.connect(self.pet.set_lipsync)
        self.speaker.speaking.connect(self.listener.set_speaking)
        # Muralla anti-auto-escucha del audio del PC: mientras Yue habla, su TTS
        # sale por los altavoces y el loopback lo capturaría. Se suspende la
        # reacción igual que se hace con el micrófono.
        self.speaker.speaking.connect(self.audio.set_speaking)
        self.speaker.speaking.connect(self.media_companion.set_speaking)
        self.listener.barge_in.connect(self.voice.on_barge_in)
        self.listener.heard.connect(self.voice.on_heard)
        self.listener.status.connect(self.voice.on_mic_status)
        # NUEVO (accesibilidad): la pregunta de confirmación se habla SIEMPRE en
        # el hilo principal (voz + UI), aunque la pida el hilo del PCWorker.
        self.confirm_request.connect(self._do_confirm_ask)
        self.confirm_notify.connect(self._yue_say)
        # NUEVO: buffer de letra/diálogo oído mientras suena media, para que YUE
        # pueda comentar la canción/el vídeo con su contenido real.
        self.media_director = MediaCompanionDirector()
        self.listener.media_heard.connect(self.media_director.remember_heard)
        self._camera_bridge.status.connect(self._on_camera_status)
        self._camera_bridge.observation.connect(self._on_camera_observation)
        self._audio_bridge.reaction.connect(self._on_audio_reaction)
        self._audio_bridge.status.connect(self._on_audio_status)
        self._media_companion_bridge.reaction.connect(self._on_media_companion_reaction)
        self._media_companion_bridge.avatar.connect(self._on_media_companion_avatar)
        self._media_companion_bridge.status.connect(self._on_media_companion_status)
        # Un único evento alimenta tanto al filtro del micrófono como al nuevo
        # coordinador. Así ambos comparten exactamente el mismo antirrebote.
        self._audio_bridge.media_state.connect(self._on_media_state)

        # NUEVO: sistema de MODOS. La personalidad de YUE no cambia; solo cambia
        # qué motor conversa. Companion es el modo por defecto (apoyo emocional) y
        # Teacher se activa/desactiva por voz o texto. Todo es aditivo y desacoplado.
        try:
            self._setup_modes()
        except Exception as exc:
            print("[modos] no se pudo inicializar el sistema de modos:", exc)
            self.modes = None

        self.pet.show()
        self.chat.show()
        self.chat.reposition(self.pet)
        self._refresh_bond()
        self._greet()
        if self.autonomy.enabled:
            self._autonomy_timer.start()
        if bool(getattr(config, "CHECKIN_ENABLED", True)):
            self._checkin_timer.start()
        self.listener.start()
        self.camera.start()
        self.media_companion.start()
        self.audio.start()
        # ADITIVO: validación de la clave de visión al arrancar. Corre en segundo
        # plano (no bloquea) y, si el proveedor rechaza la clave (401/403), avisa
        # claramente en el chat en vez de fallar en silencio más tarde.
        QTimer.singleShot(1500, self._vision_preflight)
        # NUEVO (memoria a largo plazo): al arrancar, en un hilo de fondo y a baja
        # frecuencia, consolidamos el historial viejo en un resumen persistente.
        # Best-effort: si no toca o no hay clave de IA, no hace nada. Nunca bloquea
        # el arranque ni el chat.
        QTimer.singleShot(8000, self._schedule_memory_consolidation)

        # ADITIVO (Percepción V3): sistema de percepción por cámara opt-in. Es
        # inerte si VISION_V3_ENABLED=false (por defecto): no abre la cámara, no
        # importa DeepFace y no cambia ningún comportamiento actual. Cuando se
        # activa, marshala emoción/voz/estado al hilo de la interfaz igual que
        # CameraBridge. Se engancha al final para que self.pet y self.camera ya
        # existan. Si VISION_V3_ENABLED=true, recuerda poner CAMERA_ENABLED=false
        # (o una CAMERA_INDEX distinta) para no pelear por la misma webcam.
        try:
            from core.vision import integration as vision_v3
            self.vision_v3 = vision_v3.attach(self)
        except Exception as exc:
            print("[vision-v3] no se pudo enganchar el sistema de percepción:", exc)
            self.vision_v3 = None

        # Sistema de visión MediaPipe Tasks (paquete vision/, ADITIVO). Inerte si
        # VISION_MP_ENABLED=false (por defecto): no abre la cámara ni importa
        # MediaPipe. Al activarlo, marshala emoción/voz/estado al hilo de la
        # interfaz. Si lo enciendes, usa CAMERA_ENABLED=false para el observador
        # clásico o una CAMERA_INDEX distinta para no pelear por la misma webcam.
        try:
            from vision import integration as vision_mp
            self.vision_mp = vision_mp.attach(VisionHostAdapter(self))
        except Exception as exc:
            print("[vision-mp] no se pudo enganchar el sistema de visión:", exc)
            self.vision_mp = None
        # PR 3: composición del OCRPort. core/screen_ocr ya no importa vision;
        # la fábrica del port (OCREngineChain implementa contracts.OCRPort) se
        # registra aquí para que el diagnóstico y la lectura de cámara funcionen
        # igual que antes sin el ciclo de imports.
        try:
            from core import screen_ocr
            from vision.ocr.ocr_engine import OCREngineChain
            screen_ocr.set_ocr_port_factory(OCREngineChain)
        except Exception as exc:
            print("[vision] no se pudo componer el OCRPort:", exc)

        # ADITIVO (migración, punto 16 del pedido): con VISION_REPLACE_LEGACY=true
        # el `CameraObserver` clásico se sustituye por un ADAPTADOR que expone la
        # misma interfaz (.active/.start/.stop/.latest/.describe/.context_for_ai/
        # .risk_signal/.set_fast_mode) pero por dentro habla con el motor de
        # percepción nuevo. Así se prueba la migración SIN borrar el código viejo.
        # Por defecto está en false: nada cambia.
        try:
            if (self.vision_mp is not None
                    and bool(getattr(config, "VISION_REPLACE_LEGACY", False))):
                from vision.legacy_adapter import LegacyCameraObserverAdapter
                self.camera.stop()          # libera la webcam del observador clásico
                self.camera = LegacyCameraObserverAdapter(
                    getattr(self.vision_mp, "perception", None),
                    status_callback=self._camera_bridge.status.emit,
                    context_max_age=float(getattr(config, "CAMERA_CONTEXT_MAX_AGE", 8.0)),
                )
                # El control por cabeza se reengancha al adaptador: el motor
                # nuevo entrega los landmarks crudos igual que el clásico, así
                # que el cursor sigue funcionando con UNA sola webcam.
                if bool(getattr(config, "HEAD_CONTROL_ENABLED", True)):
                    self.camera.set_landmark_consumer(self.head_control.process_landmarks)
                self.camera.start()
                print("[vision-mp] migración activa: CameraObserver -> adaptador de percepción.")
        except Exception as exc:
            print("[vision-mp] no pude activar el adaptador de migración:", exc)

    # ==================================================================
    # CEREBRO CENTRAL: latido, estado del sistema y propuestas
    # ==================================================================
    def _state_tick(self):
        """Latido del gestor de estado (cada 250 ms, en el hilo de la interfaz).

        Hace dos cosas:

        1. `state_manager.tick()` caduca las propuestas vencidas y RECALCULA el
           ganador. Antes este método existía pero no lo llamaba nadie, así que
           el modo profesora podía seguir gobernando la cara de YUE mucho
           después de haber terminado la explicación.
        2. Refresca el SYSTEM STATE con los interruptores de verdad. Los campos
           `mic`, `camera`, `voice`, etc. estaban definidos pero casi nadie los
           escribía: eran decorativos y mentían. Ahora se leen del sitio real.

        Todo va dentro de try: un fallo aquí no puede tumbar la aplicación, y
        se ejecuta muy a menudo.
        """
        gestor = getattr(self, "state_manager", None)
        if gestor is None:
            return
        try:
            gestor.tick()
        except Exception as exc:
            print("[estado] fallo en el latido:", exc)
        try:
            self._sync_system_state()
        except Exception as exc:
            print("[estado] no pude sincronizar el estado del sistema:", exc)

    def _sync_system_state(self):
        """Vuelca el estado REAL de la aplicación en el SYSTEM STATE.

        Se lee todo con `getattr` y valores por defecto: si algún subsistema no
        está disponible (o aún no se creó), el campo se queda como estaba en vez
        de reventar.
        """
        gestor = getattr(self, "state_manager", None)
        if gestor is None:
            return

        # --- micrófono ---
        escuchando = bool(getattr(getattr(self, "listener", None), "enabled", False))
        mic = "listening" if escuchando else "off"

        # --- cámara: cuenta cualquiera de los tres sistemas de visión ---
        camara = False
        for atributo in ("camera", "vision_mp", "vision_v3"):
            objeto = getattr(self, atributo, None)
            if objeto is not None and bool(getattr(objeto, "active", False)):
                camara = True
                break

        # --- voz: `is_speaking` es una PROPIEDAD, no un método (ya nos mordió) ---
        hablando = bool(getattr(getattr(self, "speaker", None), "is_speaking", False))

        # --- multimedia y modo profesora ---
        sonando = bool(getattr(getattr(self, "audio", None), "media_playing", False))
        profesora = bool(getattr(getattr(self, "teacher", None), "is_active", False))

        pc_ocupado = bool(getattr(self, "_pc_busy", False))
        vision_ocupada = bool(getattr(self, "_vision_busy", False))
        autonomia = bool(getattr(self, "_autonomy_busy", False))

        # --- actividad: lo que YUE está haciendo AHORA, de más a menos urgente ---
        if pc_ocupado:
            actividad, modo = "controlling", "control"
        elif profesora:
            actividad, modo = "teaching", "teacher"
        elif vision_ocupada:
            actividad, modo = "watching", "companion"
        elif autonomia:
            actividad, modo = "conversing", "autonomy"
        elif hablando:
            actividad, modo = "conversing", "companion"
        else:
            actividad, modo = "idle", "companion"

        gestor.update_system(
            mic=mic,
            camera="active" if camara else "off",
            voice="speaking" if hablando else "silent",
            mode=modo,
            media_playing=sonando,
            pc_busy=pc_ocupado,
            vision_busy=vision_ocupada,
            autonomy_busy=autonomia,
            teacher_active=profesora,
            activity=actividad,
        )

    def _publish_companion_state(self, resultado):
        """Vuelca un `CompanionResult` en el cerebro central.

        Aquí se hace efectiva la separación que da nombre a todo esto:

            result.affect + result.context + result.intent  →  USER STATE
            result.expression + result.decision             →  YUE PROPOSAL

        Se reutilizan las piezas que ya existían tal cual. `CompanionExpression
        Policy` ya decidía bien la cara de YUE (responde al usuario en vez de
        imitarlo); lo único que se añade es el resto del comportamiento
        (qué hace, cómo suena, cuánta iniciativa se permite) para que el estado
        final sea coherente y no una cara suelta.
        """
        gestor = getattr(self, "state_manager", None)
        if gestor is None or resultado is None:
            return
        try:
            from core.state import (
                observation_from_companion, proposal_from_companion,
                user_state_from_companion,
            )
        except Exception:
            return

        # 1) El texto entra como OBSERVACIÓN, con su peso (1.00) y su marca de
        #    explícito. Es lo que impide que una cara neutra en cámara tumbe un
        #    "estoy muy triste" escrito con todas las letras.
        try:
            observacion = observation_from_companion(resultado)
            if observacion is not None:
                gestor.observe(observacion)
        except Exception as exc:
            print("[estado] no pude registrar la observación de texto:", exc)

        # 2) Lo que ningún sensor sabe (necesidad, tendencia, riesgo) lo aporta
        #    el cerebro afectivo. NO se recalcula: se copia de AffectiveContext.
        try:
            base = gestor.user_state()
            usuario = user_state_from_companion(resultado, base=base)
            gestor.update_user(
                need=usuario.need, secondary_need=usuario.secondary_need,
                trend=usuario.trend, sustained=usuario.sustained,
                duration_s=usuario.duration_s, stability=usuario.stability,
                distress=usuario.distress, trigger=usuario.trigger,
                safety_level=usuario.safety_level,
            )
        except Exception as exc:
            print("[estado] no pude actualizar el estado del usuario:", exc)

        # 3) La reacción de YUE va como PROPUESTA. Puede perder (si la profesora
        #    o una emergencia mandan) y no pasa nada: seguirá viva y tomará el
        #    mando en cuanto la otra caduque.
        try:
            propuesta = proposal_from_companion(resultado)
            if propuesta is not None:
                gestor.propose(propuesta)
        except Exception as exc:
            print("[estado] no pude enviar la propuesta de comportamiento:", exc)

    def _set_avatar_emotion(self, name, intensity=0.6, duration_ms=4500,
                            *, priority=None, source="sistema"):
        """Propone una cara para YUE. Ya NO la aplica: eso es del renderer.

        Antes había llamadas sueltas a `pet.set_emotion(...)` desde la
        conversación, la música, la cámara y el control del PC, y ganaba siempre
        la última en llegar. Por eso una canción alegre podía poner al avatar
        eufórico justo mientras el usuario contaba algo doloroso.

        Ahora esto es solo una PROPUESTA. `YueStateManager` arbitra por
        PRIORIDAD y `AvatarRenderer` pinta al ganador (y es el único sitio del
        proyecto que llama a `pet.set_emotion`):

            EMERGENCY (seguridad) > USER (apoyo) > TEACHER (profesora) >
            CONVERSATION > EMOTION > MEDIA (música) > AMBIENT > IDLE

        Devuelve si esta propuesta gobierna AHORA. Devolver False no significa
        que se haya perdido: sigue viva y ganará cuando caduque la de arriba.

        Si el gestor no estuviera disponible (arranque degradado), se cae al
        modo directo de siempre para no dejar el avatar congelado.
        """
        try:
            from core.state import Priority
            prioridad = Priority.EMOTION if priority is None else priority
        except Exception:
            prioridad = 60

        gestor = getattr(self, "state_manager", None)
        if gestor is not None:
            try:
                # El renderer, suscrito al gestor, pintará al ganador. Aquí NO
                # se toca el avatar: esa es justamente la regla que se quería
                # imponer, y el único punto que la cumple es core/state/renderer.
                return bool(gestor.request_emotion(
                    name, intensity, duration_ms,
                    priority=int(prioridad), source=source))
            except Exception as exc:
                print("[estado] fallo al proponer la emoción:", exc)

        # EXCEPCIÓN CONSERVADA A PROPÓSITO: sin gestor de estado no hay
        # renderer, y sin renderer nadie pintaría nunca al avatar. Antes que
        # dejar a YUE con la cara congelada, se aplica directo. Solo ocurre si
        # `core.state` no llegó a importarse en el arranque.
        try:
            self.pet.set_emotion(name, intensity, duration_ms)
        except Exception as exc:
            print("[avatar] no pude aplicar la emoción:", exc)
            return False
        return True

    # ---------- interfaz ----------
    def toggle_chat(self):
        if self.chat.isVisible():
            self.chat.hide()
        else:
            self.chat.reposition(self.pet)
            self.chat.show()
            self.chat.ensure_input_ready()

    def _user_float(self, text):
        floater = FloatingText(text, self.chat.anchor_for_floating())
        floater.destroyed.connect(
            lambda *_: self._floaters.remove(floater) if floater in self._floaters else None
        )
        self._floaters.append(floater)
        floater.start()

    def _interrupt_response(self):
        """Corta voz y descarta respuestas antiguas sin bloquear el chat."""
        self.speaker.stop()
        self.pet.set_talking(False)
        self._chat_request_id += 1
        # NUEVO: cualquier interrupción cancela un auto-avance de página pendiente.
        self._teacher_autoadvance_pending = False

    def _yue_say(self, text, user_context=None):
        # La emoción del avatar se infiere del texto CRUDO (con toda su señal
        # emocional); el texto que se dice/habla va limpio. Así la emoción vive
        # en la cara y el cuerpo, nunca en las palabras.
        clean = emotion.clean_response(text)

        # NUEVO: si este turno pasó por el cerebro afectivo, la cara de YUE ya
        # está decidida por CompanionExpressionPolicy —que responde a lo que le
        # pasa al usuario en vez de imitarlo— y se REFRESCA aquí para que dure
        # toda la respuesta. Antes se recalculaba desde el texto de YUE, que es
        # justo lo que hacía que un usuario furioso acabara con un avatar
        # furioso.
        resultado = getattr(self, "_last_companion", None)
        if resultado is not None:
            # Se REPROPONE para refrescar el TTL: la cara debe durar toda la
            # respuesta. La decisión en sí ya la tomó `_publish_companion_state`.
            self._publish_companion_state(resultado)
        else:
            state = emotion.infer_conversation_state(
                user_context or self._last_user_text, text)
            self._set_avatar_emotion(state.name, state.intensity, state.duration_ms,
                                     priority=_PRIO_CONVERSACION, source="conversacion")
        # NUEVO: por defecto YUE solo habla. Muestra el texto únicamente si se
        # pidió (CHAT_MOSTRAR_RESPUESTAS) o si la voz está apagada (para no callar).
        mostrar = bool(getattr(config, "CHAT_MOSTRAR_RESPUESTAS", False)) or not self.speaker.enabled
        if mostrar:
            self.chat.show_reply(clean)
        if self.speaker.enabled:
            self.listener.set_tts_text(clean)
        # SYSTEM STATE: la voz pasa a "speaking" ANTES de hablar. Antes este
        # campo solo se refrescaba en el latido, así que durante los primeros
        # 250 ms de cada frase el estado decía que YUE estaba callada. Los
        # módulos que consultan si pueden hablar leían un dato falso justo en
        # el momento en que más importaba.
        try:
            if self.state_manager is not None:
                self.state_manager.update_system(voice="speaking")
        except Exception:
            pass
        self.speaker.say(clean)

    # ---------- vínculo y prompt ----------
    def _refresh_bond(self):
        points = self.memory.get_bond_points()
        current, _, _ = bonding.progress(points)
        self.chat.set_bond(current)
        return current

    def _greet(self):
        current = self._refresh_bond()
        message = (
            "Ah… ya llegaste. No es que te estuviera esperando. ¿Qué necesitas?"
            if current.index <= 1 else
            "Volviste. Bien… cuéntame qué haremos hoy."
        )
        self._yue_say(message, "saludo")

    def _system_prompt(self, bond_level, directive=None):
        # NUEVO (memoria a largo plazo): recuerdo consolidado de hace tiempo. Se
        # suma a lo reciente (recent_messages/get_facts, intactos). Si aún no hay
        # ningún resumen o algo falla, no se añade nada y el prompt no cambia.
        recuerdo_largo = ""
        try:
            resumenes = self.memory.get_long_term_summaries(3)
            if resumenes:
                recuerdo_largo = ("\n\nDe hace tiempo recuerdas (memoria a largo "
                                  "plazo, úsalo solo si viene a cuento): "
                                  + " ".join(r.strip() for r in resumenes if r))
        except Exception:
            recuerdo_largo = ""
        # NUEVO (memoria emocional): recuerdo de fondo del ánimo reciente. Si algo
        # falla o aún no hay registro, queda vacío y el prompt no cambia.
        try:
            mood_summary = self.memory.get_mood_summary(7)
        except Exception:
            mood_summary = ""
        # NUEVO (bienestar): decide si toca el recordatorio discreto de apoyo
        # humano/profesional. A prueba de fallos: si algo falla, no se añade nada.
        try:
            wellbeing_nudge = self._wellbeing_nudge_due()
        except Exception:
            wellbeing_nudge = False
        prompt = personality.build_system_prompt(
            bond_level=bond_level,
            facts=self.memory.get_facts(),
            goals=self.memory.list_goals(),
            safety_directive=directive,
            mood_summary=mood_summary,
            wellbeing_nudge=wellbeing_nudge,
        )
        if bool(getattr(config, "CAMERA_CONTEXT_IN_CHAT", True)):
            camera_context = self.camera.context_for_ai()
            if camera_context:
                prompt += (
                    "\n\nContexto visual local de cámara (sin identidad, sin fotos guardadas): "
                    + camera_context
                    + " No lo menciones salvo que sea pertinente o el usuario pregunte."
                )
            # NUEVO (conciencia de cámara): afirmación clara —como la del audio—
            # de que SÍ puede ver por la cámara cuando está activa. Evita que ante
            # una formulación libre ("¿alcanzas a verme?") el modelo diga que no.
            if vision_awareness is not None:
                try:
                    note = vision_awareness.camera_prompt_note(self.camera)
                    if note:
                        prompt += note
                except Exception:
                    pass
            # ADITIVO (visión avanzada): contexto breve y filtrado del motor de
            # percepción (texto leído, objetos, acción en curso, gesto nuevo,
            # estimación de ánimo). Solo entra lo reciente, estable y no
            # repetido; nunca listas largas ni coordenadas.
            if getattr(self, "vision_mp", None) is not None:
                try:
                    contexto_avanzado = self.vision_mp.context_for_ai()
                    if contexto_avanzado:
                        prompt += (
                            "\n\n" + contexto_avanzado
                            + "\n(Las estimaciones de ánimo son impresiones aproximadas, "
                              "nunca hechos. No diagnostiques ni afirmes emociones como "
                              "certezas. Menciona lo visual solo si viene a cuento.)"
                        )
                except Exception:
                    pass
        # NUEVO (percepción de pantalla v3): afirmación —como la de cámara y
        # audio— de que YUE SÍ puede mirar la PANTALLA cuando se le pregunta.
        # Respaldo para las preguntas que se escapen del regex de vision_look, y
        # así el modelo no invente "solo veo por la cámara". Fuera del gate de
        # CAMERA_CONTEXT_IN_CHAT porque la pantalla no depende de la cámara. Solo
        # afirma la CAPACIDAD de mirar, no contenido concreto.
        if vision_awareness is not None:
            try:
                nota_pantalla = vision_awareness.screen_prompt_note()
                if nota_pantalla:
                    prompt += nota_pantalla
            except Exception:
                pass
        if bool(getattr(config, "AUDIO_REACT_CONTEXT_IN_CHAT", True)):
            audio_context = self.audio.context_for_ai()
            # Si ahora mismo no hay señal fresca, usamos lo que sonó hace poco.
            if not audio_context:
                try:
                    reciente, edad = self.audio.recent_content(max_age=150.0)
                    if reciente is not None:
                        seg = int(edad or 0)
                        audio_context = (
                            f"(hace unos {seg} s) " + reciente.summary_es()
                        )
                except Exception:
                    pass
            lyrics = ""
            try:
                lyrics = self.media_director.recent_lyrics()
            except Exception:
                lyrics = ""
            if audio_context or lyrics:
                extra = ""
                if lyrics:
                    extra = f" Algo de lo que oíste (letra/diálogo, con posibles errores): «{lyrics}»."
                prompt += (
                    "\n\nSÍ puedes oír el audio que sale del PC (análisis local, sin "
                    "grabar); nunca digas que no puedes escuchar. Lo que percibes: "
                    + (audio_context or "algo estuvo sonando hace poco")
                    + extra
                    + " Úsalo solo si viene a cuento o si el usuario pregunta qué escuchas."
                )
        # Contexto multimedia fusionado y memoria de gustos/títulos. El primero
        # describe lo que acompaña ahora; el segundo permite referencias como
        # «la canción de ayer» sin inventar títulos.
        try:
            media_now = self.media_companion.context_for_ai()
        except Exception:
            media_now = ""
        try:
            media_memory = self.memory.multimedia_context(5)
        except Exception:
            media_memory = ""
        if media_now or media_memory:
            prompt += (
                "\n\nContexto de acompañamiento multimedia: "
                + " ".join(part for part in (media_now, media_memory) if part)
                + " Reacciona como compañera, no como narradora; no inventes escenas, "
                  "títulos ni gustos que no figuren aquí."
            )

        # NUEVO (comprensión emocional v2): bloque estructurado con lo que YUE
        # ha entendido del usuario en este turno —qué siente, qué necesita, qué
        # NO quiere que haga y cómo debería acompañarle—. No es una respuesta
        # prefabricada: son parámetros de comportamiento. YUE sigue escribiendo
        # con su propia voz, y el bloque le prohíbe explícitamente repetir estas
        # etiquetas en voz alta.
        #
        # Va AL FINAL, después de la personalidad y el contexto, para que pese
        # sobre lo anterior cuando haya conflicto (p. ej. su carácter travieso
        # frente a un «déjame solo»).
        try:
            resultado = getattr(self, "_last_companion", None)
            if resultado is not None and resultado.prompt_block:
                prompt += "\n\n" + resultado.prompt_block
        except Exception:
            pass

        # NUEVO (memoria episódica emocional): RECUERDOS CONCRETOS. Pocos y bien
        # elegidos —como máximo EPISODIC_MAX_CONTEXT—, priorizando lo que tiene
        # que ver con la conversación actual, lo que le pasa pronto y lo que
        # quedó pendiente. Va después del bloque de estado y antes del recuerdo
        # consolidado: es lo más específico que YUE sabe de él ahora mismo.
        #
        # El propio bloque le recuerda al modelo que son recuerdos, no datos que
        # recitar, y que no mencione registros ni bases de datos.
        try:
            if getattr(self, "episodic", None) is not None:
                bloque_episodico = self.episodic.context_block(
                    getattr(self, "_last_user_text", "") or "")
                if bloque_episodico:
                    prompt += bloque_episodico
        except Exception as exc:
            print("[episodic] no pude añadir los recuerdos al contexto:", exc)

        # NUEVO (memoria narrativa): HISTORIAS relevantes. Va justo aquí, entre
        # los episodios y el retrieval histórico, y el orden es deliberado:
        #
        #   estado emocional  -> cómo está AHORA
        #   episodios         -> QUÉ le pasó (el punto)
        #   HISTORIAS         -> qué HILO une esos puntos  <-- aquí
        #   retrieval         -> qué dijo hace tiempo (texto suelto)
        #   consolidada       -> el resumen difuso de meses
        #
        # De lo más concreto y actual a lo más difuso y antiguo. Una historia es
        # más específica que un fragmento recuperado por parecido de palabras,
        # así que debe llegar ANTES para que pese más si hubiera conflicto; pero
        # es menos inmediata que el episodio concreto, así que va DESPUÉS de él.
        #
        # Como mucho entran STORY_MAX_CONTEXT (2 por defecto), y solo si el
        # mensaje toca la historia de verdad. A prueba de fallos.
        try:
            if getattr(self, "story_memory", None) is not None:
                bloque_historias = self.story_memory.context_block(
                    getattr(self, "_last_user_text", "") or "")
                if bloque_historias:
                    prompt += bloque_historias
        except Exception as exc:
            print("[story-memory] no pude añadir las historias al contexto:", exc)

        # NUEVO (memoria histórica relevante): recuerdos del historial COMPLETO
        # que encajan con lo que el usuario acaba de decir. Es lo que permite
        # que YUE ate «Andrea volvió a escribirme» con aquella pelea de hace
        # meses, aunque ese mensaje quedara fuera de recent_messages() hace
        # mucho.
        #
        # Va después de los episodios (lo más concreto) y antes del recuerdo
        # consolidado (lo más difuso), que es el orden de menor a mayor
        # antigüedad. La capa excluye POR ID el mensaje actual y los últimos
        # MEMORY_RELEVANCE_SKIP_RECENT, así que nunca se "recuerda" a sí misma;
        # y si nada supera el umbral, devuelve "" y el prompt no cambia.
        #
        # A prueba de fallos: un problema aquí no puede impedir conversar.
        try:
            if getattr(self, "memory_ext", None) is not None:
                bloque_historico = self.memory_ext.context_block(
                    getattr(self, "_last_user_text", "") or "")
                if bloque_historico:
                    prompt += bloque_historico
        except Exception as exc:
            print("[memory-ext] no pude añadir recuerdos del historial:", exc)

        # NUEVO (memoria a largo plazo): añadimos el recuerdo consolidado al final,
        # después del contexto de cámara/audio, sin tocar nada de lo anterior.
        if recuerdo_largo:
            prompt += recuerdo_largo
        return prompt

    def _external_mood_signal(self) -> bool:
        """Señal SOSTENIDA no textual (cámara + histórico de ánimo).

        Se la pasamos al cerebro afectivo para que la seguridad pueda subir
        medio nivel cuando lo que se ve lleva un rato sin cuadrar con lo que se
        dice. Reutiliza `safety.detect_risk_from_camera`, que ya exige
        PERSISTENCIA por ambas vías, así que un gesto puntual no dispara nada.

        A prueba de fallos: ante cualquier error, no aporta señal.
        """
        try:
            return bool(safety.detect_risk_from_camera(self.camera, self.memory))
        except Exception:
            return False

    def _should_check_visual_risk(self) -> bool:
        """¿Inyectar la directiva de cuidado por señal visual/de ánimo sostenida?

        Combina la señal de cámara con el historial de mood_log (safety decide) y
        aplica un antirrebote: aunque la señal persista, YUE no pregunta '¿cómo
        estás?' en cada mensaje, sino como mucho una vez cada cierto tiempo
        (CAMERA_RISK_ASK_COOLDOWN, 15 min por defecto). Así se evita la alarma
        constante. A prueba de fallos: ante cualquier error, no dispara nada.
        """
        try:
            if not safety.detect_risk_from_camera(self.camera, self.memory):
                return False
        except Exception:
            return False
        ahora = time.time()
        ultima = float(getattr(self, "_last_visual_risk_ask", 0.0))
        cooldown = float(getattr(config, "CAMERA_RISK_ASK_COOLDOWN", 900.0))
        if (ahora - ultima) < cooldown:
            return False
        self._last_visual_risk_ask = ahora
        return True

    def _wellbeing_nudge_due(self) -> bool:
        """¿Toca el recordatorio DISCRETO de apoyo humano/profesional?

        Condiciones (cualquiera):
          (1) el riesgo textual saltó varias veces en los últimos días, o
          (2) uso muy intensivo: una sesión continua de más de X horas.
        Se espacia con una ventana breve + cooldown persistente (state), para que
        salga "de tanto en tanto" y no en cada mensaje. Se apaga por completo con
        WELLBEING_NUDGE_ENABLED. A prueba de fallos: ante error, no añade nada.
        """
        if not bool(getattr(config, "WELLBEING_NUDGE_ENABLED", True)):
            return False

        condicion = False
        # (1) riesgo textual repetido en la ventana reciente
        try:
            dias = int(getattr(config, "WELLBEING_RISK_DAYS", 7))
            minimo = int(getattr(config, "WELLBEING_MIN_RISK_EVENTS", 2))
            if self.memory.count_risk_events(dias) >= minimo:
                condicion = True
        except Exception:
            pass
        # (2) sesión continua muy larga
        if not condicion:
            try:
                horas = float(getattr(config, "WELLBEING_SESSION_HOURS", 3.0))
                gap = float(getattr(config, "WELLBEING_SESSION_GAP_MIN", 30.0)) * 60.0
                if self.memory.session_span_seconds(max_gap=gap) >= horas * 3600.0:
                    condicion = True
            except Exception:
                pass

        if not condicion:
            return False

        # Espaciado con ventana + cooldown (marca persistente en 'state').
        ahora = time.time()
        try:
            inicio = float(self.memory.get_state("wellbeing_nudge_ts", "0") or 0.0)
        except Exception:
            inicio = 0.0
        ventana = float(getattr(config, "WELLBEING_NUDGE_WINDOW_MIN", 20.0)) * 60.0
        cooldown = float(getattr(config, "WELLBEING_NUDGE_COOLDOWN_HOURS", 8.0)) * 3600.0

        # Dentro de una ventana abierta: seguimos ofreciéndolo (varios turnos para
        # que YUE lo suelte con naturalidad).
        if inicio and (ahora - inicio) < ventana:
            return True
        # En cooldown tras la última ventana: silencio.
        if inicio and (ahora - inicio) < cooldown:
            return False
        # Abrimos una ventana nueva.
        try:
            self.memory.set_state("wellbeing_nudge_ts", ahora)
        except Exception:
            pass
        return True

    # ---------- check-in de ánimo (/animo y proactivo) ----------
    def _start_mood_checkin(self):
        """Lanza el check-in explícito: pregunta la nota del 1 al 5 y espera."""
        self._pending_checkin = True
        try:
            self.autonomy.mark_checkin()
        except Exception as exc:
            print("[checkin] no pude marcar el check-in:", exc)
        self._yue_say(commands.MOOD_CHECKIN_PROMPT)

    def _resolve_mood_checkin(self, text) -> bool:
        """Interpreta la respuesta al check-in. True si era una nota válida.

        Si lo es, la guarda en mood_log con fuente='checkin' y responde. Si no,
        cierra el check-in en silencio y devuelve False para que el mensaje siga
        su curso normal (se conversa y su ánimo se capta por el texto, como
        siempre). Nunca deja al usuario atrapado en la pregunta.
        """
        parsed = commands.parse_mood_checkin(text)
        if not parsed:
            self._pending_checkin = False
            return False
        self._pending_checkin = False
        try:
            self.memory.add_mood(
                "checkin",
                parsed["emocion"],
                parsed["intensidad"],
                parsed["texto_origen"],
            )
        except Exception as exc:
            print("[checkin] no pude guardar el ánimo:", exc)
        self.chat.ensure_input_ready(focus=False)
        self._yue_say(parsed["reply"])
        return True

    def _yue_may_take_initiative(self, minimo="medium"):
        """¿Se permite YUE hablar primero ahora mismo?

        Hasta ahora `initiative` se calculaba y no lo leía nadie: era un campo
        decorativo más. Esta es su razón de existir.

        Si el usuario pidió espacio, `SupportPolicy` pone la necesidad en
        `GIVE_SPACE`, el mapeo la traduce a `initiative = "none"` y aquí se corta
        cualquier intento de YUE de arrancar a hablar. Da igual que el
        temporizador de check-in diga que toca: si alguien acaba de pedir que lo
        dejen en paz, insistir es exactamente lo que no hay que hacer.

        Devuelve True si no hay gestor (degradación: se comporta como antes).
        """
        gestor = getattr(self, "state_manager", None)
        if gestor is None:
            return True
        try:
            from core.state import INITIATIVE_LEVELS
            actual = gestor.yue_state().initiative
            return INITIATIVE_LEVELS.index(actual) >= INITIATIVE_LEVELS.index(minimo)
        except Exception:
            return True

    # ---------- memoria episódica emocional ----------
    def _episodic_observe(self, text):
        """Detecta/actualiza el episodio que pueda haber en este mensaje.

        Reutiliza el análisis afectivo y de seguridad que YA hizo el cerebro de
        acompañamiento (`self._last_companion`): no se vuelve a llamar a ningún
        modelo para saber qué siente. Lo único que se busca aquí es el
        ACONTECIMIENTO: qué es, cuándo, por qué le importa y si merece que YUE
        pregunte después.

        A prueba de fallos y no bloqueante: si algo va mal, la conversación
        sigue exactamente igual.
        """
        if getattr(self, "episodic", None) is None:
            # Sin memoria episódica, la narrativa sigue viva por su cuenta: usa
            # sus propias señales deterministas y el afecto ya calculado.
            if getattr(self, "story_memory", None) is not None:
                self._story_observe(text)
            return
        resultado = getattr(self, "_last_companion", None)
        afecto = getattr(resultado, "affect", None)
        decision = getattr(resultado, "decision", None)
        try:
            nivel = int(getattr(getattr(resultado, "safety", None), "level", 0) or 0)
        except Exception:
            nivel = 0

        # El id del mensaje recién insertado, para poder trazar el origen.
        message_id = None
        try:
            message_id = self.memory.last_message_id()
        except Exception:
            message_id = None

        def _trabajo():
            episode_id = None
            try:
                with self._episodic_lock:
                    salida = self.episodic.observe(
                        text, affect=afecto, decision=decision,
                        safety_level=nivel, message_id=message_id)
                episode_id = (salida or {}).get("episode_id")
            except Exception as exc:
                print("[episodic] fallo observando el mensaje:", exc)
            # NUEVO (memoria narrativa): va DESPUÉS y en el mismo hilo, a
            # propósito. Así recibe el episodio que se acaba de crear y puede
            # apuntar a él (`source_type='emotional_episode'`) en vez de
            # duplicar su texto, y hereda su `status` e `importance` en lugar
            # de volver a deducirlos.
            self._story_observe(
                text, affect=afecto, safety_level=nivel,
                message_id=message_id, episode_id=episode_id, inline=True)

        if bool(getattr(config, "EPISODIC_ASYNC", True)):
            threading.Thread(target=_trabajo, daemon=True,
                             name="episodic-observe").start()
        else:
            _trabajo()

    # ---------- memoria narrativa (historias que evolucionan) ----------
    def _story_observe(self, text, *, affect=None, safety_level=None,
                       message_id=None, episode_id=None, inline=False):
        """Hace avanzar las HISTORIAS con este mensaje.

        Determinista y barato: no llama a ningún modelo. Detecta a las personas
        de las que habla, encuentra la historia que ya existe (o la crea si de
        verdad lo merece), le añade el acontecimiento y recalcula su peso.

        `inline=True` significa que ya estamos en el hilo de fondo del episodio
        y no hace falta abrir otro.
        """
        if getattr(self, "story_memory", None) is None:
            return
        resultado = getattr(self, "_last_companion", None)
        if affect is None:
            affect = getattr(resultado, "affect", None)
        if safety_level is None:
            try:
                safety_level = int(
                    getattr(getattr(resultado, "safety", None), "level", 0) or 0)
            except Exception:
                safety_level = 0
        if message_id is None:
            try:
                message_id = self.memory.last_message_id()
            except Exception:
                message_id = None

        def _trabajo():
            try:
                with self._story_lock:
                    self.story_memory.observe(
                        text, affect=affect, episode_id=episode_id,
                        message_id=message_id, safety_level=int(safety_level or 0))
            except Exception as exc:
                print("[story-memory] fallo observando el mensaje:", exc)

        if inline or not bool(getattr(config, "EPISODIC_ASYNC", True)):
            _trabajo()
        else:
            threading.Thread(target=_trabajo, daemon=True,
                             name="story-observe").start()

    def _story_note_response(self, respuesta):
        """Anota la parte de YUE en la historia, no solo la de él.

        Es lo que separa «tengo un registro sobre ti» de «esto lo vivimos
        juntos»: la historia guarda también qué hizo ella en ese momento.
        Reutiliza la misma etiqueta corta que normaliza la memoria episódica.
        """
        if getattr(self, "story_memory", None) is None:
            return
        decision = getattr(getattr(self, "_last_companion", None), "decision", None)

        def _trabajo():
            try:
                etiqueta = ""
                if getattr(self, "episodic", None) is not None:
                    etiqueta = self.episodic._normalize_action(respuesta, decision)
                with self._story_lock:
                    self.story_memory.note_yue_action(etiqueta or "te escuchó")
            except Exception as exc:
                print("[story-memory] no pude anotar lo que hice:", exc)

        if bool(getattr(config, "EPISODIC_ASYNC", True)):
            threading.Thread(target=_trabajo, daemon=True,
                             name="story-action").start()
        else:
            _trabajo()

    def _episodic_note_response(self, respuesta):
        """Completa `yue_action` una vez que YUE ya ha dicho lo suyo.

        El episodio se crea ANTES de que ella responda, así que en ese momento
        no se puede saber qué hizo. Aquí se anota en una etiqueta corta
        («tranquilizó», «practicaron preguntas»); nunca la respuesta entera,
        que ya vive en `messages`.
        """
        if getattr(self, "episodic", None) is None:
            return
        decision = getattr(getattr(self, "_last_companion", None), "decision", None)

        def _trabajo():
            try:
                with self._episodic_lock:
                    self.episodic.note_yue_response(respuesta, decision=decision)
            except Exception as exc:
                print("[episodic] no pude anotar lo que hice:", exc)

        if bool(getattr(config, "EPISODIC_ASYNC", True)):
            threading.Thread(target=_trabajo, daemon=True,
                             name="episodic-action").start()
        else:
            _trabajo()

    def _episodic_followup(self):
        """Intenta retomar un acontecimiento pendiente. True si YUE va a hablar.

        Es la PRIMERA opción del check-in proactivo: si hay algo concreto que
        retomar, retomarlo es infinitamente mejor que un «¿cómo va tu día?».
        Todos los frenos (iniciativa, espacio pedido, riesgo reciente, tope de
        una pregunta por episodio) ya los aplica `due_followup`; aquí solo se
        redacta y se dice.
        """
        if getattr(self, "episodic", None) is None:
            return False
        try:
            episodio = self.episodic.due_followup()
        except Exception as exc:
            print("[episodic] fallo buscando seguimientos:", exc)
            return False
        if not episodio:
            return False

        # Se marca ANTES de hablar, a propósito: si algo falla por el camino,
        # preferimos perder una pregunta a repetirla en el siguiente latido.
        self.episodic.mark_followup_asked(episodio["id"])
        try:
            self.autonomy.mark_checkin()
        except Exception:
            pass

        respaldo = self.episodic.fallback_followup_text(episodio)

        # Con motor de IA, lo escribe ella con su propia voz. El prompt le
        # prohíbe explícitamente sonar a recordatorio automático.
        if getattr(self, "engine", None) is not None:
            try:
                instruccion = self.episodic.build_followup_prompt(episodio)
                try:
                    nivel, _, _ = bonding.progress(self.memory.get_bond_points())
                except Exception:
                    nivel = None
                sistema = self._system_prompt(nivel)
                mensajes = [
                    {"role": "system", "content": sistema},
                    {"role": "system", "content": instruccion},
                ]
                worker = AiWorker(self.engine, mensajes)
                worker.done.connect(
                    lambda texto, alt=respaldo: self._say_followup(texto or alt))
                worker.failed.connect(
                    lambda _error, alt=respaldo: self._say_followup(alt))
                self.controller_ctx.workers.track(worker)
                return True
            except Exception as exc:
                print("[episodic] no pude redactar el seguimiento:", exc)

        self._say_followup(respaldo)
        return True

    def _say_followup(self, texto):
        """Dice el seguimiento y lo deja en el historial como turno de YUE."""
        limpio = (texto or "").strip()
        if not limpio:
            return
        try:
            limpio = emotion.clean_response(limpio)
        except Exception:
            pass
        try:
            self.memory.add_message("assistant", limpio)
        except Exception:
            pass
        self._yue_say(limpio)

    def _maybe_checkin(self):
        """Evalúa (barato) si YUE debería preguntar el ánimo por iniciativa propia.

        No intrusivo: solo si está habilitado, no hay nada en curso, el usuario
        lleva un rato inactivo (misma lógica que autonomía) y la política de
        Autonomy lo permite (cada X horas, máx. una vez al día). La pregunta es
        natural y conversacional; la respuesta se capta como una charla normal.
        """
        try:
            if not bool(getattr(config, "CHECKIN_ENABLED", True)):
                return
            if getattr(self, "_pending_checkin", False):
                return
            if self._autonomy_busy or self._pc_busy or self._vision_busy:
                return
            if self.chat.is_user_composing():
                return
            # No interrumpir una clase en modo profesora.
            if getattr(self, "modes", None) is not None and self.modes.current_mode() == MODE_TEACHER:
                return
            # No hablar por encima de la voz de YUE.
            try:
                if self.speaker.is_speaking:
                    return
            except Exception:
                pass
            # NUEVO: respeta la INICIATIVA decidida por el cerebro central. Si
            # el usuario pidió espacio (GIVE_SPACE → initiative "none"), YUE no
            # pregunta nada aunque el temporizador diga que toca. Un check-in
            # justo después de que alguien pida que lo dejen en paz convierte el
            # acompañamiento en acoso.
            if not self._yue_may_take_initiative("medium"):
                return
            # Requiere inactividad real (misma lógica que la iniciativa autónoma).
            idle = time.time() - self._last_user_activity
            if idle < config.AUTONOMY_IDLE_SECONDS:
                return
            # NUEVO (memoria episódica): ANTES del check-in genérico, ¿hay algo
            # CONCRETO que retomar? Preguntar «¿y al final cómo te fue con la
            # entrevista?» es lo que distingue acompañar de rellenar silencios.
            # Solo si no hay nada pendiente se cae al «¿cómo va tu día?» de
            # siempre, que sigue intacto.
            #
            # Este camino tiene su propia política de tiempo (una pregunta por
            # episodio, ventana de calma, bloqueo por riesgo), así que no pasa
            # por `should_checkin()`: un acontecimiento no espera 20 horas.
            try:
                if self._episodic_followup():
                    return
            except Exception as exc:
                print("[episodic] fallo en el seguimiento proactivo:", exc)
            # Política de tiempo (cada X horas / máx. una vez al día): en Autonomy.
            if not self.autonomy.should_checkin():
                return
            self.autonomy.mark_checkin()
            pregunta = self.autonomy.checkin_question()
            # La dejamos en memoria como turno de YUE para que la respuesta del
            # usuario fluya con contexto por la conversación normal.
            try:
                self.memory.add_message("assistant", pregunta)
            except Exception:
                pass
            self._yue_say(pregunta)
        except Exception as exc:
            print("[checkin] fallo evaluando el check-in proactivo:", exc)

    # ---------- mensajes ----------
    def on_user_message(self, text):
        text = (text or "").strip()
        if not text:
            return

        # NUEVO (accesibilidad): si hay una confirmación de acción irreversible
        # esperando, este mensaje (voz o texto) es la respuesta sí/no. Se resuelve
        # aquí y no sigue por el flujo normal.
        if getattr(self, "_pc_confirm", None) is not None:
            self._resolve_pc_confirmation(text)
            return

        # NUEVO (palabra de activación «Yue»): YUE solo atiende una frase (por voz o
        # por texto) si empieza por su nombre. Se exceptúan: los comandos con «/», y
        # cuando está esperando la respuesta a su propio check-in de ánimo (esa
        # respuesta te la pidió ella). Las confirmaciones sí/no ya salieron arriba.
        # NO toca el sistema de escuchar música/vídeo: solo filtra mensajes del usuario.
        if (getattr(self, "_wake_word_enabled", True)
                and not getattr(self, "_pending_checkin", False)
                and not text.startswith("/")):
            stripped = self.voice.strip_wake_word(text)
            if stripped is None:
                self.voice.wake_ignored(text)
                return
            if not stripped:
                # Solo dijo/escribió «Yue» sin nada más: acusa recibo y espera.
                self._interrupt_response()
                self._yue_say("¿Sí? Dime.")
                return
            text = stripped

        self._interrupt_response()
        request_id = self._chat_request_id
        self._last_user_activity = time.time()
        self._last_user_text = text
        self._user_float(text)

        # Aprende gustos explícitos sobre lo último escuchado/visto. Nunca guarda
        # audio, vídeo ni frames: solo actualiza la afinidad del título reciente.
        try:
            self.memory.learn_multimedia_preference(text)
        except Exception as exc:
            print("[media-memory] no pude aprender la preferencia:", exc)

        # Resuelve referencias temporales antes del enrutador de comandos. Por
        # ejemplo «pon la canción de ayer» se convierte en una orden concreta.
        try:
            remembered = self.memory.resolve_multimedia_reference(text)
        except Exception:
            remembered = None
        if remembered and re.search(
                r"\b(pon|poner|reproduce|reproducir|vuelve a poner|abre)\b",
                text, re.IGNORECASE):
            platform = "spotify" if (
                "spotify" in str(remembered.get("source", "")).lower()
                or "spotify" in str(remembered.get("app", "")).lower()
            ) else "youtube"
            text = f"pon {remembered['title']} en {platform}"

        # NUEVO (/animo): si hay un check-in de ánimo esperando respuesta, este
        # mensaje puede ser la nota del 1 al 5. Si lo es, lo guardamos y cerramos
        # aquí; si no, cancelamos el check-in sin insistir y el mensaje sigue su
        # curso normal (así el usuario nunca queda atrapado en la pregunta).
        if getattr(self, "_pending_checkin", False):
            if self._resolve_mood_checkin(text):
                return

        self.autonomy.learn(text, source="user")

        # NUEVO (comprensión emocional v2): UNA sola pasada entiende el estado
        # afectivo, lo que el usuario necesita, el nivel de riesgo, cómo debe
        # acompañarle YUE y qué cara poner. El resultado se guarda para que
        # _system_prompt() y el registro de memoria lo reutilicen sin repetir
        # trabajo ni volver a llamar al modelo.
        #
        # Si el cerebro afectivo no está disponible, se cae al camino clásico
        # (infer_reaction_to_user) exactamente como antes.
        self._last_companion = None
        if getattr(self, "brain", None) is not None:
            try:
                contexto_reciente = self.memory.recent_messages(4)
            except Exception:
                contexto_reciente = None
            try:
                self._last_companion = self.brain.process(
                    text,
                    recent_messages=contexto_reciente,
                    external_signal=self._external_mood_signal(),
                )
            except Exception as exc:
                print("[affect] fallo el análisis afectivo, sigo con el clásico:", exc)
                self._last_companion = None

        if self._last_companion is not None:
            resultado = self._last_companion
            # NUEVO (cerebro central): el análisis se reparte entre los DOS
            # estados que antes estaban mezclados. Lo que le pasa al usuario va
            # a USER STATE; cómo reacciona YUE va como PROPUESTA al arbitraje.
            # La cara de YUE es una RESPUESTA a lo que le pasa al usuario, no
            # una imitación: un usuario furioso no pone a YUE furiosa.
            self._publish_companion_state(resultado)
            if bool(getattr(config, "AFFECT_DEBUG", False)):
                print("[affect]", resultado.summary)
                try:
                    if self.state_manager is not None:
                        print("[estado]", self.state_manager.global_state().summary())
                except Exception:
                    pass
            # Memoria afectiva ESTRUCTURADA: se guarda la lectura, nunca el
            # mensaje (ese ya está en 'messages'; duplicarlo era copiar
            # información privada sin ganar nada). Se mantiene además la
            # entrada clásica en mood_log —sin texto— para que el resumen
            # semanal y la señal de ánimo sostenido sigan funcionando igual.
            try:
                afecto = resultado.affect
                self.memory.add_affect(
                    emotion=str(afecto.primary_emotion),
                    secondary_emotion=str(afecto.secondary_emotion or ""),
                    valence=afecto.valence, arousal=afecto.arousal,
                    distress=afecto.distress,
                    support_need=str(resultado.decision.mode),
                    confidence=afecto.confidence,
                    sarcasm=afecto.sarcasm_probability,
                    safety_level=int(resultado.safety.level),
                    trigger=afecto.possible_trigger, source="texto")
                if str(afecto.primary_emotion) != "neutral":
                    self.memory.add_mood("texto", str(afecto.primary_emotion),
                                         max(abs(afecto.valence), afecto.arousal),
                                         None)
            except Exception as exc:
                print("[mood] no pude registrar el ánimo del texto:", exc)
        else:
            # --- Camino CLÁSICO (retrocompatibilidad total) ------------------
            reaction = emotion.infer_reaction_to_user(text)
            self._set_avatar_emotion(reaction.name, reaction.intensity,
                                     reaction.duration_ms,
                                     priority=_PRIO_APOYO, source="apoyo_usuario")
            try:
                mood = emotion.infer_emotion_state(text)
                if mood.name != "neutral":
                    # Sin texto: la migración de privacidad ya no lo duplica.
                    self.memory.add_mood("texto", mood.name, mood.intensity, None)
            except Exception as exc:
                print("[mood] no pude registrar el ánimo del texto:", exc)
        self.chat.ensure_input_ready(focus=False)

        if text.startswith("/"):
            self._handle_command(text)
            return

        # NUEVO: enrutador de modos (punto ÚNICO de decisión). Detecta si el
        # mensaje activa o desactiva un modo (companion/teacher/…) por voz o texto.
        if self.modes is not None:
            route = self.modes.handle(text)
            if route.switched:
                self._apply_mode_switch(route)
                return

        # Los comandos de control (callar, mirar pantalla, micro…) funcionan en
        # CUALQUIER modo: se comprueban antes de repartir la conversación.
        action, arg = commands.match(text)
        if action:
            self._run_internal_action(action, arg)
            return

        # NUEVO (conciencia de cámara): preguntas directas "¿puedes verme?",
        # "¿me ves?", "¿me estás viendo?"… no las cubría commands.match, así que
        # caían al LLM y respondía "no puedo verte". Aquí se responden de forma
        # determinista según el estado real de la cámara. A prueba de fallos.
        if vision_awareness is not None:
            try:
                if vision_awareness.asks_if_can_see(text):
                    self._yue_say(vision_awareness.answer_can_see(self.camera))
                    return
            except Exception as exc:
                print("[vision] no pude resolver «¿puedes verme?»:", exc)

        # ADITIVO (visión avanzada): órdenes habladas o escritas sobre la cámara
        # —"lee este texto", "describe mi habitación", "¿qué estoy haciendo?",
        # "modo privacidad", "olvida lo que viste"…—. Funciona igual desde el
        # micrófono y desde el chat porque solo trabaja con texto. Si el sistema
        # está apagado o la frase no es una orden de visión, devuelve None y el
        # flujo sigue exactamente como antes.
        if getattr(self, "vision_mp", None) is not None:
            try:
                respuesta_vision = self.vision_mp.handle_voice(text)
                if respuesta_vision:
                    self._yue_say(respuesta_vision)
                    return
            except Exception as exc:
                print("[vision] no pude procesar la orden de visión:", exc)

        # NUEVO: si hay un modo especial activo, la conversación va a su motor.
        # (Companion es el modo por defecto y sigue el flujo de abajo, intacto.)
        if self.modes is not None and self.modes.current_mode() == MODE_TEACHER:
            self._teacher_process(text)
            return

        if looks_like_pc_command(text):
            self._run_pc_order(text)
            return

        self.memory.add_message("user", text)
        points = self.memory.add_bond_points(bonding.points_for_message(text))
        current, _, _ = bonding.progress(points)
        self.chat.set_bond(current)

        # NUEVO (memoria episódica emocional): aquí es donde toca mirarlo, y no
        # antes. En este punto ya han quedado fuera los comandos, los modos y
        # las órdenes de PC (que no son acontecimientos de su vida), el análisis
        # afectivo YA está hecho —así que no se vuelve a analizar la emoción— y
        # el mensaje ya tiene id en la base.
        #
        # Se lanza en segundo plano: la extracción puede necesitar una consulta
        # al modelo y no vamos a congelar la interfaz por un recuerdo. El
        # episodio estará listo para el turno siguiente y para el seguimiento,
        # que es donde de verdad importa.
        self._episodic_observe(text)

        # Riesgo por TEXTO. Con el cerebro afectivo, la evaluación ya viene
        # hecha y es GRADUADA (NINGUNO/LEVE/MODERADO/ALTO/CRÍTICO) porque usa
        # core.safety_ext, que hasta ahora estaba escrito pero sin conectar. Se
        # conservan los niveles, la negación, los factores protectores y el
        # contexto: nada se reduce a un booleano.
        #
        # Si el cerebro no está, se usa el detector clásico exactamente igual
        # que antes.
        directive = None
        if self._last_companion is not None:
            resultado = self._last_companion
            if resultado.should_log_risk_event:
                try:
                    self.memory.add_risk_event()
                except Exception as exc:
                    print("[bienestar] no pude registrar el evento de riesgo:", exc)
            if resultado.safety_directive:
                directive = resultado.safety_directive
            elif self._should_check_visual_risk():
                directive = safety.safety_directive_visual(config.CRISIS_RESOURCES)
        elif safety.detect_risk(text):
            # Deja constancia (solo la hora) para el recordatorio de bienestar:
            # detectar un patrón de riesgo repetido sin conservar nada sensible.
            try:
                self.memory.add_risk_event()
            except Exception as exc:
                print("[bienestar] no pude registrar el evento de riesgo:", exc)
            directive = safety.safety_directive(config.CRISIS_RESOURCES)
        elif self._should_check_visual_risk():
            directive = safety.safety_directive_visual(config.CRISIS_RESOURCES)
        else:
            directive = None
        system = self._system_prompt(current, directive)
        messages = [{"role": "system", "content": system}] + self.memory.recent_messages(12)

        self.chat.set_status("Yue está pensando…")
        worker = AiWorker(self.engine, messages)
        worker.done.connect(lambda answer, rid=request_id, user=text: self._on_ai_done(rid, user, answer))
        worker.failed.connect(lambda error, rid=request_id: self._on_ai_failed(rid, error))
        self.controller_ctx.workers.track(worker)

    def _on_ai_done(self, request_id, user_text, text):
        if request_id != self._chat_request_id:
            return
        self.chat.set_status("")
        clean = emotion.clean_response(text)
        self.memory.add_message("assistant", clean)
        # NUEVO (memoria episódica): ahora SÍ se sabe qué hizo YUE en el
        # episodio que se acaba de detectar («tranquilizó», «practicaron
        # preguntas»…). Se anota como etiqueta corta, nunca la respuesta entera.
        self._episodic_note_response(clean)
        # NUEVO (memoria narrativa): la misma etiqueta corta se anota también en
        # los acontecimientos de historia que se acaban de tocar, para que la
        # historia guarde la parte de YUE y no solo la de él.
        self._story_note_response(clean)
        # Pasamos el texto CRUDO: _yue_say limpia para hablar, pero infiere la
        # emoción del avatar con toda la señal del modelo (no doble-limpiada).
        self._yue_say(text, user_text)

    def _on_ai_failed(self, request_id, error):
        if request_id != self._chat_request_id:
            return
        self.chat.set_status("")
        self._yue_say("No logro conectarme con mi motor de IA. Revisa la clave de Groq y tu conexión.")
        print("[IA] error:", error)

    # ---------- MODOS (companion por defecto, teacher bajo demanda) ----------
    def _setup_modes(self):
        """Crea el gestor de modos y registra companion + teacher. Aditivo."""
        self.modes = ModeManager()

        # Progreso de la profesora en un almacén propio (NO toca la memoria emocional).
        try:
            import os
            prog_dir = os.path.join(str(config.DATA_DIR), "teacher")
            prog_path = os.path.join(prog_dir, "progress.json")
        except Exception:
            prog_path = None
        self.teacher_progress = ProgressStore(path=prog_path)
        self.teacher = TeacherEngine(progress=self.teacher_progress, student="alumno")

        # NUEVO: memoria de clases (§5/§6/§13) y modelo pedagógico aprendido del
        # profesor (§4). Ambos en almacenes propios; si algo falla, el Modo
        # Profesora sigue funcionando igual (todo aditivo y degradable).
        try:
            import os
            teach_dir = os.path.join(str(config.DATA_DIR), "teacher")
            self.class_log = ClassLog(path=os.path.join(teach_dir, "classes.db"))
            self.pedagogy = PedagogyModel(path=os.path.join(teach_dir, "pedagogy.json"))
        except Exception as exc:
            print("[profesora] sin memoria de clases/pedagogía persistente:", exc)
            self.class_log = ClassLog(path=None)
            self.pedagogy = PedagogyModel(path=None)
        self.teacher.attach(classlog=self.class_log, pedagogy=self.pedagogy)

        # Política de participación activa durante la clase de un profesor humano (§8).
        self.teacher_participation = teacher_participation.ParticipationPolicy()

        # NUEVO: auto-avance de la lectura guiada de PDF. Cuando YUE termina de
        # explicar una página, pasa sola a la siguiente y sigue explicando. Es
        # interrumpible (si escribes, preguntas o dices «pausa» se detiene).
        self._teacher_autoadvance = bool(getattr(config, "TEACHER_AUTOADVANCE_DEFAULT", True))
        self._teacher_page_explaining = False   # ¿la última respuesta fue una página?
        self._teacher_autoadvance_pending = False

        self._register_modes()
        self.modes.subscribe(self._on_mode_event)
        # Indicador inicial: modo compañera.
        meta = self.modes.current_meta()
        self._update_mode_indicator(meta.get("emoji", "🤍"), meta.get("label", "Compañera"))

    def _register_modes(self):
        # --- Companion: modo por defecto (apoyo emocional). Su flujo vive en
        #     on_user_message; aquí solo se registra su identidad e indicador. ---
        self.modes.register_mode(ModeSpec(
            id=MODE_COMPANION, label="Compañera", emoji="🤍", is_default=True,
        ))

        # --- Teacher: profesora virtual, activable por voz o texto. ---
        self.modes.register_mode(ModeSpec(
            id=MODE_TEACHER, label="Profesora", emoji="📚",
            activation_phrases=(
                "activa el modo profesora", "activa el modo profesor",
                "modo profesora", "modo profesor", "modo ensenanza", "modo maestra",
                "quiero una clase", "dame una clase", "me das una clase",
                "ensename", "quiero aprender", "inicia una clase", "empieza una clase",
                "comienza el modo profesora", "actua como profesora", "se mi profesora",
            ),
            activation_keywords=(
                ("modo", "profesor"), ("modo", "maestra"), ("modo", "ensenanza"),
                ("quiero", "aprender"), ("quiero", "clase"), ("dame", "clase"),
                ("das", "clase"), ("actua", "profesora"), ("inicia", "clase"),
                ("empieza", "clase"), ("comienza", "clase"), ("se", "profesora"),
            ),
            deactivation_phrases=(
                "salir del modo profesora", "sal del modo profesora",
                "terminar clase", "termina la clase", "finalizar clase",
                "finaliza la clase", "desactiva modo profesora", "acaba la clase",
                "regresa al modo companera",
            ),
            deactivation_keywords=(
                ("terminar", "clase"), ("termina", "clase"), ("finalizar", "clase"),
                ("finaliza", "clase"), ("acaba", "clase"), ("salir", "profesora"),
                ("desactiva", "profesora"),
            ),
            activation_message=(
                "📚 Modo Profesora activado. Ahora puedo enseñarte cualquier tema, leer "
                "documentos y páginas web, evaluarte y registrar tu progreso. También "
                "puedo ayudarte como docente: dime «crea un examen/plan/rúbrica sobre…», "
                "«corrige este trabajo», «aprende de mi profesor» para observar una clase, "
                "o «estadísticas» para ver cómo va todo. Para salir: «Yue, salir del modo "
                "profesora»."
            ),
            deactivation_message=(
                "📚 Clase finalizada. Guardé el progreso. Vuelvo a ser tu compañera "
                "de siempre. ¿Hay algo más en lo que pueda ayudarte?"
            ),
            handler=self._teacher_process,
            on_enter=self.teacher.on_enter,
            on_exit=self.teacher.on_exit,
        ))

    def _on_mode_event(self, event):
        """Reacciona a un cambio de modo: actualiza el indicador y la expresión."""
        from mode_manager.mode_events import ENTER
        if event.type == ENTER:
            meta = event.meta or {}
            self._update_mode_indicator(meta.get("emoji", ""), meta.get("label", ""))
            # NUEVO: el modo profesora ahora PROPONE con su prioridad real
            # (TEACHER, 80) y SIN caducidad: mientras dure la clase, gobierna.
            # Antes usaba prioridad de conversación y un TTL de 2.6 s, así que
            # cualquier cosa —incluida la música— le quitaba la cara a los tres
            # segundos de empezar a explicar.
            if event.new_mode == MODE_TEACHER:
                self._propose_teacher_mode(True)
            else:
                self._propose_teacher_mode(False)
                self._set_avatar_emotion("happy", 0.55, 2400,
                                         priority=_PRIO_CONVERSACION, source="tarea")
                # NUEVO: al volver al modo compañera, YUE deja de observar al
                # profesor y de participar en clase (§4/§8). Aditivo y seguro.
                try:
                    if getattr(self, "pedagogy", None) is not None:
                        self.pedagogy.set_observing(False)
                    if getattr(self, "teacher_participation", None) is not None:
                        self.teacher_participation.set_active(False)
                except Exception:
                    pass

    def _propose_teacher_mode(self, activo):
        """Entra o sale del modo profesora en el arbitraje.

        Al ENTRAR se manda una propuesta sin TTL: la clase dura lo que dure y
        nadie por debajo de prioridad 80 puede quitarle la cara a YUE.

        Al SALIR se RETIRA la propuesta, y eso dispara el recálculo: no se cae a
        neutral, sino a lo que siguiera vivo debajo (normalmente la
        conversación). Es justo el comportamiento que se pedía en el punto 23.
        """
        gestor = getattr(self, "state_manager", None)
        if gestor is None:
            return
        try:
            if not activo:
                gestor.withdraw("teacher")
                return
            from core.state import YueProposal
            gestor.propose(YueProposal(
                emotion="focused", intensity=0.7, behavior="teaching",
                voice_style="steady", initiative="medium",
                avatar_state="explaining", source="teacher",
                priority=_PRIO_PROFESORA, ttl=None,
                reason="modo profesora activo",
            ))
        except Exception as exc:
            print("[estado] no pude cambiar el modo profesora:", exc)

    def _update_mode_indicator(self, emoji, label):
        try:
            self.chat.set_mode(emoji, label)
        except Exception as exc:
            print("[modos] no pude actualizar el indicador:", exc)

    def _apply_mode_switch(self, route):
        """Tras activar/desactivar un modo: YUE lo anuncia. El indicador ya se
        actualiza por el evento de modo."""
        if route.reply:
            self._yue_say(route.reply)

    # ---------- Motor de la Profesora ----------
    def _teacher_process(self, text):
        """Procesa un mensaje como parte de una clase (no como compañera emocional)."""
        # (a0) NUEVO: comandos especiales del Modo Profesora (aprender del profesor,
        #      generar material docente, corregir trabajos, estadísticas). Si uno
        #      de ellos maneja el mensaje, no seguimos con el flujo de enseñanza.
        try:
            if self._teacher_special_commands(text):
                return
        except Exception as exc:
            print("[profesora] comando especial falló:", exc)

        # (a1) NUEVO: si YUE está OBSERVANDO a un profesor humano (§4/§8), este
        #      mensaje es una intervención del docente: la aprende y quizá participa.
        _ped = getattr(self, "pedagogy", None)
        _part = getattr(self, "teacher_participation", None)
        if (_ped is not None and _ped.is_observing()) or (_part is not None and _part.is_active()):
            self._teacher_observe_and_participate(text)
            return

        # (a) Si hay una lectura guiada de PDF en curso y el alumno pide avanzar,
        #     YUE baja el PDF una pantalla y explica la siguiente página.
        if self.teacher.guided_active() and self.teacher.wants_next(text):
            self._teacher_next_page()
            return

        # (b) Si el mensaje trae la RUTA de un PDF, arranca la lectura guiada:
        #     lo abre en pantalla y lo explica página por página, bajándolo ella.
        pdf_path = self.teacher.detect_pdf(text)
        if pdf_path:
            self._teacher_start_guided_pdf(pdf_path)
            return

        # (c) Otras fuentes (Word, EPUB, imagen, página web): lectura completa.
        try:
            src = self.teacher.maybe_read_source(text)
        except Exception as exc:
            src = None
            print("[profesora] error leyendo fuente:", exc)
        if src is not None and not src.ok:
            self._yue_say("Quería leer eso, pero no pude: " + (src.note or "formato no disponible"))
            return
        if src is not None and src.ok:
            self.chat.set_status(f"Leí el material ({src.kind}). Preparando la clase…")
            # NUEVO: construye la representación interna del material (§2/§3) y lo
            # registra en la memoria de la clase (§5). Aditivo y a prueba de fallos.
            try:
                self.teacher.analyze_material(src.text, src.origin)
                self.teacher.log_material(src.origin, src.kind)
            except Exception as exc:
                print("[profesora] no pude analizar/registrar el material:", exc)

        messages = self.teacher.build_messages(text)
        request_id = self._chat_request_id
        self.chat.set_status("La profesora está preparando la clase…")
        worker = AiWorker(self.engine, messages)
        worker.done.connect(lambda answer, rid=request_id: self._on_teacher_done(rid, answer))
        worker.failed.connect(lambda error, rid=request_id: self._on_ai_failed(rid, error))
        self.controller_ctx.workers.track(worker)

    # ---------- Documento arrastrado al chat (NUEVO) ----------
    def _on_files_dropped(self, paths):
        """Un PDF/documento soltado sobre el chat: YUE lo lee en Modo Profesora.

        Reutiliza todo lo existente: activa el modo si hace falta y, según el tipo,
        arranca la lectura guiada del PDF o la lectura completa del material.
        """
        import os
        exts = getattr(self.chat, "DROP_EXTS", (".pdf", ".docx", ".pptx", ".epub",
                                                ".txt", ".md", ".png", ".jpg", ".jpeg"))
        docs = [p for p in (paths or []) if str(p).lower().endswith(exts)]
        if not docs:
            self._yue_say("Solo puedo leer documentos: PDF, Word, PowerPoint, EPUB, TXT o imágenes.")
            return
        path = docs[0]
        self._interrupt_response()   # por si estaba hablando en ese momento

        # La lectura de material vive en el Modo Profesora: lo activamos si aún no lo está.
        try:
            if self.modes is not None and self.modes.current_mode() != MODE_TEACHER:
                route = self.modes.activate(MODE_TEACHER)
                if route.switched:
                    self._apply_mode_switch(route)
        except Exception as exc:
            print("[profesora] no pude activar el modo al soltar el documento:", exc)

        nombre = os.path.basename(path)
        if len(docs) > 1:
            self._yue_say(f"Me pasaste {len(docs)} archivos; empiezo por «{nombre}».")

        # PDF -> lectura guiada (lo abre y lo explica página por página, bajándolo ella).
        if path.lower().endswith(".pdf"):
            self._teacher_start_guided_pdf(path)
        else:
            # Word/PPT/EPUB/imagen/TXT -> lectura completa por el flujo normal de clase.
            self._teacher_process("lee y enséñame este documento: " + path)

    # ---- Lectura guiada de PDF (YUE abre el PDF y lo explica bajándolo) ----
    def _teacher_start_guided_pdf(self, path):
        info = self.teacher.load_pdf_guided(path)
        local = info.get("path") or path
        if not info["ok"]:
            # page_count == 0: el PDF está protegido/dañado o no se pudo abrir. Como
            # último recurso lo abrimos y lo MIRAMOS con la visión de YUE.
            try:
                self.pc.open_path(local)
            except Exception as exc:
                print("[profesora] no pude abrir el PDF:", exc)
            self._yue_say(
                "No pude abrir ese PDF para leerlo (quizá está protegido o dañado). "
                "Lo abrí y voy a mirarlo con mis ojos para explicártelo."
            )
            QTimer.singleShot(1800, self._glance)
            return
        # Abre el PDF con el visor predeterminado usando la ruta ya resuelta.
        try:
            self.pc.open_path(local)
        except Exception as exc:
            print("[profesora] no pude abrir el PDF:", exc)
        self._yue_say(
            f"Listo, abrí el PDF: tiene {info['pages']} página(s). Lo voy a escanear "
            "completo —incluso si está escaneado o tiene imágenes— y te lo explico "
            "página por página, pasando yo sola a la siguiente. Dime «pausa» cuando "
            "quieras que me detenga, o «siguiente» para apurarme."
        )
        # Damos tiempo a que el visor abra y tome el foco antes de explicar.
        QTimer.singleShot(1600, self._teacher_explain_current_page)

    def _teacher_vision_available(self) -> bool:
        """¿Hay un modelo de visión configurado para MIRAR páginas de imagen?"""
        try:
            if getattr(config, "VISION_OCR_ONLY", False):
                return False
            base, key, model = self.engine._vision_endpoint()
            return bool(base and key and model)
        except Exception:
            return False

    def _teacher_explain_current_page(self):
        material = self.teacher.current_page_material()
        if not material:
            self._yue_say("Ya terminamos el documento. ¿Quieres que repasemos algo?")
            return
        # Marca esta respuesta como explicación de página: al terminar de hablarla,
        # el auto-avance podrá pasar sola a la siguiente (si está activado).
        self._teacher_page_explaining = True

        # Página de pura imagen/diagrama: si hay visión, YUE la MIRA y la explica.
        if material.get("needs_vision") and material.get("image") and self._teacher_vision_available():
            self.teacher.set_document_context(
                material.get("text", ""), f"PDF página {material['page_no']} (imagen)")
            system = self.teacher.build_system_prompt()
            instruccion = self.teacher.build_page_vision_instruction(material)
            request_id = self._chat_request_id
            self.chat.set_status(
                f"Observando la página {material['page_no']}/{material['total']} (imagen)…")
            worker = PdfPageVisionWorker(self.engine, system, instruccion, material["image"])
            worker.done.connect(lambda answer, rid=request_id: self._on_teacher_done(rid, answer))
            worker.failed.connect(lambda error, rid=request_id: self._on_ai_failed(rid, error))
            self.controller_ctx.workers.track(worker)
            return

        # Páginas con texto (embebido u OCR): explicación normal por texto. Si es una
        # imagen y NO hay visión, igual usamos lo que el OCR haya podido rescatar.
        self.teacher.set_document_context("", "")
        messages = self.teacher.build_page_messages(material)
        self.teacher.set_document_context(material["text"], f"PDF página {material['page_no']}")
        request_id = self._chat_request_id
        estado = f"Explicando la página {material['page_no']}/{material['total']}"
        if material.get("source") == "ocr":
            estado += " (escaneada, leída con OCR)"
        self.chat.set_status(estado + "…")
        worker = AiWorker(self.engine, messages)
        worker.done.connect(lambda answer, rid=request_id: self._on_teacher_done(rid, answer))
        worker.failed.connect(lambda error, rid=request_id: self._on_ai_failed(rid, error))
        self.controller_ctx.workers.track(worker)

    def _teacher_next_page(self):
        # YUE baja el PDF una pantalla (ella misma) y pasa a la siguiente página.
        self._pdf_scroll_down()
        if not self.teacher.advance_page():
            self._yue_say(
                "Esa era la última página; terminamos el documento. Si quieres, te "
                "hago un resumen o te tomo unas preguntas."
            )
            return
        QTimer.singleShot(450, self._teacher_explain_current_page)

    def _pdf_scroll_down(self):
        """Envía «Avance de página» al visor del PDF que esté enfocado (mejor esfuerzo)."""
        try:
            self.pc._pyautogui_action({"action": "press", "key": "pagedown"})
        except Exception as exc:
            print("[profesora] no pude desplazar el PDF:", exc)

    def _on_teacher_done(self, request_id, text):
        if request_id != self._chat_request_id:
            return
        self.chat.set_status("")
        # Extrae y registra la evaluación oculta (si la hay) y limpia el texto.
        clean, evals = self.teacher.process_reply(text)
        if not clean:
            clean = "Sigamos con la clase. ¿Qué te gustaría repasar?"
        self._yue_say(clean)
        # NUEVO: si acabo de explicar una página del PDF y el auto-avance está
        # activo, programo el paso a la siguiente en cuanto termine de hablar.
        fue_pagina = self._teacher_page_explaining
        self._teacher_page_explaining = False
        if (fue_pagina and self._teacher_autoadvance
                and self.teacher.guided_active()):
            self._teacher_schedule_autoadvance()

    # ---------- Auto-avance de la lectura guiada (NUEVO) ----------
    def _teacher_schedule_autoadvance(self):
        """Arranca la espera para pasar sola a la siguiente página."""
        self._teacher_autoadvance_pending = True
        QTimer.singleShot(700, self._teacher_autoadvance_tick)

    def _teacher_cancel_autoadvance(self):
        """Detiene cualquier auto-avance pendiente (al interrumpir o pausar)."""
        self._teacher_autoadvance_pending = False

    def _teacher_autoadvance_tick(self):
        """Espera a que YUE termine de hablar (y a que no estorbe) para avanzar."""
        if not self._teacher_autoadvance_pending:
            return  # se canceló (interrupción del usuario, pausa, salida de modo…)
        if not self._teacher_autoadvance or not self.teacher.guided_active():
            self._teacher_autoadvance_pending = False
            return
        # No avanzar si estás escribiendo/preguntando o si hay una orden en curso.
        if self.chat.is_user_composing() or self._pc_busy or self._vision_busy:
            QTimer.singleShot(1200, self._teacher_autoadvance_tick)
            return
        # Aún hablando: esperamos a que termine de explicar la página.
        try:
            hablando = bool(self.speaker.is_speaking)
        except Exception:
            hablando = False
        if hablando:
            QTimer.singleShot(700, self._teacher_autoadvance_tick)
            return
        # Terminó de hablar: pausa de lectura y luego pasa de página.
        self._teacher_autoadvance_pending = False
        pausa = int(getattr(config, "TEACHER_AUTOADVANCE_PAUSE_MS", 2500))
        QTimer.singleShot(max(300, pausa), self._teacher_autoadvance_do)

    def _teacher_autoadvance_do(self):
        """Verificación final y avance real a la siguiente página."""
        if not self._teacher_autoadvance or not self.teacher.guided_active():
            return
        if self.chat.is_user_composing() or self._pc_busy or self._vision_busy:
            return
        try:
            if self.speaker.is_speaking:
                return
        except Exception:
            pass
        self._teacher_next_page()

    # ---------- Comandos especiales del Modo Profesora ----------
    def _teacher_special_commands(self, text):
        """Reconoce y ejecuta comandos del Modo Profesora. Devuelve True si manejó
        el mensaje. Todo aditivo: si nada casa, devuelve False y sigue el flujo normal.
        """
        import unicodedata
        n = unicodedata.normalize("NFD", (text or "").lower())
        n = "".join(c for c in n if unicodedata.category(c) != "Mn").strip()

        # --- Auto-avance de la lectura de PDF: pausar / reanudar ---
        pausar = ("pausa la lectura", "no avances", "no pases de pagina", "no sigas sola",
                  "para de avanzar", "deja de avanzar", "espera ahi", "quedate ahi",
                  "quedate aqui", "modo manual", "no pases sola", "espera un momento",
                  "no cambies de pagina")
        reanudar = ("modo automatico", "avanza sola", "sigue sola", "sigue leyendo sola",
                    "continua sola", "lee tu sola", "pasa sola", "sigue tu sola",
                    "sigue leyendo tu", "avanza tu sola", "continua leyendo sola")
        if any(g in n for g in reanudar):
            self._teacher_autoadvance = True
            if self.teacher.guided_active():
                self._yue_say("Va, sigo yo sola pasando de página. Dime «pausa» cuando quieras parar.")
                self._teacher_next_page()
            else:
                self._yue_say("Listo, cuando leamos un PDF iré pasando de página sola.")
            return True
        if any(g in n for g in pausar):
            self._teacher_autoadvance = False
            self._teacher_cancel_autoadvance()
            self._yue_say("Ok, me quedo aquí. Dime «siguiente» para pasar, o «sigue sola» para que continúe yo.")
            return True

        # --- (§4/§8) Empezar/terminar de observar al profesor humano ---
        obs_on = ("aprende de mi profesor", "observa la clase", "observa al profesor",
                  "aprende del profesor", "escucha la clase", "modo observacion",
                  "vas a observar", "aprende como enseno", "aprende como enseña")
        obs_off = ("deja de observar", "termina de observar", "ya no observes",
                   "para de aprender", "deja de escuchar la clase", "fin de la observacion")
        if any(g in n for g in obs_off):
            self.pedagogy.set_observing(False)
            self.teacher_participation.set_active(False)
            rep = self.pedagogy.report_text_es()
            self._yue_say("Listo, dejo de observar. " + rep)
            return True
        if any(g in n for g in obs_on):
            self.pedagogy.set_observing(True)
            self.teacher_participation.set_active(True)
            self._yue_say(
                "De acuerdo, voy a observar cómo enseña el profesor para aprender su "
                "estilo, y participaré solo cuando sea oportuno. Cuando termines, dime "
                "«deja de observar»."
            )
            return True

        # --- (§4) Contar qué he aprendido del profesor ---
        if ("que aprendiste" in n and "profesor" in n) or "modelo pedagogico" in n \
                or "como enseña mi profesor" in n or "como enseno mi profesor" in n:
            self._yue_say(self.pedagogy.report_text_es())
            return True

        # --- (§13) Estadísticas educativas ---
        if any(g in n for g in ("estadisticas", "como voy", "como vamos", "mi progreso",
                                 "mi rendimiento", "resumen de clases", "como van los alumnos")):
            try:
                self._yue_say(self.class_log.stats_text_es())
            except Exception:
                self._yue_say(self.teacher.progress_summary())
            return True

        # --- (§11/§12) Asistente docente: generar material o corregir trabajos ---
        spec = teacher_assistant.detect_task(text)
        if spec is not None:
            self._teacher_run_assistant(spec, text)
            return True

        return False

    def _teacher_run_assistant(self, spec, text):
        """Genera material docente (§11) o corrige un trabajo (§12) con el modelo."""
        # Si hay una fuente (documento/página) en el mensaje, la usamos como material
        # o como el propio trabajo a corregir.
        material = ""
        try:
            src = self.teacher.maybe_read_source(text)
            if src is not None and src.ok:
                material = src.text
                self.teacher.log_material(src.origin, src.kind)
        except Exception as exc:
            print("[profesora] no pude leer la fuente para la tarea:", exc)

        # Recuerdo de clases pasadas y estilo aprendido, para un resultado informado.
        memoria = ""
        estilo = ""
        try:
            if spec.topic:
                memoria = self.class_log.recall(spec.topic)
            estilo = self.pedagogy.style_directive_es()
        except Exception:
            pass

        messages = teacher_assistant.build_messages(
            spec, text, material=material, memory_hint=memoria, style_hint=estilo)
        request_id = self._chat_request_id
        self.chat.set_status(f"Preparando {spec.label}…")
        worker = AiWorker(self.engine, messages)
        worker.done.connect(lambda answer, rid=request_id: self._on_teacher_assist_done(rid, answer, spec))
        worker.failed.connect(lambda error, rid=request_id: self._on_ai_failed(rid, error))
        self.controller_ctx.workers.track(worker)

    def _on_teacher_assist_done(self, request_id, text, spec):
        if request_id != self._chat_request_id:
            return
        self.chat.set_status("")
        salida = (text or "").strip() or f"No pude preparar {spec.label} esta vez."
        # Recordatorio de rol: YUE asiste, no reemplaza al docente (§12/§15). Solo
        # para la corrección, que es donde importa la decisión final del profesor.
        if spec.task == "correccion":
            salida += "\n\n(Recuerda: esto es una propuesta de apoyo; la calificación final la decides tú.)"
        self._yue_say(salida)

    # ---------- Observación del profesor y participación activa (§4/§8) ----------
    def _teacher_observe_and_participate(self, text):
        """Aprende del profesor humano y, si es oportuno, interviene brevemente."""
        # 1) Aprender el estilo (§4): incorpora la intervención al modelo pedagógico.
        try:
            self.pedagogy.ingest(text)
        except Exception as exc:
            print("[profesora] no pude aprender de la intervención:", exc)

        # 2) Decidir si conviene participar (§8), sin interrumpir de más.
        try:
            self.teacher_participation.note_teacher_turn()
            decision = self.teacher_participation.decide(text)
        except Exception:
            decision = teacher_participation.Decision(False)
        if not decision.should:
            return

        estilo = ""
        try:
            estilo = self.pedagogy.style_directive_es()
        except Exception:
            pass
        contexto = self.teacher.current_topic()
        messages = teacher_participation.build_intervention_messages(
            text, decision, recent_context=contexto, style_hint=estilo)
        self.teacher_participation.note_intervened()
        request_id = self._chat_request_id
        self.chat.set_status("Yue va a aportar algo a la clase…")
        worker = AiWorker(self.engine, messages)
        worker.done.connect(lambda answer, rid=request_id: self._on_teacher_done(rid, answer))
        worker.failed.connect(lambda error, rid=request_id: self._on_ai_failed(rid, error))
        self.controller_ctx.workers.track(worker)

    # ---------- visión de pantalla ----------
    def _glance(self, pregunta: str = ""):
        """Observación explícita; la visión automática permanece silenciosa.

        `pregunta` es la frase original del usuario ("¿qué error aparece?",
        "explícame este gráfico"…). Se pasa entera al analizador para que el
        modelo priorice lo que de verdad se preguntó.
        """
        # Es una petición explícita del usuario: si la visión estaba en pausa, la
        # reactivamos para poder mirar ahora mismo.
        if not self._vision_on:
            self._vision_on = True
        if self._vision_busy or self._pc_busy or self.chat.is_user_composing():
            return
        current = self._refresh_bond()
        instruction = (
            "Mira la pantalla actual y responde en español con una observación útil y breve. "
            "Ignora las ventanas del propio avatar de Yue. No enumeres todo lo visible; "
            "menciona solo lo relevante para la petición explícita del usuario."
        )
        self._vision_busy = True
        self.chat.set_status("Yue está mirando tu pantalla…")
        # ADITIVO: si se pidió visión-solo-OCR de forma EXPLÍCITA, seguimos con el
        # camino OCR de siempre. En cualquier otro caso usamos el flujo completo
        # (captura -> clasificación -> VisionRouter -> fallback OCR), que YA
        # incluye el OCR: si toda la visión multimodal se cae, YUE igual cuenta
        # qué texto hay en pantalla en vez de decir "no puedo ver".
        if bool(getattr(config, "VISION_OCR_ONLY", False)):
            instruccion_ocr = (
                "Vas a mirar la pantalla del usuario a través del texto que hay en "
                "ella (leído por OCR). Responde en español, breve y útil, a su "
                "petición. Ignora menús o barras del sistema si no vienen a cuento. "
                "Si el texto no basta para responder, dilo con naturalidad."
            )
            worker = OcrVisionWorker(self.engine, self._system_prompt(current), instruccion_ocr)
        else:
            worker = VisionWorker(
                self.engine, self._system_prompt(current), instruction,
                question=(pregunta or instruction),
                # Una petición explícita SIEMPRE captura de nuevo: nunca se
                # responde con una observación vieja cuando el usuario dice
                # "mira mi pantalla" o "qué ves ahora".
                force_fresh=True,
            )
            worker.observed.connect(self._on_screen_observed)
        worker.done.connect(self._on_vision_done)
        worker.failed.connect(self._on_vision_failed)
        self.controller_ctx.workers.track(worker)

    def _on_screen_observed(self, obs):
        """Guarda la última observación estructurada de pantalla.

        La capa conversacional puede consultarla (por ejemplo /diagvision, o para
        dar contexto al chat) sin volver a capturar.
        """
        self._last_screen_observation = obs
        try:
            print("[VISION] " + obs.resumen_log())
        except Exception:
            pass

    def _on_vision_done(self, text):
        self._vision_busy = False
        self.chat.set_status("")
        if text:
            self._yue_say(text)
        else:
            self._yue_say("Miré la pantalla pero no obtuve una descripción. ¿Lo intento de nuevo?")

    def _on_vision_failed(self, error):
        self._vision_busy = False
        self.chat.set_status("")
        # Antes esto solo se imprimía y parecía que "la visión no funciona".
        print("[vision] error:", error)
        msg = str(error or "")
        low = msg.lower()
        if any(k in low for k in ("ocr", "tesseract")):
            hablado = ("No encontré texto legible en la pantalla. Si querías que "
                       "leyera algo con texto, ábrelo en primer plano; y si no tengo "
                       "OCR instalado, hará falta Tesseract.")
        elif any(k in low for k in ("api", "key", "clave", "auth", "401", "403")):
            hablado = "No pude usar mi visión: revisa la clave del modelo de visión en la configuración."
        elif any(k in low for k in ("mss", "imagegrab", "pillow", "capturar", "captur", "screenshot", "display", "grab", "no module")):
            hablado = "No pude capturar la pantalla; puede faltar una librería o un permiso de captura."
        elif any(k in low for k in ("negra", "black")):
            hablado = ("La captura salió completamente negra. Suele pasar con vídeo "
                       "protegido o con la aceleración por hardware; prueba a "
                       "desactivarla en esa aplicación.")
        elif any(k in low for k in ("antigua", "frame_age")):
            hablado = "La captura llegó demasiado tarde; déjame intentarlo otra vez."
        elif any(k in low for k in ("todos los modelos visuales", "cuota", "quota", "rate limit", "429")):
            hablado = ("Mis modelos visuales están sin cupo ahora mismo y tampoco "
                       "encontré texto legible en la pantalla.")
        elif any(k in low for k in ("model", "modelo", "decommission", "not found", "404", "400", "unsupported", "image")):
            hablado = "El modelo de visión rechazó la imagen. Puede que el modelo ya no exista o no acepte imágenes."
        else:
            hablado = "Ahora mismo no pude ver la pantalla."
        self._yue_say(hablado)
        # Mostramos SIEMPRE el detalle técnico en el chat (aunque YUE esté en modo
        # solo-voz), para poder diagnosticarlo. Es un mensaje de sistema.
        try:
            self.chat.show_reply("⚠️ Detalle de visión: " + msg[:400])
        except Exception:
            pass

    def _diagnose_vision(self):
        """Prueba captura y modelo por separado y muestra el resultado en el chat."""
        try:
            self.chat.show_reply("🔎 Diagnóstico de visión en curso… (captura y modelo)")
        except Exception:
            pass
        self.chat.set_status("Diagnosticando la visión…")
        worker = VisionDiagWorker(self.engine)
        worker.done.connect(self._on_vision_diag)
        self.controller_ctx.workers.track(worker)

    def _on_vision_diag(self, report):
        self.chat.set_status("")
        lineas = ["🔎 Diagnóstico de visión:"]
        todo_ok = True
        for nombre, ok, detalle in report:
            lineas.append(f"{'✅' if ok else '❌'} {nombre}: {detalle}")
            todo_ok = todo_ok and ok
        try:
            self.chat.show_reply("\n".join(lineas))
        except Exception:
            pass
        if todo_ok:
            self._yue_say("Mi visión funciona bien. Ya puedo mirar tu pantalla.")
        else:
            self._yue_say("Encontré el problema de mi visión; te dejé el detalle en el chat.")

    # ---------- consolidación de memoria a largo plazo (al arrancar) ----------
    def _schedule_memory_consolidation(self):
        """Lanza la consolidación en un hilo de fondo. Best-effort: nunca bloquea
        ni interrumpe el chat; si no toca o falla, no cambia nada."""
        def _worker():
            try:
                resumen = memory_consolidation.consolidate(self.memory, self.engine)
                if resumen:
                    print("[memoria] consolidé un periodo del historial en un resumen.")
            except Exception as exc:
                print("[memoria] la consolidación falló (se reintentará):", exc)
            # NUEVO (memoria narrativa): las historias tienen su PROPIO ritmo
            # (STORY_CONSOLIDATION_DAYS) y su propia marca de tiempo, así que
            # se revisan aunque el resumen a largo plazo aún no tocara. Cuando
            # ambas tocan a la vez, `consolidate()` ya llamó a esta y la
            # segunda llamada sale enseguida por «aún no toca»: es idempotente.
            #
            # Su primera mitad ni siquiera necesita modelo: sincroniza las metas
            # y recalcula el peso de cada historia con la evidencia acumulada.
            if getattr(self, "story_memory", None) is not None:
                try:
                    informe = self.story_memory.consolidate()
                    if informe and (informe.get("created") or informe.get("events")):
                        print("[memoria] las historias que sigo con él avanzaron.")
                except Exception as exc:
                    print("[story-memory] la revisión de historias falló:", exc)
        threading.Thread(target=_worker, name="YueMemoryConsolidation",
                         daemon=True).start()

    # ---------- preflight de visión (al arrancar) ----------
    def _vision_preflight(self):
        """Al arrancar, valida en segundo plano la clave de visión y avisa claro
        si el proveedor la rechaza. No bloquea la interfaz ni gasta tokens."""
        # Si la visión es solo-OCR, no usamos modelo multimodal: la clave da igual.
        if getattr(config, "VISION_OCR_ONLY", False):
            return
        if not getattr(config, "VISION_PREFLIGHT", True):
            return
        if not getattr(config, "VISION_ENABLED", True):
            return
        worker = VisionPreflightWorker(self.engine)
        worker.done.connect(self._on_vision_preflight)
        self.controller_ctx.workers.track(worker)

    def _on_vision_preflight(self, res):
        res = res or {}
        host = res.get("host", "?")
        model = res.get("model", "?")
        detail = res.get("detail", "")
        if res.get("ok"):
            print(f"[vision] preflight OK · host={host} · modelo={model}")
            return
        reason = res.get("reason", "")
        if reason == "disabled":
            return  # el usuario apagó la visión a propósito (VISION_PROVIDER=none)
        print(f"[vision] preflight FALLÓ · motivo={reason} · host={host} · {detail}")
        if reason == "invalid_key":
            aviso = (
                f"⚠️ Mi visión está apagada: el proveedor «{host}» rechazó la clave "
                "(401/403 – clave inválida o sin acceso al modelo). Pon una clave "
                "VÁLIDA en VISION_API_KEY dentro del .env y reiníciame. "
                f"Detalle del proveedor: {detail}"
            )
        elif reason in ("no_key", "no_base"):
            aviso = (
                "⚠️ Mi visión no tiene credenciales. En el .env define VISION_PROVIDER "
                "(together/openai/groq), VISION_API_KEY y VISION_BASE_URL de tu "
                "proveedor de visión, y reiníciame."
            )
        elif reason == "unreachable":
            aviso = (
                f"⚠️ No pude contactar al proveedor de visión «{host}» (red o host "
                f"caído). Revisa tu conexión. Detalle: {detail}"
            )
        else:
            aviso = f"⚠️ Mi visión no está lista ({reason or 'desconocido'}). Detalle: {detail}"
        try:
            self.chat.show_reply(aviso)
        except Exception:
            pass

    # ---------- cámara local ----------
    def _on_camera_status(self, message, active):
        self.pet.set_camera_active(bool(active))
        print(f"[camara] estado: {message}")

    def _on_camera_observation(self, observation):
        # El dato se conserva dentro de CameraObserver. No se narra en el chat.
        if not isinstance(observation, CameraObservation):
            return
        # NUEVO (memoria emocional): guardamos en segundo plano como se ve a la
        # persona. SOLO la etiqueta y la hora: nunca fotos ni datos que la
        # identifiquen. Es independiente del espejo empatico de abajo, para que
        # el recuerdo se forme aunque YUE este hablando u ocupada.
        self._log_camera_mood(observation)
        # NUEVO (YUE habla al ver tu emoción): si detecta una emoción MUY fuerte y
        # sostenida, o distrés sostenido (peligro), te lo comenta en voz y te
        # pregunta por qué estás así. Muy racionado (no habla a cada gesto). Va
        # ANTES del espejo empático (que solo pone cara) y es independiente de él.
        try:
            self._maybe_camera_emotion_checkin(observation)
        except Exception as exc:
            print("[camara] fallo evaluando el comentario emocional:", exc)
        # ESPEJO EMPÁTICO (reescrito): la cámara pasa de DECIDIR a REPORTAR.
        #
        # Antes esto llamaba a `_set_avatar_emotion(...)` directamente, así que
        # una cara mal leída cambiaba el estado de YUE sin que nada pudiera
        # contradecirla. Ahora la cámara solo dice:
        #
        #     "creo que el usuario parece triste, con confianza 0.63"
        #
        # y el gestor decide. Si el usuario acaba de ESCRIBIR que está triste,
        # el texto (peso 1.00) manda sobre la cámara (peso 0.55) y no hay
        # conflicto. Y si el usuario dice que está bien mientras la cámara ve
        # agotamiento, eso queda como emoción SECUNDARIA: no se tira el dato,
        # pero tampoco se le lleva la contraria a la persona.
        if not bool(getattr(config, "CAMERA_EMPATHY_ENABLED", True)):
            return
        emociones = getattr(observation, "person_emotions", ()) or ()
        if not emociones:
            return
        # Nos quedamos con la emoción no neutra de mayor confianza.
        fuertes = [e for e in emociones if getattr(e, "key", "neutral") != "neutral"]
        if not fuertes:
            return
        mejor = max(fuertes, key=lambda e: getattr(e, "confidence", 0.0))
        confianza = float(getattr(mejor, "confidence", 0.0))
        if confianza < float(getattr(config, "CAMERA_EMPATHY_MIN_CONFIDENCE", 0.55)):
            return

        # Cuentagotas: solo al cambiar de ánimo y no más de una vez cada X seg.
        # La cámara analiza ~una vez por segundo; sin esto inundaría la fusión.
        ahora = time.time()
        ultima_key = getattr(self, "_last_empathy_key", "")
        ultima_at = float(getattr(self, "_last_empathy_at", 0.0))
        gap = float(getattr(config, "CAMERA_EMPATHY_MIN_GAP", 8.0))
        if mejor.key == ultima_key and (ahora - ultima_at) < gap:
            return
        self._last_empathy_key = mejor.key
        self._last_empathy_at = ahora

        # 1) OBSERVACIÓN: siempre se reporta, gobierne quien gobierne la cara.
        #    Que la música esté sonando no significa que YUE deba dejar de
        #    ENTERARSE de cómo está la persona; solo que no debe cambiar de cara.
        gestor = getattr(self, "state_manager", None)
        if gestor is not None:
            try:
                valencia, activacion = _valencia_activacion_camara(mejor.key)
                gestor.observe_emotion(
                    "camera", str(mejor.key), confianza,
                    valence=valencia, arousal=activacion,
                    explicit=False, detail="lectura facial")
            except Exception as exc:
                print("[camara] no pude registrar la observación:", exc)

        # 2) PROPUESTA: solo si la cara está libre. Se mantienen las mismas
        #    guardas de antes (multimedia, órdenes en curso, YUE hablando).
        try:
            if (getattr(self.audio, "media_playing", False) or self._pc_busy
                    or self._vision_busy or self.speaker.is_speaking):
                return
        except Exception:
            pass
        try:
            from core import face_emotion
            espejo = face_emotion.empathic_avatar_emotion(mejor.key)
        except Exception:
            espejo = None
        if not espejo:
            return
        nombre, inten, dur = espejo
        try:
            self._set_avatar_emotion(nombre, inten, dur,
                                     priority=_PRIO_MEDIA, source="camara")
        except Exception as exc:
            print("[camara] no pude proponer el espejo empático:", exc)

    def _log_camera_mood(self, observation):
        """Registra en mood_log la emoción NO neutra leída por la cámara.

        Privacidad por diseño: NO se guarda ninguna imagen ni dato identificable,
        solo la etiqueta emocional, su confianza y la hora. Lleva su propio
        cuentagotas para que el historial tenga sentido sin inflarse fotograma a
        fotograma (la cámara analiza ~una vez por segundo).
        """
        try:
            emociones = getattr(observation, "person_emotions", ()) or ()
            fuertes = [e for e in emociones if getattr(e, "key", "neutral") != "neutral"]
            if not fuertes:
                return
            mejor = max(fuertes, key=lambda e: getattr(e, "confidence", 0.0))
            conf = float(getattr(mejor, "confidence", 0.0))
            if conf < float(getattr(config, "CAMERA_MOOD_MIN_CONFIDENCE", 0.5)):
                return
            # Cuentagotas propio (independiente del espejo empático): registra al
            # cambiar de emoción o cuando pasó suficiente tiempo del mismo ánimo.
            ahora = time.time()
            ultima_key = getattr(self, "_last_mood_cam_key", "")
            ultima_at = float(getattr(self, "_last_mood_cam_at", 0.0))
            gap = float(getattr(config, "CAMERA_MOOD_MIN_GAP", 45.0))
            if mejor.key == ultima_key and (ahora - ultima_at) < gap:
                return
            self._last_mood_cam_key = mejor.key
            self._last_mood_cam_at = ahora
            self.memory.add_mood("camara", mejor.key, conf, None)
        except Exception as exc:
            print("[mood] no pude registrar el ánimo de la cámara:", exc)

    # ---------- YUE HABLA al ver tu emoción por la cámara ----------
    def _maybe_camera_emotion_checkin(self, observation):
        """Si YUE ve una emoción MUY fuerte y sostenida —o distrés sostenido, que
        tratamos como "peligro"—, te lo comenta EN VOZ y te pregunta por qué estás
        así, con su propio tono.

        Muy racionado a propósito (la petición era: que NO hable a cada gesto):
          · La emoción tiene que ser fuerte (confianza alta) Y mantenerse varias
            lecturas seguidas (racha), no un gesto de un segundo.
          · Cuentagotas global (no habla más de una vez cada pocos minutos) y por
            misma emoción (no repite el mismo ánimo hasta bastante después).
          · El "peligro" (distrés sostenido de varios minutos) tiene prioridad: usa
            un tono más cuidadoso y salta aunque no se cumpla la racha corta.
          · Respeta todos los cortes de cortesía existentes: no pisa su propia voz,
            ni interrumpe si escribes, ni durante media/orden de PC/visión o clase.
        A prueba de fallos: ante cualquier error no dice nada.
        """
        if not bool(getattr(config, "CAMERA_EMOTION_TALK_ENABLED", True)):
            return
        # Cortes de cortesía (mismos que usa el resto de iniciativas de YUE).
        try:
            if (self.speaker.is_speaking or self.chat.is_user_composing()
                    or self._pc_busy or self._vision_busy
                    or getattr(self.audio, "media_playing", False)
                    or getattr(self, "_autonomy_busy", False)
                    or getattr(self, "_emotion_talk_pending", False)):
                return
            if (getattr(self, "modes", None) is not None
                    and self.modes.current_mode() == MODE_TEACHER):
                return
        except Exception:
            return

        ahora = time.time()

        # ¿Peligro? = distrés (tristeza/tensión) SOSTENIDO durante varios minutos.
        peligro = False
        try:
            peligro = bool(self.camera.risk_signal())
        except Exception:
            peligro = False

        # Mejor emoción NO neutra de este fotograma (para el caso "muy fuerte").
        emociones = getattr(observation, "person_emotions", ()) or ()
        fuertes = [e for e in emociones if getattr(e, "key", "neutral") != "neutral"]
        mejor = max(fuertes, key=lambda e: getattr(e, "confidence", 0.0)) if fuertes else None
        conf = float(getattr(mejor, "confidence", 0.0)) if mejor else 0.0
        min_conf = float(getattr(config, "CAMERA_EMOTION_TALK_MIN_CONFIDENCE", 0.72))

        # Racha: contamos lecturas seguidas de la MISMA emoción fuerte para no
        # reaccionar a un gesto puntual. Se reinicia si cambia o baja la confianza.
        min_streak = max(1, int(getattr(config, "CAMERA_EMOTION_TALK_MIN_STREAK", 3)))
        prev_key = getattr(self, "_emotion_talk_streak_key", "")
        if mejor is not None and conf >= min_conf:
            if mejor.key == prev_key:
                self._emotion_talk_streak = int(getattr(self, "_emotion_talk_streak", 0)) + 1
            else:
                self._emotion_talk_streak_key = mejor.key
                self._emotion_talk_streak = 1
        else:
            self._emotion_talk_streak_key = ""
            self._emotion_talk_streak = 0
        racha = int(getattr(self, "_emotion_talk_streak", 0))

        # ¿Toca hablar? Peligro, o emoción fuerte con racha suficiente.
        emocion_fuerte = mejor is not None and conf >= min_conf and racha >= min_streak
        if not (peligro or emocion_fuerte):
            return

        # Elegimos la clave a comentar: si hay peligro, priorizamos la emoción
        # negativa que lo provoca; si no, la emoción fuerte del momento.
        key = (getattr(mejor, "key", "") if mejor is not None else "") or "neutral"
        if peligro and (mejor is None or mejor.key not in ("triste", "molesta")):
            key = "triste"  # el distrés sostenido se lee como tristeza/tensión

        # Cuentagotas GLOBAL (no habla seguido) y por MISMA emoción (no repite el
        # mismo ánimo pronto). El peligro relaja el corte por misma emoción, pero
        # sigue respetando el global para no volverse una alarma.
        min_gap = float(getattr(config, "CAMERA_EMOTION_TALK_MIN_GAP", 240.0))
        same_gap = float(getattr(config, "CAMERA_EMOTION_TALK_SAME_GAP", 900.0))
        ultima_at = float(getattr(self, "_last_emotion_talk_at", 0.0))
        if (ahora - ultima_at) < min_gap:
            return
        if not peligro:
            ultima_key = getattr(self, "_last_emotion_talk_key", "")
            ultima_same = float(getattr(self, "_last_emotion_talk_same_at", 0.0))
            if key == ultima_key and (ahora - ultima_same) < same_gap:
                return

        # A partir de aquí SÍ hablamos: fijamos cuentagotas y reiniciamos la racha
        # para no encadenar comentarios.
        self._last_emotion_talk_at = ahora
        self._last_emotion_talk_key = key
        self._last_emotion_talk_same_at = ahora
        self._emotion_talk_streak = 0
        self._emotion_talk_streak_key = ""

        self._deliver_emotion_checkin(key, peligro)

    def _deliver_emotion_checkin(self, key, peligro):
        """Genera con el LLM (en el tono de YUE) el comentario/pregunta sobre la
        emoción vista y lo dice en voz. Si el LLM no está, usa una frase de
        respaldo para no quedarse muda. Aditivo y a prueba de fallos."""
        from core import face_emotion
        # Marcamos pendiente para no lanzar dos a la vez (se limpia al terminar).
        self._emotion_talk_pending = True
        try:
            hint = face_emotion.talk_prompt_hint(key)
            current = self._refresh_bond()
            # Si hay peligro, inyectamos la directiva suave de cuidado (la misma que
            # usa el flujo por texto), para que acompañe con tacto.
            directive = None
            if peligro:
                try:
                    directive = safety.safety_directive_visual(config.CRISIS_RESOURCES)
                except Exception:
                    directive = None
            system = self._system_prompt(current, directive)
            if peligro:
                encargo = (
                    "IMPORTANTE: por la cámara notas que la persona lleva un buen rato "
                    f"con una expresión de que algo va mal ({hint}). No es un gesto "
                    "puntual, se ha mantenido. Con cariño y tu estilo (baja un poco el "
                    "tono tsundere aquí, sin dramatizar), dile que la ves así y "
                    "pregúntale qué le pasa o si está bien. UNA o dos frases, natural, "
                    "en español mexicano. No inventes qué le pasó; solo pregunta."
                )
            else:
                encargo = (
                    f"Por la cámara acabas de notar que a la persona {hint}. "
                    "Coméntaselo y pregúntale por qué está así, con tu estilo tsundere "
                    "juguetón (te importa aunque lo disimules). UNA o dos frases como "
                    "mucho, natural y en español mexicano. No inventes el motivo; solo "
                    "pregúntale."
                )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": encargo},
            ]
            respaldo = face_emotion.talk_fallback_line(key, riesgo=peligro)
            worker = AiWorker(self.engine, messages)
            worker.done.connect(lambda answer, k=key: self._on_emotion_talk_done(k, answer))
            worker.failed.connect(
                lambda _error, d=respaldo: self._on_emotion_talk_done(key, d)
            )
            self.controller_ctx.workers.track(worker)
        except Exception as exc:
            print("[camara] no pude preparar el comentario emocional:", exc)
            self._emotion_talk_pending = False
            try:
                self._on_emotion_talk_done(key, face_emotion.talk_fallback_line(key, riesgo=peligro))
            except Exception:
                pass

    def _on_emotion_talk_done(self, key, text):
        """Dice en voz el comentario emocional y lo deja en memoria como turno de
        YUE, para que tu respuesta ('estoy triste porque…') fluya con contexto."""
        self._emotion_talk_pending = False
        try:
            clean = emotion.clean_response(text or "")
            if not clean.strip():
                clean = None
            # Vuelve a comprobar cortesía: si mientras pensaba empezaste a hablar o
            # a escribir, no te pisamos; el momento ya pasó.
            if (self.speaker.is_speaking or self.chat.is_user_composing()
                    or self._pc_busy or self._vision_busy):
                return
            if not clean:
                return
            try:
                self.memory.add_message("assistant", clean)
            except Exception:
                pass
            self._yue_say(clean, "camara-emocion")
        except Exception as exc:
            print("[camara] no pude decir el comentario emocional:", exc)

    def _describe_audio(self):
        """Responde a «¿qué tal la música / qué escuchas / qué te pareció?» diciendo
        qué suena (o sonaba hace un momento) y dando una impresión con la voz de YUE,
        usando la letra/diálogo que captó. Nunca dice «no escucho nada» si de hecho
        acaba de sonar algo."""
        desc = ""
        try:
            desc = self.audio.describe() or ""
        except Exception as exc:
            print("[audio] describe() falló:", exc)
        # 1) ¿Hay contenido AHORA? 2) Si no, ¿sonó algo hace poco?
        prof = None
        try:
            prof = self.audio.media_profile()
        except Exception:
            prof = None
        activo = bool(getattr(self.audio, "active", False))
        ahora_suena = activo and prof is not None and getattr(prof, "media_type", "") in (
            "video_musical", "video_normal",
        )
        reciente = None
        edad = None
        if not ahora_suena:
            try:
                reciente, edad = self.audio.recent_content(max_age=150.0)
            except Exception:
                reciente, edad = (None, None)

        lyrics = self.media_director.recent_lyrics()

        # Si no hay nada actual ni reciente NI letra captada, respondemos directo.
        if not ahora_suena and reciente is None and not lyrics:
            self._yue_say(desc or "Ahora mismo no distingo nada sonando en tu PC. "
                                  "Si quieres que te diga qué tal, ponlo a sonar un momento.")
            return

        # Construimos el contexto para que YUE dé una impresión natural.
        if ahora_suena:
            percibo = desc or (prof.summary_es() if prof else "algo está sonando")
            cuando = "Ahora mismo"
        elif reciente is not None:
            percibo = reciente.summary_es()
            seg = int(edad or 0)
            cuando = f"Hace unos {seg} segundos" if seg >= 3 else "Hace un momento"
        else:
            percibo = "algo estuvo sonando hace poco"
            cuando = "Hace un momento"

        contexto_letra = ""
        if lyrics:
            contexto_letra = (
                "\nAlgo de lo que alcanzaste a oír (letra o diálogo, puede venir con "
                f"errores de transcripción): «{lyrics}»."
            )

        current = self._refresh_bond()
        system = self._system_prompt(current)
        user = (
            "El usuario te pregunta qué te pareció lo que suena (o acaba de sonar) en "
            f"su computadora. {cuando} percibes esto de tu escucha del audio del "
            f"sistema: «{percibo}».{contexto_letra}\n"
            "Responde en español, en 1-3 frases, con tu personalidad. Da una impresión "
            "natural: si es música, comenta el ambiente, la energía o de qué parece ir "
            "por lo que oíste; si es un vídeo o peli, coméntalo. Puedes referirte a lo "
            "que oíste, pero NO inventes título ni artista si no te consta. Nunca digas "
            "que no puedes escuchar: sí puedes oír el audio del PC."
        )
        request_id = self._chat_request_id
        self.chat.set_status("Yue está recordando lo que sonó…")
        worker = AiWorker(self.engine, [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ])
        # Si el modelo falla, al menos decimos la clasificación (no «no escucho nada»).
        respaldo = desc or (f"{cuando} sonaba {percibo}." if percibo else "")
        worker.done.connect(lambda answer, rid=request_id: self._on_ai_done(rid, "¿qué tal la música?", answer))
        worker.failed.connect(lambda _error, d=respaldo: (self.chat.set_status(""), self._yue_say(d)))
        self.controller_ctx.workers.track(worker)

    def _on_audio_status(self, text, active):
        print(f"[audio] {'activo' if active else 'inactivo'}: {text}")

    def _on_audio_reaction(self, reaction):
        """Compatibilidad con el reactor clásico cuando el companion está apagado."""
        if getattr(self, "media_companion", None) is not None and self.media_companion.enabled:
            return
        if not isinstance(reaction, Reaction):
            return
        # La expresión va en directo, aunque no diga nada.
        self._set_avatar_emotion(reaction.emotion, reaction.intensity, reaction.duration_ms,
                                 priority=_PRIO_MEDIA, source="multimedia")
        if not reaction.comment:
            return
        # No comenta si estorbaría: usuario escribiendo, Yue ya hablando, una
        # orden de PC/visión en curso, o la voz apagada. El cuentagotas y la
        # regla de "solo tras pausa en los diálogos" ya vienen de decide_reaction.
        if (self.chat.is_user_composing() or self._pc_busy or self._vision_busy
                or self.speaker.is_speaking):
            return
        comentario = reaction.comment
        if self.speaker.enabled:
            self.listener.set_tts_text(comentario)   # que el micro no lo tome como orden
        # NUEVO: solo escribe el comentario si se pidió mostrar respuestas o la voz
        # está apagada; en el caso normal, YUE solo lo dice en voz alta.
        if bool(getattr(config, "CHAT_MOSTRAR_RESPUESTAS", False)) or not self.speaker.enabled:
            self.chat.show_reply(comentario)
        self.speaker.say(comentario)

    def _media_companion_context(self):
        """Señales baratas para decidir si un comentario aportaría o interrumpiría."""
        teacher_mode = False
        try:
            teacher_mode = self.modes is not None and self.modes.current_mode() == MODE_TEACHER
        except Exception:
            pass
        return {
            "pc_busy": bool(getattr(self, "_pc_busy", False)),
            "vision_busy": bool(getattr(self, "_vision_busy", False)),
            "teacher_mode": teacher_mode,
            "user_idle_seconds": max(0.0, time.time() - getattr(self, "_last_user_activity", time.time())),
        }

    def _on_media_state(self, playing):
        playing = bool(playing)
        self.listener.set_media_playing(playing)
        self.media_companion.set_media_playing(playing)
        # SYSTEM STATE al instante, sin esperar al siguiente latido: quien
        # consulte `global_state()` en este mismo turno debe ver la verdad.
        try:
            if self.state_manager is not None:
                self.state_manager.update_system(media_playing=playing)
                # Al PARAR la música se retira su propuesta. No se deja
                # caducar sola: si la canción terminó, su cara ya no pinta nada
                # y el arbitraje debe recalcular ahora mismo.
                if not playing:
                    self.state_manager.withdraw("multimedia")
        except Exception as exc:
            print("[estado] no pude actualizar el estado multimedia:", exc)

    def _on_media_companion_status(self, text, active):
        if getattr(config, "DEBUG_STATUS", False):
            print(f"[media-companion] {'activo' if active else 'inactivo'}: {text}")

    def _on_media_companion_avatar(self, state):
        if isinstance(state, AvatarMediaState):
            self.pet.set_media_companion(state)

    def _on_media_companion_reaction(self, reaction):
        if not isinstance(reaction, CompanionReaction):
            return
        # El estado emocional continuo anima el avatar aunque YUE decida callar.
        self._set_avatar_emotion(reaction.emotion, reaction.intensity, reaction.duration_ms,
                                 priority=_PRIO_MEDIA, source="multimedia")
        if reaction.gesture:
            self.pet.play_gesture(reaction.gesture, min(1.0, reaction.intensity))
        if not reaction.comment:
            return
        # Segunda barrera en el hilo de UI: protege conversación, clase, órdenes y
        # composición incluso si el contexto cambió después del análisis.
        teacher_mode = False
        try:
            teacher_mode = self.modes is not None and self.modes.current_mode() == MODE_TEACHER
        except Exception:
            pass
        if (self.chat.is_user_composing() or self._pc_busy or self._vision_busy
                or teacher_mode or self.speaker.is_speaking):
            return
        comment = reaction.comment
        if self.speaker.enabled:
            self.listener.set_tts_text(comment)
        if bool(getattr(config, "CHAT_MOSTRAR_RESPUESTAS", False)) or not self.speaker.enabled:
            self.chat.show_reply(comment)
        self.media_companion.mark_comment_delivered(
            comment, reaction.emotion, reaction.intensity
        )
        self.speaker.say(comment)

    def _on_pc_action_log(self, actions):
        """Recibe la bitácora desde PCWorker y la pinta en el hilo de Qt."""
        try:
            from core import ui_bridge
            ui_bridge.post_to_ui_thread(lambda: self.chat.set_pc_actions(actions))
        except Exception as exc:
            print("[control-pc] no pude mostrar la bitácora:", exc)

    # ---------- control del PC ----------
    def _run_pc_order(self, instruction):
        if self._pc_busy:
            self._yue_say("Ya hay una orden en curso. Di «Yue, detente» para cancelarla.")
            return
        self._pc_busy = True
        self._pc_instruction = instruction      # la necesitan los hooks de aprendizaje
        self.chat.set_status("Yue está comprendiendo y planificando la orden…")
        self._set_avatar_emotion("focused", 0.95, 9000,
                                 priority=_PRIO_CONVERSACION, source="control_pc")
        worker = PCWorker(self.pc, self.engine, instruction)
        worker.progress.connect(self.chat.set_status)
        worker.done.connect(self._on_pc_done)
        worker.failed.connect(self._on_pc_failed)
        self.controller_ctx.workers.track(worker)

    def _on_pc_done(self, result):
        self._pc_busy = False
        self.chat.set_status("")
        # NUEVO (recuperación): recuerda este bloque para "deshacer"/"repetir",
        # también si vino de una receta aprendida (que no pasa por execute()).
        try:
            self.pc.remember_result(self._pc_instruction, result)
        except Exception as exc:
            print("[control-pc] no pude recordar el bloque:", exc)
        # --- aprendizaje: refuerza o crea la receta si todo salió bien ---
        try:
            from core.learning import integration as learning
            learning.after_result(self._pc_instruction, result)
        except Exception as exc:
            print("[aprendizaje] no pude registrar el resultado:", exc)
        count = len(result.get("actions", []))
        cycles = result.get("cycles", 1)
        if result.get("from_skill"):
            self._yue_say(f"Listo. Esto ya lo sabía hacer: {count} paso{'s' if count != 1 else ''} de memoria.")
            return
        mode = (
            f"con el agente autónomo en {cycles} fase{'s' if cycles != 1 else ''}"
            if result.get("used_ai") else "con un plan determinista verificado"
        )
        if result.get("used_ai") and not result.get("completed", False):
            self._yue_say(
                f"Ejecuté {count} paso{'s' if count != 1 else ''} {mode}, pero alcancé el límite sin verificar el final. Revisa el resultado."
            )
        else:
            self._yue_say(f"Ya terminé. Ejecuté {count} paso{'s' if count != 1 else ''} {mode}.")

    def _on_pc_failed(self, error):
        self._pc_busy = False
        self.chat.set_status("")
        # --- aprendizaje: guarda el fallo y pide la reflexión en segundo plano ---
        try:
            from core.learning import integration as learning
            learning.after_error(self._pc_instruction, error, self.pc.last_history)
        except Exception as exc:
            print("[aprendizaje] no pude registrar el error:", exc)
        if "cancelada" not in str(error).lower():
            self._yue_say(f"No completé esa orden: {error}")
        print("[control-pc] error:", error)

    # ---------- recuperación: deshacer / repetir lo último ----------
    def _undo_last_pc(self):
        """«Deshaz eso»: revierte la última acción si es reversible con seguridad."""
        if self._pc_busy:
            self._yue_say("Espera a que termine lo de ahora y enseguida te lo deshago.")
            return
        self._pc_busy = True
        self.chat.set_status("Deshaciendo lo último…")
        worker = PCRecoveryWorker(self.pc, "undo")
        worker.done.connect(self._on_undo_done)
        worker.failed.connect(self._on_recovery_failed)
        self.controller_ctx.workers.track(worker)

    def _on_undo_done(self, res):
        self._pc_busy = False
        self.chat.set_status("")
        if res.get("undone"):
            accion = res.get("action", "lo último")
            self._yue_say(f"Hecho, deshice {accion}.")
            return
        motivo = str(res.get("reason", ""))
        if "no tengo" in motivo or "no hay" in motivo:
            self._yue_say("No tengo ninguna acción reciente que deshacer.")
        else:
            # Sin adivinar: le digo con claridad por qué no me arriesgo.
            self._yue_say(
                f"No puedo deshacer eso: {motivo}. Prefiero no adivinar y liarla más. "
                "Dime qué quieres corregir y lo hago."
            )

    def _repeat_last_pc(self):
        """«Repite eso» / «hazlo de nuevo»: reejecuta el último bloque."""
        if self._pc_busy:
            self._yue_say("Ya hay algo en curso; cuando termine te lo repito.")
            return
        self._pc_busy = True
        self.chat.set_status("Repitiendo lo último…")
        self._set_avatar_emotion("focused", 0.9, 6000,
                                 priority=_PRIO_CONVERSACION, source="control_pc")
        worker = PCRecoveryWorker(self.pc, "repeat")
        worker.progress.connect(self.chat.set_status)
        worker.done.connect(self._on_repeat_done)
        worker.failed.connect(self._on_recovery_failed)
        self.controller_ctx.workers.track(worker)

    def _on_repeat_done(self, res):
        self._pc_busy = False
        self.chat.set_status("")
        if not res.get("repeated"):
            motivo = str(res.get("reason", ""))
            if "no tengo" in motivo:
                self._yue_say("No tengo ninguna orden reciente que repetir. Pídemela una vez y luego ya puedo repetirla.")
            else:
                self._yue_say(f"No pude repetir lo último: {motivo}.")
            return
        n = len(res.get("actions", []))
        pasos = f"{n} paso{'s' if n != 1 else ''}"
        if res.get("ok"):
            self._yue_say(f"Listo, repetí lo último ({pasos}).")
        else:
            self._yue_say(f"Repetí lo último ({pasos}), pero algo no salió igual que antes. Échale un vistazo.")

    def _on_recovery_failed(self, error):
        self._pc_busy = False
        self.chat.set_status("")
        self._yue_say(f"No pude completar eso: {error}")

    # ---------- rutinas guardadas ----------
    def _save_routine(self, nombre):
        """«Guarda esto como rutina X»: guarda el último plan ejecutado."""
        nombre = (nombre or "").strip()
        if not nombre:
            self._yue_say("Dime un nombre para la rutina, por ejemplo «guárdalo como rutina correo».")
            return
        rec = self.pc.last_executed
        pasos = (rec or {}).get("actions") if rec else None
        if not pasos:
            self._yue_say(
                "No tengo ninguna orden reciente que guardar. Pídeme primero que haga algo "
                "y luego di «guarda esto como rutina» y el nombre."
            )
            return
        try:
            self.memory.add_routine(nombre, pasos)
        except Exception as exc:
            self._yue_say(f"No pude guardar la rutina: {exc}")
            return
        n = len(pasos)
        self._yue_say(
            f"Guardado como rutina «{nombre}» ({n} paso{'s' if n != 1 else ''}). "
            f"Cuando quieras, solo di «ejecuta mi rutina {nombre}»."
        )

    def _run_routine(self, nombre):
        """«Ejecuta mi rutina X»: reejecuta el plan por el pipeline seguro."""
        nombre = (nombre or "").strip()
        if not nombre:
            self._list_routines(prefijo="¿Cuál de estas quieres que ejecute? ")
            return
        rutina = self.memory.get_routine(nombre)
        if not rutina or not rutina.get("pasos"):
            self._yue_say(
                f"No encuentro una rutina llamada «{nombre}». Di «mis rutinas» para ver las que tienes."
            )
            return
        if self._pc_busy:
            self._yue_say("Ya hay algo en curso; cuando termine lanzo tu rutina.")
            return
        self._pc_busy = True
        self._pc_instruction = f"rutina: {rutina['nombre']}"
        self.chat.set_status(f"Ejecutando la rutina «{rutina['nombre']}»…")
        self._set_avatar_emotion("focused", 0.9, 8000,
                                 priority=_PRIO_CONVERSACION, source="control_pc")
        worker = PCRecoveryWorker(self.pc, "routine", actions=rutina["pasos"], label=rutina["nombre"])
        worker.progress.connect(self.chat.set_status)
        worker.done.connect(self._on_routine_done)
        worker.failed.connect(self._on_recovery_failed)
        self.controller_ctx.workers.track(worker)

    def _on_routine_done(self, res):
        self._pc_busy = False
        self.chat.set_status("")
        # Aprendizaje: refuerza/crea receta si todo salió bien (igual que una orden).
        try:
            from core.learning import integration as learning
            learning.after_result(self._pc_instruction, res)
        except Exception as exc:
            print("[aprendizaje] no pude registrar la rutina:", exc)
        if not res.get("ran"):
            self._yue_say(f"No pude ejecutar la rutina: {res.get('reason', 'algo salió mal')}.")
            return
        n = len(res.get("actions", []))
        pasos = f"{n} paso{'s' if n != 1 else ''}"
        if res.get("ok"):
            self._yue_say(f"Rutina completada ({pasos}).")
        else:
            self._yue_say(f"Ejecuté la rutina ({pasos}), pero algo no salió como esperaba. Échale un vistazo.")

    def _list_routines(self, prefijo=""):
        """«Mis rutinas»: lista las guardadas."""
        rutinas = self.memory.list_routines()
        if not rutinas:
            self._yue_say(
                "Aún no tienes rutinas guardadas. Cuando haga algo por ti, di "
                "«guarda esto como rutina» y un nombre, y lo reutilizamos con una frase."
            )
            return
        nombres = ", ".join(r["nombre"] for r in rutinas)
        self._yue_say(
            f"{prefijo}Tienes {len(rutinas)} rutina{'s' if len(rutinas) != 1 else ''}: {nombres}. "
            "Di «ejecuta mi rutina» y el nombre para lanzarla."
        )

    def _show_activity(self):
        """«/actividad» o «mi actividad»: resumen legible de la última semana."""
        resumen = ""
        try:
            resumen = self.memory.get_activity_summary(7)
        except Exception as exc:
            print("[actividad] no pude leer el resumen:", exc)
        if resumen:
            self._yue_say(resumen[0].upper() + resumen[1:])
        else:
            self._yue_say("Todavía no hay mucho en la bitácora de esta semana.")

    # ---------- accesibilidad: confirmación verbal de acciones irreversibles ----------
    def _pc_confirm_by_voice(self, question: str) -> bool:
        """Pide confirmación por voz y ESPERA la respuesta (sí/no).

        Se llama desde el hilo del PCWorker, así que aquí SOLO se marshaliza la
        pregunta al hilo principal (voz + UI) y se bloquea en un Event hasta que
        el listener/chat entregue la respuesta o venza el tiempo. Si no hay
        respuesta clara a tiempo, devuelve False (no ejecutar) por seguridad.
        """
        event = threading.Event()
        holder = {"result": False}
        self._pc_confirm = {"event": event, "holder": holder}
        # Que YUE lo pregunte por voz en el hilo principal (usa core/voice.py).
        self.confirm_request.emit(question)
        timeout = float(getattr(config, "ACCESSIBILITY_CONFIRM_TIMEOUT", 25.0))
        answered = event.wait(max(3.0, timeout))
        self._pc_confirm = None
        if not answered:
            self.confirm_notify.emit(
                "No te escuché, así que mejor no lo hago. Si lo quieres, dímelo otra vez."
            )
            return False
        return bool(holder["result"])

    def _do_confirm_ask(self, question: str):
        """Habla la pregunta de confirmación (siempre en el hilo principal)."""
        self.chat.set_status("Esperando tu «sí» o «no»…")
        self._yue_say(question)

    def _resolve_pc_confirmation(self, text: str):
        """Interpreta la respuesta del usuario a una confirmación pendiente."""
        pending = self._pc_confirm
        if pending is None:
            return
        self._user_float(text)
        verdict = self._parse_yes_no(text)
        if verdict is None:
            # No fue un sí/no claro: re-preguntamos y seguimos esperando.
            self._yue_say("Perdona, solo dime «sí» o «no».")
            return
        if pending["event"].is_set():
            return  # ya resuelto (p. ej. dos respuestas seguidas)
        pending["holder"]["result"] = verdict
        pending["event"].set()
        self.chat.set_status("")
        # Un reconocimiento breve; el resultado final de la orden lo confirmará.
        self._yue_say("Vale, lo hago." if verdict else "Vale, lo dejo así.")

    @staticmethod
    def _parse_yes_no(text: str):
        """Devuelve True (sí), False (no) o None (no está claro)."""
        base = unicodedata.normalize("NFD", (text or "").lower())
        n = "".join(c for c in base if unicodedata.category(c) != "Mn")
        n = re.sub(r"[^a-z0-9 ]", " ", n)
        n = re.sub(r"\s+", " ", n).strip()
        if not n:
            return None
        tokens = n.split()
        # Incertidumbre explícita: no la tomamos como "no", volvemos a preguntar.
        if n in ("no se", "no lo se", "ni idea", "quiza", "quizas", "tal vez") \
                or n.startswith("no estoy segur"):
            return None
        no_frases = ("mejor no", "no lo hagas", "no quiero", "para nada", "ni se te ocurra")
        si_frases = ("de acuerdo", "por supuesto", "esta bien", "hazlo ya", "adelante con eso")
        no_palabras = {"no", "nel", "negativo", "cancela", "cancelar", "detente",
                       "dejalo", "olvidalo", "nop", "nope"}
        si_palabras = {"si", "sii", "sisi", "claro", "dale", "hazlo", "adelante",
                       "confirmo", "confirmado", "correcto", "vale", "ok", "okay",
                       "afirmativo", "sip", "hagalo", "procede", "eso"}
        # La negación tiene prioridad (más seguro ante ambigüedad).
        if any(f in n for f in no_frases) or (no_palabras & set(tokens)):
            return False
        if any(f in n for f in si_frases) or (si_palabras & set(tokens)):
            return True
        return None

    # ---------- iniciativa automática ----------
    def _toggle_autonomy(self):
        enabled = self.autonomy.toggle()
        if enabled:
            self._autonomy_timer.start()
            self._yue_say("Iniciativa automática activada. Prepararé aportes útiles cuando estés inactivo.")
        else:
            self._autonomy_timer.stop()
            self._yue_say("Iniciativa automática pausada.")

    def _autonomous_create(self):
        if self._autonomy_busy or self._pc_busy or self._vision_busy:
            return
        if not self.autonomy.can_create() or self.chat.is_user_composing():
            return
        # NUEVO: la iniciativa autónoma también respeta el estado de YUE. Si el
        # cerebro central decidió que toca acompañar en silencio, no se generan
        # aportes «útiles» por encima de eso.
        if not self._yue_may_take_initiative("medium"):
            return
        idle = time.time() - self._last_user_activity
        if idle < config.AUTONOMY_IDLE_SECONDS:
            return

        self._autonomy_busy = True
        self._autonomy_started_at = self._last_user_activity
        self.chat.set_status("Yue está preparando algo útil por iniciativa propia…")
        messages = self.autonomy.build_prompt(
            self.memory.recent_messages(10),
            self.memory.get_facts(),
            self.memory.list_goals(),
        )
        worker = AutonomyWorker(self.engine, messages)
        worker.done.connect(self._on_autonomy_done)
        worker.failed.connect(self._on_autonomy_failed)
        self.controller_ctx.workers.track(worker)

    def _on_autonomy_done(self, text):
        self._autonomy_busy = False
        self.chat.set_status("")
        path = self.autonomy.save_creation(text)
        still_idle = (
            self._last_user_activity == self._autonomy_started_at
            and time.time() - self._last_user_activity >= config.AUTONOMY_IDLE_SECONDS
            and not self.chat.is_user_composing()
        )
        opened = False
        if still_idle and config.AUTONOMY_PC_ENABLED and config.AUTONOMY_OPEN_CREATIONS:
            try:
                self.pc.open_path(path)
                opened = True
            except Exception as exc:
                print("[autonomia] no pude abrir la creación:", exc)
        if still_idle and config.AUTONOMY_NOTIFY:
            suffix = " y la abrí" if opened else ""
            self._yue_say(f"Preparé algo nuevo, lo guardé como {path.name}{suffix}.")
        else:
            print("[autonomia] creación guardada:", path)

    def _on_autonomy_failed(self, error):
        self._autonomy_busy = False
        self.chat.set_status("")
        print("[autonomia] error:", error)

    # ---------- acciones internas y comandos ----------
    def _run_internal_action(self, action, arg):
        if action == "stop_current":
            self.pc.cancel()
            self.speaker.stop()
            self._chat_request_id += 1
            self.chat.set_status("")
            self._yue_say("Me detuve. Te escucho.")
        elif action == "pc_undo":
            self._undo_last_pc()
        elif action == "pc_repeat":
            self._repeat_last_pc()
        elif action == "routine_save":
            self._save_routine(commands.routine_name(arg))
        elif action == "routine_run":
            self._run_routine(commands.routine_name(arg))
        elif action == "routine_list":
            self._list_routines()
        elif action == "activity_summary":
            self._show_activity()
        elif action == "voice_on":
            if not self.speaker.enabled:
                self.speaker.toggle()
            self._yue_say("Bien, volveré a hablar en voz alta.")
        elif action == "voice_off":
            if self.speaker.enabled:
                self.speaker.toggle()
            self.chat.show_reply("Voz desactivada.")
        elif action == "mic_off":
            if self.listener._wanted_enabled:
                self.listener.toggle()
            self._yue_say("Dejé de escucharte por el micrófono.")
        elif action == "mic_on":
            if not self.listener._wanted_enabled:
                self.listener.toggle()
            self._yue_say("Micrófono activado. Puedes interrumpirme mientras hablo.")
        elif action == "vision_look":
            # Pasamos la frase EXACTA del usuario ("¿qué error aparece?",
            # "explícame este gráfico") para que el analizador priorice eso y no
            # dé una descripción genérica de toda la pantalla.
            self._glance(pregunta=str(arg or ""))
        elif action == "vision_off":
            self._vision_on = False
            self._yue_say("De acuerdo, dejo de mirar la pantalla.")
        elif action == "audio_describe":
            # YUE dice qué está sonando y, si hay contenido, da su impresión.
            self._describe_audio()
        elif action == "camera_status":
            self._yue_say(self.camera.describe())
        elif action == "show_goals":
            self._handle_command("/metas")
        elif action == "autonomy_toggle":
            self._toggle_autonomy()
        elif action == "head_control_on":
            self._set_head_control(True)
        elif action == "head_control_off":
            self._set_head_control(False)
        elif action == "help":
            self._handle_command("/help")

    def _set_head_control(self, on: bool):
        """Activa/pausa el control del cursor con la cabeza (voz o comando).

        Enciende la cadencia rápida de la cámara solo mientras está activo, y
        habla desde el carácter de YUE. Respeta el interruptor maestro de config.
        """
        if not bool(getattr(config, "HEAD_CONTROL_ENABLED", True)):
            self._yue_say("El control por cabeza está desactivado en la configuración.")
            return
        if not getattr(self, "head_control", None):
            self._yue_say("El control por cabeza no está disponible ahora mismo.")
            return
        if on:
            if not self.camera.active:
                self._yue_say("Necesito la cámara encendida para moverte el cursor con la cabeza.")
                return
            self.head_control.enable()
            self.camera.set_fast_mode(True)
            self._yue_say(
                "Bien… control por cabeza activado. Mira de frente un segundo mientras "
                "te calibro. Para hacer clic, deja el cursor quietecito un momento."
            )
        else:
            self.head_control.disable()
            self.camera.set_fast_mode(False)
            self._yue_say("Listo, solté el cursor. Control por cabeza desactivado.")

    def _on_vision_status(self, text, active):
        """Estado del sistema de visión por cámara (llega en el hilo de la UI).

        Aditivo y silencioso: solo registra en consola. Si quieres verlo en el
        chat, cambia el print por self._yue_say(text).
        """
        try:
            print(f"[vision-mp] {'ON' if active else 'off'}: {text}")
        except Exception:
            pass
        # ADITIVO: el indicador de cámara del avatar sigue el estado real de la
        # visión nueva, no solo el del observador clásico.
        try:
            if getattr(self, "pet", None) is not None:
                self.pet.set_camera_active(bool(active))
        except Exception:
            pass

    # ==================================================================
    # DEPURACIÓN: inspeccionar por qué YUE hizo lo que hizo
    # ==================================================================
    def debug_state(self, imprimir=True):
        """Foto completa del cerebro central: los tres estados y el arbitraje.

        Muestra USER STATE, YUE STATE, SYSTEM STATE, la propuesta ganadora, las
        propuestas activas con su TTL restante y las observaciones vivas de cada
        sensor con su confianza ponderada.

        Es la herramienta para cuando YUE ponga una cara rara y no se sepa por
        qué: aquí se ve quién la pidió, con qué prioridad y cuánto le queda.

            >>> app.debug_state()
        """
        gestor = getattr(self, "state_manager", None)
        if gestor is None:
            if imprimir:
                print("[estado] el gestor central no está disponible.")
            return {}
        try:
            foto = gestor.debug_snapshot()
        except Exception as exc:
            print("[estado] no pude tomar la foto:", exc)
            return {}
        if not imprimir:
            return foto

        u, y, s = foto["user"], foto["yue"], foto["system"]
        print("\n" + "=" * 62)
        print("  ESTADO DE YUE  ·  " + foto["summary"])
        print("=" * 62)
        print(f"  USUARIO   {u['emotion']}"
              f"{'/' + u['secondary_emotion'] if u['secondary_emotion'] else ''}"
              f"  conf={u['confidence']}  fuente={u['dominant_source']}"
              f"  explícito={u['explicit']}")
        print(f"            necesidad={u['need']}  tendencia={u['trend']}"
              f"  sostenido={u['sustained']}  riesgo={u['safety_level']}")
        print(f"            confianza por fuente → texto={u['text_confidence']}"
              f"  voz={u['voice_confidence']}  cámara={u['camera_confidence']}")
        print(f"  YUE       {y['emotion']} ({y['intensity']})"
              f"  comportamiento={y['behavior']}  voz={y['voice_style']}")
        print(f"            iniciativa={y['initiative']}  avatar={y['avatar_state']}"
              f"  ganador={y['source']} (p{y['priority']})  ttl={y['ttl_remaining']}")
        if y.get("reason"):
            print(f"            motivo: {y['reason']}")
        print(f"  SISTEMA   micro={s['mic']}  cámara={s['camera']}  voz={s['voice']}"
              f"  modo={s['mode']}  actividad={s['activity']}")
        print(f"            multimedia={s['media_playing']}  pc={s['pc_busy']}"
              f"  visión={s['vision_busy']}  autonomía={s['autonomy_busy']}")

        print("  ── PROPUESTAS ACTIVAS " + "─" * 39)
        if not foto["proposals"]:
            print("     (ninguna: YUE en reposo)")
        for p in foto["proposals"]:
            marca = "►" if p["source"] == foto["winner"] else " "
            print(f"   {marca} p{p['priority']:<4} {p['source']:<14}"
                  f" {p['emotion']:<10} {p['behavior']:<14}"
                  f" ttl={p['ttl_remaining']}")

        print("  ── OBSERVACIONES VIVAS " + "─" * 38)
        if not foto["observations"]:
            print("     (ninguna)")
        for o in foto["observations"]:
            print(f"     {o['source']:<8} {o['emotion']:<12}"
                  f" bruta={o['confidence']:<6} ponderada={o['effective']:<6}"
                  f" explícito={o['explicit']}  hace {o['age_s']}s")
        try:
            from core import voice_affect
            if not voice_affect.AVAILABLE:
                print("  nota: " + voice_affect.describe())
        except Exception:
            pass
        print("=" * 62 + "\n")
        return foto

    # ==================================================================
    # ADITIVO: estado vivo de percepción visual (punto 10)
    # ==================================================================
    def vision_state(self):
        """El `vision_state` que puede consultar CUALQUIER parte de YUE.

        Devuelve SIEMPRE un diccionario con la forma completa (personas,
        emociones, objetos, gestos, texto, postura, mirada, escena, cámara y
        marca de actualización), incluso con la visión apagada. Así quien lo
        use no necesita comprobar None ni claves ausentes:

            estado = self.vision_state()
            if estado["personas"]["hay_persona"] and estado["mirada"]["mira_a_yue"]:
                ...
        """
        sistema = getattr(self, "vision_mp", None)
        if sistema is not None:
            try:
                return sistema.vision_state()
            except Exception as exc:
                print("[vision] no pude leer el estado visual:", exc)
        from vision.live_state import _estado_vacio
        return _estado_vacio()

    def vision_resumen(self):
        """Una línea en español con lo que YUE ve ahora mismo."""
        sistema = getattr(self, "vision_mp", None)
        if sistema is None:
            return "El sistema de visión por cámara está apagado."
        try:
            return sistema.resumen_visual()
        except Exception:
            return "No consigo leer el estado de la visión."

    def _handle_command(self, text):
        parts = text.split(" ", 1)
        command = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if command == "/help":
            self._yue_say("Usa /pc seguido de una orden, /mira, /camara, /recuerda, /recuerdos, /meta, /metas, /vinculo, /autonomia, /animo, /animo_historial, /cabeza, /rutinas, /actividad, /reescanear_apps, /modo, /reporte, /diagvision o /diagvoz.")
        elif command in {"/actividad", "/bitacora"}:
            self._show_activity()
        elif command in {"/reescanear_apps", "/reescanear-apps", "/apps"}:
            self.chat.set_status("Reescaneando aplicaciones instaladas…")
            worker = AppScanWorker(self.pc)
            worker.done.connect(lambda result: (
                self.chat.set_status(""),
                self._yue_say(f"Catálogo actualizado: encontré {result.get('count', 0)} aplicaciones.")
            ))
            worker.failed.connect(lambda error: (
                self.chat.set_status(""),
                self._yue_say(f"No pude reescanear las aplicaciones: {error}")
            ))
            self.controller_ctx.workers.track(worker)
        elif command in {"/rutinas", "/rutina"}:
            arg_l = (arg or "").strip()
            if not arg_l:
                self._list_routines()
            else:
                # "/rutinas <nombre>" ejecuta esa rutina directamente.
                self._run_routine(arg_l)
        elif command in {"/animo", "/ánimo"}:
            self._start_mood_checkin()
        elif command in {"/animo_historial", "/ánimo_historial", "/animohistorial", "/animo-historial"}:
            resumen = ""
            try:
                resumen = self.memory.get_mood_summary(7)
            except Exception as exc:
                print("[checkin] no pude leer el resumen de ánimo:", exc)
            if resumen:
                # get_mood_summary empieza en minúscula ("esta semana…"); la
                # ponemos con mayúscula inicial para leerla sola.
                self._yue_say(resumen[0].upper() + resumen[1:])
            else:
                self._yue_say(
                    "Todavía no tengo suficientes señales de tu ánimo estos días. "
                    "Cuéntame cómo estás, o usa /animo para dejarme una nota del 1 al 5."
                )
        elif command == "/pc" and arg:
            self._run_pc_order(arg)
        elif command == "/mira":
            self._glance()
        elif command in {"/diagvision", "/diagnosticovision", "/diagvisión"}:
            self._diagnose_vision()
        elif command in {"/diagvoz", "/diagmicro", "/diagmic", "/diagoido", "/diagoído"}:
            self.voice.diagnose_voice()
        elif command in {"/camara", "/cámara"}:
            # Aditivo: si el sistema de visión MediaPipe está enganchado, deja que
            # maneje on/off; si no reconoce el argumento, cae al comportamiento
            # clásico de siempre (self.camera.describe()).
            _resp = None
            try:
                from vision import commands as _vision_cmds
                _resp = _vision_cmds.handle(getattr(self, "vision_mp", None), command, arg)
            except Exception:
                _resp = None
            self._yue_say(_resp if _resp else self.camera.describe())
        elif command in {"/vision", "/visión"}:
            # Comandos del sistema de visión por cámara (MediaPipe Tasks).
            _resp = None
            try:
                from vision import commands as _vision_cmds
                _resp = _vision_cmds.handle(getattr(self, "vision_mp", None), command, arg)
            except Exception as _exc:
                _resp = f"No pude consultar la visión: {_exc}"
            self._yue_say(_resp or "El sistema de visión no está disponible.")
        elif command == "/recuerda" and arg:
            self.memory.add_fact(arg)
            self._yue_say("Lo guardé en mi memoria.")
        elif command in {"/recuerdos", "/diagmemoria", "/diagrecuerdos"}:
            self._diagnose_memory(arg)
        elif command == "/meta" and arg:
            self.memory.add_goal(arg)
            self.memory.add_bond_points(2)
            self._refresh_bond()
            self._yue_say("Meta registrada. Te ayudaré a mantenerla presente.")
        elif command == "/metas":
            goals = self.memory.list_goals()
            self._yue_say(
                "Tus metas: " + " · ".join(goal["text"] for goal in goals)
                if goals else "No tienes metas activas."
            )
        elif command == "/vinculo":
            points = self.memory.get_bond_points()
            current, next_level, _ = bonding.progress(points)
            if next_level:
                self._yue_say(
                    f"Nivel {current.index}/10. Faltan {next_level.threshold - points} puntos para avanzar."
                )
            else:
                self._yue_say("El vínculo está en el nivel máximo.")
        elif command == "/autonomia":
            if arg.lower() in {"on", "activar", "activa"} and not self.autonomy.enabled:
                self._toggle_autonomy()
            elif arg.lower() in {"off", "pausar", "desactivar"} and self.autonomy.enabled:
                self._toggle_autonomy()
            else:
                self._yue_say("La iniciativa automática está " + ("activa." if self.autonomy.enabled else "pausada."))
        elif command in {"/cabeza", "/head"}:
            arg_l = arg.lower().strip()
            if arg_l in {"off", "no", "pausa", "pausar", "desactiva", "desactivar"}:
                self._set_head_control(False)
            elif arg_l in {"on", "si", "sí", "activa", "activar", ""}:
                # Sin argumento: alterna según el estado actual.
                if not arg_l and getattr(self, "head_control", None) and self.head_control.is_active:
                    self._set_head_control(False)
                else:
                    self._set_head_control(True)
            else:
                self._set_head_control(True)
        elif command == "/modo":
            # NUEVO: consulta o cambia de modo por comando.
            if not getattr(self, "modes", None):
                self._yue_say("El sistema de modos no está disponible.")
            elif arg:
                route = self.modes.handle(arg)
                if route.switched:
                    self._apply_mode_switch(route)
                else:
                    self._yue_say(f"No reconocí ese modo. Ahora estoy en modo {self.modes.current_meta().get('label','')}.")
            else:
                meta = self.modes.current_meta()
                self._yue_say(f"Estoy en modo {meta.get('emoji','')} {meta.get('label','')}.")
        elif command == "/reporte":
            # NUEVO: exporta el progreso de la clase (md por defecto; csv/xlsx opcional).
            if not getattr(self, "teacher", None):
                self._yue_say("El modo profesora no está disponible.")
            else:
                fmt = (arg or "md").lower().strip()
                try:
                    import os
                    ruta = os.path.join(str(config.DATA_DIR), "teacher", f"reporte.{ 'xlsx' if fmt in ('xlsx','excel') else 'csv' if fmt=='csv' else 'md'}")
                    salida = self.teacher.export_report(ruta, fmt)
                    self._yue_say(f"Guardé el reporte del progreso en: {salida}")
                except Exception as exc:
                    self._yue_say("No pude generar el reporte.")
                    print("[profesora] error de reporte:", exc)
        else:
            self._yue_say("No conozco ese comando. Usa /help.")

    def _diagnose_memory(self, consulta=""):
        """NUEVO (memoria histórica): enseña QUÉ recuerdos encontraría YUE.

        `/recuerdos Andrea volvió a escribirme` responde con los recuerdos
        antiguos que entrarían al prompt y su puntuación. Sin argumento usa el
        último mensaje del usuario. Es una herramienta de diagnóstico: sale por
        el chat como texto plano (sin voz), igual que /diagvoz.
        """
        consulta = (consulta or getattr(self, "_last_user_text", "") or "").strip()
        if getattr(self, "memory_ext", None) is None:
            self.chat.show_reply(
                "La memoria histórica está desactivada "
                "(MEMORY_RELEVANCE_ENABLED=false) o no se pudo cargar.")
            return
        if not consulta:
            self.chat.show_reply(
                "Escribe algo que buscar: /recuerdos Andrea volvió a escribirme")
            return
        try:
            hits = self.memory_ext.search_history(consulta)
        except Exception as exc:
            self.chat.show_reply(f"No pude buscar en el historial: {exc}")
            return
        if not hits:
            self.chat.show_reply(
                f"Sin recuerdos por encima del umbral para «{consulta}».\n"
                "Nada de esto llegaría al prompt (que es lo correcto: mejor no "
                "recordar que recordar cualquier cosa).")
            return
        lineas = [f"Recuerdos que YUE usaría para «{consulta}»:"]
        for n, h in enumerate(hits, start=1):
            lineas.append(
                f"{n}. score={h['score']} · similitud={h['similitud']} "
                f"· {h['grado']} · {h['fecha_humana']} ({h['fecha']})\n"
                f"   [{h['role']}] {h['texto']}")
        informe = "\n".join(lineas)
        print("[memory-ext] diagnóstico:\n" + informe)
        self.chat.show_reply(informe)

    def shutdown(self):
        self.pc.cancel()
        self.speaker.stop()
        self.listener.shutdown()
        self.camera.stop()
        self.audio.stop()
        try:
            self.media_companion.stop()
        except Exception:
            pass
        # ADITIVO (Percepción V3): apaga el sistema opt-in y libera su cámara si
        # estuviera activo. Inofensivo si nunca se enganchó o está desactivado.
        try:
            if getattr(self, "vision_v3", None) is not None:
                self.vision_v3.stop()
        except Exception:
            pass
        # CORRECCIÓN (punto 15): el sistema de visión MediaPipe/percepción NO se
        # estaba deteniendo al cerrar. Sus hilos quedaban vivos y la webcam
        # tomada hasta que moría el proceso. Ahora se apaga igual que los demás.
        try:
            if getattr(self, "vision_mp", None) is not None:
                self.vision_mp.stop()
        except Exception:
            pass


def main():
    from PyQt5.QtCore import Qt

    if "--mics" in sys.argv:
        from core.listener import list_microphones
        microphones = list_microphones()
        if not microphones:
            print("No se detectaron micrófonos.")
        else:
            for index, name in microphones:
                print(f"{index}: {name}")
        return

    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    controller = Controller()
    app._ctrl = controller
    app.aboutToQuit.connect(controller.shutdown)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
