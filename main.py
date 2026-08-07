"""YUE · avatar 3D, voz interrumpible, control visual e iniciativa local."""
from __future__ import annotations

import os
import sys

def enable_dpi_awareness() -> bool:
    """Declara DPI awareness antes de crear ventanas o tomar capturas."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        # PER_MONITOR_AWARE_V2 (Windows 10 Creators Update+).
        try:
            if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return True
        except Exception:
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
            return True
        except Exception:
            pass
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            return True
        except Exception:
            return False
    except Exception:
        return False

# Se ejecuta antes de importar Qt/pyautogui para que coordenadas y capturas usen
# la escala física real en Windows 125 %, 150 %, etc.
enable_dpi_awareness()

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
        # 0) Qué proveedor/modelo de visión está resuelto (sin exponer la clave).
        try:
            info = self.engine.vision_diag_info()
            report.append((
                "Proveedor de visión",
                bool(info.get("ok")),
                f"host={info.get('host','?')} · modelo={info.get('modelo','?')} · clave={info.get('clave','?')}",
            ))
        except Exception as exc:
            report.append(("Proveedor de visión", False, str(exc)[:200]))
        # 1) Captura de pantalla.
        info_cap = vision.capture_info()
        if info_cap["ok"]:
            report.append(("Captura de pantalla", True,
                           f"{info_cap['width']}x{info_cap['height']}px, {info_cap['bytes']} bytes"))
        else:
            report.append(("Captura de pantalla", False, info_cap["note"]))
            self.done.emit(report)
            return
        # 2) Llamada real al modelo de visión.
        try:
            b64 = vision.capture_b64()
            txt = self.engine.look(
                "Eres la visión de YUE. Responde en una sola frase breve, en español.",
                b64,
                "¿Qué se ve en la pantalla? Una sola frase.",
            )
            report.append(("Modelo de visión", True, (txt or "(respuesta vacía)")[:180]))
        except Exception as exc:
            report.append(("Modelo de visión", False, str(exc)[:300]))
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
        self.pc.set_action_log_callback(self._on_pc_action_log)

        self._workers = []
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
        self._input_source = "texto"
        self._wake_re = None   # se construye la primera vez (perezoso)
        self._checkin_timer = QTimer(self)
        self._checkin_timer.setInterval(
            max(30, int(getattr(config, "CHECKIN_CHECK_INTERVAL", 90))) * 1000
        )
        self._checkin_timer.timeout.connect(self._maybe_checkin)

        self.chat.send_message.connect(self._on_text_message)
        # NUEVO: arrastrar un PDF/documento al chat -> YUE lo lee en Modo Profesora.
        self.chat.files_dropped.connect(self._on_files_dropped)
        self.pet.clicked.connect(self.toggle_chat)
        self.pet.moved.connect(lambda: self.chat.reposition(self.pet))
        self.pet.request_quit.connect(QApplication.instance().quit)
        self.pet.toggle_voice.connect(self._toggle_voice)
        self.pet.toggle_mic.connect(self._toggle_mic)
        self.pet.toggle_autonomy.connect(self._toggle_autonomy)
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
        self.listener.barge_in.connect(self._on_barge_in)
        self.listener.heard.connect(self._on_heard)
        self.listener.status.connect(self._on_mic_status)
        # NUEVO (accesibilidad): la pregunta de confirmación se habla SIEMPRE en
        # el hilo principal (voz + UI), aunque la pida el hilo del PCWorker.
        self.confirm_request.connect(self._do_confirm_ask)
        self.confirm_notify.connect(self._yue_say)
        # NUEVO: buffer de letra/diálogo oído mientras suena media, para que YUE
        # pueda comentar la canción/el vídeo con su contenido real.
        self._media_heard_buffer = []   # lista de (timestamp, texto)
        self.listener.media_heard.connect(self._on_media_heard)
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
            self.vision_mp = vision_mp.attach(self)
        except Exception as exc:
            print("[vision-mp] no se pudo enganchar el sistema de visión:", exc)
            self.vision_mp = None

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
        state = emotion.infer_conversation_state(user_context or self._last_user_text, text)
        self.pet.set_emotion(state.name, state.intensity, state.duration_ms)
        # NUEVO: por defecto YUE solo habla. Muestra el texto únicamente si se
        # pidió (CHAT_MOSTRAR_RESPUESTAS) o si la voz está apagada (para no callar).
        mostrar = bool(getattr(config, "CHAT_MOSTRAR_RESPUESTAS", False)) or not self.speaker.enabled
        if mostrar:
            self.chat.show_reply(clean)
        if self.speaker.enabled:
            self.listener.set_tts_text(clean)
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
                lyrics = self._recent_lyrics()
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

        # NUEVO (memoria a largo plazo): añadimos el recuerdo consolidado al final,
        # después del contexto de cámara/audio, sin tocar nada de lo anterior.
        if recuerdo_largo:
            prompt += recuerdo_largo
        return prompt

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
            # Requiere inactividad real (misma lógica que la iniciativa autónoma).
            idle = time.time() - self._last_user_activity
            if idle < config.AUTONOMY_IDLE_SECONDS:
                return
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
            stripped = self._strip_wake_word(text)
            if stripped is None:
                self._wake_ignored(text)
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
        reaction = emotion.infer_reaction_to_user(text)
        self.pet.set_emotion(reaction.name, reaction.intensity, reaction.duration_ms)
        # NUEVO (memoria emocional): registramos, sin ruido, el tono del mensaje
        # del usuario. Es memoria de fondo (mood_log): solo etiqueta, intensidad
        # y hora. Se ignora lo neutro para que el recuerdo tenga sentido.
        try:
            mood = emotion.infer_emotion_state(text)
            if mood.name != "neutral":
                self.memory.add_mood("texto", mood.name, mood.intensity, text)
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

        # Riesgo por TEXTO (palabras explícitas) tiene prioridad absoluta. Si no
        # lo hay, comprobamos la señal VISUAL / de ánimo SOSTENIDA (cámara +
        # mood_log) con antirrebote, para que YUE pregunte con suavidad cómo está
        # el usuario sin convertirlo en una alarma que salte en cada mensaje.
        if safety.detect_risk(text):
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
        self._track_worker(worker)

    def _on_ai_done(self, request_id, user_text, text):
        if request_id != self._chat_request_id:
            return
        self.chat.set_status("")
        clean = emotion.clean_response(text)
        self.memory.add_message("assistant", clean)
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
            # Un pequeño cambio de expresión, sin tocar la identidad.
            if event.new_mode == MODE_TEACHER:
                self.pet.set_emotion("focused", 0.6, 2600)
            else:
                self.pet.set_emotion("happy", 0.55, 2400)
                # NUEVO: al volver al modo compañera, YUE deja de observar al
                # profesor y de participar en clase (§4/§8). Aditivo y seguro.
                try:
                    if getattr(self, "pedagogy", None) is not None:
                        self.pedagogy.set_observing(False)
                    if getattr(self, "teacher_participation", None) is not None:
                        self.teacher_participation.set_active(False)
                except Exception:
                    pass

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
        self._track_worker(worker)

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
            self._track_worker(worker)
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
        self._track_worker(worker)

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
        self._track_worker(worker)

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
        self._track_worker(worker)

    # ---------- visión de pantalla ----------
    def _glance(self):
        """Observación explícita; la visión automática permanece silenciosa."""
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
        # ADITIVO: si se pidió visión-solo-OCR (o no hay modelo multimodal válido),
        # leemos la pantalla por OCR y respondemos con el modelo de chat. Así YUE
        # "mira" sin depender de una clave de visión (perfecto para PDFs y texto).
        usar_ocr = bool(getattr(config, "VISION_OCR_ONLY", False))
        if usar_ocr:
            instruccion_ocr = (
                "Vas a mirar la pantalla del usuario a través del texto que hay en "
                "ella (leído por OCR). Responde en español, breve y útil, a su "
                "petición. Ignora menús o barras del sistema si no vienen a cuento. "
                "Si el texto no basta para responder, dilo con naturalidad."
            )
            worker = OcrVisionWorker(self.engine, self._system_prompt(current), instruccion_ocr)
        else:
            worker = VisionWorker(self.engine, self._system_prompt(current), instruction)
        worker.done.connect(self._on_vision_done)
        worker.failed.connect(self._on_vision_failed)
        self._track_worker(worker)

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
        self._track_worker(worker)

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
        self._track_worker(worker)

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
        # NUEVO (lectura de emociones): ESPEJO EMPÁTICO. Si YUE ve con claridad
        # cómo te sientes, ACOMPAÑA con la cara del avatar (si te ve triste se
        # preocupa; no te imita el enfado). No habla sola: solo pone gesto.
        if not bool(getattr(config, "CAMERA_EMPATHY_ENABLED", True)):
            return
        emociones = getattr(observation, "person_emotions", ()) or ()
        if not emociones:
            return
        # Cede la cara cuando ya la gobierna otra cosa (música/vídeo sonando, una
        # orden de PC/visión en curso, o mientras YUE habla).
        try:
            if (getattr(self.audio, "media_playing", False) or self._pc_busy
                    or self._vision_busy or self.speaker.is_speaking):
                return
        except Exception:
            pass
        # Nos quedamos con la emoción no neutra de mayor confianza.
        fuertes = [e for e in emociones if getattr(e, "key", "neutral") != "neutral"]
        if not fuertes:
            return
        mejor = max(fuertes, key=lambda e: getattr(e, "confidence", 0.0))
        if getattr(mejor, "confidence", 0.0) < float(
            getattr(config, "CAMERA_EMPATHY_MIN_CONFIDENCE", 0.55)
        ):
            return
        try:
            from core import face_emotion
            espejo = face_emotion.empathic_avatar_emotion(mejor.key)
        except Exception:
            espejo = None
        if not espejo:
            return
        # Cuentagotas: solo al cambiar de ánimo y no más de una vez cada X seg.
        ahora = time.time()
        ultima_key = getattr(self, "_last_empathy_key", "")
        ultima_at = float(getattr(self, "_last_empathy_at", 0.0))
        gap = float(getattr(config, "CAMERA_EMPATHY_MIN_GAP", 8.0))
        if mejor.key == ultima_key and (ahora - ultima_at) < gap:
            return
        self._last_empathy_key = mejor.key
        self._last_empathy_at = ahora
        nombre, inten, dur = espejo
        try:
            self.pet.set_emotion(nombre, inten, dur)
        except Exception as exc:
            print("[camara] no pude aplicar el espejo empático:", exc)

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
            self._track_worker(worker)
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

    def _on_media_heard(self, text):
        """Guarda la letra/diálogo que YUE oye mientras suena media (rolling)."""
        text = (text or "").strip()
        if not text:
            return
        ahora = time.time()
        buf = getattr(self, "_media_heard_buffer", None)
        if buf is None:
            self._media_heard_buffer = buf = []
        buf.append((ahora, text))
        # Nos quedamos con lo oído en los últimos ~3 min y como mucho 25 fragmentos.
        corte = ahora - 180.0
        self._media_heard_buffer = [(t, s) for (t, s) in buf if t >= corte][-25:]

    def _recent_lyrics(self, max_age: float = 150.0, max_chars: int = 600) -> str:
        """Texto reciente oído del audio (letra/diálogo), para dar contexto a YUE."""
        ahora = time.time()
        buf = getattr(self, "_media_heard_buffer", []) or []
        trozos = [s for (t, s) in buf if ahora - t <= max_age]
        if not trozos:
            return ""
        # De lo más reciente hacia atrás, sin pasarnos de longitud.
        salida = []
        total = 0
        for s in reversed(trozos):
            if total + len(s) > max_chars:
                break
            salida.append(s)
            total += len(s)
        return " … ".join(reversed(salida)).strip()

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

        lyrics = self._recent_lyrics()

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
        self._track_worker(worker)

    def _on_audio_status(self, text, active):
        print(f"[audio] {'activo' if active else 'inactivo'}: {text}")

    def _on_audio_reaction(self, reaction):
        """Compatibilidad con el reactor clásico cuando el companion está apagado."""
        if getattr(self, "media_companion", None) is not None and self.media_companion.enabled:
            return
        if not isinstance(reaction, Reaction):
            return
        # La expresión va en directo, aunque no diga nada.
        self.pet.set_emotion(reaction.emotion, reaction.intensity, reaction.duration_ms)
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
        self.pet.set_emotion(reaction.emotion, reaction.intensity, reaction.duration_ms)
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
        self.pet.set_emotion("focused", 0.95, 9000)
        worker = PCWorker(self.pc, self.engine, instruction)
        worker.progress.connect(self.chat.set_status)
        worker.done.connect(self._on_pc_done)
        worker.failed.connect(self._on_pc_failed)
        self._track_worker(worker)

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
        self._track_worker(worker)

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
        self.pet.set_emotion("focused", 0.9, 6000)
        worker = PCRecoveryWorker(self.pc, "repeat")
        worker.progress.connect(self.chat.set_status)
        worker.done.connect(self._on_repeat_done)
        worker.failed.connect(self._on_recovery_failed)
        self._track_worker(worker)

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
        self.pet.set_emotion("focused", 0.9, 8000)
        worker = PCRecoveryWorker(self.pc, "routine", actions=rutina["pasos"], label=rutina["nombre"])
        worker.progress.connect(self.chat.set_status)
        worker.done.connect(self._on_routine_done)
        worker.failed.connect(self._on_recovery_failed)
        self._track_worker(worker)

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
        self._track_worker(worker)

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
            self._glance()
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

    def _handle_command(self, text):
        parts = text.split(" ", 1)
        command = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if command == "/help":
            self._yue_say("Usa /pc seguido de una orden, /mira, /camara, /recuerda, /meta, /metas, /vinculo, /autonomia, /animo, /animo_historial, /cabeza, /rutinas, /actividad, /reescanear_apps, /modo, /reporte, /diagvision o /diagvoz.")
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
            self._track_worker(worker)
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
            self._diagnose_voice()
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

    # ---------- voz y micrófono ----------
    def _toggle_voice(self):
        enabled = self.speaker.toggle()
        if enabled:
            self._yue_say("Voz activada.")
        else:
            self.chat.show_reply("Voz desactivada.")

    def _toggle_mic(self):
        enabled = self.listener.toggle()
        self._yue_say("Micrófono activado." if enabled else "Micrófono desactivado.")

    def _diagnose_voice(self):
        """NUEVO (arreglo "no me escucha"): muestra el informe del micrófono en el
        chat y en consola, para ver de un vistazo por qué YUE no te está oyendo."""
        try:
            informe = self.listener.diagnose()
        except Exception as exc:
            informe = f"No pude diagnosticar el micrófono: {exc}"
        print("[oido] diagnóstico:\n" + informe)
        # Al chat lo mandamos como texto plano (sin TTS) para que se lea completo.
        self.chat.show_reply("Diagnóstico del micrófono:\n" + informe)

    def _on_barge_in(self, text):
        # Se ejecuta antes de heard: corta la historia/voz al instante.
        self.speaker.stop()
        self.pet.set_talking(False)
        self.pet.set_emotion("focused", 0.78, 2600)
        self.chat.set_status("Te escucho…")

    def _on_heard(self, text):
        # NUEVO (palabra de activación): marcamos que este mensaje llegó por VOZ,
        # para que, si se ignora por no empezar con «Yue», no demos aviso hablado.
        self._input_source = "voz"
        self.on_user_message(text)

    def _on_text_message(self, text):
        # NUEVO: mensaje escrito en el chat. Marcamos la fuente como TEXTO.
        self._input_source = "texto"
        self.on_user_message(text)

    # ---------- palabra de activación «Yue» ----------
    def _build_wake_re(self):
        """Compila el patrón de la palabra de activación desde config (perezoso).

        Acepta variantes frecuentes del reconocedor de voz (yue/llue/jue…) y un
        saludo opcional antes («oye Yue», «hey Yue»). Editable con WAKE_WORDS.
        """
        import re
        palabras = getattr(config, "WAKE_WORDS", "yue,yué,llue,jue,hue")
        if isinstance(palabras, str):
            palabras = [p.strip() for p in palabras.split(",") if p.strip()]
        if not palabras:
            palabras = ["yue"]
        alternativas = "|".join(re.escape(p) for p in palabras)
        # (saludo opcional) + palabra de activación + separadores (coma, dos puntos…)
        patron = (r"^\s*(?:(?:oye|oiga|hey|ey|ok|okay|escucha|disculpa)[\s,]+)?"
                  r"(?:" + alternativas + r")\b[\s,:.\-–—!¡¿?]*")
        self._wake_re = re.compile(patron, re.IGNORECASE)
        return self._wake_re

    def _strip_wake_word(self, text):
        """Si el texto empieza por «Yue», devuelve el resto (sin el prefijo).
        Si NO empieza por «Yue», devuelve None (el mensaje se ignora)."""
        rex = self._wake_re or self._build_wake_re()
        m = rex.match(text or "")
        if not m:
            return None
        return (text[m.end():]).strip()

    def _wake_ignored(self, text):
        """Mensaje ignorado por no empezar con «Yue». Aviso discreto solo si se
        escribió (para no confundir); por voz se queda callada."""
        if getattr(self, "_input_source", "texto") == "texto":
            try:
                self.chat.set_status("Empieza con «Yue…» para que te responda.")
            except Exception:
                pass
        # Por voz no decimos nada: así no reacciona a conversaciones ajenas.

    def _on_mic_status(self, message):
        if getattr(config, "DEBUG_STATUS", False):
            print(f"[oido] estado: {message}")
        if message.startswith(("no llego", "no pude", "sin ")):
            self.chat.show_reply("No te estoy oyendo bien: " + message)

    # ---------- workers ----------
    def _track_worker(self, worker):
        worker.finished.connect(
            lambda w=worker: self._workers.remove(w) if w in self._workers else None
        )
        self._workers.append(worker)
        worker.start()

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
