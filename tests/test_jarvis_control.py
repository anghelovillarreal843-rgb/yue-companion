"""Regresiones de las mejoras Jarvis del control de PC.

Ejecutar con:
    python tests/test_jarvis_control.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from core import desktop_ui, ui_bridge
from core.app_catalog import AppCatalog, clean_windows_target
from core.office_control import OfficeUnavailable
from core.pc_control import (PCController, PCControlError, ActionResult, looks_like_pc_command,
                             _ALLOWED_ACTIONS)

fallos: list[str] = []


def check(nombre: str, condition: bool, detail: str = ""):
    print(("  OK  " if condition else "  FALLO  ") + nombre + (f" · {detail}" if detail and not condition else ""))
    if not condition:
        fallos.append(nombre)


pc = PCController()

print("\n[1] Elementos reales antes que coordenadas")
check("clic nombrado -> click_element",
      pc._direct_plan("haz clic en Guardar") == [{"action": "click_element", "name": "Guardar"}])
check("texto explícito -> click_text",
      pc._direct_plan("haz clic en el texto Aceptar") == [{"action": "click_text", "text": "Aceptar"}])
step = pc._validate_step({"action": "click", "x_pct": .8, "y_pct": .2, "name": "Guardar"})
check("coordenada con nombre se repara", step["action"] == "click_element", str(step))

print("\n[2] Auditoría del método de entrada")
class FakePyAutoGUI:
    FAILSAFE = False
    PAUSE = 0
    def size(self): return (1000, 800)
    def click(self, *args, **kwargs): pass
    def doubleClick(self, *args, **kwargs): pass
    def moveTo(self, *args, **kwargs): pass
    def dragTo(self, *args, **kwargs): pass
    def press(self, *args, **kwargs): pass
    def hotkey(self, *args, **kwargs): pass
    def scroll(self, *args, **kwargs): pass
    def hscroll(self, *args, **kwargs): pass

old_pyauto = sys.modules.get("pyautogui")
sys.modules["pyautogui"] = FakePyAutoGUI()
try:
    detail = pc._pyautogui_action({"action": "click", "x_pct": .5, "y_pct": .5, "button": "left"})
    check("resultado registra método coordenada", "método=coordenada" in detail, detail)
finally:
    if old_pyauto is None:
        sys.modules.pop("pyautogui", None)
    else:
        sys.modules["pyautogui"] = old_pyauto

print("\n[3] Office nativo")
check("Word nuevo -> office_create", pc._direct_plan("abre word")[0]["action"] == "office_create")
plan = pc._direct_plan("abre excel y escribe ventas")
check("Excel escribir -> office_write", [p["action"] for p in plan] == ["office_write"], str(plan))
check("leer Word -> office_read", pc._direct_plan("lee el documento abierto de word")[0]["action"] == "office_read")
check("generar informe en Word se enruta al control", looks_like_pc_command("genera un informe en Word"))
check("acciones Office registradas", {"office_create", "office_write", "office_save", "office_read"} <= _ALLOWED_ACTIONS)
old_create = pc.office.create_document
old_fallback = pc._office_keyboard_fallback
try:
    pc.office.create_document = lambda app: (_ for _ in ()).throw(OfficeUnavailable("sin COM"))
    pc._office_keyboard_fallback = lambda app, text, create_only: "método=teclado-fallback"
    check("COM falla -> teclado fallback", "fallback" in pc._act_office_create({"app": "word"}))
finally:
    pc.office.create_document = old_create
    pc._office_keyboard_fallback = old_fallback

print("\n[4] Autocorrección profunda")
check("6 ciclos configurables", config.PC_MAX_CYCLES >= 5, str(config.PC_MAX_CYCLES))
check("bloques precisos", config.PC_MAX_ACTIONS_PER_CYCLE <= 5, str(config.PC_MAX_ACTIONS_PER_CYCLE))
diag = pc._failure_diagnostics(
    [{"action": "click_element", "name": "Guardar"}],
    [ActionResult("click_element", False, "no encontré el control")],
)
check("diagnóstico explica intento/motivo/alternativa",
      bool(diag) and "click_text" in diag[0] and "motivo" in diag[0], str(diag))

print("\n[5] Deshacer ampliado")
originals = {
    "window_snapshot": desktop_ui.window_snapshot,
    "windows_snapshot": desktop_ui.windows_snapshot,
    "move_window": desktop_ui.move_window,
    "restore_window_snapshot": desktop_ui.restore_window_snapshot,
    "reopen_window_snapshot": desktop_ui.reopen_window_snapshot,
    "close_window": desktop_ui.close_window,
    "active_window_title": desktop_ui.active_window_title,
}
restored = []
reopened = []
try:
    desktop_ui.window_snapshot = lambda title="": {
        "title": "Bloc de notas", "rect": [10, 20, 410, 320], "safe_to_reopen": True,
        "executable": "notepad.exe",
    }
    desktop_ui.windows_snapshot = lambda limit=40: [{"title": "Bloc de notas", "rect": [10, 20, 410, 320]}]
    desktop_ui.move_window = lambda *args, **kwargs: "Bloc de notas"
    desktop_ui.restore_window_snapshot = lambda snap: restored.append(snap) or "Bloc de notas"
    action = pc._validate_step({"action": "move_window", "title": "Bloc de notas", "x": 200, "y": 100})
    result = pc._execute_action(action)
    pc.remember_result("mueve la ventana", {"ok": result.ok, "actions": [action], "results": [result.__dict__]})
    undone = pc.undo_last()
    check("move_window guarda snapshot", "_reversible_snapshot" in action, str(action))
    check("move_window se restaura", undone.get("undone") and bool(restored), str(undone))

    desktop_ui.close_window = lambda title: "Bloc de notas"
    desktop_ui.reopen_window_snapshot = lambda snap: reopened.append(snap) or "Bloc de notas"
    close_action = pc._validate_step({"action": "close_window", "title": "Bloc de notas"})
    close_result = pc._execute_action(close_action)
    pc.remember_result("cierra la ventana", {"ok": close_result.ok, "actions": [close_action], "results": [close_result.__dict__]})
    close_undo = pc.undo_last()
    check("close_window se reabre con snapshot seguro", close_undo.get("undone") and bool(reopened), str(close_undo))

    # Arrastre: solo se invierte si ventana activa y lista de ventanas siguen iguales.
    desktop_ui.active_window_title = lambda: "Bloc de notas"
    old_pyauto_drag = sys.modules.get("pyautogui")
    sys.modules["pyautogui"] = FakePyAutoGUI()
    try:
        drag_action = pc._validate_step({
            "action": "drag", "from_x_pct": .2, "from_y_pct": .3,
            "to_x_pct": .7, "to_y_pct": .8, "duration": .2,
        })
        drag_result = pc._execute_action(drag_action)
        pc.remember_result("arrastra", {"ok": drag_result.ok, "actions": [drag_action], "results": [drag_result.__dict__]})
        drag_undo = pc.undo_last()
        check("drag se invierte en el mismo contexto", drag_undo.get("undone") is True, str(drag_undo))
    finally:
        if old_pyauto_drag is None:
            sys.modules.pop("pyautogui", None)
        else:
            sys.modules["pyautogui"] = old_pyauto_drag
finally:
    for name, value in originals.items():
        setattr(desktop_ui, name, value)

print("\n[6] Catálogo dinámico")
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "apps.json"
    path.write_text(json.dumps({
        "version": 1, "updated_at": time.time(),
        "apps": [{"name": "Visual Studio Code", "key": "visual studio code", "target": "code.exe", "source": "test"}],
    }), encoding="utf-8")
    catalog = AppCatalog(path)
    check("resuelve nombre exacto desde caché", catalog.resolve("Visual Studio Code")["target"] == "code.exe")
    check("resuelve alias parcial conservador", catalog.resolve("visual studio") is not None)
check("limpia DisplayIcon", clean_windows_target('"C:\\Apps\\demo.exe",0') == "C:\\Apps\\demo.exe")

print("\n[7] Permisos granulares")
old_perm = config.PERM_ENVIAR_CORREOS
old_overwrite = config.PERM_SOBRESCRIBIR_ARCHIVOS
try:
    config.PERM_ENVIAR_CORREOS = False
    try:
        pc._check_instruction("envía este correo")
        denied = False
    except PCControlError:
        denied = True
    check("permiso apagado rechaza antes de confirmar", denied)
    decision = pc._confirm_if_needed({"action": "click_element", "name": "Enviar"})
    check("paso sensible también se deniega", decision == "deny", decision)
    with tempfile.NamedTemporaryFile(suffix=".docx") as existing:
        config.PERM_SOBRESCRIBIR_ARCHIVOS = False
        overwrite = pc._confirm_if_needed({"action": "office_save", "app": "word", "path": existing.name})
        check("Office no sobrescribe con permiso apagado", overwrite == "deny", overwrite)
finally:
    config.PERM_ENVIAR_CORREOS = old_perm
    config.PERM_SOBRESCRIBIR_ARCHIVOS = old_overwrite

print("\n[8] Bitácora visible sin bloquear")
seen = []
pc.set_action_log_callback(lambda rows: seen.append(rows))
pc._publish_action_result(ActionResult("click_element", True, "método=elemento · Guardar"))
check("publica últimas acciones", bool(seen) and seen[-1][-1]["ok"] is True)
check("ui_bridge ofrece publicación asíncrona", callable(getattr(ui_bridge, "post_to_ui_thread", None)))

print("\n[9] Segundo plano y cancelación")
pc2 = PCController()
state = {"cancelled": False}
def waiter():
    try:
        pc2._interruptible_wait(2.0)
    except PCControlError:
        state["cancelled"] = True
thread = threading.Thread(target=waiter)
thread.start()
time.sleep(0.08)
pc2.cancel()
thread.join(timeout=1.0)
check("cancel interrumpe una tarea independiente", state["cancelled"] and not thread.is_alive())

print("\n[10] DPI y comandos de ventana")
main_source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
check("DPI awareness antes de Qt", main_source.index("\nenable_dpi_awareness()\n") < main_source.index("from PyQt5"))
check("enrutador reconoce mover ventana", looks_like_pc_command("mueve la ventana del bloc de notas a 100, 200"))
check("plan nativo de mover ventana", pc._direct_plan("mueve la ventana del bloc de notas a 100, 200")[0]["action"] == "move_window")

print("\n" + ("TODO OK" if not fallos else f"FALLOS: {fallos}"))
raise SystemExit(1 if fallos else 0)
