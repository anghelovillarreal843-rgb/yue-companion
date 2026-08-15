"""ControllerContext: service-locator compartido de los directores.

Regla del refactor (REFACTOR_SPEC §4.1): ningún director recibe referencia
al Controller (main.py); todo dato que pertenece a otra pieza se publica
y lee aquí. Cada PR de extracción agrega sus dependencias a este contexto.

Paso 1 (PR 5): publica `workers` (WorkerRegistry).
"""

from __future__ import annotations

from engine.worker_registry import WorkerRegistry


class ControllerContext:
    """Service-locator minimalista, crece por paso de PR 5.

    Componentes del host (los publica Controller al arrancar): speaker,
    listener, chat, pet, state_manager. Callbacks del host (se re-sustituyen
    cuando su dueño se extraiga): say (_yue_say), user_message
    (on_user_message -> DiálogoDirector), avatar_emotion
    (_set_avatar_emotion -> EmotionOrchestrator). Estado mutable con get/set
    (patrón last_visual_risk_ask): input_source.
    """

    def __init__(self) -> None:
        self.workers = WorkerRegistry()
        # -- componentes del host (publicados por Controller en el arranque)
        self.speaker = None
        self.listener = None
        self.chat = None
        self.pet = None
        self.state_manager = None
        self.pc = None          # PR 5.4 (PCDirector)
        self.engine = None      # PR 5.4 (PCDirector)
        self.memory = None      # PR 5.4 (PCDirector)
        # -- callbacks del host (cables; dueños futuros: DiálogoDirector/Orchestrator)
        self.say = None
        self.user_message = None
        self.avatar_emotion = None
        self.confirm_speak = None    # PR 5.4: emit de confirm_request
        self.confirm_notify = None   # PR 5.4: emit de confirm_notify
        self.user_float = None       # PR 5.4: _user_float del Controller
        self.list_routines = None    # PR 5.4: _list_routines del Controller
        # -- estado mutable compartido (get/set explícito)
        self._input_source = "texto"
        self._pc_busy = False    # PR 5.4: flag del cerebro central (13 lectores en main)
        self._pc_confirm = None  # PR 5.4: confirmación verbal pendiente

    @property
    def input_source(self) -> str:
        return self._input_source

    @input_source.setter
    def input_source(self, valor: str) -> None:
        self._input_source = valor

    @property
    def pc_busy(self) -> bool:
        return self._pc_busy

    @pc_busy.setter
    def pc_busy(self, valor: bool) -> None:
        self._pc_busy = valor

    @property
    def pc_confirm(self):
        return self._pc_confirm

    @pc_confirm.setter
    def pc_confirm(self, valor) -> None:
        self._pc_confirm = valor

    @property
    def avatar_conversation_priority(self) -> int:
        """Equivale a core.state.Priority.CONVERSATION (70), congelado."""
        return 70