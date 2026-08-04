"""Pruebas de la memoria a largo plazo (consolidación). Sin red, sin GUI.

Cubre:
  - core/memory.py           -> tabla+métodos nuevos (messages_between, resúmenes).
  - core/memory_consolidation.consolidate -> sin mensajes no consolida; con
    mensajes + LLM mockeado guarda resumen y marca la fecha; LLM que revienta no
    rompe ni guarda y permite reintentar después.
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fallos = []
def check(nombre, cond):
    print(("  OK  " if cond else "  FALLO  ") + nombre)
    if not cond:
        fallos.append(nombre)


from core.memory import Memory
from core import memory_consolidation as mc


def nueva_memoria():
    ruta = Path(tempfile.mkdtemp()) / "yue_test.db"
    return Memory(str(ruta))


# --- dobles del motor de IA (nunca tocan la red) ---
class FakeEngine:
    def __init__(self, reply, api_key="k"):
        self.api_key = api_key
        self._reply = reply
        self.llamadas = 0
    def chat(self, messages, timeout=60, model=None):
        self.llamadas += 1
        return self._reply

class BoomEngine:
    api_key = "k"
    def chat(self, *a, **k):
        raise RuntimeError("se cayó / tardó demasiado")

class NoKeyEngine:
    api_key = ""
    def chat(self, *a, **k):
        raise AssertionError("no debería llamarse sin api_key")


print("\n[1] Memory: métodos nuevos")
m = nueva_memoria()
check("get_long_term_summaries vacío -> []", m.get_long_term_summaries() == [])
m.add_long_term_summary(100.0, 200.0, "Hablaron de música y del proyecto YUE.")
m.add_long_term_summary(200.0, 300.0, "Semana intensa de programación.")
res = m.get_long_term_summaries(5)
check("resúmenes en orden cronológico",
      res == ["Hablaron de música y del proyecto YUE.", "Semana intensa de programación."])
# messages_between filtra por rango.
m.add_message("user", "hola")
m.add_message("assistant", "hola, ¿qué tal?")
ahora = time.time()
entre = m.messages_between(0.0, ahora + 1)
check("messages_between trae el rango", len(entre) == 2 and entre[0]["content"] == "hola")
check("messages_between fuera de rango -> []", m.messages_between(ahora + 100, ahora + 200) == [])


print("\n[2] consolidate: sin mensajes -> no consolida")
m2 = nueva_memoria()
r = mc.consolidate(m2, FakeEngine("RESUMEN: nada\nHECHOS: -"))
check("sin mensajes -> None", r is None)
check("sin mensajes -> no guarda resumen", m2.get_long_term_summaries() == [])
check("sin mensajes -> no marca fecha", m2.get_state("ultima_consolidacion") is None)


print("\n[3] consolidate: con mensajes + LLM mockeado")
m3 = nueva_memoria()
for i in range(6):
    m3.add_message("user", f"mensaje {i} sobre salsa y código")
    m3.add_message("assistant", f"respuesta {i}")
reply = ("RESUMEN: Charlaron sobre música salsa y el desarrollo de YUE. Tono "
         "animado y cercano.\nHECHOS: se llama Jamir | le gusta la salsa")
eng = FakeEngine(reply)
r = mc.consolidate(m3, eng)
check("con mensajes -> devuelve resumen", isinstance(r, str) and "salsa" in r.lower())
check("resumen guardado", len(m3.get_long_term_summaries()) == 1)
check("resumen SIN etiqueta 'RESUMEN:'", not m3.get_long_term_summaries()[0].lower().startswith("resumen"))
check("marca de fecha actualizada", m3.get_state("ultima_consolidacion") is not None)
facts = [f.lower() for f in m3.get_facts()]
check("hecho automático: nombre", any("jamir" in f for f in facts))
check("hecho automático: preferencia", any("salsa" in f for f in facts))
# No borra los mensajes originales (consolidación no destructiva).
check("mensajes originales intactos", len(m3.messages_between(0.0, time.time() + 1)) == 12)

# Justo después, el cuentagotas de días impide reconsolidar.
r2 = mc.consolidate(m3, eng)
check("no reconsolida antes de tiempo", r2 is None and len(m3.get_long_term_summaries()) == 1)


print("\n[4] consolidate: degradación (LLM revienta / sin api_key)")
m4 = nueva_memoria()
for i in range(4):
    m4.add_message("user", f"algo {i}")
r = mc.consolidate(m4, BoomEngine())
check("LLM excepción -> None", r is None)
check("LLM excepción -> no guarda", m4.get_long_term_summaries() == [])
check("LLM excepción -> no marca fecha (reintentará)", m4.get_state("ultima_consolidacion") is None)
# Sin api_key: no consolida y no llama a chat.
r = mc.consolidate(m4, NoKeyEngine())
check("sin api_key -> None", r is None and m4.get_long_term_summaries() == [])
# Y tras el fallo, un motor sano SÍ consolida (se permitía reintentar).
ok = mc.consolidate(m4, FakeEngine("RESUMEN: resumen válido de prueba.\nHECHOS: -"))
check("reintento posterior sí consolida", isinstance(ok, str) and len(m4.get_long_term_summaries()) == 1)


print("\n" + ("TODO OK" if not fallos else f"FALLOS: {fallos}"))
sys.exit(1 if fallos else 0)
