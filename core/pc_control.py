"""Fachada de Control de PC sobre el motor autónomo modular de YUE.

Toda instrucción se comprende y planifica antes de ejecutarse. El núcleo usa un
grafo de tareas atómicas, dependencias, cola estricta, locks de recursos,
verificación observable, contexto temporal, recuperación y registro estructurado.
Este módulo conserva los adaptadores físicos y la API pública histórica.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
import threading
import time
import unicodedata
import urllib.parse
import webbrowser
from collections import deque
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import config
from core import screen_diff, screen_text, youtube, activity
from core.app_catalog import AppCatalog
from core.office_control import OfficeController, OfficeError, OfficeUnavailable
from platforms import get_platform_controller
from core.agent import (
    AgentLogger,
    AgentRuntimeError,
    ApplicationManager,
    AutonomousAgentRuntime,
    ContextManager as AgentContextManager,
    FocusManager,
    LocksManager,
    RecoveryEngine,
    SmartWaiter,
    TaskExecutor,
    TaskPlanner,
    TaskQueue,
    VerificationEngine,
    WindowManager,
    build_default_registry,
)


class PCControlError(RuntimeError):
    pass


@dataclass
class ActionResult:
    action: str
    ok: bool
    detail: str = ""


_BLOCKED_TERMS = (
    "formatea", "formatear", "format disk", "borrar todo", "elimina todo",
    "rm -rf", "del /f", "registro de windows", "regedit", "desactiva antivirus",
    "deshabilita antivirus", "contraseña", "password", "transferencia bancaria",
    "comprar cripto", "vaciar papelera", "apaga la pc", "reinicia la pc",
    "powershell", "cmd.exe", "terminal como administrador", "elimina la cuenta",
    "borra el disco", "desinstala el antivirus", "desactiva el firewall",
)
_ALLOWED_ACTIONS = {
    "open_app", "open_url", "open_path", "search_web", "type_text", "press",
    "hotkey", "click", "right_click", "double_click", "move", "drag", "scroll",
    "hscroll", "wait", "screenshot",
    # --- acciones basadas en elementos reales (aditivas) ---
    "click_element", "click_text", "focus_window", "list_windows", "close_window",
    "move_window", "office_create", "office_write", "office_save", "office_read",
}
# Acciones cuyo efecto se comprueba con una mini-captura antes/después.
_VERIFY_ACTIONS = {
    "click", "double_click", "type_text", "press", "click_element", "click_text",
}
# Acciones cuyo fallo NO aborta el plan: se marca ok=False y el ciclo siguiente
# ve el motivo en el historial y puede probar otro camino.
_SOFT_FAIL_ACTIONS = {
    "click_element", "click_text", "focus_window", "list_windows", "close_window",
    "move_window", "office_create", "office_write", "office_save", "office_read",
}
# Tipos de control UIA que el planificador puede pedir en click_element.
_CONTROL_TYPES = {
    "button", "menuitem", "tabitem", "listitem", "hyperlink", "edit", "text",
    "checkbox", "radiobutton", "combobox", "treeitem", "image", "document",
    "splitbutton", "group", "pane", "window", "toolbar", "custom",
}
_SAFE_KEYS = {
    "enter", "tab", "esc", "escape", "space", "backspace", "delete", "home",
    "end", "up", "down", "left", "right", "pageup", "pagedown", "insert",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
}
_SAFE_MODIFIERS = {"ctrl", "alt", "shift", "win", "command"}
_ALLOWED_BUTTONS = {"left", "right", "middle"}

# Acciones con reversión conservadora. type_text usa Ctrl+Z; ventanas y arrastres
# requieren un snapshot previo y validaciones de contexto. Un clic normal, abrir
# una app o cualquier estado ambiguo siguen sin revertirse a ciegas.
_UNDOABLE_ACTIONS = {"type_text", "move_window", "close_window", "drag"}

# --- Accesibilidad: acciones IRREVERSIBLES que piden confirmación verbal ---
# No son bloqueos (eso es _BLOCKED_TERMS, prohibido siempre): son acciones
# legítimas pero sin vuelta atrás. Se detectan por su intención (nombre de botón,
# texto, teclas), ya que no hay un "tipo de acción" propio para cada una.
_CONFIRM_DELETE = (
    "eliminar", "elimina", "borrar", "borra", "suprimir", "suprime", "delete",
    "remove", "quitar archivo", "mover a la papelera", "enviar a la papelera",
    "a la papelera", "eliminar permanentemente",
)
_CONFIRM_SEND = (
    "enviar", "envia", "enviar correo", "enviar mensaje", "send", "mandar",
    "manda", "publicar", "publica", "post", "tuitear", "twittear",
    "responder a todos", "reply all",
)
_CONFIRM_DISCARD = (
    "no guardar", "no guardes", "sin guardar", "dont save", "descartar",
    "descarta", "discard", "cerrar sin guardar", "salir sin guardar",
)
_CONFIRM_OVERWRITE = (
    "sobrescribir", "sobreescribir", "sobrescribe", "overwrite", "reemplazar",
    "reemplaza", "replace", "sustituir", "sustituye",
)
_INTENT_TERMINAL = (
    "terminal", "consola", "command prompt", "simbolo del sistema", "windows terminal",
    "bash", "wsl",
)
_INTENT_INSTALL = (
    "instalar", "instala", "install", "setup", "installer", "msi", "agregar programa",
)
_PERMISSION_BY_CATEGORY = {
    "delete": "PERM_BORRAR_ARCHIVOS",
    "send": "PERM_ENVIAR_CORREOS",
    "discard": "PERM_CERRAR_VENTANAS",
    "overwrite": "PERM_SOBRESCRIBIR_ARCHIVOS",
    "terminal": "PERM_USAR_TERMINAL",
    "install": "PERM_INSTALAR_SOFTWARE",
}


def _norm_kw(text: str) -> str:
    """Normaliza para comparar palabras clave: minúsculas y SIN acentos."""
    base = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in base if unicodedata.category(c) != "Mn")

_APP_ALIASES = {
    "bloc de notas": "notepad", "notas": "notepad", "notepad": "notepad",
    "calculadora": "calculator", "calc": "calculator", "calculator": "calculator",
    "paint": "paint", "explorador": "files", "explorador de archivos": "files",
    "archivos": "files", "configuracion": "settings", "ajustes": "settings",
    "chrome": "chrome", "google chrome": "chrome", "edge": "edge",
    "firefox": "firefox", "word": "word", "excel": "excel",
    "powerpoint": "powerpoint", "spotify": "spotify", "discord": "discord",
}

_APP_WINDOW_HINTS = {
    "calculator": ("calculadora", "calculator"),
    "notepad": ("bloc de notas", "notepad"),
    "paint": ("paint",),
    "files": ("explorador de archivos", "file explorer"),
    "settings": ("configuración", "settings"),
    "chrome": ("google chrome", "chrome"),
    "edge": ("microsoft edge", "edge"),
    "firefox": ("mozilla firefox", "firefox"),
    "word": ("microsoft word", "word"),
    "excel": ("microsoft excel", "excel"),
    "powerpoint": ("microsoft powerpoint", "powerpoint"),
    "spotify": ("spotify",),
    "discord": ("discord",),
}

_CONTROL_NAME_ALIASES = {
    "0": "Cero", "1": "Uno", "2": "Dos", "3": "Tres", "4": "Cuatro",
    "5": "Cinco", "6": "Seis", "7": "Siete", "8": "Ocho", "9": "Nueve",
    "+": "Más", "-": "Menos", "x": "Multiplicar por", "*": "Multiplicar por",
    "/": "Dividir por", "=": "Igual a", ".": "Separador decimal", ",": "Separador decimal",
}


# Claves donde los modelos suelen poner el nombre de la acción cuando no usan "action".
_ACTION_KEYS = ("action", "accion", "type", "tool", "tool_name", "function",
                "command", "cmd", "op", "operation", "step", "name")
# Sinónimos que devuelven los modelos. Se mapean a las acciones reales.
_ACTION_ALIASES = {
    "click_on": "click", "click_at": "click", "mouse_click": "click",
    "left_click": "click", "rightclick": "right_click", "doubleclick": "double_click",
    "click_on_element": "click_element", "click_element_by_name": "click_element",
    "click_button": "click_element", "click_control": "click_element",
    "click_on_text": "click_text", "click_text_on_screen": "click_text",
    "type": "type_text", "write": "type_text", "write_text": "type_text",
    "input_text": "type_text", "enter_text": "type_text", "escribir": "type_text",
    "press_key": "press", "key": "press", "keypress": "press", "presionar": "press",
    "key_combination": "hotkey", "shortcut": "hotkey", "hot_key": "hotkey",
    "open": "open_app", "launch": "open_app", "start_app": "open_app",
    "run": "open_app", "open_application": "open_app", "abrir": "open_app",
    "open_website": "open_url", "goto": "open_url", "navigate": "open_url",
    "browse": "open_url", "search": "search_web", "google": "search_web",
    "buscar": "search_web", "sleep": "wait", "pause": "wait", "esperar": "wait",
    "capture": "screenshot", "screen_shot": "screenshot",
    "focus": "focus_window", "activate_window": "focus_window",
    "switch_window": "focus_window", "get_windows": "list_windows",
    "windows": "list_windows",
    "move_window_to": "move_window", "position_window": "move_window",
    "office_create_document": "office_create", "create_office": "office_create",
    "office_insert": "office_write", "office_type": "office_write",
    "office_save_document": "office_save", "office_read_document": "office_read",
}
# Parámetro principal de cada acción, para reconstruir {"open_app": "notepad"}.
_PRIMARY_PARAM = {
    "open_app": "name", "open_url": "url", "open_path": "path", "search_web": "query",
    "type_text": "text", "press": "key", "hotkey": "keys", "wait": "seconds",
    "scroll": "amount", "hscroll": "amount", "screenshot": "name",
    "click_element": "name", "click_text": "text",
    "focus_window": "title", "close_window": "title", "move_window": "title",
    "office_create": "app", "office_write": "app", "office_save": "app", "office_read": "app",
}


def _canon_action(value) -> str:
    """Normaliza el nombre de una acción: camelCase, guiones, sinónimos."""
    if isinstance(value, dict):
        for clave in ("name", "action", "type"):
            if isinstance(value.get(clave), str):
                value = value[clave]
                break
        else:
            return ""
    if not isinstance(value, str):
        return ""
    texto = value.strip()
    texto = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", texto)     # clickElement -> click_element
    texto = re.sub(r"[\s\-]+", "_", texto).lower()
    texto = re.sub(r"[^a-z_]", "", texto)
    if texto in _ALLOWED_ACTIONS:
        return texto
    return _ACTION_ALIASES.get(texto, "")


def _coerce_step(item):
    """Intenta entender un paso mal formado antes de tirarlo a la basura.

    Los modelos devuelven cosas como {"type":"open_app"}, {"open_app":"notepad"},
    "open_app" a secas o {"action":{"name":"click"}}. Todo eso era «Acción no
    permitida: vacía» y se perdía.
    """
    # 1) Un string suelto: "list_windows"
    if isinstance(item, str):
        accion = _canon_action(item)
        return {"action": accion} if accion else None
    if not isinstance(item, dict) or not item:
        return None

    paso = dict(item)
    # 2) La acción está en otra clave ("type", "tool", "command"...)
    for clave in _ACTION_KEYS:
        if clave not in paso:
            continue
        accion = _canon_action(paso.get(clave))
        if accion:
            if clave != "action":
                paso.pop(clave, None)
            paso["action"] = accion
            return paso

    # 3) Forma {"open_app": "notepad"} o {"click_element": {"name": "Guardar"}}
    for clave, valor in item.items():
        accion = _canon_action(clave)
        if not accion:
            continue
        paso = {k: v for k, v in item.items() if k != clave}
        paso["action"] = accion
        if isinstance(valor, dict):
            paso.update(valor)
        elif valor is not None and not isinstance(valor, (list, tuple)):
            principal = _PRIMARY_PARAM.get(accion)
            if principal and principal not in paso:
                paso[principal] = valor
        elif isinstance(valor, (list, tuple)) and accion == "hotkey":
            paso["keys"] = list(valor)
        return paso
    return None


def _texto_accion(valor) -> str:
    """Cómo llamó el modelo a la acción, para el mensaje de error."""
    if valor is None or valor == "":
        return "vacía"
    return str(valor)[:60]


def _resumen_paso(item) -> str:
    """Vuelca el paso tal cual llegó, para poder ver qué inventó el modelo."""
    try:
        import json as _json
        return _json.dumps(item, ensure_ascii=False, default=str)[:180]
    except Exception:
        return repr(item)[:180]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def looks_like_pc_command(text: str) -> bool:
    n = _norm(text)
    office_intent = bool(
        re.match(r"^(?:yue[,: ]+)?(?:crea|crear|genera|generar|haz|hacer|prepara|preparar|"
                 r"elabora|elaborar|redacta|redactar|edita|editar|modifica|modificar|"
                 r"inserta|insertar|lee|leer|guarda|guardar)\b", n)
        and re.search(r"\b(word|excel|powerpoint|power point|documento|docx|xlsx|pptx|"
                      r"hoja de calculo|presentacion)\b", _norm_kw(n))
    )
    if office_intent:
        return True
    return bool(re.match(
        r"^(yue[,: ]+)?(abre|inicia|ejecuta|escribe|pon|presiona|pulsa|haz clic|"
        r"clic derecho|doble clic|mueve el mouse|mueve la ventana|arrastra|desplaza|scroll|"
        r"busca en internet|controla|usa mi pc|en mi computadora|en mi ordenador|"
        r"selecciona todo|copia|pega|guarda|deshaz|rehaz|nueva pestaña|recarga|"
        # --- verbos de las acciones por elementos reales (aditivo) ---
        r"enfoca|activa la ventana|ve a la ventana|cambia a la ventana|"
        r"minimiza|maximiza|restaura la ventana|"
        r"cierra (la ventana|el programa|la aplicacion|la app)|"
        r"(lista(me)?|listar|muestrame) (las )?ventanas|"
        # "dime qué ventanas TENGO/HAY" sí; "qué ventanas TIENE tu casa" no.
        r"(dime |)(que|cuales) ventanas (tengo|hay|estan|siguen))\b",
        n,
    ))


class PCController:
    def __init__(self, platform_controller=None):
        self.platform = platform_controller if platform_controller is not None else get_platform_controller()
        self.max_actions = max(1, config.PC_MAX_ACTIONS)
        self.max_cycles = max(1, getattr(config, "PC_MAX_CYCLES", 3))
        self.max_actions_per_cycle = max(1, getattr(config, "PC_MAX_ACTIONS_PER_CYCLE", 8))
        self.action_pause = max(0.02, config.PC_ACTION_PAUSE)
        self.visual_recheck = bool(getattr(config, "PC_VISUAL_RECHECK", True))
        self.verify_actions = bool(getattr(config, "PC_VERIFY_ACTIONS", True))
        self.ui_context = bool(getattr(config, "PC_UI_CONTEXT", True))
        self._last_history: list[str] = []      # historial del último execute()
        self.last_discarded: list[str] = []     # pasos que el validador tiró
        self._verify_rects: list[list[int]] = []
        self._cancel_event = threading.Event()
        # NUEVO (recuperación): último bloque de acciones ejecutado, para poder
        # "deshacer" la última acción o "repetir" el bloque sin redictar la orden.
        self._last_executed: dict | None = None
        # NUEVO (accesibilidad): callback que pide confirmación por voz antes de
        # ejecutar una acción irreversible. Lo inyecta main.py; recibe la pregunta
        # y devuelve True (ejecutar) o False (saltar ese paso). Si es None, las
        # acciones sensibles se saltan por seguridad cuando la confirmación está
        # activa. No afecta a los bloqueos duros (esos siguen prohibidos siempre).
        self._confirm_callback: Callable[[str], bool] | None = None
        # Control nativo y descubrimiento dinámico. El escaneo corre en daemon y
        # nunca bloquea el arranque ni las pruebas fuera de Windows.
        self.office = OfficeController(visible=True)
        self.apps = AppCatalog()
        if bool(getattr(config, "PC_APP_SCAN_ON_START", True)):
            self.apps.scan_async(force=False)
        # Historial visible: cola corta y callback hacia la UI.
        self._action_log = deque(maxlen=max(5, int(getattr(config, "PC_ACTION_LOG_SIZE", 8))))
        self._action_log_callback: Callable[[list[dict]], None] | None = None
        # Motor autónomo modular. PCController queda como fachada compatible
        # con main.py y como adaptador de las acciones físicas ya probadas.
        self._agent_local = threading.local()
        self._init_agent_runtime()

    def _init_agent_runtime(self):
        self.agent_registry = build_default_registry()
        self.agent_locks = LocksManager()
        self.agent_waiter = SmartWaiter(self._check_cancelled)
        self.window_manager = WindowManager(self.platform, self.agent_waiter)
        self.focus_manager = FocusManager(self.window_manager)
        self.application_manager = ApplicationManager(
            self.window_manager, _APP_ALIASES, _APP_WINDOW_HINTS, self.focus_manager
        )
        log_path = Path(getattr(
            config,
            "PC_AGENT_LOG_PATH",
            config.DATA_DIR / "logs" / "pc_agent.jsonl",
        ))
        self.agent_logger = AgentLogger(log_path)
        self.agent_planner = TaskPlanner(
            self.agent_registry,
            direct_planner=self._direct_plan,
            action_validator=self._validate_step,
            default_retries=int(getattr(config, "PC_AGENT_RETRY_ATTEMPTS", 3)),
            default_timeout=float(getattr(config, "PC_AGENT_WAIT_TIMEOUT", 10.0)),
        )
        self.agent_verifier = VerificationEngine(
            windows=self.window_manager,
            applications=self.application_manager,
            waiter=self.agent_waiter,
            screen_signature=self._agent_screen_signature,
            screen_changed=screen_diff.changed,
            focused_element=self.platform.element_has_focus,
            element_present=lambda name: self.platform.find_element_center(name),
        )
        self.agent_recovery = RecoveryEngine()
        self.agent_runtime = AutonomousAgentRuntime(
            planner=self.agent_planner,
            queue_factory=self._agent_queue_factory,
            context_factory=lambda instruction: AgentContextManager(
                instruction, state_probe=self._agent_state_probe
            ),
            logger=self.agent_logger,
            screen_probe=self._screen_info,
            max_cycles=int(getattr(config, "PC_AGENT_MAX_REPLANS", self.max_cycles)),
            max_actions=self.max_actions,
            visual_recheck=self.visual_recheck,
        )

    def register_agent_action(
        self,
        name: str,
        handler,
        *,
        resources=(),
        idempotent: bool = False,
        parallel_safe: bool = False,
        verifier: str = "auto",
        timeout: float = 15.0,
        expander=None,
        description: str = "",
        parameters: str = "",
        replace_existing: bool = False,
    ) -> None:
        """Registra una capacidad nueva sin tocar el núcleo ni `_execute_action`.

        El handler recibe el diccionario atómico de acción y puede devolver un
        ActionResult, un dict ``{ok, detail}``, un bool o un texto descriptivo.
        """
        self.agent_registry.register_action(
            name, handler, resources=resources, idempotent=idempotent,
            parallel_safe=parallel_safe, default_timeout=timeout,
            verifier=verifier, expander=expander, description=description, parameters=parameters,
            replace_existing=replace_existing,
        )

    def _agent_state_probe(self) -> dict:
        try:
            windows = self.window_manager.list(limit=int(getattr(config, "PC_UI_MAX_WINDOWS", 18)))
        except Exception:
            windows = []
        return {
            "active_window": self.window_manager.active_title(),
            "windows": windows,
        }

    def _agent_screen_signature(self):
        try:
            return screen_diff.signature(self._ignore_rects())
        except Exception:
            return None

    def _agent_action_runner(self, step: dict) -> ActionResult:
        self._agent_local.verification_active = True
        try:
            return self._execute_action(step)
        finally:
            self._agent_local.verification_active = False

    def _agent_publish(self, result):
        legacy = ActionResult(result.action, result.ok, result.detail)
        self._publish_action_result(legacy)

    def _agent_queue_factory(self, context, progress):
        executor = TaskExecutor(
            registry=self.agent_registry,
            locks=self.agent_locks,
            context=context,
            verifier=self.agent_verifier,
            action_runner=self._agent_action_runner,
            logger=self.agent_logger,
            confirm=lambda task: self._confirm_if_needed(task.as_action(), progress),
            publish=self._agent_publish,
            cancel_check=self._check_cancelled,
        )
        return TaskQueue(
            registry=self.agent_registry,
            executor=executor,
            recovery=self.agent_recovery,
            context=context,
            logger=self.agent_logger,
            max_workers=int(getattr(config, "PC_AGENT_MAX_WORKERS", 3)),
            allow_parallel=bool(getattr(config, "PC_AGENT_PARALLEL", True)),
            progress=progress,
            cancel_check=self._check_cancelled,
        )

    @property
    def last_history(self) -> list[str]:
        """Pasos y notas del último execute(); útil para registrar lecciones."""
        return list(self._last_history)

    def cancel(self):
        self._cancel_event.set()

    def set_confirm_callback(self, callback: Callable[[str], bool] | None):
        """Inyecta el canal de confirmación verbal (lo usa el modo accesibilidad).

        `callback(pregunta)` debe hablarle al usuario y devolver True/False según
        confirme o no. main.py lo conecta a la voz + el listener.
        """
        self._confirm_callback = callback

    def set_action_log_callback(self, callback: Callable[[list[dict]], None] | None):
        """Publica las últimas acciones en el chat sin acoplar el core a Qt."""
        self._action_log_callback = callback

    @property
    def action_log(self) -> list[dict]:
        return [dict(item) for item in self._action_log]

    def _publish_action_result(self, result: ActionResult):
        item = {
            "action": result.action,
            "ok": bool(result.ok),
            "detail": str(result.detail or "")[:180],
            "ts": time.time(),
        }
        self._action_log.append(item)
        callback = self._action_log_callback
        if callback is not None:
            try:
                callback(self.action_log)
            except Exception as exc:
                print("[control-pc] no pude publicar la bitácora:", exc)

    def rescan_apps(self) -> dict:
        """Reconstruye data/apps_catalogo.json y devuelve un resumen."""
        data = self.apps.scan(force=True)
        return {"ok": True, "count": len(data.get("apps", [])), "path": str(self.apps.path)}

    def open_path(self, path: str | Path):
        """Abre de forma segura un archivo/carpeta existente con la app predeterminada."""
        target = Path(path).expanduser().resolve()
        if not target.exists():
            raise PCControlError(f"No existe: {target}")
        system = platform.system().lower()
        if system == "windows":
            os.startfile(str(target))
        elif system == "darwin":
            subprocess.Popen(["open", str(target)], shell=False)
        else:
            subprocess.Popen(["xdg-open", str(target)], shell=False)

    def execute(
        self,
        instruction: str,
        engine=None,
        progress: Callable[[str], None] | None = None,
    ) -> dict:
        """Ejecuta la orden con las ventanas de Yue apartadas del mouse."""
        # El avatar y el chat van SIEMPRE ENCIMA. Mientras Yue controla el PC se
        # estorba a sí misma: el clic aterriza en su propio avatar. Durante la
        # orden se vuelven atravesables y al terminar se restauran, pase lo que
        # pase (por eso el try/finally: si peta a mitad, no queremos dejar el
        # chat inutilizable).
        result = self._windows_aside(
            lambda: self._execute_inner(instruction, engine, progress)
        )
        # NUEVO: recordamos el bloque para "deshacer"/"repetir".
        self.remember_result(instruction, result)
        return result

    def _windows_aside(self, fn):
        """Ejecuta `fn` con las ventanas de Yue apartadas del mouse (click-through).

        Reutilizable por execute(), repeat_last() y undo_last(): así los comandos
        de recuperación no clican sobre el propio avatar de Yue.
        """
        atravesables = False
        try:
            atravesables = bool(self.platform.set_click_through(True))
            if atravesables:
                print("[control-pc] mis ventanas no estorban al mouse durante la orden")
            return fn()
        finally:
            if atravesables:
                self.platform.set_click_through(False)

    # ------------------------------------------------------------------
    # Recuperación para usuarios que no pueden corregir rápido con el mouse
    # ------------------------------------------------------------------
    @property
    def last_executed(self) -> dict | None:
        """Copia del último bloque ejecutado (o None si aún no hay ninguno)."""
        return dict(self._last_executed) if self._last_executed else None

    def remember_result(self, instruction: str, result: dict):
        """Registra el último bloque ejecutado a partir del dict de resultado.

        Público a propósito: main.py también lo llama en _on_pc_done, para cubrir
        rutas que no pasan por execute() (p. ej. una receta aprendida). Solo
        recuerda si de verdad se ejecutaron acciones.
        """
        try:
            if not isinstance(result, dict):
                return
            actions = list(result.get("actions", []) or [])
            if not actions:
                return  # verificación sin pasos: nada que deshacer ni repetir
            self._last_executed = {
                "instruction": str(instruction or ""),
                "actions": actions,
                "action_task_ids": list(result.get("_action_task_ids", []) or []),
                "results": list(result.get("results", []) or []),
                "ok": bool(result.get("ok")),
                "ts": time.time(),
            }
            # NUEVO (bitácora): un plan completado es una actividad "control_pc"
            # (con_usuario=True). Idempotente: main.py también llama a este método
            # como respaldo, así que marcamos el result para no duplicar el log.
            if not result.get("_activity_logged"):
                result["_activity_logged"] = True
                activity.log("control_pc", self._resumen_plan(actions),
                             con_usuario=True, origen="pc_control")
        except Exception as exc:
            print("[control-pc] no pude recordar el último bloque:", exc)

    def _resumen_plan(self, actions) -> str:
        """Resumen CORTO del plan (no cada paso): primeras acciones distintas."""
        vistos = []
        for a in actions:
            d = self._describe(a)
            if d and d not in vistos:
                vistos.append(d)
            if len(vistos) >= 3:
                break
        resumen = ", ".join(vistos) if vistos else "varias acciones"
        extra = "…" if len(actions) > len(vistos) else ""
        return f"controlé el PC: {resumen}{extra}"

    def _last_reversible(self):
        """Devuelve la última acción reversible y un motivo si no es seguro."""
        rec = self._last_executed
        if not rec:
            return None, "no tengo ninguna acción reciente registrada"
        actions = rec.get("actions") or []
        if not actions:
            return None, "no hay ninguna acción reciente que deshacer"
        last = actions[-1]
        results = rec.get("results") or []
        action = str(last.get("action", ""))
        last_ok = True
        task_ids = rec.get("action_task_ids") or []
        task_id = task_ids[-1] if len(task_ids) == len(actions) else ""
        matching = [item for item in results if task_id and item.get("task_id") == task_id]
        if not matching:
            matching = [item for item in results if item.get("action") == action]
        if matching:
            last_ok = any(bool(item.get("ok")) or bool(item.get("recovered")) for item in matching)
        desc = self._describe(last)
        if action not in _UNDOABLE_ACTIONS:
            return None, f"la última acción fue {desc}, y no tiene una reversión fiable"
        if not last_ok:
            return None, f"la última vez que intenté {desc} no se completó, así que no hay un cambio claro que revertir"
        if action != "type_text":
            snapshot = last.get("_reversible_snapshot")
            if not isinstance(snapshot, dict):
                return None, f"no tengo un snapshot seguro para revertir {desc}"
            if action == "close_window" and not snapshot.get("window", {}).get("safe_to_reopen"):
                return None, ("no puedo reabrir esa ventana con seguridad: podría haber "
                              "contenido sin guardar que ya no es recuperable")
        return last, ""

    @staticmethod
    def _window_titles(snapshot) -> set[str]:
        return {str(item.get("title", "")) for item in (snapshot or []) if item.get("title")}

    def undo_last(self) -> dict:
        """Revierte texto, movimiento/cierre de ventana o arrastre si es seguro."""
        last, motivo = self._last_reversible()
        if last is None:
            return {"ok": False, "undone": False, "reason": motivo}
        action = str(last.get("action", ""))
        snapshot = last.get("_reversible_snapshot") or {}
        try:
            locks = getattr(self, "agent_locks", None)
            if action == "type_text":
                keys = ["command", "z"] if platform.system().lower() == "darwin" else ["ctrl", "z"]
                guard = locks.acquire(("active_window", "keyboard"), timeout=5.0) if locks else nullcontext()
                with guard:
                    self._windows_aside(
                        lambda: self._pyautogui_action({"action": "hotkey", "keys": keys})
                    )
            elif action == "move_window":
                guard = locks.acquire(("active_window", "mouse"), timeout=5.0) if locks else nullcontext()
                with guard:
                    self.platform.restore_window_snapshot(snapshot.get("window") or {})
            elif action == "close_window":
                guard = locks.acquire(("active_window",), timeout=5.0) if locks else nullcontext()
                with guard:
                    self.platform.reopen_window_snapshot(snapshot.get("window") or {})
            elif action == "drag":
                # Solo invertimos el arrastre si seguimos en la misma ventana y no
                # se abrió/cerró ninguna otra: evita soltar algo en otro contexto.
                active_before = str(snapshot.get("active_window", ""))
                active_now = self.platform.active_window_title()
                if active_before and active_now and self.platform.similarity(active_before, active_now) < 0.72:
                    return {"ok": False, "undone": False,
                            "reason": "la ventana activa cambió; no invertiré el arrastre a ciegas"}
                before_titles = self._window_titles(snapshot.get("windows"))
                now_titles = self._window_titles(self.platform.windows_snapshot())
                if before_titles and now_titles and before_titles != now_titles:
                    return {"ok": False, "undone": False,
                            "reason": "cambió la lista de ventanas; no es seguro invertir el arrastre"}
                reverse = {
                    "action": "drag",
                    "from_x_pct": snapshot["to_x_pct"],
                    "from_y_pct": snapshot["to_y_pct"],
                    "to_x_pct": snapshot["from_x_pct"],
                    "to_y_pct": snapshot["from_y_pct"],
                    "duration": snapshot.get("duration", 0.6),
                    "button": snapshot.get("button", "left"),
                }
                guard = locks.acquire(("active_window", "mouse"), timeout=5.0) if locks else nullcontext()
                with guard:
                    self._windows_aside(lambda: self._pyautogui_action(reverse))
            else:
                return {"ok": False, "undone": False, "reason": "esa acción no tiene reversión"}
        except PCControlError as exc:
            return {"ok": False, "undone": False, "reason": str(exc)}
        except Exception as exc:
            return {"ok": False, "undone": False, "reason": f"no pude deshacer: {exc}"}
        return {"ok": True, "undone": True, "action": self._describe(last)}

    def repeat_last(self, progress: Callable[[str], None] | None = None) -> dict:
        """Reejecuta el último bloque a través de la cola autónoma completa."""
        rec = self._last_executed
        if not rec or not rec.get("actions"):
            return {"ok": False, "repeated": False, "reason": "no tengo una orden reciente que repetir"}
        progress = progress or (lambda _msg: None)
        instruction = str(rec.get("instruction", "repetir lo último"))
        self._cancel_event.clear()
        try:
            result = self._windows_aside(lambda: self.agent_runtime.execute_actions(
                rec["actions"], objective=instruction, progress=progress
            ))
        except Exception as exc:
            return {"ok": False, "repeated": False, "reason": f"no pude repetir: {exc}"}
        result["repeated"] = True
        self.remember_result(instruction, result)
        return result

    def run_actions(self, actions, progress: Callable[[str], None] | None = None, label: str = "") -> dict:
        """Ejecuta una rutina guardada con el mismo agente, locks y recuperación."""
        if not actions:
            return {"ok": False, "ran": False, "reason": "la rutina no tiene pasos guardados"}
        progress = progress or (lambda _msg: None)
        objective = f"rutina: {label}" if label else "rutina guardada"
        self._cancel_event.clear()
        try:
            result = self._windows_aside(lambda: self.agent_runtime.execute_actions(
                actions, objective=objective, progress=progress
            ))
        except Exception as exc:
            return {"ok": False, "ran": False, "reason": f"no pude ejecutar la rutina: {exc}"}
        result["ran"] = True
        self.remember_result(objective, result)
        return result

    def _execute_inner(
        self,
        instruction: str,
        engine=None,
        progress: Callable[[str], None] | None = None,
    ) -> dict:
        """Analiza, planifica y ejecuta mediante el motor autónomo modular."""
        instruction = (instruction or "").strip()
        if not instruction:
            raise PCControlError("La orden está vacía.")
        self._check_instruction(instruction)
        self._cancel_event.clear()
        progress = progress or (lambda _msg: None)
        print(f"[control-pc/agente] ── orden: «{instruction}»")

        history: list[str] = []
        self._last_history = history
        try:
            from core.learning import integration as _learning
            history.extend(_learning.enrich_history(instruction))
        except Exception as exc:
            print("[aprendizaje] sin lecciones previas:", exc)

        try:
            result = self.agent_runtime.execute(
                instruction,
                engine=engine,
                progress=progress,
                initial_history=history,
            )
        except AgentRuntimeError as exc:
            history.append(f"[FALLÓ] {exc}")
            raise PCControlError(str(exc)) from exc
        except PCControlError:
            raise
        except Exception as exc:
            history.append(f"[FALLÓ] error interno del agente: {exc}")
            raise PCControlError(f"El motor autónomo no pudo completar la orden: {exc}") from exc

        history.extend(
            ("[OK] " if item.get("ok") else "[FALLÓ] ") + str(item.get("detail", ""))
            for item in result.get("results", [])
        )
        print(
            f"[control-pc/agente] ── fin: ok={result.get('ok')} "
            f"completed={result.get('completed')} ciclos={result.get('cycles')} "
            f"pasos={len(result.get('actions', []))}"
        )
        return result

    @staticmethod
    def _log_resultados(results: list[ActionResult]):
        """Deja en la consola qué funcionó y qué no, con el motivo."""
        for r in results:
            print(f"[control-pc]    {'✓' if r.ok else '✗'} {r.action}: {r.detail}")

    @staticmethod
    def _failure_diagnostics(actions: list[dict], results: list[ActionResult]) -> list[str]:
        """Diagnóstico explícito que recibe el siguiente ciclo del planificador."""
        suggestions = {
            "click_element": "prueba click_text con el texto visible exacto; si no hay OCR, revisa ui_elements y el control_type",
            "click_text": "prueba click_element con un nombre de ui_elements; solo después usa coordenadas",
            "focus_window": "usa list_windows y copia el título real antes de volver a enfocar",
            "list_windows": "continúa con la ventana activa descrita por active_window",
            "close_window": "usa el título exacto de windows y verifica que el permiso de cierre esté activo",
            "move_window": "vuelve a listar ventanas y usa el título exacto",
            "click": "busca primero un control real o texto OCR equivalente",
            "office_create": "si COM no está disponible, deja que el fallback de teclado abra un archivo en blanco",
            "office_write": "reintenta con Office COM o divide creación, escritura y guardado",
            "office_save": "comprueba la ruta y el permiso de sobrescritura",
            "office_read": "asegúrate de que hay un archivo abierto en la aplicación correcta",
        }
        out = []
        for step, result in zip(actions, results):
            if result.ok:
                continue
            action = str(step.get("action", result.action))
            soft = action in _SOFT_FAIL_ACTIONS
            why = str(result.detail or "fallo sin detalle")
            hint = suggestions.get(action, "cambia de estrategia y no repitas el mismo paso")
            out.append(
                f"DIAGNÓSTICO acción fallida: se intentó {action}; "
                f"motivo: {why}; tipo: {'recuperable' if soft else 'duro'}; alternativa: {hint}."
            )
        return out

    # ------------------------------------------------------------------
    # Accesibilidad: confirmación verbal de acciones irreversibles
    # ------------------------------------------------------------------
    def _needs_confirmation(self, step: dict):
        """¿Este paso es irreversible y requiere confirmación verbal?

        Devuelve (necesita, categoria, objetivo). La detección es por INTENCIÓN
        (nombre de botón, texto a clicar o combinación de teclas), porque no hay
        un tipo de acción exclusivo para "borrar", "enviar", etc.
        """
        action = str(step.get("action", ""))
        if action in ("click_element", "click_text"):
            raw = str(step.get("name") or step.get("text") or "")
            blob = _norm_kw(raw)
            for categoria, palabras in (
                ("delete", _CONFIRM_DELETE),
                ("send", _CONFIRM_SEND),
                ("discard", _CONFIRM_DISCARD),
                ("overwrite", _CONFIRM_OVERWRITE),
            ):
                if any(p in blob for p in palabras):
                    # El nombre del botón suele ser el VERBO ("Eliminar"), no el
                    # objeto; no lo usamos como objetivo para no confundir.
                    return True, categoria, ""
            return False, "", ""
        if action == "close_window":
            # Cerrar una ventana puede perder cambios sin guardar.
            return True, "discard", str(step.get("title") or "")
        if action in {"office_save", "office_write"}:
            path = str(step.get("path") or "").strip()
            if path:
                try:
                    if Path(path).expanduser().exists():
                        return True, "overwrite", path
                except Exception:
                    pass
        if action == "hotkey":
            keys = {str(k).lower().strip() for k in step.get("keys", [])}
            if {"shift", "delete"} <= keys or {"shift", "del"} <= keys:
                return True, "delete", ""       # borrado permanente
            if ({"ctrl", "enter"} <= keys or {"command", "enter"} <= keys
                    or {"cmd", "enter"} <= keys):
                return True, "send", ""          # atajo típico de "enviar"
        # Permisos nuevos: también detectan la intención en abrir apps o clicar
        # elementos. _BLOCKED_TERMS sigue siendo un muro absoluto independiente.
        blob = _norm_kw(" ".join(str(step.get(k, "")) for k in (
            "name", "text", "title", "path", "url", "query", "app"
        )))
        if any(term in blob for term in _INTENT_TERMINAL):
            return True, "terminal", ""
        if any(term in blob for term in _INTENT_INSTALL):
            return True, "install", ""
        return False, "", ""

    @staticmethod
    def _confirmation_question(categoria: str, objetivo: str) -> str:
        """Arma la pregunta que YUE dirá por voz. Clara y sin adivinar el objeto."""
        objetivo = (objetivo or "").strip()
        if categoria == "discard" and objetivo:
            return f"¿Seguro que cierro «{objetivo}» sin guardar? Dime sí o no."
        generico = {
            "delete": "¿Seguro que quieres que elimine esto? Dime sí o no.",
            "send": "¿Seguro que quieres que lo envíe? Dime sí o no.",
            "discard": "¿Seguro que cierro sin guardar los cambios? Dime sí o no.",
            "overwrite": "¿Seguro que sobrescribo el archivo que ya existe? Dime sí o no.",
            "terminal": "¿Seguro que quieres que use una terminal? Dime sí o no.",
            "install": "¿Seguro que quieres que inicie una instalación? Dime sí o no.",
        }
        return generico.get(categoria, "¿Seguro que quieres que haga esto? Dime sí o no.")

    def _confirm_if_needed(self, step: dict, progress=None) -> str:
        """Devuelve 'ok' (ejecutar) o 'skip' (saltar solo este paso).

        Solo actúa si el modo de confirmación está activo Y el paso es sensible.
        Si no hay canal de confirmación, salta el paso por seguridad.
        """
        needs, categoria, objetivo = self._needs_confirmation(step)
        if needs:
            permission_name = _PERMISSION_BY_CATEGORY.get(categoria, "")
            if permission_name and not bool(getattr(config, permission_name, False)):
                print(f"[control-pc] permiso denegado ({permission_name}): {self._describe(step)}")
                return "deny"
        if not bool(getattr(config, "ACCESSIBILITY_CONFIRM_REQUIRED", False)):
            return "ok"
        if not needs:
            return "ok"
        if self._confirm_callback is None:
            print("[control-pc] acción sensible sin canal de confirmación; "
                  "la salto por seguridad:", self._describe(step))
            return "skip"
        if progress:
            try:
                progress("Espero tu confirmación por voz (sí/no)…")
            except Exception:
                pass
        pregunta = self._confirmation_question(categoria, objetivo)
        print(f"[control-pc] confirmación requerida ({categoria}): {self._describe(step)}")
        try:
            aprobado = bool(self._confirm_callback(pregunta))
        except Exception as exc:
            print("[control-pc] fallo en la confirmación; salto el paso:", exc)
            return "skip"
        print(f"[control-pc] confirmación {'concedida' if aprobado else 'denegada'}")
        return "ok" if aprobado else "skip"

    def _execute_batch(self, actions: list[dict], progress, cycle: int, total_cycles: int):
        results: list[ActionResult] = []
        for index, action in enumerate(actions, 1):
            self._check_cancelled()
            prefix = f"Ciclo {cycle}/{total_cycles} · " if total_cycles > 1 else ""
            progress(f"{prefix}paso {index}/{len(actions)}: {self._describe(action)}")
            # NUEVO (accesibilidad): antes de un paso irreversible, pedimos
            # confirmación verbal. Si el usuario dice que no (o no contesta), se
            # SALTA solo este paso; los demás del plan continúan normalmente.
            decision = self._confirm_if_needed(action, progress)
            if decision in {"skip", "deny"}:
                reason = ("permiso desactivado" if decision == "deny"
                          else "cancelada: no se confirmó por voz")
                result = ActionResult(
                    str(action.get("action", "")),
                    False,
                    f"{self._describe(action)} · {reason}",
                )
                results.append(result)
                self._publish_action_result(result)
                continue
            result = self._execute_action(action)
            results.append(result)
            self._publish_action_result(result)
            self._check_cancelled()
        return results

    def _check_cancelled(self):
        if self._cancel_event.is_set():
            raise PCControlError("Orden cancelada por el usuario.")

    def _check_instruction(self, instruction: str):
        n = _norm(instruction)
        # También compara sin artículos para bloquear variantes naturales como
        # «desactiva EL antivirus», no solo la cadena exacta del catálogo.
        stop = {"el", "la", "los", "las", "un", "una", "mi", "mis", "the", "a"}
        compact = " ".join(token for token in re.findall(r"[\w./-]+", n) if token not in stop)
        for term in _BLOCKED_TERMS:
            term_n = _norm(term)
            term_compact = " ".join(
                token for token in re.findall(r"[\w./-]+", term_n) if token not in stop
            )
            if term_n in n or (term_compact and term_compact in compact):
                raise PCControlError(
                    "Esa orden puede borrar datos, exponer credenciales o cambiar la seguridad del sistema."
                )
        normalized = _norm_kw(instruction)
        intent_permissions = (
            (_CONFIRM_DELETE, "PERM_BORRAR_ARCHIVOS", "borrar archivos"),
            (_CONFIRM_SEND, "PERM_ENVIAR_CORREOS", "enviar contenido"),
            (_INTENT_TERMINAL, "PERM_USAR_TERMINAL", "usar la terminal"),
            (_INTENT_INSTALL, "PERM_INSTALAR_SOFTWARE", "instalar software"),
        )
        for terms, attr, label in intent_permissions:
            if any(term in normalized for term in terms) and not bool(getattr(config, attr, False)):
                raise PCControlError(
                    f"Permiso desactivado para {label}. Activa {attr}=true en .env si realmente lo necesitas."
                )

    def _screen_info(self) -> dict:
        info = {"width": 1920, "height": 1080, "image_b64": "", "hash": ""}
        shot = None
        try:
            import pyautogui
            width, height = pyautogui.size()
            shot = pyautogui.screenshot()
            import io, base64
            buf = io.BytesIO()
            shot.convert("RGB").save(buf, format="JPEG", quality=74)
            image_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            info.update({
                "width": width,
                "height": height,
                "image_b64": image_b64,
                # Firma de ESTA captura: no gastamos una segunda captura.
                "hash": screen_diff.signature_from_image(shot).hash,
            })
        except Exception as exc:
            print("[control-pc] captura no disponible:", exc)

        # --- contexto de UI real: ventanas y controles de la ventana activa ---
        if self.ui_context:
            info["windows"] = self._windows_context()
            info["ui_elements"] = self._elements_context()
            info["active_window"] = next(
                (w["title"] for w in info["windows"] if w.get("active")), ""
            )

        # --- OCR: los "ojos" cuando no hay modelo de visión ---
        # El árbol UIA dice qué BOTONES hay, pero no qué se está VIENDO: el
        # visor de la calculadora, el texto ya escrito en el Bloc de notas o el
        # resultado de una operación. Sin esto el modelo repite el mismo clic
        # porque no tiene forma de saber que ya surtió efecto.
        if getattr(config, "PC_OCR_CONTEXT", True) and shot is not None:
            info["screen_text"] = self._ocr_context(shot)
        return info

    def _ocr_context(self, shot) -> str:
        """Texto visible de la ventana activa, recortado y sin ruido."""
        try:
            if not screen_text.get_engine().available():
                return ""
        except Exception:
            return ""
        try:
            recorte = shot
            rect = self._active_window_rect()
            if rect:
                izq, arr, der, aba = rect
                ancho, alto = shot.size
                izq, arr = max(0, izq), max(0, arr)
                der, aba = min(ancho, der), min(alto, aba)
                if der - izq > 40 and aba - arr > 40:
                    recorte = shot.crop((izq, arr, der, aba))
            texto = screen_text.read_screen(recorte)
            limite = int(getattr(config, "PC_OCR_MAX_CHARS", 900))
            return " ".join(texto.split())[:limite]
        except Exception as exc:
            print("[control-pc] OCR de contexto no disponible:", exc)
            return ""

    def _active_window_rect(self):
        """Rectángulo de la ventana con el foco, o None."""
        try:
            for w in self.platform.list_windows(limit=25):
                if w.get("active") and w.get("rect"):
                    return [int(v) for v in w["rect"]]
        except Exception:
            pass
        return None

    def _windows_context(self) -> list[dict]:
        """Ventanas abiertas y cuál tiene el foco (nunca revienta el ciclo).

        Las ventanas de la propia Yue se omiten: no son objetivos válidos y solo
        gastarían atención del modelo.
        """
        try:
            limite = int(getattr(config, "PC_UI_MAX_WINDOWS", 18))
            ventanas = self.platform.list_windows(limit=limite)
            propias = {tuple(r) for r in self._ignore_rects()}
            return [
                {"title": w.get("title", ""), "active": bool(w.get("active"))}
                for w in ventanas
                if tuple(w.get("rect") or ()) not in propias
            ]
        except Exception as exc:
            print("[control-pc] no pude listar ventanas:", exc)
            return []

    def _elements_context(self) -> list[dict]:
        """Hasta PC_UI_MAX_ELEMENTS controles visibles de la ventana activa."""
        try:
            limite = int(getattr(config, "PC_UI_MAX_ELEMENTS", 40))
            elementos = self.platform.active_window_elements(limit=limite)
            return [
                {"name": e.get("name", ""), "type": e.get("control_type", "")}
                for e in elementos
                if e.get("name")
            ]
        except Exception as exc:
            print("[control-pc] no pude leer los controles:", exc)
            return []

    def _direct_plan(self, instruction: str) -> list[dict]:
        raw = instruction.strip()
        n = _norm(raw)
        n = re.sub(r"^yue[,: ]+", "", n)

        url = re.search(r"https?://\S+", raw)
        if url and re.search(r"\b(abr\w*|ve|entra)\b", n):
            return [{"action": "open_url", "url": url.group(0)}]

        # Operaciones Office frecuentes sin LLM: una sola llamada COM.
        m = re.match(
            r"^(?:crea|crear)\s+(?:un|una)?\s*(?:nuevo|nueva)?\s*"
            r"(?:documento|libro|hoja|presentaci[oó]n)?\s*(?:de|en)?\s*"
            r"(word|excel|power\s*point)\s*$", raw, re.I,
        )
        if m:
            return [{"action": "office_create", "app": m.group(1)}]
        m = re.match(
            r"^(?:lee|leer)\s+(?:(?:el )?contenido\s+de\s+)?(?:el|la)?\s*"
            r"(?:documento|archivo|libro|hoja|presentaci[oó]n)?\s*(?:abierto|abierta)?\s*"
            r"(?:de|en)?\s*(word|excel|power\s*point)\s*$", raw, re.I,
        )
        if m:
            return [{"action": "office_read", "app": m.group(1)}]
        m = re.match(
            r"^(?:escribe|inserta|agrega|añade)\s+(.+?)\s+en\s+"
            r"(word|excel|power\s*point)\s*$", raw, re.I,
        )
        if m and not self._pide_contenido(m.group(1)):
            return [{"action": "office_write", "app": m.group(2),
                     "text": m.group(1).strip(), "new_document": False}]

        m_raw = re.match(
            r"^(?:yue[,: ]+)?(?:abre|inicia|ejecuta)\s+(?:el |la )?(.+?)\s+y\s+"
            r"(?:escribe|pon|redacta)\s*:?\s+(.+)$",
            raw, re.I,
        )
        if m_raw:
            app = m_raw.group(1).strip()
            texto = m_raw.group(2).strip()
            # Si te piden REDACTAR algo ("un parrafo sobre el Peru"), escribir eso
            # tal cual es absurdo: hay que generarlo. Se lo dejamos al
            # planificador con IA, que si sabe redactar.
            if self._pide_contenido(texto):
                return []
            if self._es_app_office(app):
                return [{"action": "office_write", "app": app, "text": texto,
                         "new_document": True}]
            return [
                {"action": "open_app", "name": app},
                {"action": "wait", "seconds": self._espera_de_apertura(app)},
                {"action": "focus_window", "title": app},
                {"action": "type_text", "text": texto},
            ]

        m_raw = re.match(
            r"^(?:yue[,: ]+)?(?:abre|inicia|ejecuta)\s+(?:el |la )?(.+?)\s+y\s+"
            r"(?:busca|busca en google)\s+(.+)$",
            raw, re.I,
        )
        if m_raw:
            return [
                {"action": "open_app", "name": m_raw.group(1).strip()},
                {"action": "wait", "seconds": 1.4},
                {"action": "hotkey", "keys": ["ctrl", "l"]},
                {"action": "type_text", "text": m_raw.group(2).strip()},
                {"action": "press", "key": "enter"},
            ]

        # Cierre de ventanas: se resuelve directamente y no se deja al modelo visual.
        m = re.match(
            r"^(?:cierra|cerrar)\s+(?:(?:la )?ventana(?: de)?|(?:el )?programa|"
            r"(?:la )?(?:aplicacion|aplicación|app))\s+(.+)$",
            n,
        )
        if m:
            return [{"action": "close_window", "title": m.group(1).strip()}]

        # Movimiento explícito de ventana por píxeles. Es una API nativa y queda
        # snapshot para poder deshacerlo después.
        m = re.match(
            r"^(?:mueve|mover)\s+(?:la\s+)?ventana(?:\s+(?:de|del))?\s+(.+?)\s+"
            r"(?:a|hasta)\s+(?:x\s*)?(-?\d+)\s*[,; ]+\s*(?:y\s*)?(-?\d+)\s*$",
            n,
        )
        if m:
            return [{
                "action": "move_window", "title": m.group(1).strip(),
                "x": int(m.group(2)), "y": int(m.group(3)),
            }]

        # YouTube: "pon la cancion X en youtube" debe REPRODUCIR, no dejar una
        # lista de resultados abierta. Se resuelve el id del video y se abre la
        # URL de reproduccion; si YouTube no coopera, cae a la busqueda normal.
        yt = self._plan_youtube(raw)
        if yt:
            return yt

        # Carpetas conocidas: abrirlas por ruta es un paso fiable. Navegar el
        # árbol del Explorador a clics no lo es: «Descargas» vive en un panel
        # que UIA a veces no expone y el modelo se queda dando vueltas.
        # Carpetas conocidas: abrirlas por ruta es un paso fiable. Navegar el
        # arbol del Explorador a clics no lo es: «Descargas» vive en un panel
        # que UIA a veces no expone y el modelo se queda dando vueltas.
        # Ojo: aqui hace falta quitar acentos y el punto final, que _norm no toca
        # («entra en la carpeta Descargas.» -> «...descargas.» no casaba con $).
        nav = self._nav_norm(raw)
        m = re.match(
            r"^(?:abre|abrir|abreme|entra|entrar|ve|muestra)\b(?P<medio>.*?)\b"
            r"(?P<carpeta>descargas|documentos|escritorio|imagenes|musica|videos|"
            r"downloads|documents|desktop|pictures|music)$",
            nav,
        )
        # Si la orden ademas escribe o busca algo, ya no es una simple navegacion.
        if m and not re.search(r"\b(escrib|busca|copia|pega|guarda|crea|renombra|borra)", nav):
            medio = m.group("medio").strip()
            # Solo relleno ("en la", "a mi") o una referencia explicita a navegar.
            relleno = {"", "en", "el", "la", "a", "al", "de", "mi", "mis", "tus",
                       "sus", "los", "las",
                       "y", "carpeta", "explorador", "archivos", "entra", "ve"}
            palabras = set(medio.split())
            if palabras <= relleno or "carpeta" in palabras or "explorador" in palabras:
                carpeta = self._carpeta_conocida(m.group("carpeta"))
                if carpeta:
                    return [{"action": "open_path", "path": str(carpeta)}]

        # Orden frecuente: "abre la calculadora y haz clic en el 3".
        m_raw = re.match(
            r"^(?:yue[,: ]+)?(?:abre|inicia|ejecuta)\s+(?:el |la )?(.+?)\s+y\s+"
            r"(?:(?:haz|da)\s+)?(?:clic|click|pulsa|presiona)\s+(?:en\s+)?"
            r"(?:el\s+bot[oó]n\s+)?(.+?)\s*$",
            raw, re.I,
        )
        if m_raw:
            app = m_raw.group(1).strip()
            target = self._control_name(m_raw.group(2).strip())
            return [
                {"action": "open_app", "name": app},
                {"action": "wait", "seconds": 1.1},
                {"action": "focus_window", "title": app},
                {"action": "click_element", "name": target, "control_type": "Button"},
            ]

        shortcuts = (
            (r"^(?:selecciona todo|seleccionar todo)$", ["ctrl", "a"]),
            (r"^(?:copia|copiar)$", ["ctrl", "c"]),
            (r"^(?:pega|pegar)$", ["ctrl", "v"]),
            (r"^(?:guarda|guardar)$", ["ctrl", "s"]),
            (r"^(?:deshaz|deshacer)$", ["ctrl", "z"]),
            (r"^(?:rehaz|rehacer)$", ["ctrl", "y"]),
            (r"^(?:nueva pestaña|abre una nueva pestaña)$", ["ctrl", "t"]),
            (r"^(?:cierra la pestaña|cerrar pestaña)$", ["ctrl", "w"]),
            (r"^(?:recarga|actualiza la pagina|refresca)$", ["ctrl", "r"]),
            (r"^(?:ve atras|volver atras|atras)$", ["alt", "left"]),
            (r"^(?:ve adelante|adelante)$", ["alt", "right"]),
        )
        for pattern, keys in shortcuts:
            if re.match(pattern, n):
                return [{"action": "hotkey", "keys": keys}]

        # Las órdenes largas se dejan al planificador visual iterativo.
        if " y " in n or " luego " in n or " despues " in n or " después " in n:
            return []

        m = re.match(r"^(?:abre|inicia|ejecuta)\s+(?:el |la )?(.+)$", n)
        if m:
            app = m.group(1).strip()
            if self._es_app_office(app):
                return [{"action": "office_create", "app": app}]
            return [{"action": "open_app", "name": app}]

        m = re.match(r"^(?:escribe|pon|redacta)\s+(.+)$", raw, re.I)
        if m:
            contenido = m.group(1)
            # "escribe hola" se teclea; "escribe un ensayo sobre X" hay que
            # REDACTARLO: se deja al planificador con IA, que si sabe generar.
            if self._pide_contenido(contenido):
                return []
            return [{"action": "type_text", "text": contenido}]

        m = re.match(r"^(?:busca en internet|busca en google|googlea)\s+(.+)$", raw, re.I)
        if m:
            return [{"action": "search_web", "query": m.group(1)}]

        m = re.match(r"^(?:presiona|pulsa)\s+(.+)$", n)
        if m:
            keys = [k.strip() for k in re.split(r"\s*\+\s*|\s+y\s+", m.group(1)) if k.strip()]
            if len(keys) == 1:
                return [{"action": "press", "key": keys[0]}]
            return [{"action": "hotkey", "keys": keys}]

        # Clic directo por objetivo nombrado: evita una llamada al LLM y evita
        # coordenadas. El texto explícito usa OCR; el resto usa UI Automation.
        m = re.match(
            r"^(?:(?:haz|da)\s+)?(?:(?:clic|click)(?!\s+derecho\b)|pulsa|presiona)\s+(?:en\s+)?"
            r"(?:el\s+)?texto\s+[«\"']?(.+?)[»\"']?$", raw, re.I,
        )
        if m:
            return [{"action": "click_text", "text": m.group(1).strip()}]
        m = re.match(
            r"^(?:(?:haz|da)\s+)?(?:(?:clic|click)(?!\s+derecho\b)|pulsa|presiona)\s+(?:en\s+)?"
            r"(?:(?:el|la)\s+)?(?:bot[oó]n\s+)?[«\"']?(.+?)[»\"']?$", raw, re.I,
        )
        if m and self._position_words(n)[0] is None:
            objetivo = m.group(1).strip()
            if objetivo and not re.fullmatch(r"(?:arriba|abajo|centro|izquierda|derecha)(?:\s+.+)?", self._nav_norm(objetivo)):
                return [{"action": "click_element", "name": self._control_name(objetivo)}]

        if "clic derecho" in n or "click derecho" in n:
            x, y = self._position_words(n)
            if x is not None:
                return [{"action": "right_click", "x_pct": x, "y_pct": y}]
        if "doble clic" in n or "doble click" in n:
            x, y = self._position_words(n)
            if x is not None:
                return [{"action": "double_click", "x_pct": x, "y_pct": y}]
        if "clic" in n or "click" in n:
            x, y = self._position_words(n)
            if x is not None:
                return [{"action": "click", "x_pct": x, "y_pct": y}]

        if "desplaza" in n or "scroll" in n:
            amount = -700 if "abajo" in n else 700
            return [{"action": "scroll", "amount": amount}]
        return []

    def _plan_youtube(self, raw: str) -> list[dict]:
        """Convierte «pon X en youtube» en un plan que de verdad reproduce."""
        nav = self._nav_norm(raw)
        if "youtube" not in nav and "you tube" not in nav:
            return []
        # Verbos que significan "quiero oirlo/verlo ya", no "abre youtube".
        patrones = (
            r"^(?:yue[,: ]+)?(?:pon|ponme|reproduce|reproduceme|escucha|escuchemos|"
            r"quiero (?:oir|escuchar|ver)|dale a|toca|suena|"
            r"busca(?:me)?(?:\s+y\s+(?:pon|reproduce|reproducelo))?)\s+(?P<q>.+)$",
            r"^(?:yue[,: ]+)?(?:abre|entra a|ve a)\s+youtube\s+y\s+"
            r"(?:pon|reproduce|busca|escucha|ponme)\s+(?P<q>.+)$",
        )
        consulta = ""
        for patron in patrones:
            m = re.match(patron, nav)
            if m:
                consulta = m.group("q")
                break
        if not consulta:
            return []
        # Quitamos las coletillas de sitio: "en youtube", "de youtube", "por youtube".
        consulta = re.sub(r"\b(?:en|de|por|desde|con)\s+you\s?tube\b", " ", consulta)
        consulta = re.sub(r"\byou\s?tube\b", " ", consulta)
        consulta = re.sub(r"^\s*(?:la|el|una|un)\s+(?:cancion|musica|video|tema|rola)\s+(?:de\s+)?",
                          "", consulta)
        consulta = re.sub(r"\s+", " ", consulta).strip(" .,")
        if not consulta:
            return []
        url, reproducible = youtube.resolver(consulta)
        estado = "reproduciendo" if reproducible else "solo la busqueda (YouTube no dio el id)"
        print(f"[youtube] «{consulta}» -> {estado}")
        return [{"action": "open_url", "url": url}]

    # Apps que tardan en arrancar de verdad. El Bloc de notas abre en medio
    # segundo; Word puede tardar ocho con su pantalla de bienvenida.
    _APPS_LENTAS = ("word", "excel", "powerpoint", "outlook", "chrome", "firefox",
                    "edge", "photoshop", "visual studio", "illustrator")

    # Apps de ofimática que arrancan en una PANTALLA DE INICIO (galería de
    # plantillas), no en un documento en blanco. Si escribes ahí, el texto se
    # pierde o cae en el buscador de plantillas. Hay que crear el lienzo antes.
    _APPS_OFFICE = ("word", "winword", "excel", "powerpoint", "powerpnt")

    @classmethod
    def _espera_de_apertura(cls, app: str) -> float:
        base = cls._nav_norm(app)
        for lenta in cls._APPS_LENTAS:
            if lenta in base:
                return 5.0
        return 1.4

    @classmethod
    def _es_app_office(cls, app: str) -> bool:
        """¿La app abre en la galería de plantillas en vez de en blanco?"""
        base = cls._nav_norm(app)
        return any(nombre in base for nombre in cls._APPS_OFFICE)

    @classmethod
    def _pasos_documento_en_blanco(cls, app: str) -> list[dict]:
        """Ctrl+N crea un documento/hoja/presentación en blanco en Office.

        Funciona tanto desde la pantalla de inicio como con la app ya abierta,
        y no toca lo que el usuario tuviera sin guardar: solo añade un lienzo
        nuevo. Para el resto de apps devuelve [] a propósito: el Bloc de notas
        ya abre en blanco y en Chrome/Edge Ctrl+N abriría otra ventana.
        """
        if not cls._es_app_office(app):
            return []
        return [
            {"action": "hotkey", "keys": ["ctrl", "n"]},
            {"action": "wait", "seconds": 1.5},
        ]

    @classmethod
    def _pide_contenido(cls, texto: str) -> bool:
        """¿«escribe X» significa redactar X, o teclear X literalmente?

        Se peca de generoso a propósito: es preferible que Yue redacte de más
        (el usuario borra si no le vale) a que teclee «sobre la contaminación»
        tal cual. Solo se considera literal cuando el usuario lo marca de forma
        explícita (comillas o dos puntos) o cuando es texto corto sin ninguna
        señal de redacción.
        """
        crudo = (texto or "").strip()
        # Comillas o dos puntos = es literal, lo quiere tal cual.
        if crudo[:1] in {'"', "'", ":", "\u00ab"}:
            return False
        base = cls._nav_norm(crudo)
        if not base:
            return False
        sustantivos = (r"parrafo|parrafos|carta|lista|plan|resumen|poema|ensayo|"
                       r"cuento|historia|correo|mensaje|articulo|informe|redaccion|"
                       r"nota|texto|discurso|receta|cancion|guion|acta|solicitud|"
                       r"curriculum|cv|biografia|descripcion|definicion|borrador|"
                       r"email|reseña|resena|comentario|conclusion|introduccion|"
                       r"dialogo|entrada|publicacion|post|documento")
        # "un parrafo sobre...", "una lista de...", "tres parrafos de..."
        if re.match(rf"^(?:un|una|unos|unas|el|la|los|las|mi|mis|"
                    rf"dos|tres|cuatro|cinco)?\s*(?:{sustantivos})\b", base):
            return True
        # Verbo de redacción al frente: "hazme una carta", "genera un correo",
        # "explica la fotosintesis", "resume el articulo", "describe a...".
        verbos = (r"haz|hazme|hazle|genera|generame|crea|creame|inventa|"
                  r"compon|componme|desarrolla|elabora|prepara|preparame|"
                  r"resume|resumeme|explica|explicame|describe|describeme|"
                  r"cuenta|cuentame|narra|redacta|redactame|escribeme")
        if re.match(rf"^(?:{verbos})\b", base):
            return True
        # Marcador de tema al frente: "sobre X", "acerca de X", "algo sobre X".
        if re.match(r"^(?:sobre|acerca de|algo (?:sobre|acerca)|un texto sobre)\b", base):
            return True
        return False

    @staticmethod
    def _nav_norm(text: str) -> str:
        """Minusculas, sin acentos y sin puntuacion final. Solo para navegacion."""
        import unicodedata
        base = _norm(text)
        base = "".join(
            c for c in unicodedata.normalize("NFD", base)
            if unicodedata.category(c) != "Mn"
        )
        return base.strip(" .,;:!?\u00a1\u00bf\"'")

    @staticmethod
    def _carpeta_conocida(nombre: str):
        """Ruta real de una carpeta del usuario, o None si no existe.

        En Windows en español la carpeta se VE como «Descargas» pero en disco
        sigue llamándose «Downloads»; por eso se prueban los dos nombres.
        """
        equivalencias = {
            "descargas": ("Downloads", "Descargas"),
            "downloads": ("Downloads", "Descargas"),
            "documentos": ("Documents", "Documentos"),
            "documents": ("Documents", "Documentos"),
            "escritorio": ("Desktop", "Escritorio"),
            "desktop": ("Desktop", "Escritorio"),
            "imagenes": ("Pictures", "Imágenes", "Imagenes"),
            "pictures": ("Pictures", "Imágenes", "Imagenes"),
            "musica": ("Music", "Música", "Musica"),
            "music": ("Music", "Música", "Musica"),
            "videos": ("Videos", "Vídeos"),
        }
        for candidato in equivalencias.get(PCController._nav_norm(nombre), ()):
            ruta = Path.home() / candidato
            if ruta.exists():
                return ruta
        return None

    @staticmethod
    def _control_name(text: str) -> str:
        clean = _norm(text)
        clean = re.sub(r"^(?:el|la)\s+", "", clean).strip(" .")
        return _CONTROL_NAME_ALIASES.get(clean, text.strip())

    @staticmethod
    def _position_words(text: str):
        x, y = 0.5, 0.5
        found = False
        if "izquierda" in text:
            x, found = 0.18, True
        elif "derecha" in text:
            x, found = 0.82, True
        elif "centro" in text or "medio" in text:
            x, found = 0.5, True
        if "arriba" in text:
            y, found = 0.18, True
        elif "abajo" in text:
            y, found = 0.82, True
        elif "centro" in text or "medio" in text:
            y, found = 0.5, True
        return (x, y) if found else (None, None)

    def _validate_plan(
        self,
        actions: Iterable[dict],
        remaining: int | None = None,
        discarded: list[str] | None = None,
    ) -> list[dict]:
        """Valida el plan del ciclo.

        Un paso malformado ya NO aborta todo: se descarta y se anota en
        `discarded` (y en self.last_discarded) para que el siguiente ciclo lo
        sepa. Solo se lanza error si TODOS los pasos son inválidos.
        """
        if isinstance(actions, dict):
            actions = actions.get("actions", [])
        if not isinstance(actions, list) or not actions:
            raise PCControlError("La IA no produjo un plan válido.")
        limit = min(self.max_actions, remaining if remaining is not None else self.max_actions)
        if len(actions) > limit:
            actions = actions[:limit]

        if discarded is None:
            discarded = []
        self.last_discarded = discarded

        clean = []
        for item in actions:
            try:
                step = self._validate_step(item)
            except PCControlError as exc:
                discarded.append(str(exc))
                print("[control-pc] paso descartado:", exc)
                continue
            except Exception as exc:
                discarded.append(f"paso ilegible ({exc})")
                print("[control-pc] paso ilegible descartado:", exc)
                continue
            clean.append(step)

        if not clean:
            motivos = "; ".join(discarded[:4]) or "plan vacío"
            raise PCControlError(f"Ningún paso del ciclo era válido ({motivos}).")
        return clean

    def _validate_step(self, item) -> dict:
        """Valida UN paso. Lanza PCControlError si hay que descartarlo."""
        original = item
        # _canon_action entiende camelCase, sinónimos y {"name": "click"}.
        accion_cruda = item.get("action") if isinstance(item, dict) else None
        action = _canon_action(accion_cruda)
        if not action:
            # Antes de tirarlo, intentamos entenderlo (type/tool/{"open_app":...}).
            reparado = _coerce_step(item)
            if reparado is None:
                raise PCControlError(
                    f"Acción no permitida: {_texto_accion(accion_cruda)} · "
                    f"recibí {_resumen_paso(original)}"
                )
            print(f"[control-pc] paso reparado: {_resumen_paso(original)} "
                  f"-> {reparado.get('action')}")
            item = reparado
            action = str(item.get("action", ""))
        step = dict(item) if isinstance(item, dict) else {}
        step["action"] = action

        # Si el modelo dio coordenadas Y también nombró el objetivo, convertimos
        # el paso al método real. Las coordenadas quedan como último recurso.
        if action in {"click", "right_click", "double_click"} and str(step.get("name", "")).strip():
            action = "click_element"
            step["action"] = action
            step["name"] = str(step.get("name", "")).strip()
        elif action in {"click", "right_click", "double_click"} and str(step.get("text", "")).strip():
            action = "click_text"
            step["action"] = action
            step["text"] = str(step.get("text", "")).strip()

        if action in {"click", "right_click", "double_click", "move"}:
            step["x_pct"] = min(1.0, max(0.0, float(step.get("x_pct", 0.5))))
            step["y_pct"] = min(1.0, max(0.0, float(step.get("y_pct", 0.5))))
            step["button"] = str(step.get("button", "right" if action == "right_click" else "left")).lower()
            if step["button"] not in _ALLOWED_BUTTONS:
                step["button"] = "left"
        elif action == "drag":
            for key in ("from_x_pct", "from_y_pct", "to_x_pct", "to_y_pct"):
                step[key] = min(1.0, max(0.0, float(step.get(key, 0.5))))
            step["duration"] = min(3.0, max(0.1, float(step.get("duration", 0.6))))
            step["button"] = str(step.get("button", "left")).lower()
            if step["button"] not in _ALLOWED_BUTTONS:
                step["button"] = "left"
        elif action == "wait":
            step["seconds"] = min(6.0, max(0.05, float(step.get("seconds", 0.5))))
        elif action == "type_text":
            step["text"] = str(step.get("text", ""))[: config.PC_MAX_TYPE_CHARS]
        elif action == "press":
            # El modelo escribe press{key:"ctrl+c"} o parte el atajo en tres
            # press sueltos ("ctrl", "c"). Antes, un press{key:"ctrl"} lanzaba
            # «Tecla no permitida: ctrl» EN EJECUCIÓN y mataba la orden entera.
            # Aquí se convierte en hotkey si tiene sentido, y si no, se descarta
            # como un paso más (el ciclo siguiente lo ve y rectifica).
            bruto = str(step.get("key", "")).lower().strip()
            partes = [p.strip() for p in re.split(r"[+\-\s]+", bruto) if p.strip()]
            if len(partes) > 1 and partes[0] in _SAFE_MODIFIERS:
                step = {"action": "hotkey", "keys": partes}
                action = "hotkey"
            elif bruto in _SAFE_MODIFIERS:
                raise PCControlError(
                    f"press«{bruto}» es solo un modificador: usa "
                    f"hotkey{{keys:[\"{bruto}\",\"c\"]}} con la tecla que lo acompaña."
                )
            else:
                step["key"] = bruto
        elif action in {"scroll", "hscroll"}:
            step["amount"] = int(max(-2200, min(2200, int(step.get("amount", 0)))))

        # --- acciones nuevas basadas en elementos reales ---
        elif action == "click_element":
            nombre = str(step.get("name", "")).strip()
            if not nombre:
                raise PCControlError("click_element sin «name»")
            step["name"] = nombre[:120]
            tipo = str(step.get("control_type", "")).strip()
            if tipo and tipo.lower() not in _CONTROL_TYPES:
                tipo = ""              # tipo raro: buscamos por nombre y ya
            step["control_type"] = tipo
            step["button"] = str(step.get("button", "left")).lower()
            if step["button"] not in _ALLOWED_BUTTONS:
                step["button"] = "left"
        elif action == "click_text":
            texto = str(step.get("text", "")).strip()
            if not texto:
                raise PCControlError("click_text sin «text»")
            step["text"] = texto[:80]
            step["button"] = str(step.get("button", "left")).lower()
            if step["button"] not in _ALLOWED_BUTTONS:
                step["button"] = "left"
        elif action in {"focus_window", "close_window", "move_window"}:
            titulo = str(step.get("title", "")).strip()
            if not titulo:
                raise PCControlError(f"{action} sin «title»")
            step["title"] = titulo[:120]
            if action == "move_window":
                for key in ("x", "y"):
                    step[key] = int(step.get(key, 0))
                for key in ("width", "height"):
                    if step.get(key) is not None:
                        step[key] = max(80, int(step[key]))
        elif action in {"office_create", "office_write", "office_save", "office_read"}:
            app = str(step.get("app") or step.get("name") or "").strip()
            if not app or not self._es_app_office(app):
                raise PCControlError(f"{action} necesita app=word, excel o powerpoint")
            step["app"] = app[:40]
            if action == "office_write":
                step["text"] = str(step.get("text", ""))[: config.PC_MAX_TYPE_CHARS]
                step["title"] = str(step.get("title", ""))[:200]
                step["bold"] = bool(step.get("bold", False))
                step["new_document"] = bool(step.get("new_document", True))
                step["path"] = str(step.get("path", ""))[:500]
            elif action == "office_save":
                step["path"] = str(step.get("path", ""))[:500]
        return step

    def _execute_action(self, step: dict) -> ActionResult:
        self._check_cancelled()
        action = step["action"]
        if action in {"move_window", "close_window", "drag"} and not step.get("_reversible_snapshot"):
            snapshot = self._capture_reversible_snapshot(step)
            if snapshot:
                step["_reversible_snapshot"] = snapshot
        antes = self._before_signature(action)    # firma previa (solo si toca verificar)
        extra = ""
        try:
            if action == "open_app":
                extra = self._open_app(str(step.get("name", ""))) or ""
            elif action == "open_url":
                self._open_url(str(step.get("url", "")))
            elif action == "open_path":
                self.open_path(str(step.get("path", "")))
            elif action == "search_web":
                query = urllib.parse.quote_plus(str(step.get("query", "")))
                webbrowser.open(f"https://www.google.com/search?q={query}")
            elif action == "wait":
                self._interruptible_wait(float(step["seconds"]))
            elif action == "screenshot":
                extra = str(self._save_screenshot(str(step.get("name", ""))))
            # --- acciones nuevas basadas en elementos reales ---
            elif action in _SOFT_FAIL_ACTIONS:
                handler = {
                    "click_element": self._act_click_element,
                    "click_text": self._act_click_text,
                    "focus_window": self._act_focus_window,
                    "list_windows": self._act_list_windows,
                    "close_window": self._act_close_window,
                    "move_window": self._act_move_window,
                    "office_create": self._act_office_create,
                    "office_write": self._act_office_write,
                    "office_save": self._act_office_save,
                    "office_read": self._act_office_read,
                }[action]
                try:
                    extra = handler(step)
                except PCControlError as exc:
                    if "cancelada" in str(exc).lower():
                        raise
                    # No abortamos el plan: el ciclo siguiente leerá el motivo.
                    return ActionResult(action, False, f"{self._describe(step)} · {exc}")
            else:
                extra = self._pyautogui_action(step) or ""
            detalle = self._describe(step) + (f" · {extra}" if extra else "")
            return self._verify_result(ActionResult(action, True, detalle), antes, step)
        except PCControlError:
            raise
        except Exception as exc:
            raise PCControlError(f"Falló {action}: {exc}") from exc

    def _capture_reversible_snapshot(self, step: dict) -> dict | None:
        """Captura solo lo necesario para un deshacer conservador."""
        action = str(step.get("action", ""))
        try:
            if action in {"move_window", "close_window"}:
                snap = self.platform.window_snapshot(str(step.get("title", "")))
                if snap:
                    return {"kind": action, "window": snap,
                            "windows": self.platform.windows_snapshot()}
            if action == "drag":
                return {
                    "kind": "drag",
                    "active_window": self.platform.active_window_title(),
                    "windows": self.platform.windows_snapshot(),
                    "from_x_pct": float(step.get("from_x_pct", 0.5)),
                    "from_y_pct": float(step.get("from_y_pct", 0.5)),
                    "to_x_pct": float(step.get("to_x_pct", 0.5)),
                    "to_y_pct": float(step.get("to_y_pct", 0.5)),
                    "button": str(step.get("button", "left")),
                    "duration": float(step.get("duration", 0.6)),
                }
        except Exception as exc:
            print("[control-pc] snapshot reversible no disponible:", exc)
        return None

    # ------------------------------------------------------------------
    # Acciones nuevas: elementos reales, texto en pantalla y ventanas
    # ------------------------------------------------------------------
    def _act_click_element(self, step: dict) -> str:
        """Clica el centro REAL del control cuyo nombre coincida (difuso)."""
        nombre = str(step.get("name", ""))
        tipo = str(step.get("control_type", "")) or None
        if not self.platform.is_available():
            raise PCControlError(
                "click_element necesita pywinauto en Windows (pip install pywinauto)."
            )
        encontrado = self.platform.find_element_center(nombre, tipo)
        if not encontrado:
            # El modelo pide «7» pero el control real se llama «Siete»; o acierta
            # el nombre y falla el control_type. Antes de rendirnos (y quemar un
            # ciclo entero) probamos el alias y sin filtro de tipo.
            for otro_nombre, otro_tipo in (
                (self._control_name(nombre), tipo),
                (nombre, None),
                (self._control_name(nombre), None),
            ):
                if not otro_nombre or (otro_nombre == nombre and otro_tipo == tipo):
                    continue
                encontrado = self.platform.find_element_center(otro_nombre, otro_tipo)
                if encontrado:
                    break
        if not encontrado:
            raise PCControlError(
                f"No encontré un control visible parecido a «{nombre}» en la ventana activa."
            )
        x, y, real = encontrado
        self._click_xy(x, y, str(step.get("button", "left")))
        return f"método=elemento · «{real}» en ({x},{y})"

    def _act_click_text(self, step: dict) -> str:
        """Clica el centro del texto visible localizado con OCR."""
        texto = str(step.get("text", ""))
        try:
            encontrado = screen_text.find_text(texto)
        except screen_text.OCRError as exc:
            raise PCControlError(str(exc)) from exc
        if not encontrado:
            raise PCControlError(f"El OCR no encontró «{texto}» en la pantalla.")
        x, y, real = encontrado
        self._click_xy(x, y, str(step.get("button", "left")))
        return f"método=texto · texto «{real}» en ({x},{y})"

    def _act_focus_window(self, step: dict) -> str:
        try:
            real = self.platform.focus_window(str(step.get("title", "")))
            if self.platform.is_available():
                self.window_manager.wait_focused(real, timeout=5.0)
        except Exception as exc:
            raise PCControlError(str(exc)) from exc
        return f"foco en «{real}»"

    def _act_list_windows(self, step: dict) -> str:
        ventanas = self.platform.list_windows(limit=int(getattr(config, "PC_UI_MAX_WINDOWS", 18)))
        if not ventanas:
            raise PCControlError("No pude leer las ventanas abiertas.")
        return "ventanas: " + self.platform.describe_windows(ventanas)

    def _act_close_window(self, step: dict) -> str:
        titulo = str(step.get("title", ""))
        # Doble muro: la lista de términos bloqueados también aplica al título.
        self._check_instruction(titulo)
        try:
            real = self.platform.close_window(titulo)
            if self.platform.is_available():
                self.window_manager.wait_absent(real, timeout=8.0)
        except Exception as exc:
            raise PCControlError(str(exc)) from exc
        return f"cerré «{real}»"

    def _act_move_window(self, step: dict) -> str:
        try:
            real = self.platform.move_window(
                str(step.get("title", "")), int(step.get("x", 0)), int(step.get("y", 0)),
                step.get("width"), step.get("height"),
            )
        except Exception as exc:
            raise PCControlError(str(exc)) from exc
        return f"ventana «{real}» movida con API nativa"

    def _office_enabled(self) -> bool:
        return bool(getattr(config, "PC_OFFICE_COM_ENABLED", True))

    def _act_office_create(self, step: dict) -> str:
        app = str(step.get("app", ""))
        if self._office_enabled():
            try:
                result = self.office.create_document(app)
                return f"método=COM · {result.detail}"
            except (OfficeUnavailable, OfficeError) as exc:
                print("[office] COM falló; uso respaldo:", exc)
                if not bool(getattr(config, "PC_OFFICE_FALLBACK_KEYBOARD", True)):
                    raise PCControlError(str(exc)) from exc
        return self._office_keyboard_fallback(app, text="", create_only=True)

    def _act_office_write(self, step: dict) -> str:
        app = str(step.get("app", ""))
        text = str(step.get("text", ""))
        if self._office_enabled():
            try:
                result = self.office.write_document(
                    app, text, title=str(step.get("title", "")),
                    bold=bool(step.get("bold", False)),
                    save_path=None,
                    new_document=False,
                )
                extra = f" · {result.path}" if result.path else ""
                return f"método=COM · {result.detail}{extra}"
            except (OfficeUnavailable, OfficeError) as exc:
                print("[office] COM falló; uso respaldo:", exc)
                if not bool(getattr(config, "PC_OFFICE_FALLBACK_KEYBOARD", True)):
                    raise PCControlError(str(exc)) from exc
        return self._office_keyboard_fallback(app, text=text, create_only=False)

    def _act_office_save(self, step: dict) -> str:
        app = str(step.get("app", ""))
        com_error: Exception | None = None
        if self._office_enabled():
            try:
                result = self.office.save_document(app, str(step.get("path", "")) or None)
                return f"método=COM · {result.detail} · {result.path}"
            except (OfficeUnavailable, OfficeError) as exc:
                com_error = exc
                print("[office] COM falló al guardar; uso respaldo:", exc)
        if not bool(getattr(config, "PC_OFFICE_FALLBACK_KEYBOARD", True)):
            raise PCControlError(str(com_error or "Office COM está desactivado."))
        # Ctrl+S es seguro; si hay path se usa Guardar como de forma simulada solo
        # como último recurso. El permiso de sobrescritura sigue validándose aparte.
        if step.get("path"):
            before_title = self.platform.active_window_title()
            self._pyautogui_action({"action": "hotkey", "keys": ["ctrl", "shift", "s"]})
            self._wait_for_save_dialog(before_title)
            self._pyautogui_action({"action": "type_text", "text": str(step["path"])})
            self._pyautogui_action({"action": "press", "key": "enter"})
            return "método=teclado-fallback · guardar como"
        self._pyautogui_action({"action": "hotkey", "keys": ["ctrl", "s"]})
        return "método=teclado-fallback · guardar"

    def _act_office_read(self, step: dict) -> str:
        try:
            result = self.office.read_open_document(str(step.get("app", "")))
            # El contenido completo queda en el detalle del resultado para que el
            # llamador/LLM pueda usarlo, con límite para no inflar el historial.
            content = result.content[:2000]
            return f"método=COM · {result.detail} · contenido: {content}"
        except (OfficeUnavailable, OfficeError) as exc:
            raise PCControlError(str(exc)) from exc

    def _office_keyboard_fallback(self, app: str, text: str, create_only: bool) -> str:
        self._open_app(app)  # ApplicationManager espera una ventana real y reutiliza instancias.
        try:
            self.platform.focus_window(app)
            self.window_manager.wait_focused(app, timeout=5.0)
        except Exception:
            # En entornos sin UI Automation, el launcher ya realizó la mejor
            # comprobación disponible y el verificador externo decidirá el resultado.
            pass
        self._pyautogui_action({"action": "hotkey", "keys": ["ctrl", "n"]})
        self._wait_for_office_ready(app)
        if not create_only and text:
            self._pyautogui_action({"action": "type_text", "text": text})
        return "método=teclado-fallback · COM no disponible"

    def _wait_for_office_ready(self, app: str, timeout: float = 8.0) -> None:
        """Espera por foco/controles observables, nunca por un número fijo de segundos."""
        if not self.platform.is_available():
            return
        hints = _APP_WINDOW_HINTS.get(_APP_ALIASES.get(_norm(app), _norm(app)), (app,))
        self.agent_waiter.until(
            lambda: self.window_manager.find(hints) and (
                self.window_manager.active_title()
                or self.platform.active_window_elements(limit=12)
            ),
            timeout=timeout,
            description=f"Office listo ({app})",
        )

    def _wait_for_save_dialog(self, previous_title: str, timeout: float = 8.0) -> None:
        """Detecta el diálogo Guardar como mediante título o controles accesibles."""
        if not self.platform.is_available():
            return

        def dialog_ready():
            title = self.platform.active_window_title()
            if title and title != previous_title:
                normalized = _norm(title)
                if any(token in normalized for token in ("guardar", "save", "archivo", "file")):
                    return title
            for element in self.platform.active_window_elements(limit=35) or []:
                name = _norm(str(element.get("name", "")))
                if any(token in name for token in ("nombre de archivo", "file name", "guardar", "save")):
                    return element
            return None

        self.agent_waiter.until(
            dialog_ready, timeout=timeout, description="el diálogo Guardar como"
        )

    def _click_xy(self, x: int, y: int, button: str = "left"):
        """Clic en coordenadas ABSOLUTAS reales (no porcentuales)."""
        try:
            import pyautogui
        except Exception as exc:
            raise PCControlError("Instala pyautogui para poder clicar.") from exc
        if button not in _ALLOWED_BUTTONS:
            button = "left"
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = self.action_pause
        ancho, alto = pyautogui.size()
        x = int(min(max(0, x), ancho - 1))
        y = int(min(max(0, y), alto - 1))
        pyautogui.click(x, y, button=button)

    def safe_click(self, x=None, y=None, button: str = "left"):
        """Clic seguro reutilizable (p. ej. por accesibilidad / control por cabeza).

        Hereda EXACTAMENTE el mismo envelope de seguridad que el resto del control
        de PC: solo botones de _ALLOWED_BUTTONS (si no, cae a 'left'), coordenadas
        recortadas a la pantalla y FAILSAFE activo. Sin coordenadas, clica en la
        posición actual del cursor. No puede escribir, usar atajos ni disparar
        ninguna de las acciones bloqueadas: solo un clic puntual.
        """
        try:
            import pyautogui
        except Exception as exc:
            raise PCControlError("Instala pyautogui para poder clicar.") from exc
        if button not in _ALLOWED_BUTTONS:
            button = "left"
        ancho, alto = pyautogui.size()
        if x is None or y is None:
            pos = pyautogui.position()
            x = pos[0] if x is None else x
            y = pos[1] if y is None else y
        x = int(min(max(0, int(x)), ancho - 1))
        y = int(min(max(0, int(y)), alto - 1))
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = self.action_pause
        locks = getattr(self, "agent_locks", None)
        if locks is None:
            pyautogui.click(x, y, button=button)
        else:
            with locks.acquire(("mouse", "active_window"), timeout=5.0):
                pyautogui.click(x, y, button=button)

    # ------------------------------------------------------------------
    # Verificación por acción: ¿la pantalla cambió realmente?
    # ------------------------------------------------------------------
    def _ignore_rects(self) -> list[list[int]]:
        """Zonas a excluir de la comparación: las ventanas de la propia Yue."""
        try:
            return self.platform.own_window_rects()
        except Exception:
            return []

    def _before_signature(self, action: str):
        """Firma previa del pipeline legado.

        El agente nuevo captura y verifica de forma dinámica; evita duplicar la
        comprobación y sus antiguas esperas fijas cuando la llamada viene de él.
        """
        if getattr(getattr(self, "_agent_local", None), "verification_active", False):
            return None
        if not self.verify_actions or action not in _VERIFY_ACTIONS:
            return None
        try:
            # Las mismas zonas se reutilizan en la firma posterior.
            self._verify_rects = self._ignore_rects()
            return screen_diff.signature(self._verify_rects)
        except Exception as exc:
            print("[control-pc] no pude firmar la pantalla:", exc)
            return None

    def _verify_result(self, result: ActionResult, antes, step: dict | None = None) -> ActionResult:
        """Verificación legado por condición; el motor autónomo usa VerificationEngine."""
        if antes is None or not result.ok:
            return result

        def observed_effect():
            despues = screen_diff.signature(getattr(self, "_verify_rects", []))
            if despues and screen_diff.changed(antes, despues):
                return despues
            if self._foco_confirma(step):
                return "focus"
            return None

        try:
            observed = self.agent_waiter.until(
                observed_effect,
                timeout=float(getattr(config, "PC_AGENT_WAIT_TIMEOUT", 10.0)),
                description="el efecto visible de la acción",
            )
            if observed == "focus":
                return ActionResult(result.action, True, f"{result.detail} · con el foco puesto")
            return result
        except Exception:
            return ActionResult(result.action, False, f"{result.detail} · sin efecto visible")

    def _foco_confirma(self, step: dict | None) -> bool:
        """Un clic que no cambió la pantalla pero SÍ movió el foco, funcionó.

        Clicar dentro de un Bloc de notas vacío solo dibuja el cursor: cuatro
        píxeles que ninguna comparación de pantalla detecta. UIA sí sabe quién
        tiene el foco, y eso es la prueba real de que el clic entró.
        """
        if not step or step.get("action") != "click_element":
            return False
        try:
            return self.platform.element_has_focus(str(step.get("name", "")))
        except Exception:
            return False

    def _interruptible_wait(self, seconds: float):
        """Temporizador explícito cancelable; no se usa para esperar carga de UI."""
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            self._check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._cancel_event.wait(min(0.1, remaining))

    def _pyautogui_action(self, step: dict):
        """Ejecuta una acción de teclado/mouse y devuelve un detalle auditable."""
        try:
            import pyautogui
        except Exception as exc:
            raise PCControlError("Instala pyautogui para controlar teclado y mouse.") from exc
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = self.action_pause
        action = step["action"]
        width, height = pyautogui.size()

        def xy(x_pct, y_pct):
            return (
                int(float(x_pct) * max(1, width - 1)),
                int(float(y_pct) * max(1, height - 1)),
            )

        if action in {"click", "right_click", "double_click", "move"}:
            x, y = xy(step["x_pct"], step["y_pct"])
            button = step.get("button", "right" if action == "right_click" else "left")
            if action == "move":
                pyautogui.moveTo(x, y, duration=min(1.5, max(0.05, float(step.get("duration", 0.25)))))
                return f"método=coordenada · cursor a ({x},{y})"
            if action == "double_click":
                pyautogui.doubleClick(x, y, interval=0.12, button=button)
            else:
                pyautogui.click(x, y, clicks=min(3, max(1, int(step.get("clicks", 1)))), button=button)
            return f"método=coordenada · ({x},{y})"
        if action == "drag":
            sx, sy = xy(step["from_x_pct"], step["from_y_pct"])
            ex, ey = xy(step["to_x_pct"], step["to_y_pct"])
            pyautogui.moveTo(sx, sy, duration=0.15)
            pyautogui.dragTo(ex, ey, duration=step["duration"], button=step.get("button", "left"))
            return f"método=coordenada · arrastre ({sx},{sy})→({ex},{ey})"
        if action == "type_text":
            self._paste_text(step.get("text", ""), pyautogui, platform.system().lower())
            return "método=portapapeles/teclado"
        if action == "press":
            key = str(step.get("key", "")).lower().strip()
            if key not in _SAFE_KEYS and not (len(key) == 1 and key.isalnum()):
                raise PCControlError(f"Tecla no permitida: {key}")
            repeat = min(20, max(1, int(step.get("repeat", 1))))
            pyautogui.press("esc" if key == "escape" else key, presses=repeat, interval=0.04)
            return f"método=teclado · {key}"
        if action == "hotkey":
            keys = [str(k).lower().strip() for k in step.get("keys", [])][:4]
            if not keys or any(
                k not in _SAFE_MODIFIERS | _SAFE_KEYS and not (len(k) == 1 and k.isalnum())
                for k in keys
            ):
                raise PCControlError("Combinación de teclas no permitida.")
            blocked = {"alt", "f4"}.issubset(keys) or {"ctrl", "alt", "delete"}.issubset(keys)
            if blocked:
                raise PCControlError("Esa combinación puede cerrar o bloquear el sistema.")
            pyautogui.hotkey(*keys)
            return "método=teclado · " + "+".join(keys)
        if action == "scroll":
            pyautogui.scroll(step["amount"])
            return "método=rueda"
        if action == "hscroll":
            if hasattr(pyautogui, "hscroll"):
                pyautogui.hscroll(step["amount"])
            return "método=rueda-horizontal"
        return ""

    @staticmethod
    def _paste_text(text: str, pyautogui, system: str):
        try:
            import pyperclip
            previous = pyperclip.paste()
            pyperclip.copy(text)
            pyautogui.hotkey("command" if system == "darwin" else "ctrl", "v")
            # El evento de teclado es síncrono; el Clipboard Lock del agente evita
            # que otra tarea cambie el contenido durante esta operación.
            pyperclip.copy(previous)
        except Exception:
            pyautogui.write(text, interval=max(0.0, config.PC_TYPE_INTERVAL))

    def _save_screenshot(self, requested_name: str = "") -> Path:
        try:
            import pyautogui
        except Exception as exc:
            raise PCControlError("No pude tomar la captura: falta pyautogui.") from exc
        root = Path(getattr(config, "PC_SCREENSHOT_DIR", config.DATA_DIR / "screenshots"))
        root.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", requested_name).strip("_")[:40]
        name = safe or time.strftime("captura_%Y%m%d_%H%M%S")
        path = root / f"{name}.png"
        pyautogui.screenshot(str(path))
        return path

    def _open_url(self, url: str):
        url = url.strip()
        if not re.match(r"^https?://", url, re.I):
            raise PCControlError("Solo se permiten enlaces http o https.")
        webbrowser.open(url)

    def _open_app(self, requested: str):
        """Reutiliza una instancia existente o abre una sola y espera su ventana."""
        requested = _norm(requested)
        manager = getattr(self, "application_manager", None)
        if manager is None or not self.platform.is_available():
            return self._launch_app_process(requested)
        title, reused, detail = manager.ensure_open(
            requested,
            self._launch_app_process,
            timeout=float(getattr(config, "PC_AGENT_APP_TIMEOUT", 15.0)),
        )
        if reused:
            return f"ya estaba abierta; enfoqué «{title}»"
        return f"{detail} · ventana lista «{title}»"

    def _launch_app_process(self, requested: str):
        """Lanza el proceso sin decidir reutilización; ApplicationManager lo coordina."""
        requested = _norm(requested)
        system = platform.system().lower()
        if system == "windows":
            entry = self.apps.resolve(requested)
            if entry:
                try:
                    self.apps.launch(entry)
                    return f"catálogo dinámico ({entry.get('source', 'cache')})"
                except Exception as exc:
                    print("[apps] la entrada del catálogo falló; uso alias fijo:", exc)
        alias = _APP_ALIASES.get(requested, requested)
        commands = self._app_commands(system, alias)
        if commands is None:
            path = Path(requested).expanduser()
            if path.exists():
                self.open_path(path)
                return "ruta local"
            try:
                import pyautogui
                before = self.platform.active_window_title()
                if system == "darwin":
                    pyautogui.hotkey("command", "space")
                else:
                    pyautogui.press("win")
                waiter = getattr(self, "agent_waiter", SmartWaiter(self._check_cancelled))
                try:
                    waiter.until(
                        lambda: self.platform.active_window_title() != before
                        or any(str(e.get("name", "")).strip() for e in self.platform.active_window_elements(limit=10)),
                        timeout=2.5,
                        description="el buscador de aplicaciones",
                    )
                except Exception:
                    # Algunos menús Inicio no exponen UIA; se conserva el fallback
                    # sin introducir una pausa fija.
                    pass
                self._paste_text(requested, pyautogui, system)
                pyautogui.press("enter")
                return "búsqueda del sistema"
            except Exception as exc:
                raise PCControlError(f"Aplicación no reconocida: {requested}") from exc
        subprocess.Popen(commands, shell=False)
        return "inicio de proceso solicitado"

    @staticmethod
    def _focus_existing_app(alias: str, requested: str) -> str:
        hints = _APP_WINDOW_HINTS.get(alias, (requested, alias))
        try:
            windows = self.platform.list_windows(limit=40)
        except Exception:
            return ""
        best_title, best_score = "", 0.0
        for win in windows:
            title = str(win.get("title", ""))
            if not title:
                continue
            score = max(self.platform.similarity(hint, title) for hint in hints if hint)
            if score > best_score:
                best_title, best_score = title, score
        if best_title and best_score >= 0.72:
            try:
                return self.platform.focus_window(best_title)
            except Exception:
                return ""
        return ""

    @staticmethod
    def _app_commands(system: str, app: str):
        maps = {
            "windows": {
                "notepad": ["notepad.exe"], "calculator": ["calc.exe"],
                "paint": ["mspaint.exe"], "files": ["explorer.exe"],
                "settings": ["cmd", "/c", "start", "", "ms-settings:"],
                "chrome": ["cmd", "/c", "start", "", "chrome"],
                "edge": ["cmd", "/c", "start", "", "msedge"],
                "firefox": ["cmd", "/c", "start", "", "firefox"],
                "word": ["cmd", "/c", "start", "", "winword"],
                "excel": ["cmd", "/c", "start", "", "excel"],
                "powerpoint": ["cmd", "/c", "start", "", "powerpnt"],
                "spotify": ["cmd", "/c", "start", "", "spotify"],
                "discord": ["cmd", "/c", "start", "", "discord"],
            },
            "darwin": {
                "notepad": ["open", "-a", "TextEdit"], "calculator": ["open", "-a", "Calculator"],
                "paint": ["open", "-a", "Preview"], "files": ["open", "."],
                "settings": ["open", "x-apple.systempreferences:"],
                "chrome": ["open", "-a", "Google Chrome"], "edge": ["open", "-a", "Microsoft Edge"],
                "firefox": ["open", "-a", "Firefox"], "word": ["open", "-a", "Microsoft Word"],
                "excel": ["open", "-a", "Microsoft Excel"], "powerpoint": ["open", "-a", "Microsoft PowerPoint"],
                "spotify": ["open", "-a", "Spotify"], "discord": ["open", "-a", "Discord"],
            },
            "linux": {
                "notepad": ["gedit"], "calculator": ["gnome-calculator"],
                "paint": ["pinta"], "files": ["xdg-open", "."],
                "settings": ["gnome-control-center"], "chrome": ["google-chrome"],
                "edge": ["microsoft-edge"], "firefox": ["firefox"],
                "word": ["libreoffice", "--writer"], "excel": ["libreoffice", "--calc"],
                "powerpoint": ["libreoffice", "--impress"], "spotify": ["spotify"], "discord": ["discord"],
            },
        }
        return maps.get(system, maps["linux"]).get(app)

    @staticmethod
    def _describe(step: dict) -> str:
        action = step.get("action", "")
        descriptions = {
            "open_app": f"abrir {step.get('name', '')}",
            "open_url": "abrir un enlace",
            "open_path": f"abrir {step.get('path', '')}",
            "search_web": f"buscar {step.get('query', '')}",
            "type_text": "escribir texto",
            "press": f"presionar {step.get('key', '')}",
            "hotkey": "usar " + "+".join(step.get("keys", [])),
            "click": "hacer clic",
            "right_click": "hacer clic derecho",
            "double_click": "hacer doble clic",
            "move": "mover el cursor",
            "drag": "arrastrar un elemento",
            "scroll": "desplazar verticalmente",
            "hscroll": "desplazar horizontalmente",
            "wait": "esperar",
            "screenshot": "guardar una captura",
            "click_element": f"clic en el control «{step.get('name', '')}»",
            "click_text": f"clic en el texto «{step.get('text', '')}»",
            "focus_window": f"enfocar la ventana «{step.get('title', '')}»",
            "list_windows": "listar las ventanas abiertas",
            "close_window": f"cerrar la ventana «{step.get('title', '')}»",
            "move_window": f"mover la ventana «{step.get('title', '')}»",
            "office_create": f"crear un archivo nuevo en {step.get('app', '')}",
            "office_write": f"escribir en {step.get('app', '')} con Office nativo",
            "office_save": f"guardar el archivo de {step.get('app', '')}",
            "office_read": f"leer el archivo abierto de {step.get('app', '')}",
        }
        return descriptions.get(action, action)
