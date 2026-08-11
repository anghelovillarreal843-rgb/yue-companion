"""Pruebas de la capa de apoyo (core.support).

    python -m pytest tests/test_support.py
    python tests/test_support.py

Aquí se prueba lo que YUE HACE con lo que entendió: qué necesita el usuario,
cómo se comporta ella y qué cara pone. Todo offline y sin motor de IA.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.affect import Boundary, Emotion  # noqa: E402
from core.affect.models import AffectiveState  # noqa: E402
from core.affect.rules import analyze  # noqa: E402
from core.support import (  # noqa: E402
    AVATAR_EMOTIONS, SafetyAssessment, SafetyLevel, SupportNeed,
    build_state_block, decide, detect, express,
)


def _pipeline(texto, safety=None):
    """Atajo: texto → (afecto, intención, decisión, expresión)."""
    afecto = analyze(texto)
    intencion = detect(texto, afecto)
    decision = decide(afecto, intencion, safety)
    expresion = express(afecto, decision, safety)
    return afecto, intencion, decision, expresion


# ======================================================================
# 1 · DETECCIÓN DE NECESIDADES
# ======================================================================
def test_pide_escuchar():
    _, i, d, _ = _pipeline("No quiero consejos, solo necesitaba contárselo a alguien.")
    assert i.primary_need == SupportNeed.LISTEN
    assert i.wants_advice is False
    assert d.offer_advice is False
    assert d.ask_question is False, "pidió contar, no que le interrogaran"


def test_pide_consejo():
    _, i, d, _ = _pipeline("¿Qué hago ahora?")
    assert i.primary_need == SupportNeed.ADVISE
    assert i.wants_advice is True
    assert d.offer_advice is True


def test_pide_solucion():
    _, i, d, _ = _pipeline("Ayúdame a resolver esto paso a paso.")
    assert i.primary_need == SupportNeed.SOLVE
    assert d.mode == SupportNeed.SOLVE
    assert d.offer_advice is True


def test_pide_espacio():
    _, i, d, _ = _pipeline("Déjame solo un rato, luego hablamos.")
    assert i.primary_need == SupportNeed.GIVE_SPACE
    assert d.give_space is True
    assert d.ask_question is False, "no se insiste a quien pidió espacio"
    assert d.should_follow_up is False, "el siguiente paso lo da la persona"


def test_celebra():
    _, i, d, _ = _pipeline("¡Lo conseguí! Me aceptaron.")
    assert i.primary_need == SupportNeed.CELEBRATE
    assert d.mode == SupportNeed.CELEBRATE
    assert d.tone == "celebratory"
    assert d.validate_first is False, "una alegría no se convierte en terapia"


def test_pide_distraccion():
    _, i, d, _ = _pipeline("Distráeme un rato.")
    assert i.primary_need == SupportNeed.DISTRACT
    assert d.mode == SupportNeed.DISTRACT


def test_wants_advice_es_ternario():
    """La ausencia de petición NO significa que quiera consejo."""
    _, i, _, _ = _pipeline("Hoy fue un día raro")
    assert i.wants_advice is None, "no saberlo es un resultado válido"


def test_ausencia_de_peticion_no_habilita_consejo():
    _, i, d, _ = _pipeline("Se me hizo larguísimo el día")
    assert i.wants_advice is None
    assert d.offer_advice is False, "por defecto NO se aconseja"


# ======================================================================
# 2 · PRIORIDADES DE LA POLÍTICA
# ======================================================================
def test_seguridad_tiene_prioridad_maxima():
    """Con riesgo serio, cualquier otra intención pasa a segundo plano."""
    afecto = analyze("Ayúdame a arreglar esto")
    intencion = detect("Ayúdame a arreglar esto", afecto)
    riesgo = SafetyAssessment(level=SafetyLevel.HIGH, directive="contén")
    d = decide(afecto, intencion, riesgo)
    assert d.mode == SupportNeed.SAFETY
    assert d.offer_advice is False
    assert d.give_space is False, "nunca se deja sola a una persona en riesgo"


def test_seguridad_ignora_peticion_de_espacio():
    afecto = analyze("Déjame solo")
    intencion = detect("Déjame solo", afecto)
    riesgo = SafetyAssessment(level=SafetyLevel.CRITICAL)
    d = decide(afecto, intencion, riesgo)
    assert d.mode == SupportNeed.SAFETY
    assert d.give_space is False


def test_limite_gana_a_la_deduccion():
    """Aunque haya malestar, un «no me aconsejes» se obedece."""
    _, _, d, _ = _pipeline(
        "Estoy fatal por lo del trabajo, pero no quiero consejos")
    assert d.offer_advice is False


def test_incertidumbre_lleva_a_preguntar():
    _, _, d, _ = _pipeline("No estoy triste")
    assert d.mode in (SupportNeed.ASK, SupportNeed.LISTEN)
    assert d.offer_advice is False
    assert d.acknowledge_uncertainty is True, "debe reconocer que no está segura"


def test_malestar_sin_peticion_no_resuelve():
    _, _, d, _ = _pipeline("Estoy destrozado, hoy fue horrible")
    assert d.mode in (SupportNeed.COMFORT, SupportNeed.LISTEN)
    assert d.offer_advice is False
    assert d.validate_first is True


def test_sarcasmo_no_se_responde_con_alegria():
    _, _, d, _ = _pipeline("Qué genial, perdí todo mi trabajo.")
    assert d.mode != SupportNeed.CELEBRATE
    assert d.tone in ("gentle", "warm")
    assert d.offer_advice is False


def test_malestar_sostenido_deja_de_preguntar():
    """A quien lleva tres turnos hundido no se le interroga más."""
    afecto = analyze("Estoy destrozado")
    intencion = detect("Estoy destrozado", afecto)
    d = decide(afecto, intencion, None, context_summary={"sustained": True})
    assert d.ask_question is False


def test_no_preguntar_si_lo_pidio():
    _, _, d, _ = _pipeline("Deja de preguntarme, en serio")
    assert d.ask_question is False


# ======================================================================
# 3 · EXPRESIÓN: YUE RESPONDE, NO IMITA
# ======================================================================
def test_usuario_enojado_no_pone_a_yue_enojada():
    """El caso que motivó separar afecto de expresión."""
    afecto = AffectiveState(primary_emotion=Emotion.ANGER, valence=-0.8,
                            arousal=0.9, confidence=0.85)
    intencion = detect("estoy furioso", afecto)
    d = decide(afecto, intencion)
    e = express(afecto, d)
    assert e.name != "angry", "YUE no imita la rabia: la acompaña"
    assert e.name in ("worried", "focused", "relaxed", "curious")
    assert e.intensity <= 0.8, "acompaña sin desbordarse"


def test_tristeza_del_usuario_da_preocupacion_en_yue():
    afecto = AffectiveState(primary_emotion=Emotion.SADNESS, valence=-0.7,
                            arousal=0.2, confidence=0.85)
    d = decide(afecto, detect("estoy triste", afecto))
    e = express(afecto, d)
    assert e.name in ("worried", "relaxed")


def test_la_alegria_si_se_comparte():
    afecto = AffectiveState(primary_emotion=Emotion.PRIDE, valence=0.8,
                            arousal=0.7, confidence=0.85)
    d = decide(afecto, detect("¡lo conseguí!", afecto))
    e = express(afecto, d)
    assert e.name in ("proud", "excited", "happy")
    assert e.intensity >= 0.6, "alegrarse a medias no alegra a nadie"


def test_expresion_siempre_valida_para_el_avatar():
    """Ninguna etiqueta puede salirse del vocabulario del VRM."""
    textos = ["Estoy triste", "¡Lo conseguí!", "Déjame solo",
              "Qué genial, perdí todo", "¿Qué hago?", "", "No sé"]
    for t in textos:
        _, _, _, e = _pipeline(t)
        assert e.name in AVATAR_EMOTIONS, f"«{e.name}» no existe en el avatar"
        assert 0.25 <= e.intensity <= 1.0
        assert e.duration_ms >= 1200


def test_seguridad_da_expresion_de_presencia():
    afecto = analyze("ya no puedo más con nada")
    riesgo = SafetyAssessment(level=SafetyLevel.HIGH)
    d = decide(afecto, detect("ya no puedo más", afecto), riesgo)
    e = express(afecto, d, riesgo)
    assert e.name == "worried"
    assert e.priority == "SAFETY"
    assert e.duration_ms >= 8000, "los momentos delicados duran"


def test_no_celebra_sobre_ironia():
    afecto = AffectiveState(primary_emotion=Emotion.JOY, valence=0.5,
                            arousal=0.6, confidence=0.7,
                            sarcasm_probability=0.8)
    from core.support.needs import SupportIntent
    intencion = SupportIntent(SupportNeed.CELEBRATE, explicit=True)
    from core.support.policy import SupportDecision
    d = SupportDecision(mode=SupportNeed.CELEBRATE)
    e = express(afecto, d)
    assert e.name != "excited", "no se celebra a ciegas sobre una ironía"


# ======================================================================
# 4 · BLOQUE DE PROMPT
# ======================================================================
def test_bloque_incluye_prohibiciones():
    a, i, d, _ = _pipeline("No quiero consejos, solo necesitaba contárselo")
    bloque = build_state_block(a, i, None, d)
    assert "NO des consejos" in bloque
    assert "LÍMITES DE ESTE TURNO" in bloque


def test_bloque_avisa_de_sarcasmo():
    a, i, d, _ = _pipeline("Qué maravilla, se borraron seis horas de trabajo.")
    bloque = build_state_block(a, i, None, d)
    assert "ironía" in bloque or "sarcasmo" in bloque


def test_bloque_menciona_emocion_negada():
    a, i, d, _ = _pipeline("No estoy enojado, solo cansado")
    bloque = build_state_block(a, i, None, d)
    assert "NEGÓ" in bloque


def test_bloque_prohibe_repetir_etiquetas():
    a, i, d, _ = _pipeline("Estoy triste")
    bloque = build_state_block(a, i, None, d)
    assert "NO la menciones" in bloque or "no se dice en voz alta" in bloque


def test_bloque_pide_prudencia_con_poca_confianza():
    a, i, d, _ = _pipeline("No estoy triste")
    bloque = build_state_block(a, i, None, d)
    assert "no estás segura" in bloque or "condicional" in bloque


def test_bloque_vacio_sin_decision():
    a, i, _, _ = _pipeline("hola")
    assert build_state_block(a, i, None, None) == ""


# ======================================================================
# 5 · CONVERSIÓN DE SEGURIDAD (sin perder matices)
# ======================================================================
def test_safety_assessment_conserva_niveles():
    informe = {"level": 3, "score": 0.8, "directive": "contén",
               "reasons": ["ideación clara"], "protective": False}
    ev = SafetyAssessment.from_safety_ext(informe)
    assert ev.level == SafetyLevel.HIGH
    assert ev.is_serious
    assert ev.reasons == ("ideación clara",)


def test_safety_assessment_tolera_vacio():
    ev = SafetyAssessment.from_safety_ext(None)
    assert ev.level == SafetyLevel.NONE
    assert not ev.is_serious


if __name__ == "__main__":
    fallos = []
    total = 0
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            total += 1
            try:
                _f()
            except AssertionError as exc:
                fallos.append(f"{_n}: {exc}")
            except Exception as exc:  # pragma: no cover
                fallos.append(f"{_n}: ERROR {exc}")
    print(f"{total - len(fallos)}/{total} pruebas de apoyo OK")
    for f in fallos:
        print("  FALLO ·", f)
    sys.exit(1 if fallos else 0)
