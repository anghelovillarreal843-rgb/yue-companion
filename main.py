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
from core import bonding, personality, safety, emotion, commands, activity
from core import memory_consolidation
from engine.controller_ctx import ControllerContext
from engine.ai_worker import AiWorker
from engine.media_director import MediaCompanionDirector
from engine.memory_proactive import MemoryProactive
from engine.vision_director import VisionDirector, VisionHostAdapter
from engine.pc_director import PCDirector
from engine.pc_worker import PCWorker, PCRecoveryWorker
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

# NUEVO: arquitectura de modos inteligentes (companion por defecto, teacher bajo
# demanda). El paquete completo de la Profesora vive en engine/teacher_director.py.
from engine.teacher_director import MODE_COMPANION, MODE_TEACHER, TeacherDirector


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
        self.autonomy = Autonomy()
        self.controller_ctx = ControllerContext()  # PR 5.1: workers viven en ctx
        self._camera_bridge = CameraBridge()
        # El CameraObserver y el head_control los crea/engancha setup_camera
        # (PR 5.7): el observador vive en el VisionDirector y publica su
        # registro en ctx.camera.
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

        self.vision_director = VisionDirector(self.controller_ctx)
        self.setup_camera()
        self._floaters = []
        # El módulo de lecciones necesita el LLM para reflexionar sobre los fallos.
        try:
            from core.learning import integration as learning
            learning.bind_engine(self.engine)
        except Exception as exc:
            print("[aprendizaje] paquete no disponible:", exc)
        self._vision_on = bool(config.VISION_ENABLED)
        self._autonomy_started_at = 0.0
        self.controller_ctx.chat_request_id = 0

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
        # PR 5.4: PCDirector (~16 métodos del paquete PC) vive en engine/.
        self.controller_ctx.pc = self.pc
        self.controller_ctx.engine = self.engine
        self.controller_ctx.memory = self.memory
        self.controller_ctx.pc_busy = False
        self.controller_ctx.pc_confirm = None
        self.controller_ctx.confirm_speak = self.confirm_request.emit
        self.controller_ctx.confirm_notify = self.confirm_notify.emit
        self.controller_ctx.user_float = self._user_float
        self.controller_ctx.list_routines = self._list_routines
        # PR 5.3: MemoryProactive (13 métodos de memoria/ánimo) vive en engine/.
        self.controller_ctx.system_prompt = self._system_prompt
        self.controller_ctx.autonomy = self.autonomy
        self.controller_ctx.audio = self.audio
        self.controller_ctx.episodic = getattr(self, "episodic", None)
        self.controller_ctx.story_memory = getattr(self, "story_memory", None)
        self.controller_ctx.memory_ext = getattr(self, "memory_ext", None)
        self.controller_ctx.pending_checkin = False
        self.controller_ctx.last_user_activity = time.time()
        self.controller_ctx.last_companion = None
        self.controller_ctx.last_user_text = ""
        self.controller_ctx.autonomy_busy = False
        self.controller_ctx.vision_busy = False
        self.memory_proactive = MemoryProactive(self.controller_ctx)
        self.controller_ctx.memory_proactive = self.memory_proactive
        self.pc_director = PCDirector(self.controller_ctx)
        self.teacher_director = TeacherDirector(self.controller_ctx)
        # PR 5.6: hooks del cerebro central que usa el TeacherDirector.
        self.controller_ctx.apply_mode_switch = self.teacher_director.apply_mode_switch
        self.controller_ctx.ai_failed = self._on_ai_failed
        self.controller_ctx.interrupt_response = self._interrupt_response
        self.controller_ctx.glance = self._glance
        # Canal de confirmación verbal + bitácora: pc_control llama desde el
        # hilo del PCWorker; YUE pregunta por voz esperando un "sí/no".
        self.pc.set_confirm_callback(self.pc_director.pc_confirm_by_voice)
        self.pc.set_action_log_callback(self.pc_director.on_pc_action_log)
        self.voice = VoiceDirector(self.controller_ctx)
        self._checkin_timer = QTimer(self)
        self._checkin_timer.setInterval(
            max(30, int(getattr(config, "CHECKIN_CHECK_INTERVAL", 90))) * 1000
        )
        self._checkin_timer.timeout.connect(self.memory_proactive.maybe_checkin)

        self.chat.send_message.connect(self.voice.on_text_message)
        # NUEVO: arrastrar un PDF/documento al chat -> YUE lo lee en Modo Profesora.
        self.chat.files_dropped.connect(self.teacher_director.on_files_dropped)
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
        self.confirm_request.connect(self.pc_director.do_confirm_ask)
        self.confirm_notify.connect(self.controller_ctx.say)
        # NUEVO: buffer de letra/diálogo oído mientras suena media, para que YUE
        # pueda comentar la canción/el vídeo con su contenido real.
        self.media_director = MediaCompanionDirector()
        self.listener.media_heard.connect(self.media_director.remember_heard)
        self._camera_bridge.status.connect(self.vision_director.on_camera_status)
        self._camera_bridge.observation.connect(self.vision_director.on_camera_observation)
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
            self.teacher_director.setup_modes()
        except Exception as exc:
            print("[modos] no se pudo inicializar el sistema de modos:", exc)
            self.controller_ctx.modes = None

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
        self.media_companion.start()
        self.audio.start()
        # ADITIVO: validación de la clave de visión al arrancar. Corre en segundo
        # plano (no bloquea) y, si el proveedor rechaza la clave (401/403), avisa
        # claramente en el chat en vez de fallar en silencio más tarde.
        # NUEVO (memoria a largo plazo): al arrancar, en un hilo de fondo y a baja
        # frecuencia, consolidamos el historial viejo en un resumen persistente.
        # Best-effort: si no toca o no hay clave de IA, no hace nada. Nunca bloquea
        # el arranque ni el chat.
        QTimer.singleShot(8000, self.memory_proactive.schedule_memory_consolidation)

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
            self.vision_mp = vision_mp.attach(VisionHostAdapter(self.vision_director))
            self.controller_ctx.vision_mp = self.vision_mp
            self.vision_director.check_legacy_migration()
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
    def setup_camera(self):
        """PR 5.7: reenganchado y arranque del sistema de cámara. El VisionDirector
        hace el reenganche fino (ctx, bridges, migración si VISION_REPLACE_LEGACY)
        y el maestro arranca la cámara física y agenda el preflight de visión."""
        self.vision_director.setup_camera(
            observation_callback=self._camera_bridge.observation.emit,
            status_callback=self._camera_bridge.status.emit,
        )
        # NUEVO (accesibilidad): control del cursor con la cabeza. Reutiliza los
        # landmarks de la MISMA cámara y clica por el camino seguro de pc_control
        # (solo clic izquierdo; nunca puede disparar acciones bloqueadas). Se
        # aparta si hay una orden de PC en curso, para no pelear por el ratón.
        self.head_control = HeadCursorController(
            click_fn=self.pc.safe_click,
            is_blocked=lambda: self.controller_ctx.pc_busy,
        )
        if bool(getattr(config, "HEAD_CONTROL_ENABLED", True)):
            self.camera.set_landmark_consumer(self.head_control.process_landmarks)
        self.camera.start()
        QTimer.singleShot(1500, self._vision_preflight)


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
        profesora = bool(getattr(getattr(self.controller_ctx, "teacher", None), "is_active", False))

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
        self.controller_ctx.chat_request_id += 1
        # NUEVO: cualquier interrupción cancela un auto-avance de página pendiente.
        self.controller_ctx.teacher_autoadvance_pending = False

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
        resultado = self.controller_ctx.last_companion
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
            resultado = self.controller_ctx.last_companion
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

    # ---------- mensajes ----------
    def on_user_message(self, text):
        text = (text or "").strip()
        if not text:
            return

        # NUEVO (accesibilidad): si hay una confirmación de acción irreversible
        # esperando, este mensaje (voz o texto) es la respuesta sí/no. Se resuelve
        # aquí y no sigue por el flujo normal.
        if self.controller_ctx.pc_confirm is not None:
            self.pc_director.resolve_pc_confirmation(text)
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
        request_id = self.controller_ctx.chat_request_id
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
        if self.controller_ctx.pending_checkin:
            if self.memory_proactive.resolve_mood_checkin(text):
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
        self.controller_ctx.last_companion = None
        if getattr(self, "brain", None) is not None:
            try:
                contexto_reciente = self.memory.recent_messages(4)
            except Exception:
                contexto_reciente = None
            try:
                self.controller_ctx.last_companion = self.brain.process(
                    text,
                    recent_messages=contexto_reciente,
                    external_signal=self.memory_proactive.external_mood_signal(),
                )
            except Exception as exc:
                print("[affect] fallo el análisis afectivo, sigo con el clásico:", exc)
                self.controller_ctx.last_companion = None

        if self.controller_ctx.last_companion is not None:
            resultado = self.controller_ctx.last_companion
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
        if self.controller_ctx.modes is not None:
            route = self.controller_ctx.modes.handle(text)
            if route.switched:
                self.controller_ctx.apply_mode_switch(route)
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
        if self.controller_ctx.modes is not None and self.controller_ctx.modes.current_mode() == MODE_TEACHER:
            self.teacher_director.teacher_process(text)
            return

        if looks_like_pc_command(text):
            self.pc_director.run_pc_order(text)
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
        self.memory_proactive.episodic_observe(text)

        # Riesgo por TEXTO. Con el cerebro afectivo, la evaluación ya viene
        # hecha y es GRADUADA (NINGUNO/LEVE/MODERADO/ALTO/CRÍTICO) porque usa
        # core.safety_ext, que hasta ahora estaba escrito pero sin conectar. Se
        # conservan los niveles, la negación, los factores protectores y el
        # contexto: nada se reduce a un booleano.
        #
        # Si el cerebro no está, se usa el detector clásico exactamente igual
        # que antes.
        directive = None
        if self.controller_ctx.last_companion is not None:
            resultado = self.controller_ctx.last_companion
            if resultado.should_log_risk_event:
                try:
                    self.memory.add_risk_event()
                except Exception as exc:
                    print("[bienestar] no pude registrar el evento de riesgo:", exc)
            if resultado.safety_directive:
                directive = resultado.safety_directive
            elif self.memory_proactive.should_check_visual_risk():
                directive = safety.safety_directive_visual(config.CRISIS_RESOURCES)
        elif safety.detect_risk(text):
            # Deja constancia (solo la hora) para el recordatorio de bienestar:
            # detectar un patrón de riesgo repetido sin conservar nada sensible.
            try:
                self.memory.add_risk_event()
            except Exception as exc:
                print("[bienestar] no pude registrar el evento de riesgo:", exc)
            directive = safety.safety_directive(config.CRISIS_RESOURCES)
        elif self.memory_proactive.should_check_visual_risk():
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
        self.memory_proactive.episodic_note_response(clean)
        # NUEVO (memoria narrativa): la misma etiqueta corta se anota también en
        # los acontecimientos de historia que se acaban de tocar, para que la
        # historia guarde la parte de YUE y no solo la de él.
        self.memory_proactive.story_note_response(clean)
        # Pasamos el texto CRUDO: _yue_say limpia para hablar, pero infiere la
        # emoción del avatar con toda la señal del modelo (no doble-limpiada).
        self._yue_say(text, user_text)

    def _on_ai_failed(self, request_id, error):
        if request_id != self.controller_ctx.chat_request_id:
            return
        self.chat.set_status("")
        self._yue_say("No logro conectarme con mi motor de IA. Revisa la clave de Groq y tu conexión.")
        print("[IA] error:", error)

    # ---------- visión de pantalla ----------
    # ---------- visión de pantalla (delegada al VisionDirector) ----------
    def _glance(self, pregunta: str = ""):
        self.vision_director.glance(pregunta=pregunta)

    def _diagnose_vision(self):
        self.vision_director.diagnose_vision()
    # ---------- preflight de visión (al arrancar) ----------
    def _vision_preflight(self):
        self.vision_director.vision_preflight()

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
        if (self.chat.is_user_composing() or self.controller_ctx.pc_busy or self.controller_ctx.vision_busy
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
            teacher_mode = self.controller_ctx.modes is not None and self.controller_ctx.modes.current_mode() == MODE_TEACHER
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
            teacher_mode = self.controller_ctx.modes is not None and self.controller_ctx.modes.current_mode() == MODE_TEACHER
        except Exception:
            pass
        if (self.chat.is_user_composing() or self.controller_ctx.pc_busy or self.controller_ctx.vision_busy
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
        if self.controller_ctx.autonomy_busy or self.controller_ctx.pc_busy or self.controller_ctx.vision_busy:
            return
        if not self.autonomy.can_create() or self.chat.is_user_composing():
            return
        # NUEVO: la iniciativa autónoma también respeta el estado de YUE. Si el
        # cerebro central decidió que toca acompañar en silencio, no se generan
        # aportes «útiles» por encima de eso.
        if not self.memory_proactive.yue_may_take_initiative("medium"):
            return
        idle = time.time() - self._last_user_activity
        if idle < config.AUTONOMY_IDLE_SECONDS:
            return

        self.controller_ctx.autonomy_busy = True
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
        self.controller_ctx.autonomy_busy = False
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
        self.controller_ctx.autonomy_busy = False
        self.chat.set_status("")
        print("[autonomia] error:", error)

    # ---------- acciones internas y comandos ----------
    def _run_internal_action(self, action, arg):
        if action == "stop_current":
            self.pc.cancel()
            self.speaker.stop()
            self.controller_ctx.chat_request_id += 1
            self.chat.set_status("")
            self._yue_say("Me detuve. Te escucho.")
        elif action == "pc_undo":
            self.pc_director.undo_last_pc()
        elif action == "pc_repeat":
            self.pc_director.repeat_last_pc()
        elif action == "routine_save":
            self._save_routine(commands.routine_name(arg))
        elif action == "routine_run":
            self.pc_director.run_routine(commands.routine_name(arg))
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
            self.memory_proactive.start_mood_checkin()
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
            self.pc_director.run_pc_order(arg)
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
            self.memory_proactive.diagnose_memory(arg)
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
            if not getattr(self.controller_ctx, "modes", None):
                self._yue_say("El sistema de modos no está disponible.")
            elif arg:
                route = self.controller_ctx.modes.handle(arg)
                if route.switched:
                    self.controller_ctx.apply_mode_switch(route)
                else:
                    self._yue_say(f"No reconocí ese modo. Ahora estoy en modo {self.controller_ctx.modes.current_meta().get('label','')}.")
            else:
                meta = self.controller_ctx.modes.current_meta()
                self._yue_say(f"Estoy en modo {meta.get('emoji','')} {meta.get('label','')}.")
        elif command == "/reporte":
            # NUEVO: exporta el progreso de la clase (md por defecto; csv/xlsx opcional).
            if not getattr(self.controller_ctx, "teacher", None):
                self._yue_say("El modo profesora no está disponible.")
            else:
                fmt = (arg or "md").lower().strip()
                try:
                    import os
                    ruta = os.path.join(str(config.DATA_DIR), "teacher", f"reporte.{ 'xlsx' if fmt in ('xlsx','excel') else 'csv' if fmt=='csv' else 'md'}")
                    salida = self.controller_ctx.teacher.export_report(ruta, fmt)
                    self._yue_say(f"Guardé el reporte del progreso en: {salida}")
                except Exception as exc:
                    self._yue_say("No pude generar el reporte.")
                    print("[profesora] error de reporte:", exc)
        else:
            self._yue_say("No conozco ese comando. Usa /help.")

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
