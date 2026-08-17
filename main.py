"""YUE · avatar 3D, voz interrumpible, control visual e iniciativa local."""
from __future__ import annotations

import os
import sys

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
from engine.autonomy_director import AutonomyDirector
from engine.dialogue_director import DialogueDirector
from engine.emotion_orchestrator import EmotionOrchestrator
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
            self._state_timer.start()

        self._autonomy_timer = QTimer(self)
        self._autonomy_timer.setInterval(max(60, config.AUTONOMY_INTERVAL) * 1000)

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
        # PR 5 paso 10: DialogueDirector (dueño del dialogo y canales de voz/status)
        self.dialogue_director = DialogueDirector(self.controller_ctx)
        self.controller_ctx.user_message = self.dialogue_director.on_user_message
        # PR 5.4: PCDirector (~16 métodos del paquete PC) vive en engine/.
        self.controller_ctx.pc = self.pc
        self.controller_ctx.engine = self.engine
        self.controller_ctx.brain = self.brain
        self.controller_ctx.memory = self.memory
        self.controller_ctx.pc_busy = False
        self.controller_ctx.pc_confirm = None
        self.controller_ctx.confirm_speak = self.confirm_request.emit
        self.controller_ctx.confirm_notify = self.confirm_notify.emit
        self.controller_ctx.user_float = self._user_float
        self.controller_ctx.list_routines = self._list_routines
        # PR 5.3: MemoryProactive (13 métodos de memoria/ánimo) vive en engine/.
        self.controller_ctx.system_prompt = self.dialogue_director._system_prompt
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
        self.autonomy_director = AutonomyDirector(self.controller_ctx)
        self.emotion_orchestrator = EmotionOrchestrator(self.controller_ctx)
        # Canales de emoción (PR 5 paso 9): el orquestador publica avatar_emotion;
        # el maestro reengancha el latido AHORA que el orquestador ya existe, y
        # publica system_context (lo lee el VisionDirector: glance y checkin).
        if getattr(self, "_state_timer", None) is not None:
            self._state_timer.timeout.connect(self.emotion_orchestrator.state_tick)
        if getattr(self, "_autonomy_timer", None) is not None:
            self._autonomy_timer.timeout.connect(
                self.autonomy_director.autonomous_create)
        self.controller_ctx.system_context = (
            lambda: self.emotion_orchestrator.refresh_bond())
        # Canales de composición (PR 5 paso 8): el AutonomyDirector se comunica
        # con estos dueños SOLO por el ctx, sin acoplamiento director->director.
        self.controller_ctx.pc_director = self.pc_director
        self.controller_ctx.autonomy_director = self.autonomy_director
        self.controller_ctx.vision_director = self.vision_director
        # PR 5 paso 10.2: canales del núcleo conversacional (dueño DialogueDirector)
        self.controller_ctx.media_companion = self.media_companion
        self.controller_ctx.teacher_director = self.teacher_director
        self.controller_ctx.wake_word_enabled = getattr(self, "_wake_word_enabled", True)
        self.controller_ctx.handle_command = self.dialogue_director.handle_command
        self.controller_ctx.autonomy_timer = self._autonomy_timer
        # PR 5.6: hooks del cerebro central que usa el TeacherDirector.
        self.controller_ctx.apply_mode_switch = self.teacher_director.apply_mode_switch
        self.controller_ctx.ai_failed = self.dialogue_director._on_ai_failed
        self.controller_ctx.interrupt_response = self._interrupt_response
        self.controller_ctx.glance = self._glance
        # Canal de confirmación verbal + bitácora: pc_control llama desde el
        # hilo del PCWorker; YUE pregunta por voz esperando un "sí/no".
        self.pc.set_confirm_callback(self.pc_director.pc_confirm_by_voice)
        self.pc.set_action_log_callback(self.pc_director.on_pc_action_log)
        self.voice = VoiceDirector(self.controller_ctx)
        self.controller_ctx.voice = self.voice
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
        self.pet.toggle_autonomy.connect(self.autonomy_director.toggle_autonomy)
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
        # PR 5.11.2: ahora recibe el ctx (publica ctx.describe_audio).
        self.media_director = MediaCompanionDirector(self.controller_ctx)
        # PR 5.10.2: canal del director (creado aqui, tras media_director, no antes).
        self.controller_ctx.media_director = self.media_director
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
        self.emotion_orchestrator.refresh_bond()
        self.dialogue_director._greet()
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
        self.controller_ctx.head_control = self.head_control
        if bool(getattr(config, "HEAD_CONTROL_ENABLED", True)):
            self.controller_ctx.camera.set_landmark_consumer(self.head_control.process_landmarks)
        self.controller_ctx.camera.start()
        QTimer.singleShot(1500, self._vision_preflight)


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


    # ---------- mensajes ----------
    # ---------- visión de pantalla ----------
    # ---------- visión de pantalla (delegada al VisionDirector) ----------
    def _glance(self, pregunta: str = ""):
        self.vision_director.glance(pregunta=pregunta)

    # ---------- preflight de visión (al arrancar) ----------
    def _vision_preflight(self):
        self.vision_director.vision_preflight()

    def _on_audio_status(self, text, active):
        print(f"[audio] {'activo' if active else 'inactivo'}: {text}")

    def _on_audio_reaction(self, reaction):
        """Compatibilidad con el reactor clásico cuando el companion está apagado."""
        if getattr(self, "media_companion", None) is not None and self.media_companion.enabled:
            return
        if not isinstance(reaction, Reaction):
            return
        # La expresión va en directo, aunque no diga nada.
        self.emotion_orchestrator.set_avatar_emotion(reaction.emotion, reaction.intensity, reaction.duration_ms,
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
        self.emotion_orchestrator.set_avatar_emotion(reaction.emotion, reaction.intensity, reaction.duration_ms,
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
    def _list_routines(self, prefijo=""):
        """«Mis rutinas»: lista las guardadas."""
        rutinas = self.memory.list_routines()
        if not rutinas:
            self.controller_ctx.say(
                "Aún no tienes rutinas guardadas. Cuando haga algo por ti, di "
                "«guarda esto como rutina» y un nombre, y lo reutilizamos con una frase."
            )
            return
        nombres = ", ".join(r["nombre"] for r in rutinas)
        self.controller_ctx.say(
            f"{prefijo}Tienes {len(rutinas)} rutina{'s' if len(rutinas) != 1 else ''}: {nombres}. "
            "Di «ejecuta mi rutina» y el nombre para lanzarla."
        )

    def _on_vision_status(self, text, active):
        """Estado del sistema de visión por cámara (llega en el hilo de la UI).

        Aditivo y silencioso: solo registra en consola. Si quieres verlo en el
        chat, cambia el print por self.controller_ctx.say(text).
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

    def vision_resumen(self):
        """Una línea en español con lo que YUE ve ahora mismo."""
        sistema = getattr(self, "vision_mp", None)
        if sistema is None:
            return "El sistema de visión por cámara está apagado."
        try:
            return sistema.resumen_visual()
        except Exception:
            return "No consigo leer el estado de la visión."

    def shutdown(self):
        self.pc.cancel()
        self.speaker.stop()
        self.listener.shutdown()
        self.controller_ctx.camera.stop()
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
