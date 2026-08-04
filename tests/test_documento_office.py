"""Pruebas de Office COM y de la lectura de INTENCIÓN.

Verifica, sin Windows ni LLM, que:
  · Word/Excel/PowerPoint se enrutan a Office COM,
  · el Bloc de notas y Chrome conservan la ruta normal,
  · «escribe sobre X» / «hazme un resumen» se entienden como redacción (van a la IA),
  · el texto literal («hola», entre comillas, con dos puntos) se sigue tecleando tal cual.

    python tests/test_documento_office.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.pc_control import PCController

fallos = []


def check(nombre, cond, detalle=""):
    print(("  OK  " if cond else "  FALLO  ") + nombre + (f"  ·  {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


pc = PCController()


def acciones(orden):
    return [p["action"] for p in pc._direct_plan(orden)]


# =====================================================================
print("\n[1] Abrir Office usa creación nativa COM")
for orden in ("abre word", "abre excel", "abre powerpoint",
              "abre Microsoft Word", "inicia el word"):
    plan = pc._direct_plan(orden)
    accs = [p["action"] for p in plan]
    check(f"«{orden}» -> office_create", accs == ["office_create"], str(accs))


# =====================================================================
print("\n[2] El Bloc de notas / Chrome conservan la apertura normal")
for orden in ("abre el bloc de notas", "abre chrome", "abre la calculadora"):
    plan = pc._direct_plan(orden)
    check(f"«{orden}» -> open_app", [p["action"] for p in plan] == ["open_app"], str(plan))


# =====================================================================
print("\n[3] Abrir + escribir LITERAL en Word usa una sola acción COM")
plan = pc._direct_plan('abre word y escribe "hola equipo"')
accs = [p["action"] for p in plan]
check("ruta nativa compacta", accs == ["office_write"], str(accs))
literal = plan[0] if plan else {}
check("el texto literal llega a office_write", "hola equipo" in literal.get("text", ""),
      literal.get("text", ""))
check("crea documento nuevo", literal.get("new_document") is True, str(literal))


# =====================================================================
print("\n[4] Abrir + escribir CONTENIDO: se deja a la IA (no plan directo)")
for orden in (
    "abre word y escribe un ensayo sobre el Perú",
    "abre word y escribe sobre la contaminación",
    "abre word y redacta una carta de renuncia",
    "abre word y escribe un resumen del libro",
):
    plan = pc._direct_plan(orden)
    check(f"«{orden[:42]}…» -> planificador IA", plan == [], str([p['action'] for p in plan]))


# =====================================================================
print("\n[5] Intención vs. literal en _pide_contenido")
casos_contenido = [
    "un ensayo sobre el agua",
    "sobre la fotosíntesis",
    "acerca de la independencia",
    "hazme una carta formal",
    "genera un correo de disculpa",
    "explica la relatividad",
    "resume el capítulo 3",
    "describe a un dragón",
    "una lista de compras",
    "un documento con las conclusiones",
]
for texto in casos_contenido:
    check(f"«{texto[:38]}» = redactar", pc._pide_contenido(texto) is True)

casos_literales = [
    "hola",
    "hola mundo como estas",
    '"esto va tal cual"',
    ":recordatorio de la reunion",
    "reunion a las 5 con el equipo",
    "leche pan huevos",
]
for texto in casos_literales:
    check(f"«{texto[:38]}» = literal", pc._pide_contenido(texto) is False)


# =====================================================================
print("\n[6] Bare «escribe X»: literal se teclea, contenido va a la IA")
plan_lit = pc._direct_plan("escribe hola equipo")
check("«escribe hola equipo» -> type_text",
      [p["action"] for p in plan_lit] == ["type_text"], str(plan_lit))
plan_cont = pc._direct_plan("escribe un resumen sobre el cambio climático")
check("«escribe un resumen sobre…» -> IA", plan_cont == [], str(plan_cont))


# =====================================================================
print("\n[7] Las acciones Office pasan la validación")
for raw in (
    {"action": "office_create", "app": "word"},
    {"action": "office_write", "app": "excel", "text": "A\tB"},
    {"action": "office_save", "app": "powerpoint", "path": "demo.pptx"},
    {"action": "office_read", "app": "word"},
):
    paso = pc._validate_step(raw)
    check(f"{raw['action']} válido", paso.get("action") == raw["action"], str(paso))


print("\n" + ("TODO OK" if not fallos else f"FALLOS: {fallos}"))
sys.exit(1 if fallos else 0)
