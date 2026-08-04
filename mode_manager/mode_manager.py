"""Gestor de modos de YUE (fachada).

Une las piezas:
    mode_state.py     -> qué modo está activo (arranca en companion)
    mode_commands.py  -> detección de intención (activar/desactivar), voz o texto
    mode_router.py    -> registro de modos y despacho al motor correcto
    mode_events.py    -> avisos de cambio de modo (para UI, voz, avatar)

Filosofía:
    - La PERSONALIDAD e IDENTIDAD de YUE no cambian nunca aquí. Este módulo solo
      decide qué motor conversa; el "quién es YUE" vive en personality.py.
    - Es ADITIVO y desacoplado: no importa Qt, ni la IA, ni main.py. main registra
      los modos (companion, teacher, …) y consulta al gestor en un único punto.
    - Escalable: para un modo nuevo basta registrar un `ModeSpec` con su id. La
      lógica de detección y despacho no se toca.

Uso típico desde main.py:

    from mode_manager import ModeManager, ModeSpec
    from mode_manager.mode_state import DEFAULT_MODE

    modes = ModeManager()
    modes.register_mode(ModeSpec(id="companion", label="Compañera", emoji="🤍",
                                 is_default=True, handler=companion_process))
    modes.register_mode(ModeSpec(id="teacher", label="Profesora", emoji="📚",
                                 activation_phrases=(...), activation_message=..., ...,
                                 handler=teacher_process, on_enter=..., on_exit=...))

    result = modes.handle(user_text)
    if result.switched:
        say(result.reply); update_indicator(result.emoji, result.label)
    elif result.mode == "teacher":
        teacher_process(user_text)
    else:
        companion_flow(user_text)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from mode_manager import mode_commands
from mode_manager.mode_events import ENTER, EXIT, SWITCH, ModeEvent, ModeEventBus
from mode_manager.mode_router import ModeRouter
from mode_manager.mode_state import DEFAULT_MODE, ModeState


# --------------------------------------------------------------------------
# Descripción de un modo (lo que main.py registra)
# --------------------------------------------------------------------------
@dataclass
class ModeSpec:
    id: str
    label: str = ""
    emoji: str = ""
    is_default: bool = False

    # Gatillos de lenguaje natural (para modos NO por defecto).
    activation_phrases: tuple = ()
    activation_keywords: tuple = ()
    deactivation_phrases: tuple = ()
    deactivation_keywords: tuple = ()

    # Mensajes que YUE dice al entrar/salir de este modo.
    activation_message: str = ""
    deactivation_message: str = ""

    # Motor del modo: process(text, ctx). Lo aporta main.py.
    handler: object = None
    # Ganchos de ciclo de vida: on_enter(ctx) / on_exit(ctx). Opcionales.
    on_enter: object = None
    on_exit: object = None


@dataclass(frozen=True)
class RouteResult:
    switched: bool          # ¿hubo cambio de modo en este mensaje?
    mode: str               # modo resultante
    reply: str | None = None    # mensaje que YUE debe decir (al cambiar de modo)
    label: str = ""
    emoji: str = ""
    intent_kind: str = ""   # "activate" | "deactivate" | ""


# --------------------------------------------------------------------------
# Gestor
# --------------------------------------------------------------------------
class ModeManager:
    def __init__(self, default_mode: str = DEFAULT_MODE):
        self._default = default_mode
        self.state = ModeState(default_mode)
        self.router = ModeRouter(self.state, default_mode)
        self.events = ModeEventBus()

    # ---- registro ----
    def register_mode(self, spec: ModeSpec) -> None:
        if spec.is_default:
            # El modo marcado como por defecto define el "modo normal".
            self._default = spec.id
            self.state = ModeState(spec.id)
            self.router = ModeRouter(self.state, spec.id)
        self.router.register(spec)

    # ---- consulta ----
    def current_mode(self) -> str:
        return self.state.get()

    def is_default(self) -> bool:
        return self.state.is_default()

    def current_meta(self) -> dict:
        return self.router.meta(self.state.get())

    def subscribe(self, callback) -> None:
        self.events.subscribe(callback)

    # ---- activación directa (sin depender de frases; p. ej. al soltar un PDF) ----
    def activate(self, mode_id: str, ctx=None) -> RouteResult:
        """Cambia a `mode_id` de forma programática y devuelve el `RouteResult`.

        Útil cuando la intención no viene del texto (arrastrar un documento al
        chat, un botón, etc.). Si ya está en ese modo, no hace nada.
        """
        if mode_id == self.state.get():
            meta = self.router.meta(mode_id)
            return RouteResult(False, mode_id, None, meta["label"], meta["emoji"])
        return self._switch_to(mode_id, ctx)

    # ---- decisión principal (punto único) ----
    def handle(self, text: str, ctx=None) -> RouteResult:
        """Detecta si el mensaje activa/desactiva un modo y aplica el cambio.

        NO ejecuta el motor del modo (eso lo hace main con su flujo asíncrono):
        devuelve un `RouteResult` que le dice a main qué hacer.
        """
        actual = self.state.get()
        intent = mode_commands.detect(text, actual, self.router.registry, self._default)

        if intent is None:
            meta = self.router.meta(actual)
            return RouteResult(False, actual, None, meta["label"], meta["emoji"])

        if intent.kind == "activate":
            return self._switch_to(intent.mode_id, ctx)

        # deactivate -> volver al modo por defecto
        return self._switch_to(self._default, ctx)

    # ---- transición ----
    def _switch_to(self, nuevo: str, ctx=None) -> RouteResult:
        anterior = self.state.get()
        spec_ant = self.router.get(anterior)
        spec_new = self.router.get(nuevo)

        # Mensaje que YUE dirá: al volver al modo por defecto usamos la despedida
        # del modo anterior; al entrar a un modo especial, su bienvenida.
        if nuevo == self._default and spec_ant is not None:
            reply = getattr(spec_ant, "deactivation_message", "") or None
        else:
            reply = getattr(spec_new, "activation_message", "") or None

        # on_exit del modo anterior (p. ej. guardar progreso, soltar módulos).
        self._safe_lifecycle(getattr(spec_ant, "on_exit", None), ctx, anterior, "on_exit")
        self.events.emit(ModeEvent(EXIT, anterior, nuevo, self.router.meta(anterior)))

        self.state.set(nuevo)

        # on_enter del modo nuevo (p. ej. habilitar lectura de documentos).
        self._safe_lifecycle(getattr(spec_new, "on_enter", None), ctx, nuevo, "on_enter")
        meta = self.router.meta(nuevo)
        self.events.emit(ModeEvent(ENTER, anterior, nuevo, meta))
        self.events.emit(ModeEvent(SWITCH, anterior, nuevo, meta))

        kind = "deactivate" if nuevo == self._default else "activate"
        return RouteResult(True, nuevo, reply, meta["label"], meta["emoji"], kind)

    @staticmethod
    def _safe_lifecycle(fn, ctx, mode_id, nombre):
        if callable(fn):
            try:
                fn(ctx)
            except Exception as exc:
                print(f"[modos] {nombre} de '{mode_id}' falló:", exc)
