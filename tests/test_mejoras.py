"""Pruebas rápidas de la lógica nueva (sin Windows, sin pyautogui, sin LLM)."""
import sys, types, tempfile, os, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# --- stubs mínimos para poder importar sin GUI ---
tmp = Path(tempfile.mkdtemp())
os.environ["LEARNING_ENABLED"] = "true"

import config
config.DATA_DIR = tmp
config.LEARNING_DIR = tmp / "learning"
config.LEARNING_DIR.mkdir(parents=True, exist_ok=True)

from core.pc_control import PCController, PCControlError, _ALLOWED_ACTIONS
from core.learning.skills import SkillLibrary, similarity, normalize
from core.learning.lessons import LessonBook
from core import screen_diff

fallos = []
def check(nombre, cond):
    print(("  OK  " if cond else "  FALLO  ") + nombre)
    if not cond:
        fallos.append(nombre)

print("\n[1] Acciones registradas")
for a in ("click_element", "click_text", "focus_window", "list_windows", "close_window"):
    check(f"{a} en _ALLOWED_ACTIONS", a in _ALLOWED_ACTIONS)

print("\n[2] Validacion tolerante (el bug 'Accion no permitida: vacia')")
pc = PCController()
plan = [
    {"action": "open_app", "name": "notepad"},
    {"action": ""},                                  # el paso que abortaba TODO
    {"action": "explota_la_pc"},                     # no permitida
    {"action": "click_element", "name": "Guardar", "control_type": "Button"},
    {"action": "click_element"},                     # sin name -> descartado
    {"action": "type_text", "text": "hola"},
]
desc = []
limpio = pc._validate_plan(plan, remaining=10, discarded=desc)
check("sobreviven los 3 pasos validos", len(limpio) == 3)
check("se descartaron 3 pasos", len(desc) == 3)
check("el mensaje del log aparece como descarte", any("vacía" in d for d in desc))
check("no se lanzo excepcion", True)

print("\n[3] Solo revienta si TODO es invalido")
try:
    pc._validate_plan([{"action": ""}, {"action": "nope"}], remaining=10)
    check("lanza PCControlError", False)
except PCControlError as e:
    check("lanza PCControlError con motivos", "válido" in str(e) or "válido" in str(e))

print("\n[4] Coercion de tipos raros no aborta")
desc = []
limpio = pc._validate_plan(
    [{"action": "click", "x_pct": "no-soy-un-numero", "y_pct": 0.5},
     {"action": "wait", "seconds": 99}], remaining=10, discarded=desc)
check("el click ilegible se descarta", len(limpio) == 1)
check("wait se recorta a 6s", limpio[0]["seconds"] == 6.0)

print("\n[5] Hash perceptual")
from PIL import Image
a = Image.new("RGB", (400, 300), (10, 10, 10))
b = Image.new("RGB", (400, 300), (10, 10, 10))
c = Image.new("RGB", (400, 300), (240, 30, 30))
sa, sb, sc = (screen_diff.signature_from_image(x) for x in (a, b, c))
check("pantallas identicas -> no cambio", not screen_diff.changed(sa, sb))
check("pantalla distinta -> cambio", screen_diff.changed(sa, sc))
d = a.copy()
for x in range(200, 206):
    for y in range(140, 160):
        d.putpixel((x, y), (255, 255, 255))
check("cambio pequeno (un cursor/letra) -> detectado",
      screen_diff.changed(sa, screen_diff.signature_from_image(d)))

print("\n[6] Similitud de instrucciones")
check("identicas = 1.0", similarity("Abre el bloc de notas", "abre el bloc de notas") == 1.0)
check("sin tildes/mayus", normalize("Yue, ABRE la Configuración!") == "abre la configuracion")
s1 = similarity("abre notepad y escribe hola", "abre notepad y escribe adios")
check(f"texto distinto NO llega a 0.85 ({s1:.2f})", s1 < 0.85)
s2 = similarity("abre el bloc de notas", "abre el bloc de notas por favor")
check(f"cortesia ignorada: variante supera 0.85 ({s2:.2f})", s2 >= 0.85)
s3 = similarity("Yue, abre el bloc de notas por favor", "abre el bloc de notas")
check(f"vocativo + cortesia = misma orden ({s3:.2f})", s3 == 1.0)

print("\n[7] Biblioteca de habilidades")
lib = SkillLibrary(config.LEARNING_DIR / "skills.json")
plan_ok = [{"action": "open_app", "name": "notepad"}, {"action": "type_text", "text": "hola"}]
lib.record_success("abre notepad y escribe hola", plan_ok, cycles=3)
check("1 exito -> aun no se repite (min 2)", lib.find("abre notepad y escribe hola") is None)
lib.record_success("abre notepad y escribe hola", plan_ok, cycles=1)
r = lib.find("abre notepad y escribe hola")
check("2 exitos -> receta disponible", r is not None)
check("avg_cycles promediado", r["avg_cycles"] == 2.0)
check("guarda en disco", (config.LEARNING_DIR / "skills.json").exists())
check("literales: NO reusa la receta con otro texto",
      lib.find("abre notepad y escribe chau mundo") is None)

lib.record_failure("abre notepad y escribe hola")
check("1 fallo -> sigue viva (ratio 2/3=0.67 < 0.7 -> se retira)",
      lib.find("abre notepad y escribe hola") is None)
lib.record_success("abre notepad y escribe hola", plan_ok, cycles=1)
lib.record_success("abre notepad y escribe hola", plan_ok, cycles=1)
check("mas exitos -> vuelve", lib.find("abre notepad y escribe hola") is not None)
lib.record_failure("abre notepad y escribe hola")
lib.record_failure("abre notepad y escribe hola")
check("2 fallos seguidos -> degradada", lib.find("abre notepad y escribe hola") is None)
check("degraded=True en disco", any(s["degraded"] for s in lib.all()))
lib.record_success("abre notepad y escribe hola", plan_ok, cycles=1)
check("un exito nuevo quita degraded", not any(s["degraded"] for s in lib.all()))
check("pero el ratio historico (5/8) la sigue reteniendo",
      lib.find("abre notepad y escribe hola") is None)
for _ in range(3):
    lib.record_success("abre notepad y escribe hola", plan_ok, cycles=1)
check("con ratio >= 0.7 vuelve a estar disponible",
      lib.find("abre notepad y escribe hola") is not None)

print("\n[8] Lecciones")
book = LessonBook(config.LEARNING_DIR / "lessons.json", engine=None)
book.record("abre el spotify", ["abrir spotify", "sin efecto visible"], "no encontre la ventana")
check("guarda el fallo aunque no haya LLM", len(book.all()) == 1)
check("sin leccion no se devuelve", book.relevant("abre el spotify") == [])

class FakeEngine:
    def chat(self, messages, timeout=40):
        return "Falló porque Spotify tardó en abrir. La próxima usa wait 2s y focus_window."
book2 = LessonBook(config.LEARNING_DIR / "lessons.json", engine=FakeEngine())
book2.record("abre el spotify y pon musica", [], "timeout", blocking=True)
rel = book2.relevant("abre el spotify y pon musica")
check("reflexion guardada y recuperable", len(rel) == 1 and "wait" in rel[0])
check("orden lejana no la trae", book2.relevant("crea una hoja de excel") == [])

print("\n[9] enrich_history")
import core.learning.integration as integ
integ._lessons = book2
h = integ.enrich_history("abre el spotify y pon musica")
check("formato 'Leccion aprendida:'", len(h) == 1 and h[0].startswith("Lección aprendida:"))

print("\n[10] Prompt del planificador")
from core.ai_engine import AIEngine
txt = AIEngine._ui_context_text({
    "windows": [{"title": "Bloc de notas", "active": True}, {"title": "Chrome", "active": False}],
    "active_window": "Bloc de notas",
    "ui_elements": [{"name": "Guardar", "type": "Button"}],
})
check("incluye ventanas", "Bloc de notas [ACTIVA]" in txt)
check("incluye controles", '"Guardar"' in txt)
check("vacio si no hay contexto", AIEngine._ui_context_text({"width": 1}) == "")

print("\n[11] Similitud de controles UIA (bug real: «Siete» -> «I»)")
from core.desktop_ui import similarity as sim_ui
U = 0.75
casos_ui = [
    ("Siete", "I", False),                              # el bug del log
    ("Siete", "Siete", True), ("7", "Siete", False),
    ("Guardar", "Guardar como...", True),
    ("File", "Profile", False), ("View", "Review", False),
    ("Minimizar", "Minimize", True), ("Edit", "Editar", True),
    ("Explorer", "Explorer (Ctrl+Shift+E)", True),      # sufijo de atajo
    ("Toggle Chat", "Toggle Panel (Ctrl+J)", False),
    ("Aceptar", "&Aceptar", True), ("Aceptar", "Cancelar", False),
    ("x", "Explorer", False), ("boton enviar", "Enviar", True),
]
for a, b, esperado in casos_ui:
    v = sim_ui(a, b)
    check(f"{v:.2f}  {a!r} vs {b!r} -> {'match' if esperado else 'NO match'}", (v >= U) == esperado)

print("\n[12] Enmascarado del avatar de Yue")
base = Image.new("RGB", (400, 300), (30, 30, 30))
con_avatar = base.copy()
for x in range(300, 400):                 # el VRM parpadeando en una esquina
    for y in range(200, 300):
        con_avatar.putpixel((x, y), (250, 200, 180))
rect = [[300, 200, 400, 300]]
s_base = screen_diff.signature_from_image(base, rect)
s_avatar = screen_diff.signature_from_image(con_avatar, rect)
check("con mascara: el avatar NO cuenta como cambio", not screen_diff.changed(s_base, s_avatar))
check("sin mascara: contaria como cambio (por eso hace falta)",
      screen_diff.changed(screen_diff.signature_from_image(base),
                          screen_diff.signature_from_image(con_avatar)))
otro = base.copy()
for x in range(10, 120):                  # cambio real fuera de la mascara
    for y in range(10, 60):
        otro.putpixel((x, y), (255, 255, 255))
check("con mascara: un cambio real SI se detecta",
      screen_diff.changed(s_base, screen_diff.signature_from_image(otro, rect)))

print("\n[13] Enrutador con los verbos nuevos")
from core.pc_control import looks_like_pc_command as router
for frase, esperado in [
    ("enfoca la ventana del bloc de notas", True), ("cierra la ventana del bloc de notas", True),
    ("que ventanas tengo abiertas", True), ("listame las ventanas", True),
    ("cierra los ojos y imagina", False), ("que ventanas tiene tu casa imaginaria", False),
    ("hola como estas", False), ("abre la calculadora", True),
]:
    check(f"«{frase}» -> {esperado}", router(frase) == esperado)

print("\n[14] Reparacion de pasos malformados (la causa raiz del log real)")
plan_sucio = [
    ({"type": "open_app", "name": "notepad"}, "open_app"),
    ({"open_app": "calculadora"}, "open_app"),
    ({"click_element": {"name": "Siete", "control_type": "Button"}}, "click_element"),
    ("list_windows", "list_windows"),
    ({"action": "clickElement", "name": "Guardar"}, "click_element"),
    ({"action": "press_key", "key": "enter"}, "press"),
    ({"tool": "type_text", "text": "hola"}, "type_text"),
    ({"action": {"name": "focus_window"}, "title": "Bloc"}, "focus_window"),
    ({"accion": "esperar", "seconds": 2}, "wait"),
    ({"action": "hotkey", "keys": ["ctrl", "s"]}, "hotkey"),
]
for crudo, esperado in plan_sucio:
    d = []
    r = pc._validate_plan([crudo], remaining=5, discarded=d)
    check(f"{str(crudo)[:46]:<46} -> {esperado}", bool(r) and r[0]["action"] == esperado)
for basura in ({"action": ""}, {"pensamiento": "creo que..."}, {}, None, 42):
    d = []
    try:
        pc._validate_plan([basura], remaining=5, discarded=d)
        check(f"basura {str(basura)[:24]} se descarta", False)
    except PCControlError:
        check(f"basura {str(basura)[:24]} se descarta con motivo", bool(d))
d = []
pc._validate_plan([{"action": "explota"}], remaining=5, discarded=d) if False else None
try:
    pc._validate_plan([{"nonsense": 1}], remaining=5, discarded=d)
except PCControlError as e:
    check("el error dice QUE llego (no solo 'vacia')", "recibí" in str(e))

print("\n[15] Parseo tolerante del JSON del plan")
from core.ai_engine import AIEngine
P = AIEngine._parse_plan_json
for texto, nombre in [
    ('{"done":false,"actions":[{"action":"press","key":"enter"}]}', "JSON limpio"),
    ('```json\n{"done":true,"actions":[]}\n```', "con fences"),
    ('Claro: {"done":false,"actions":[{"action":"wait","seconds":1}]} listo.', "con preambulo"),
    ('{"done":false,"actions":[{"action":"press","key":"enter"},]}', "coma colgante"),
    ('{"done":False,"actions":[]}', "dialecto Python"),
]:
    try:
        P(texto); check(f"{nombre} se interpreta", True)
    except Exception as e:
        check(f"{nombre} se interpreta ({e})", False)
try:
    P('{"a": [{"text": "dijo "hola" y se fue"}]}')
    check("JSON irrecuperable da error claro", False)
except RuntimeError as e:
    check("JSON irrecuperable -> RuntimeError entendible", "JSON válido" in str(e))
except Exception as e:
    check(f"JSON irrecuperable se escapo crudo: {type(e).__name__}", False)

print("\n[16] Cada fallo con su mensaje (no todos «la pantalla no cambio»)")
def _pc(ciclos=6):
    p = PCController(); p.max_cycles = ciclos; p.verify_actions = False
    p._screen_info = lambda: {"width": 1, "height": 1, "image_b64": "", "hash": "abc"}
    return p
ORD = "haz algo raro que no matchea el regex y luego mas"
class Roto1:
    n = 0
    def plan_pc_task(self, **kw):
        Roto1.n += 1
        if Roto1.n == 1: raise RuntimeError("Expecting ':' delimiter")
        return {"done": True, "summary": "", "actions": []}
try:
    r = _pc().execute(ORD, engine=Roto1())
    check("un ciclo con JSON roto NO mata la orden", r["completed"])
except PCControlError as e:
    check(f"un ciclo con JSON roto NO mata la orden ({e})", False)
class SiempreRoto:
    def plan_pc_task(self, **kw): raise RuntimeError("Expecting ':' delimiter")
try:
    _pc().execute(ORD, engine=SiempreRoto()); check("3 JSON rotos -> error", False)
except PCControlError as e:
    check("3 JSON rotos -> habla del PLAN, no de la pantalla", "plan legible" in str(e))
class SiempreVacio:
    def plan_pc_task(self, **kw): return {"done": False, "actions": [{"action": ""}]}
try:
    _pc().execute(ORD, engine=SiempreVacio()); check("3 ciclos sin pasos -> error", False)
except PCControlError as e:
    check("3 ciclos sin pasos validos -> habla de los PASOS", "ni un paso válido" in str(e))

print("\n[17] Planes directos para evitar ciclos repetidos")
plan = pc._direct_plan("abre la calculadora y haz clic en el 3")
check("abre+clic usa plan directo", [x["action"] for x in plan] == ["open_app", "wait", "focus_window", "click_element"])
check("el 3 se convierte al nombre UIA Tres", plan[-1].get("name") == "Tres")
plan = pc._direct_plan("cierra la ventana de Calculadora")
check("cerrar ventana usa close_window", len(plan) == 1 and plan[0]["action"] == "close_window")

print("\n[18] Señales locales de cámara")
from core.camera_observer import summarize_blendshapes, summarize_pose, CameraObservation
cues = summarize_blendshapes({"mouthSmileLeft": 0.8, "mouthSmileRight": 0.7, "jawOpen": 0.5})
check("detecta sonrisa visible", "sonrisa visible" in cues)
check("detecta boca abierta", "boca abierta" in cues)
pts = [{"x": 0.5, "y": 0.5, "visibility": 1.0} for _ in range(33)]
pts[0] = {"x": 0.5, "y": 0.25, "visibility": 1.0}
pts[11] = {"x": 0.42, "y": 0.45, "visibility": 1.0}
pts[12] = {"x": 0.58, "y": 0.45, "visibility": 1.0}
pts[15] = {"x": 0.42, "y": 0.25, "visibility": 1.0}
pts[16] = {"x": 0.58, "y": 0.25, "visibility": 1.0}
pts[23] = {"x": 0.42, "y": 0.75, "visibility": 1.0}
pts[24] = {"x": 0.58, "y": 0.75, "visibility": 1.0}
body = summarize_pose(pts)
check("detecta ambos brazos levantados", "ambos brazos levantados" in body)
summary = CameraObservation(time.time(), 0, faces=1, face_cues=("sonrisa visible",)).summary_es()
check("el resumen aclara que es estimacion", "no una lectura segura" in summary)

print("\n" + ("TODO OK" if not fallos else f"FALLOS: {fallos}"))
sys.exit(1 if fallos else 0)
