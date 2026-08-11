"""Pruebas de INTEGRACIÓN del acompañamiento emocional.

    python -m pytest tests/test_companion_integration.py
    python tests/test_companion_integration.py

Aquí no se prueba una pieza suelta sino la cadena entera:

    mensaje → afecto → necesidad → seguridad → decisión → expresión → prompt
            + arbitraje del avatar + memoria estructurada

Incluye los doce escenarios que se pidieron como comportamiento final, además
de conversaciones de varios turnos, retrocompatibilidad y degradación cuando
faltan módulos.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import emotion  # noqa: E402
from core.affect import Boundary, Emotion  # noqa: E402
from core.companion_brain import CompanionBrain  # noqa: E402
from core.memory import Memory  # noqa: E402
from core.state import Priority, YueStateManager  # noqa: E402
from core.support import SafetyLevel, SupportNeed  # noqa: E402


def _brain():
    """Cerebro offline: reglas locales, sin red y sin coste."""
    return CompanionBrain(engine=None, use_semantic=False)


# ======================================================================
# 1 · LOS DOCE ESCENARIOS OBLIGATORIOS
# ======================================================================
def test_01_tristeza_explicita():
    r = _brain().process("Estoy triste.")
    assert r.affect.primary_emotion == Emotion.SADNESS
    assert r.affect.confidence >= 0.6
    assert r.decision.mode in (SupportNeed.COMFORT, SupportNeed.LISTEN)
    assert r.decision.offer_advice is False


def test_02_decepcion_implicita():
    r = _brain().process("Bueno, al final no vino. Da igual, ya me lo esperaba.")
    assert r.affect.primary_emotion in (Emotion.DISAPPOINTMENT, Emotion.SADNESS)
    assert r.affect.primary_emotion != Emotion.NEUTRAL
    assert r.decision.offer_advice is False, "escucha antes de aconsejar"


def test_03_sarcasmo_no_es_alegria():
    r = _brain().process("Sí sí, estoy súper feliz de haber perdido todo mi trabajo 🙄")
    assert r.affect.sarcasm_probability >= 0.6
    assert r.affect.primary_emotion in (Emotion.FRUSTRATION, Emotion.DISAPPOINTMENT)
    assert r.decision.mode != SupportNeed.CELEBRATE
    assert r.expression.name not in ("happy", "excited", "playful")


def test_04_tristeza_negada_con_espacio():
    r = _brain().process("No estoy triste, simplemente necesito estar solo.")
    assert Emotion.SADNESS in r.affect.negated_emotions
    assert r.decision.mode == SupportNeed.GIVE_SPACE
    assert r.decision.offer_advice is False
    assert r.decision.ask_question is False, "nada de interrogatorio"


def test_05_enojo_negado_es_cansancio():
    r = _brain().process("No estoy enojado, solo cansado.")
    assert r.affect.primary_emotion != Emotion.ANGER
    assert r.affect.primary_emotion == Emotion.TIREDNESS


def test_06_pide_escuchar_no_consejo():
    r = _brain().process("No quiero consejos, solo necesitaba contárselo a alguien.")
    assert r.decision.mode == SupportNeed.LISTEN
    assert r.intent.wants_advice is False
    assert r.decision.offer_advice is False


def test_07_pide_consejo():
    r = _brain().process("¿Qué hago ahora?")
    assert r.decision.mode == SupportNeed.ADVISE
    assert r.decision.offer_advice is True


def test_08_pide_solucion():
    r = _brain().process("Ayúdame a resolver esto paso a paso.")
    assert r.decision.mode == SupportNeed.SOLVE
    assert r.decision.offer_advice is True


def test_09_celebra():
    r = _brain().process("¡Lo conseguí! Me aceptaron.")
    assert r.decision.mode == SupportNeed.CELEBRATE
    assert r.expression.name in ("excited", "proud", "happy")
    assert r.decision.validate_first is False


def test_10_distraccion():
    r = _brain().process("Distráeme un rato.")
    assert r.decision.mode == SupportNeed.DISTRACT


def test_11_espacio_sin_seguimiento():
    r = _brain().process("Déjame solo un rato, luego hablamos.")
    assert r.decision.mode == SupportNeed.GIVE_SPACE
    assert r.decision.give_space is True
    assert r.decision.should_follow_up is False


def test_12_estoy_bien_no_es_euforia():
    r = _brain().process("Estoy bien.")
    assert r.affect.valence < 0.5
    assert r.decision.mode != SupportNeed.CELEBRATE


# ======================================================================
# 2 · SEGURIDAD INTEGRADA (safety_ext, graduada)
# ======================================================================
def test_riesgo_alto_toma_el_mando():
    r = _brain().process("Ya no quiero seguir viviendo.")
    assert r.safety.level >= SafetyLevel.HIGH
    assert r.decision.mode == SupportNeed.SAFETY
    assert r.is_risk
    assert r.safety_directive, "debe traer directiva de contención"
    assert r.expression.name == "worried"


def test_riesgo_conserva_niveles_no_booleano():
    b = _brain()
    leve = b.process("Estoy un poco triste hoy")
    b.reset()
    grave = b.process("Tengo un plan para quitarme la vida")
    assert grave.safety.level > leve.safety.level, "debe haber gradación"
    assert grave.safety.level == SafetyLevel.CRITICAL


def test_riesgo_negado_baja_el_nivel():
    b = _brain()
    afirma = b.process("Quiero desaparecer para siempre")
    b.reset()
    niega = b.process("Ya no quiero desaparecer, de verdad estoy mejor")
    assert niega.safety.level < afirma.safety.level


def test_seguridad_ignora_peticion_de_espacio():
    r = _brain().process("Déjame solo, ya no quiero seguir viviendo")
    assert r.decision.mode == SupportNeed.SAFETY
    assert r.decision.give_space is False


def test_contexto_de_ficcion_no_alarma():
    r = _brain().process("En la película el protagonista quería morir al final")
    assert r.safety.level < SafetyLevel.HIGH


def test_senal_externa_sube_el_cuidado():
    b = _brain()
    sin = b.process("Hoy no hice gran cosa")
    b.reset()
    con = b.process("Hoy no hice gran cosa", external_signal=True)
    assert con.safety.level >= sin.safety.level


# ======================================================================
# 3 · SEPARACIÓN AFECTO / EXPRESIÓN
# ======================================================================
def test_yue_no_imita_al_usuario_enfadado():
    r = _brain().process("Estoy furioso, me hierve la sangre con esto")
    assert r.affect.primary_emotion == Emotion.ANGER
    assert r.expression.name != "angry", "YUE responde, no imita"


def test_expresion_valida_en_todos_los_casos():
    from core.support import AVATAR_EMOTIONS
    b = _brain()
    for t in ["Estoy triste", "¡Genial!", "Déjame", "¿Qué hago?",
              "Qué maravilla, lo perdí todo", "", "..."]:
        r = b.process(t)
        assert r.expression.name in AVATAR_EMOTIONS
        b.reset()


# ======================================================================
# 4 · ARBITRAJE DEL AVATAR (YueStateManager)
# ======================================================================
def test_la_musica_no_pisa_el_apoyo():
    """El escenario exacto del diseño: música alegre durante algo doloroso."""
    sm = YueStateManager()
    r = _brain().process("Estoy destrozado por lo de mi abuela")
    assert sm.request_emotion(r.expression.name, r.expression.intensity,
                              r.expression.duration_ms,
                              priority=Priority.USER, source="apoyo_usuario")
    aplicado = sm.request_emotion("excited", 0.9, 4000,
                                  priority=Priority.MEDIA, source="musica")
    assert aplicado is False, "la música NO puede cambiar el avatar aquí"
    assert sm.get_state().emotion_primary == r.expression.name


def test_la_seguridad_pisa_a_todos():
    sm = YueStateManager()
    sm.request_emotion("happy", 0.8, 5000, priority=Priority.USER, source="apoyo")
    assert sm.request_emotion("worried", 0.9, 9000,
                              priority=Priority.EMERGENCY, source="seguridad")
    assert sm.get_state().emotion_primary == "worried"


def test_release_emotion_libera_el_turno():
    sm = YueStateManager()
    sm.request_emotion("worried", 0.8, 9000, priority=Priority.USER, source="apoyo")
    assert sm.release_emotion("apoyo") is True
    assert sm.request_emotion("excited", 0.9, 3000,
                              priority=Priority.MEDIA, source="musica")


# ======================================================================
# 5 · MEMORIA ESTRUCTURADA Y PRIVACIDAD
# ======================================================================
def _memoria_temporal():
    carpeta = tempfile.mkdtemp()
    return Memory(os.path.join(carpeta, "test.db"))


def test_memoria_guarda_lectura_no_texto():
    m = _memoria_temporal()
    r = _brain().process("Estoy fatal porque discutí con mi hermano")
    m.add_affect(
        emotion=str(r.affect.primary_emotion), valence=r.affect.valence,
        arousal=r.affect.arousal, distress=r.affect.distress,
        support_need=str(r.decision.mode), confidence=r.affect.confidence,
        trigger=r.affect.possible_trigger)
    fila = m.recent_affect(1)[0]
    assert fila["emotion"] == str(r.affect.primary_emotion)
    assert fila["trigger_category"] == "relaciones", "categoría, no frase literal"
    texto_guardado = " ".join(str(v) for v in fila.values())
    assert "hermano" not in texto_guardado, "el mensaje NO debe duplicarse"


def test_migracion_limpia_textos_antiguos():
    m = _memoria_temporal()
    m.add_mood("texto", "sad", 0.8, "algo muy privado que dije")
    assert m.purge_mood_texts() == 1
    assert m.purge_mood_texts() == 0, "la migración es idempotente"
    # El resumen de ánimo sigue funcionando tras la limpieza.
    assert isinstance(m.get_mood_summary(7), str)


def test_tendencia_afectiva():
    m = _memoria_temporal()
    for _ in range(3):
        m.add_affect(emotion="sadness", valence=-0.6, distress=0.5,
                     trigger="el examen del instituto")
    tendencia = m.affect_trend()
    assert tendencia["dominante"] == "sadness"
    assert tendencia["valence_media"] < 0
    assert tendencia["causa_frecuente"] == "estudios"


def test_mood_summary_entiende_taxonomia_nueva():
    m = _memoria_temporal()
    m.add_mood("texto", "disappointment", 0.7, None)
    m.add_mood("texto", "loneliness", 0.6, None)
    resumen = m.get_mood_summary(7)
    assert resumen, "las etiquetas nuevas deben traducirse al resumen"


# ======================================================================
# 6 · CONVERSACIÓN DE VARIOS TURNOS
# ======================================================================
def test_contexto_entre_turnos():
    b = _brain()
    b.process("Mañana tengo una entrevista muy importante.")
    r = b.process("Al final no vino nadie.")
    assert r.affect.primary_emotion != Emotion.NEUTRAL
    assert r.affect.valence < 0


def test_cambio_de_necesidad_en_la_conversacion():
    """Primero desahogo, luego pide soluciones: YUE debe cambiar de modo."""
    b = _brain()
    r1 = b.process("No quiero consejos, solo necesitaba contarlo")
    assert r1.decision.offer_advice is False
    r2 = b.process("Bueno, ahora sí, ayúdame a resolverlo paso a paso")
    assert r2.decision.mode == SupportNeed.SOLVE
    assert r2.decision.offer_advice is True


def test_malestar_sostenido_deja_de_interrogar():
    b = _brain()
    for m in ["Estoy fatal", "Todo me sale mal", "No puedo con nada"]:
        r = b.process(m)
    assert b.interpreter.context.sustained
    assert r.decision.ask_question is False


# ======================================================================
# 7 · BLOQUE DE PROMPT
# ======================================================================
def test_prompt_incluye_las_prohibiciones():
    r = _brain().process("No quiero consejos, solo necesitaba contárselo")
    assert "NO des consejos" in r.prompt_block


def test_prompt_para_espacio_es_contenido():
    r = _brain().process("Déjame solo un rato")
    assert "DA ESPACIO" in r.prompt_block
    assert "NO hagas preguntas" in r.prompt_block


def test_prompt_no_se_genera_sin_necesidad():
    r = _brain().process("hola")
    assert isinstance(r.prompt_block, str)


# ======================================================================
# 8 · RETROCOMPATIBILIDAD Y DEGRADACIÓN
# ======================================================================
def test_api_clasica_sigue_viva():
    """Nada de lo anterior puede haberse roto."""
    assert emotion.clean_response("[Curiosa] Estoy curiosa. Cuéntame más.") == "Cuéntame más."
    estado = emotion.infer_emotion_state("¡Qué genial!")
    assert estado.name and estado.intensity > 0
    reaccion = emotion.infer_reaction_to_user("Estoy triste")
    assert reaccion.name == "worried"


def test_puente_de_compatibilidad():
    resultado = emotion.analyze_user_message("Estoy triste")
    assert resultado is not None
    estado = emotion.to_emotion_state(resultado.expression)
    assert estado.name in ("worried", "sad", "relaxed", "curious")
    assert estado.duration_ms >= 1200


def test_traduccion_a_etiquetas_del_avatar():
    assert emotion.affect_to_avatar_name("sadness") == "sad"
    assert emotion.affect_to_avatar_name("anxiety") == "worried"
    assert emotion.affect_to_avatar_name("inventada") == "neutral"


def test_degradacion_sin_motor():
    b = CompanionBrain(engine=None, use_semantic=True)
    r = b.process("Total, que nada, cosas que pasan")
    assert r.affect is not None
    assert r.decision is not None


def test_nunca_lanza_excepciones():
    """Ningún mensaje puede tumbar una conversación."""
    b = _brain()
    for t in ["", "   ", "\n", "😭" * 50, "x" * 3000, "?!¿¡", "SELECT * FROM;",
              "null", "{}", "<script>"]:
        r = b.process(t)
        assert r is not None and r.expression.name
        b.reset()


def test_resultado_serializable():
    r = _brain().process("Estoy triste")
    d = r.to_dict()
    import json
    json.dumps(d)  # no debe reventar
    assert d["affect"]["primary_emotion"] == "sadness"


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
    print(f"{total - len(fallos)}/{total} pruebas de integración OK")
    for f in fallos:
        print("  FALLO ·", f)
    sys.exit(1 if fallos else 0)
