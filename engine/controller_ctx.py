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
        # -- callbacks del host (cables; dueños futuros: DiálogoDirector/Orchestrator)
        self.say = None
        self.user_message = None
        self.avatar_emotion = None
        # -- estado mutable compartido (get/set explícito)
        self._input_source = "texto"

    @property
    def input_source(self) -> str:
        return self._input_source

    @input_source.setter
    def input_source(self, valor: str) -> None:
        self._input_source = valor

    @property
    def avatar_conversation_priority(self) -> int:
        """Equivale a core.state.Priority.CONVERSATION (70), congelado."""
        return 70