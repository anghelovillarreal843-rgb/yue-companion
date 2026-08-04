"""Pruebas de los fallos vistos en las órdenes difíciles.

Cada test reproduce un fallo REAL del log, no un caso inventado:
    python tests/test_ordenes_dificiles.py
"""
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fallos = []


def check(nombre, condicion, detalle=""):
    if condicion:
        print(f"  OK  {nombre}")
    else:
        print(f"  FALLA  {nombre} {detalle}")
        fallos.append(nombre)


print("\n[1] JSON con una llave de más (rompía un ciclo entero)")
from core.ai_engine import AIEngine

crudo = ('{"done":false,"summary":"comienzo a escribir el plan de estudio",'
         '"actions":[{"action":"click_element","name":"Editor de texto","control_type":"Edit"},'
         '{"action":"type_text","text":"Plan de estudio:"}]}}')
try:
    plan = AIEngine._parse_plan_json(crudo)
    check("recupera el plan pese al '}}' final", len(plan["actions"]) == 2)
except Exception as exc:
    check("recupera el plan pese al '}}' final", False, str(exc))

for nombre, txt in {
    "bloque ```json con }}": '```json\n{"done":true,"summary":"x","actions":[]}}\n```',
    "preámbulo antes del JSON": 'Claro:\n{"done":false,"summary":"x","actions":[{"action":"wait","seconds":1}]}',
    "comillas curvas": '{\u201cdone\u201d:false,"summary":"x","actions":[]}',
    "coma colgante": '{"done":false,"summary":"x","actions":[{"action":"wait","seconds":1},]}',
    "llave dentro de un string": '{"done":false,"summary":"escribo }","actions":[]}',
}.items():
    try:
        AIEngine._parse_plan_json(txt)
        check(nombre, True)
    except Exception as exc:
        check(nombre, False, str(exc))


print("\n[2] El OCR llega al planificador (sus ojos sin modelo de visión)")
info = {
    "width": 1920, "height": 1080,
    "windows": [{"title": "Calculadora", "active": True}],
    "active_window": "Calculadora",
    "ui_elements": [{"name": "Siete", "type": "Button"}],
    "screen_text": "Estandar 785 x 64 Mostrar 50240",
}
ctx = AIEngine._ui_context_text(info)
check("el modelo ve el visor de la calculadora", "50240" in ctx)
check("se le explica para qué sirve", "surtieron efecto" in ctx)
check("sin OCR no se inventa la sección", "OCR" not in AIEngine._ui_context_text(
    {"windows": [], "ui_elements": []}))


print("\n[3] Carpetas conocidas por ruta, sin pelearse con el árbol del Explorador")
_falso = pathlib.Path("/tmp/_yue_home_test")
for d in ("Downloads", "Desktop", "Documents", "Pictures"):
    (_falso / d).mkdir(parents=True, exist_ok=True)
os.environ["HOME"] = str(_falso)
os.environ["USERPROFILE"] = str(_falso)
from core.pc_control import PCController

c = PCController.__new__(PCController)
casos_ok = [
    "Abre el explorador de archivos y entra en la carpeta Descargas.",  # del log
    "abre la carpeta descargas",
    "abre descargas",
    "entra en el escritorio",
    "muestra mis im\u00e1genes",
]
for orden in casos_ok:
    plan = c._direct_plan(orden)
    check(f"«{orden[:38]}»", bool(plan) and plan[0]["action"] == "open_path",
          str([a["action"] for a in plan]))

casos_no = [
    # OJO: «una lista de compras» hay que REDACTARLA, asi que ya no es plan
    # directo: se la queda el planificador con IA. Ver bloque [9].
    ("Abre el bloc de notas y escribe hola", "type_text"),
    ("abre el explorador de archivos", "open_app"),
    ("abre la calculadora", "open_app"),
]
for orden, esperado in casos_no:
    plan = c._direct_plan(orden)
    acciones = [a["action"] for a in plan]
    check(f"NO confunde «{orden[:32]}»", esperado in acciones, str(acciones))


print("\n[4] Alias de controles: el modelo pide «7», el bot\u00f3n se llama «Siete»")
check("7 -> Siete", c._control_name("7") == "Siete")
check("el 3 -> Tres", c._control_name("el 3") == "Tres")
check("x -> Multiplicar por", c._control_name("x") == "Multiplicar por")


print("\n[5] Verificaci\u00f3n: un d\u00edgito de la calculadora S\u00cd cuenta como cambio")
from PIL import Image, ImageDraw
from core import screen_diff

antes = Image.new("L", (1920, 1080), 40)
ImageDraw.Draw(antes).rectangle([700, 300, 1020, 800], fill=200)
despues = antes.copy()
ImageDraw.Draw(despues).rectangle([950, 340, 975, 380], fill=10)
fa, fd = screen_diff.signature_from_image(antes), screen_diff.signature_from_image(despues)
check("detecta el d\u00edgito nuevo", screen_diff.changed(fa, fd))
check("no inventa cambios donde no los hay",
      not screen_diff.changed(fa, screen_diff.signature_from_image(antes.copy())))


print("\n[6] L\u00edmites: 3 ciclos no alcanzan para «785 x 64»")
import config
check("PC_MAX_CYCLES >= 6", config.PC_MAX_CYCLES >= 6, f"= {config.PC_MAX_CYCLES}")
check("PC_OCR_CONTEXT activo", bool(config.PC_OCR_CONTEXT))

print("\n[7] press«ctrl» ya no mata la orden entera")
from core.pc_control import PCControlError

c2 = PCController.__new__(PCController)
c2.max_actions = 24
r = c2._validate_step({"action": "press", "key": "ctrl+c"})
check("press ctrl+c -> hotkey", r["action"] == "hotkey" and r["keys"] == ["ctrl", "c"])
try:
    c2._validate_step({"action": "press", "key": "ctrl"})
    check("press ctrl suelto se descarta", False)
except PCControlError:
    check("press ctrl suelto se descarta", True)
plan = c2._validate_plan(
    [{"action": "open_app", "name": "bloc de notas"},
     {"action": "type_text", "text": "48"},
     {"action": "press", "key": "ctrl"},
     {"action": "press", "key": "ctrl+c"}],
    remaining=24, discarded=[])
check("el resto del plan sobrevive", [a["action"] for a in plan] ==
      ["open_app", "type_text", "hotkey"])


print("\n[8] YouTube: reproducir, no dejar una lista de resultados")
from core import youtube

check("saca el id del HTML real",
      youtube._RE_VIDEO_ID.findall('{"videoRenderer":{"videoId":"dQw4w9WgXcQ","x":1}}')
      == ["dQw4w9WgXcQ"])
check("url de reproducción", youtube.url_video("dQw4w9WgXcQ")
      == "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
_real = youtube.resolver
youtube.resolver = lambda q, timeout=8.0: ("https://www.youtube.com/watch?v=X", True)
for orden in ("pon jazz en youtube", "reproduce musica relajante en youtube",
              "busca un video de gatos en youtube", "abre youtube y pon los simpsons"):
    check(f"«{orden[:34]}»", bool(c2._plan_youtube(orden)))
for orden in ("abre youtube", "abre la calculadora", "busca en google el clima"):
    check(f"NO dispara «{orden[:30]}»", not c2._plan_youtube(orden))
youtube.resolver = _real


print("\n[9] «escribe un párrafo sobre X» hay que redactarlo, no teclearlo")
check("párrafo -> al planificador IA", c2._pide_contenido("un párrafo sobre el Perú"))
check("lista -> al planificador IA", c2._pide_contenido("una lista de compras"))
check("carta -> al planificador IA", c2._pide_contenido("una carta para mi profesor"))
check("texto literal se teclea", not c2._pide_contenido("hola"))
check("entre comillas es literal", not c2._pide_contenido('"una lista de cosas"'))
check("Word espera más que el Bloc", c2._espera_de_apertura("Word") >
      c2._espera_de_apertura("bloc de notas"))

print("\n[10] El cuelgue («Python no responde») no puede repetirse")
import threading
import time as _t
from core import desktop_ui, ui_bridge

_original = ui_bridge._bridge

t0 = _t.time()
ui_bridge._bridge = None
r = desktop_ui.set_click_through(True)
check("sin puente no bloquea", _t.time() - t0 < 0.5 and r == 0)


class _UiAtascada:
    """Un hilo de interfaz que nunca contesta: el escenario del cuelgue."""
    def enviar(self, tarea):
        pass


ui_bridge._bridge = _UiAtascada()
t0 = _t.time()
r = ui_bridge.run_on_ui_thread(lambda: 42, timeout=0.4)
tardo = _t.time() - t0
check("con la UI atascada se rinde en vez de colgar", r is None and tardo < 1.0,
      f"tardó {tardo:.2f}s")


class _UiSana:
    def enviar(self, tarea):
        threading.Thread(target=tarea, daemon=True).start()


ui_bridge._bridge = _UiSana()
check("con la UI sana sí se aplica", ui_bridge.run_on_ui_thread(lambda: 42, timeout=1) == 42)


def _explota():
    raise RuntimeError("boom")


check("una tarea que revienta no propaga", ui_bridge.run_on_ui_thread(_explota, timeout=1) is None)

# Y lo más importante: nunca dejar el chat transparente para siempre.
_llamadas = []
_set_real = desktop_ui.set_click_through
desktop_ui.set_click_through = lambda a: (_llamadas.append(a), 1)[1]
c3 = PCController.__new__(PCController)
c3._execute_inner = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("orden fallida"))
try:
    c3.execute("lo que sea")
except RuntimeError:
    pass
check("se restaura aunque la orden reviente", _llamadas == [True, False], str(_llamadas))
desktop_ui.set_click_through = _set_real
ui_bridge._bridge = _original

print("\nTODO OK" if not fallos else f"\n{len(fallos)} FALLOS: {fallos}")
sys.exit(1 if fallos else 0)
