"""Pruebas de SEGURIDAD y límites del control de PC (sin Windows, sin LLM).

Cubre los caminos que las otras suites no tocan: la lista negra completa,
el bloqueo de atajos peligrosos, el recorte de coordenadas/longitud y el
muro de la lista negra sobre los títulos de close_window.

Es aditiva y autoejecutable, como test_mejoras.py:

    python tests/test_seguridad_control.py

Un pyautogui de mentira registra los clics/atajos para poder verificar el
recorte y el bloqueo SIN mover el ratón de verdad.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from core.pc_control import (
    PCController,
    PCControlError,
    _BLOCKED_TERMS,
    _SAFE_KEYS,
)

fallos = []


def check(nombre, cond, detalle=""):
    print(("  OK  " if cond else "  FALLO  ") + nombre + (f"  ·  {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


# --- pyautogui de mentira: registra lo que se le pide, no toca la pantalla ---
class FakePyAutoGUI(types.ModuleType):
    def __init__(self):
        super().__init__("pyautogui")
        self.FAILSAFE = True
        self.PAUSE = 0.0
        self.calls = []

    def size(self):
        return (1920, 1080)

    def click(self, x=None, y=None, clicks=1, button="left"):
        self.calls.append(("click", x, y, button))

    def hotkey(self, *keys):
        self.calls.append(("hotkey", tuple(keys)))

    def press(self, key, presses=1, interval=0.0):
        self.calls.append(("press", key, presses))

    def write(self, text, interval=0.0):
        self.calls.append(("write", text))

    def scroll(self, amount):
        self.calls.append(("scroll", amount))

    def hscroll(self, amount):
        self.calls.append(("hscroll", amount))

    def moveTo(self, *a, **k):
        self.calls.append(("moveTo", a))

    def doubleClick(self, *a, **k):
        self.calls.append(("doubleClick", a))

    def dragTo(self, *a, **k):
        self.calls.append(("dragTo", a))


fake = FakePyAutoGUI()
sys.modules["pyautogui"] = fake
pc = PCController()


# =====================================================================
print("\n[1] Lista negra COMPLETA (regresión: nadie borra un término sin darse cuenta)")
# Cada término, envuelto en una frase realista, debe ser rechazado.
plantillas = {
    "rm -rf": "corre rm -rf en la terminal",
    "del /f": "haz del /f de la carpeta",
}
protegidos = 0
for termino in _BLOCKED_TERMS:
    frase = plantillas.get(termino, f"por favor {termino} ahora mismo")
    try:
        pc._check_instruction(frase)
        check(f"«{termino}» debería bloquearse", False, "no se bloqueó")
    except PCControlError:
        protegidos += 1
check(f"los {len(_BLOCKED_TERMS)} términos de la lista negra bloquean",
      protegidos == len(_BLOCKED_TERMS), f"{protegidos}/{len(_BLOCKED_TERMS)}")

# Y una orden normal NO debe bloquearse por accidente.
try:
    pc._check_instruction("abre el bloc de notas y escribe una lista de compras")
    check("una orden inofensiva pasa el filtro", True)
except PCControlError as e:
    check("una orden inofensiva pasa el filtro", False, str(e))


# =====================================================================
print("\n[2] execute() se niega de raíz a una orden peligrosa")
for orden in ("formatea el disco C", "desactiva el antivirus", "abre regedit"):
    try:
        pc.execute(orden, engine=None)
        check(f"«{orden}» rechazada antes de tocar nada", False, "no lanzó")
    except PCControlError:
        check(f"«{orden}» rechazada antes de tocar nada", True)


# =====================================================================
print("\n[3] Atajos que apagan/cierran el sistema quedan bloqueados")
for keys, motivo in (
    (["alt", "f4"], "alt+f4 cierra la ventana activa"),
    (["ctrl", "alt", "delete"], "ctrl+alt+supr bloquea el equipo"),
):
    try:
        pc._pyautogui_action({"action": "hotkey", "keys": keys})
        check(f"{'+'.join(keys)} bloqueado ({motivo})", False, "se ejecutó")
    except PCControlError:
        check(f"{'+'.join(keys)} bloqueado ({motivo})", True)

# Un atajo legítimo SÍ pasa (y llega al pyautogui de mentira).
fake.calls.clear()
try:
    pc._pyautogui_action({"action": "hotkey", "keys": ["ctrl", "c"]})
    check("ctrl+c (copiar) sí se ejecuta", ("hotkey", ("ctrl", "c")) in fake.calls)
except PCControlError as e:
    check("ctrl+c (copiar) sí se ejecuta", False, str(e))


# =====================================================================
print("\n[4] Teclas sueltas: solo la lista blanca + un carácter alfanumérico")
# Una tecla peligrosa/rara se rechaza en ejecución.
try:
    pc._pyautogui_action({"action": "press", "key": "volumeup"})
    check("una tecla fuera de la lista blanca se rechaza", False, "se pulsó")
except PCControlError:
    check("una tecla fuera de la lista blanca se rechaza", True)

# Las de la lista blanca (enter, f7…) y un dígito suelto sí pasan.
fake.calls.clear()
for key in ("enter", "f7", "7"):
    try:
        pc._pyautogui_action({"action": "press", "key": key})
        check(f"press «{key}» permitido", ("press", key, 1) in fake.calls)
    except PCControlError as e:
        check(f"press «{key}» permitido", False, str(e))
check("todas las _SAFE_KEYS siguen siendo teclas conocidas", "enter" in _SAFE_KEYS and "f7" in _SAFE_KEYS)


# =====================================================================
print("\n[5] Recorte de coordenadas: el modelo no puede clicar fuera de la pantalla")
# a) porcentajes fuera de rango se sujetan a [0,1] en la validación
paso = pc._validate_step({"action": "click", "x_pct": 5.0, "y_pct": -2.0})
check("x_pct/y_pct se sujetan a [0,1]", paso["x_pct"] == 1.0 and paso["y_pct"] == 0.0,
      f"{paso['x_pct']},{paso['y_pct']}")
# b) el clic absoluto se sujeta al tamaño real de la pantalla (1920x1080)
fake.calls.clear()
pc._click_xy(999999, -50)
accion = fake.calls[-1]
check("_click_xy nunca sale de la pantalla",
      accion == ("click", 1919, 0, "left"), str(accion))


# =====================================================================
print("\n[6] Límites de longitud y desplazamiento")
largo = pc._validate_step({"action": "type_text", "text": "x" * 9000})
check(f"type_text se recorta a PC_MAX_TYPE_CHARS ({config.PC_MAX_TYPE_CHARS})",
      len(largo["text"]) == config.PC_MAX_TYPE_CHARS, str(len(largo["text"])))
sc = pc._validate_step({"action": "scroll", "amount": 999999})
check("scroll se sujeta a 2200", sc["amount"] == 2200, str(sc["amount"]))


# =====================================================================
print("\n[7] close_window: la lista negra también aplica al TÍTULO")
# Un título con un término prohibido no puede colarse por close_window.
try:
    pc._act_close_window({"action": "close_window", "title": "formatea disco"})
    check("un título con término prohibido se rechaza", False, "no lanzó")
except PCControlError:
    check("un título con término prohibido se rechaza", True)


print("\n" + ("TODO OK" if not fallos else f"FALLOS: {fallos}"))
sys.exit(1 if fallos else 0)
