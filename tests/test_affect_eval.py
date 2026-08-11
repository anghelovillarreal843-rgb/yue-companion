"""Banco de evaluación del sistema afectivo.

    python -m pytest tests/test_affect_eval.py     # como test (exige umbrales)
    python tests/test_affect_eval.py               # informe detallado por categoría

A diferencia de los tests unitarios, que comprueban comportamientos concretos,
esto mide la CALIDAD GLOBAL sobre un corpus de frases reales. Sirve para dos
cosas: detectar regresiones cuando se toque el léxico, y ver de un vistazo qué
categoría va peor.

El corpus vive en `tests/eval/afecto_eval.jsonl` (un caso JSON por línea), lo
que permite crecer de 68 a 300 ejemplos sin tocar una sola línea de código.

Campos que puede declarar un caso (todos opcionales salvo `text`):

    emotion         lista de emociones aceptables como principal
    not_emotion     emociones que NO deben salir como principal
    negated         emociones que deben aparecer como negadas
    not_negated     emociones que NO deben marcarse como negadas
    boundary        límites explícitos que deben detectarse
    need            modos de apoyo aceptables en la decisión final
    wants_advice    true / false esperado
    min_conf        confianza mínima
    min_sarcasm     probabilidad mínima de sarcasmo
    max_sarcasm     probabilidad máxima de sarcasmo
    min_safety      nivel mínimo de riesgo (0-4)
    max_safety      nivel máximo de riesgo (0-4)
    max_valence     valencia máxima
    risk_negated    la frase de riesgo debe verse como negada
    no_advice       la decisión NO debe permitir consejo
    no_question     la decisión NO debe hacer preguntas
    no_follow_up    la decisión NO debe querer retomar el tema

Todo se evalúa OFFLINE, solo con las reglas locales: así el resultado es
reproducible y no depende de la red ni gasta llamadas.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.affect import risk_phrase_is_negated  # noqa: E402
from core.affect.rules import analyze  # noqa: E402
from core.companion_brain import CompanionBrain  # noqa: E402

CORPUS = Path(__file__).resolve().parent / "eval" / "afecto_eval.jsonl"

#: Aciertos mínimos exigidos. Se dejan por debajo del resultado actual a
#: propósito: son un SUELO contra regresiones, no una marca a batir.
UMBRAL_GLOBAL = 0.90
UMBRAL_CATEGORIA = 0.75


def cargar_casos():
    """Lee el corpus. Ignora líneas vacías para poder agruparlo visualmente."""
    casos = []
    with open(CORPUS, encoding="utf-8") as fh:
        for linea in fh:
            linea = linea.strip()
            if linea:
                casos.append(json.loads(linea))
    return casos


def evaluar_caso(caso, brain):
    """Devuelve (ok, lista_de_errores) para un caso del corpus."""
    texto = caso["text"]
    resultado = brain.process(texto, allow_semantic=False)
    afecto = resultado.affect
    decision = resultado.decision
    fallos = []

    principal = str(afecto.primary_emotion)
    secundaria = str(afecto.secondary_emotion or "")

    if "emotion" in caso:
        aceptables = set(caso["emotion"])
        if principal not in aceptables and secundaria not in aceptables:
            fallos.append(f"emoción {principal} ∉ {sorted(aceptables)}")

    for e in caso.get("not_emotion", []):
        if principal == e:
            fallos.append(f"no debía ser {e}")

    negadas = {str(x) for x in afecto.negated_emotions}
    for e in caso.get("negated", []):
        if e not in negadas:
            fallos.append(f"debía marcar {e} como negada")
    for e in caso.get("not_negated", []):
        if e in negadas:
            fallos.append(f"NO debía marcar {e} como negada")

    limites = {str(b) for b in afecto.explicit_boundary}
    for b in caso.get("boundary", []):
        if b not in limites:
            fallos.append(f"debía detectar el límite {b}")

    if "need" in caso:
        aceptables = set(caso["need"])
        modo = str(decision.mode)
        segundo = str(decision.secondary_mode or "")
        intencion = str(resultado.intent.primary_need)
        if not ({modo, segundo, intencion} & aceptables):
            fallos.append(f"necesidad {modo}/{intencion} ∉ {sorted(aceptables)}")

    if "wants_advice" in caso:
        esperado = caso["wants_advice"]
        if resultado.intent.wants_advice != esperado:
            fallos.append(
                f"wants_advice={resultado.intent.wants_advice}, esperado {esperado}")

    if "min_conf" in caso and afecto.confidence < caso["min_conf"]:
        fallos.append(f"confianza {afecto.confidence:.2f} < {caso['min_conf']}")
    if "min_sarcasm" in caso and afecto.sarcasm_probability < caso["min_sarcasm"]:
        fallos.append(
            f"sarcasmo {afecto.sarcasm_probability:.2f} < {caso['min_sarcasm']}")
    if "max_sarcasm" in caso and afecto.sarcasm_probability > caso["max_sarcasm"]:
        fallos.append(
            f"sarcasmo {afecto.sarcasm_probability:.2f} > {caso['max_sarcasm']}")

    nivel = int(resultado.safety.level)
    if "min_safety" in caso and nivel < caso["min_safety"]:
        fallos.append(f"seguridad {nivel} < {caso['min_safety']}")
    if "max_safety" in caso and nivel > caso["max_safety"]:
        fallos.append(f"seguridad {nivel} > {caso['max_safety']}")

    if "max_valence" in caso and afecto.valence > caso["max_valence"]:
        fallos.append(f"valencia {afecto.valence:.2f} > {caso['max_valence']}")

    if caso.get("risk_negated") and not risk_phrase_is_negated(texto):
        fallos.append("la frase de riesgo debía verse como negada")

    if caso.get("no_advice") and decision.offer_advice:
        fallos.append("no debía ofrecer consejo")
    if caso.get("no_question") and decision.ask_question:
        fallos.append("no debía preguntar")
    if caso.get("no_follow_up") and decision.should_follow_up:
        fallos.append("no debía querer retomar el tema")

    return (not fallos), fallos


def ejecutar():
    """Corre el corpus entero. Devuelve (global, por_categoria, detalles)."""
    brain = CompanionBrain(engine=None, use_semantic=False)
    casos = cargar_casos()
    por_cat = defaultdict(lambda: [0, 0])   # categoría -> [aciertos, total]
    detalles = []

    for caso in casos:
        cat = caso.get("cat", "otros")
        ok, fallos = evaluar_caso(caso, brain)
        por_cat[cat][1] += 1
        if ok:
            por_cat[cat][0] += 1
        else:
            detalles.append((caso.get("id", "?"), cat, caso["text"], fallos))
        brain.reset()   # cada caso se evalúa aislado, sin arrastre de contexto

    aciertos = sum(v[0] for v in por_cat.values())
    total = sum(v[1] for v in por_cat.values())
    return (aciertos / total if total else 0.0), dict(por_cat), detalles


# ======================================================================
# Pruebas (pytest)
# ======================================================================
def test_corpus_existe_y_es_valido():
    casos = cargar_casos()
    assert len(casos) >= 50, "el banco debe tener al menos 50 frases"
    assert all("text" in c and "cat" in c for c in casos)
    ids = [c.get("id") for c in casos]
    assert len(ids) == len(set(ids)), "hay identificadores repetidos"


def test_cobertura_de_categorias():
    """Las 11 categorías del diseño deben estar representadas."""
    esperadas = {
        "explicit_emotion", "implicit_emotion", "negation", "sarcasm",
        "support_need", "boundaries", "celebration", "advice",
        "problem_solving", "safety", "neutral",
    }
    presentes = {c["cat"] for c in cargar_casos()}
    assert esperadas <= presentes, f"faltan categorías: {esperadas - presentes}"


def test_acierto_global():
    ratio, _, detalles = ejecutar()
    mensaje = "\n".join(f"  [{i}] {t} → {'; '.join(f)}" for i, _c, t, f in detalles)
    assert ratio >= UMBRAL_GLOBAL, (
        f"acierto global {ratio:.1%} < {UMBRAL_GLOBAL:.0%}\n{mensaje}")


def test_acierto_por_categoria():
    _, por_cat, _ = ejecutar()
    flojas = {c: v[0] / v[1] for c, v in por_cat.items()
              if v[1] and (v[0] / v[1]) < UMBRAL_CATEGORIA}
    assert not flojas, f"categorías por debajo del umbral: {flojas}"


def test_evaluacion_es_offline():
    """El banco no debe depender de la red ni gastar llamadas al modelo."""
    class MotorProhibido:
        def chat(self, *a, **k):
            raise AssertionError("la evaluación NO debe llamar al modelo")

    brain = CompanionBrain(engine=MotorProhibido(), use_semantic=False)
    for caso in cargar_casos()[:20]:
        brain.process(caso["text"], allow_semantic=False)


if __name__ == "__main__":
    ratio, por_cat, detalles = ejecutar()
    print("=" * 62)
    print("BANCO DE EVALUACIÓN AFECTIVA · YUE")
    print("=" * 62)
    for cat in sorted(por_cat):
        ok, total = por_cat[cat]
        barra = "█" * int(20 * ok / total) + "·" * (20 - int(20 * ok / total))
        print(f"  {cat:18} {barra} {ok:2}/{total:2}  {ok / total:5.1%}")
    print("-" * 62)
    aciertos = sum(v[0] for v in por_cat.values())
    total = sum(v[1] for v in por_cat.values())
    print(f"  {'GLOBAL':18} {aciertos}/{total}  {ratio:.1%}")
    if detalles:
        print("\nCasos fallados:")
        for ident, cat, texto, fallos in detalles:
            print(f"  [{ident}·{cat}] «{texto}»")
            for f in fallos:
                print(f"      - {f}")
    print("=" * 62)
    sys.exit(0 if ratio >= UMBRAL_GLOBAL else 1)
