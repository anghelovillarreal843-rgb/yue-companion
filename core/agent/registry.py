"""Registro extensible de acciones y sus políticas de ejecución."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable, Any
import hashlib
import json

from .models import AgentTask


ResourceResolver = Callable[[AgentTask], Iterable[str]]
ActionHandler = Callable[[dict], Any]
ActionExpander = Callable[[dict], list[dict]]


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    name: str
    resources: tuple[str, ...] | ResourceResolver = ()
    idempotent: bool = False
    parallel_safe: bool = False
    default_timeout: float = 0.0
    verifier: str = "auto"
    handler: ActionHandler | None = None
    expander: ActionExpander | None = None
    description: str = ""
    parameters: str = ""

    def resources_for(self, task: AgentTask) -> tuple[str, ...]:
        value = self.resources(task) if callable(self.resources) else self.resources
        return tuple(dict.fromkeys(str(item) for item in value if item))


class ActionRegistry:
    def __init__(self):
        self._definitions: dict[str, ActionDefinition] = {}

    def register(self, definition: ActionDefinition, *, replace: bool = False) -> None:
        if definition.name in self._definitions and not replace:
            raise ValueError(f"La acción {definition.name!r} ya está registrada.")
        self._definitions[definition.name] = definition

    def register_action(
        self,
        name: str,
        handler: ActionHandler,
        *,
        resources: tuple[str, ...] | ResourceResolver = (),
        idempotent: bool = False,
        parallel_safe: bool = False,
        default_timeout: float = 15.0,
        verifier: str = "auto",
        expander: ActionExpander | None = None,
        description: str = "",
        parameters: str = "",
        replace_existing: bool = False,
    ) -> None:
        """Registra una capacidad completa sin modificar el núcleo del agente."""
        self.register(
            ActionDefinition(
                name=name, handler=handler, resources=resources,
                idempotent=idempotent, parallel_safe=parallel_safe,
                default_timeout=default_timeout, verifier=verifier, expander=expander,
                description=description, parameters=parameters,
            ),
            replace=replace_existing,
        )

    def bind_handler(self, name: str, handler: ActionHandler) -> None:
        """Asocia o reemplaza el ejecutor de una acción ya registrada."""
        self._definitions[name] = replace(self.get(name), handler=handler)

    def get(self, name: str) -> ActionDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise KeyError(f"Acción no registrada: {name}") from exc

    def has(self, name: str) -> bool:
        return name in self._definitions

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def planner_catalog(self) -> list[dict]:
        """Catálogo serializable que el planificador puede pasar al modelo."""
        return [
            {
                "action": item.name,
                "description": item.description or item.name.replace("_", " "),
                "parameters": item.parameters,
                "idempotent": item.idempotent,
                "parallel_safe": item.parallel_safe,
                "resources": list(item.resources) if isinstance(item.resources, tuple) else ["dynamic"],
            }
            for item in sorted(self._definitions.values(), key=lambda value: value.name)
        ]

    def expand(self, raw: dict) -> list[dict]:
        """Expande una capacidad compuesta mediante su plugin de normalización."""
        action = str(raw.get("action", "")) if isinstance(raw, dict) else ""
        if not action or not self.has(action):
            return [raw]
        expander = self.get(action).expander
        if expander is None:
            return [raw]
        expanded = expander(dict(raw))
        if not isinstance(expanded, list) or not expanded:
            raise ValueError(f"El expander de {action!r} no devolvió acciones.")
        return expanded

    def configure_task(self, task: AgentTask) -> AgentTask:
        definition = self.get(task.action)
        task.resources = definition.resources_for(task)
        task.idempotent = definition.idempotent
        task.parallel_safe = definition.parallel_safe
        if task.verification.kind == "auto":
            task.verification.kind = definition.verifier
        if task.verification.timeout <= 0:
            task.verification.timeout = definition.default_timeout
        return task


def _app_resources(task: AgentTask):
    app = str(task.params.get("app") or task.params.get("name") or "").strip().lower()
    base = ["active_window"]
    if app:
        base.append(f"app:{app}")
    return base


def _launcher_resources(task: AgentTask):
    # Un launcher desconocido puede caer al buscador del sistema y usar teclado
    # y portapapeles. El lock conservador evita interferencias aun si el alias
    # normal termina abriéndose solo mediante proceso.
    return (*_app_resources(task), "keyboard", "clipboard")


def _office_resources(task: AgentTask):
    # COM no necesita entrada física, pero su fallback sí.
    return (*_app_resources(task), "keyboard", "clipboard")


def _wait_resources(task: AgentTask):
    """Un wait_for de recurso se cumple al adquirir su lock correspondiente."""
    condition = str(task.params.get("condition", "")).strip().lower()
    mapping = {
        "cursor_available": ("mouse",),
        "mouse_available": ("mouse",),
        "keyboard_available": ("keyboard",),
        "clipboard_available": ("clipboard",),
        "active_window_available": ("active_window",),
    }
    return mapping.get(condition, ())


def _office_write_expander(raw: dict) -> list[dict]:
    """Migra la acción histórica crear+escribir+guardar a pasos atómicos."""
    new_document = bool(raw.get("new_document", False))
    save_path = str(raw.get("path", "") or "").strip()
    if not new_document and not save_path:
        clean = dict(raw)
        clean.pop("new_document", None)
        clean.pop("path", None)
        return [clean]

    seed = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    fallback_id = "office_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10]
    base_id = str(raw.get("id") or raw.get("task_id") or fallback_id)
    original_deps = raw.get("depends_on", raw.get("depends", []))
    if isinstance(original_deps, str):
        original_deps = [original_deps]
    policy = {
        key: raw[key] for key in ("max_attempts", "retries", "timeout", "allow_repeat")
        if key in raw
    }
    app = raw.get("app", "")
    output: list[dict] = []
    write_dependencies = list(original_deps or [])
    if new_document:
        create_id = f"{base_id}__create"
        output.append({
            "id": create_id, "action": "office_create", "app": app,
            "depends_on": list(original_deps or []), **policy,
        })
        write_dependencies = [create_id]

    write = {
        "id": base_id, "action": "office_write", "app": app,
        "text": raw.get("text", raw.get("content", "")),
        "depends_on": write_dependencies, **policy,
    }
    for key in ("title", "bold"):
        if key in raw:
            write[key] = raw[key]
    output.append(write)
    if save_path:
        output.append({
            "id": f"{base_id}__save", "action": "office_save", "app": app,
            "path": save_path, "depends_on": [base_id], **policy,
        })
    return output


def build_default_registry() -> ActionRegistry:
    reg = ActionRegistry()
    defs = [
        ActionDefinition("open_app", _launcher_resources, idempotent=True, verifier="app_open", description="Abrir o reutilizar una aplicación", parameters="name"),
        ActionDefinition("open_url", ("active_window",), idempotent=True, verifier="browser_ready", description="Abrir una URL en el navegador", parameters="url"),
        ActionDefinition("open_path", ("active_window",), idempotent=True, verifier="path_open", description="Abrir un archivo o carpeta", parameters="path"),
        ActionDefinition("search_web", ("active_window",), idempotent=True, verifier="browser_ready", description="Realizar una búsqueda web", parameters="query"),
        ActionDefinition("type_text", ("active_window", "keyboard", "clipboard"), idempotent=True, verifier="ui_effect", description="Escribir texto en el destino enfocado", parameters="text"),
        ActionDefinition("press", ("active_window", "keyboard"), verifier="ui_effect", description="Pulsar una tecla", parameters="key, presses opcional"),
        ActionDefinition("hotkey", ("active_window", "keyboard"), verifier="ui_effect", description="Ejecutar un atajo de teclado", parameters="keys"),
        ActionDefinition("click", ("active_window", "mouse"), verifier="ui_effect", description="Hacer clic por coordenada relativa", parameters="x_pct, y_pct, target"),
        ActionDefinition("right_click", ("active_window", "mouse"), verifier="ui_effect", description="Clic derecho", parameters="x_pct, y_pct"),
        ActionDefinition("double_click", ("active_window", "mouse"), verifier="ui_effect", description="Doble clic", parameters="x_pct, y_pct"),
        ActionDefinition("move", ("mouse",), verifier="cursor", description="Mover el cursor", parameters="x_pct, y_pct"),
        ActionDefinition("drag", ("active_window", "mouse"), verifier="ui_effect", description="Arrastrar el cursor", parameters="from_x_pct, from_y_pct, to_x_pct, to_y_pct"),
        ActionDefinition("scroll", ("active_window", "mouse"), verifier="ui_effect", description="Desplazamiento vertical", parameters="amount"),
        ActionDefinition("hscroll", ("active_window", "mouse"), verifier="ui_effect", description="Desplazamiento horizontal", parameters="amount"),
        ActionDefinition("wait", (), parallel_safe=True, verifier="none", description="Compatibilidad: espera breve transformada a condición", parameters="seconds"),
        ActionDefinition("wait_for", _wait_resources, parallel_safe=True, verifier="condition", description="Esperar dinámicamente una condición", parameters="condition, target, timeout"),
        ActionDefinition("screenshot", ("screen",), verifier="file_exists", description="Guardar captura de pantalla", parameters="path opcional"),
        ActionDefinition("click_element", ("active_window", "mouse"), verifier="ui_effect", description="Pulsar un control accesible por nombre", parameters="name, control_type opcional"),
        ActionDefinition("click_text", ("active_window", "mouse"), verifier="ui_effect", description="Localizar y pulsar texto visible mediante OCR", parameters="text"),
        ActionDefinition("focus_window", ("active_window",), idempotent=True, verifier="window_focused", description="Enfocar una ventana existente", parameters="title"),
        ActionDefinition("list_windows", ("active_window",), parallel_safe=True, verifier="none", description="Consultar las ventanas abiertas", parameters=""),
        ActionDefinition("close_window", ("active_window",), idempotent=True, verifier="window_closed", description="Cerrar una ventana concreta", parameters="title"),
        ActionDefinition("move_window", ("active_window", "mouse"), idempotent=True, verifier="window_moved", description="Mover una ventana", parameters="title, x, y"),
        # Office puede caer al fallback de teclado; por eso toma todos los locks
        # interactivos aunque COM normalmente no los necesite.
        ActionDefinition("office_create", _office_resources, idempotent=False, verifier="office_ready", description="Crear un documento de Office", parameters="app, template opcional"),
        ActionDefinition("office_write", _office_resources, idempotent=True, verifier="office_content", expander=_office_write_expander, description="Escribir contenido en el documento activo de Office", parameters="app, text, title opcional, bold opcional"),
        ActionDefinition("office_save", _office_resources, idempotent=True, verifier="file_saved", description="Guardar el documento de Office", parameters="app, path"),
        ActionDefinition("office_read", _app_resources, parallel_safe=False, verifier="none", description="Leer el documento activo de Office", parameters="app"),
    ]
    for definition in defs:
        reg.register(definition)
    return reg
