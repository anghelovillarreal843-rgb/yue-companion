"""Pruebas del sistema afectivo (core.affect).

Se puede correr de dos formas:
    python -m pytest tests/test_affect.py
    python tests/test_affect.py

Cubren los casos que motivaron el rediseño: negación con alcance, sarcasmo sin
emoji, mensajes implícitos y honestidad de la confianza. TODO corre offline y
sin motor de IA: lo que se prueba aquí son las reglas locales.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.affect import AffectiveInterpreter, Boundary, Emotion  # noqa: E402
from core.affect import negation, sarcasm  # noqa: E402
from core.affect.models import AffectiveState  # noqa: E402
from core.affect.rules import analyze  # noqa: E402
from core.affect.semantic import merge, parse_json_state  # noqa: E402


# ======================================================================
# 1 · EMOCIÓN EXPLÍCITA
# ======================================================================
def test_tristeza_explicita():
    """«Estoy triste.» debe leerse como tristeza con confianza razonable."""
    s = analyze("Estoy triste.")
    assert s.primary_emotion == Emotion.SADNESS
    assert s.confidence >= 0.6
    assert s.valence < -0.3


def test_alegria_explicita():
    s = analyze("Estoy muy feliz hoy, todo salió bien")
    assert s.primary_emotion in (Emotion.JOY, Emotion.RELIEF, Emotion.PRIDE)
    assert s.valence > 0.3
    assert s.sarcasm_probability < 0.4


def test_estoy_bien_no_es_euforia():
    """«Estoy bien.» NO debe leerse como felicidad extrema."""
    s = analyze("Estoy bien.")
    assert s.valence < 0.5, "no debe asumir alegría intensa"
    assert s.primary_emotion != Emotion.EXCITEMENT


# ======================================================================
# 2 · EMOCIÓN IMPLÍCITA
# ======================================================================
def test_decepcion_implicita():
    """Sin la palabra «triste», pero con decepción evidente."""
    s = analyze("Bueno, al final no vino. Da igual, ya me lo esperaba.")
    assert s.primary_emotion in (Emotion.DISAPPOINTMENT, Emotion.SADNESS)
    assert s.primary_emotion != Emotion.NEUTRAL, "no debe caer en neutral automático"
    assert s.valence < 0


def test_causa_extraida_del_texto():
    """El disparador se extrae del mensaje; nunca se inventa."""
    s = analyze("Estoy fatal porque discutí con mi hermano")
    assert s.possible_trigger, "debería identificar la causa"
    assert "hermano" in s.possible_trigger


def test_no_inventa_causa_si_no_la_hay():
    s = analyze("Estoy triste.")
    assert s.possible_trigger == "", "sin evidencia, no se inventa una causa"


# ======================================================================
# 3 · NEGACIÓN (alcance, no búsqueda de la palabra «no»)
# ======================================================================
def test_negacion_simple_no_produce_tristeza():
    s = analyze("No estoy triste, simplemente necesito estar solo.")
    assert s.primary_emotion != Emotion.SADNESS
    assert Emotion.SADNESS in s.negated_emotions
    assert Boundary.WANTS_SPACE in s.explicit_boundary


def test_negacion_con_emocion_alternativa():
    """«No estoy enojado, solo cansado.» → cansancio, NO enojo."""
    s = analyze("No estoy enojado, solo cansado.")
    assert s.primary_emotion != Emotion.ANGER
    assert Emotion.ANGER in s.negated_emotions
    assert s.primary_emotion == Emotion.TIREDNESS


def test_alcance_se_corta_en_marcador_de_contraste():
    tn = negation.normalize("No estoy triste, solo cansado")
    negado = [sc.text for sc in negation.find_scopes(tn)]
    assert any("triste" in t for t in negado)
    assert not any("cansado" in t for t in negado), "«cansado» NO está negado"


def test_negacion_multiple():
    s = analyze("No estoy enfadado ni frustrado, de verdad")
    assert s.primary_emotion not in (Emotion.ANGER, Emotion.FRUSTRATION)


def test_frase_de_riesgo_negada():
    """«Ya no quiero desaparecer» NO es una afirmación activa de riesgo."""
    assert negation.risk_phrase_is_negated("Ya no quiero desaparecer.")
    assert negation.risk_phrase_is_negated("No me quiero morir")
    assert not negation.risk_phrase_is_negated("Quiero desaparecer")


def test_hecho_negado_no_es_emocion_negada():
    """«No vino» es un HECHO negativo, no una emoción negada."""
    s = analyze("No vino nadie a la reunión.")
    assert Emotion.DISAPPOINTMENT not in s.negated_emotions
    assert s.valence <= 0


# ======================================================================
# 4 · SARCASMO E IRONÍA
# ======================================================================
def test_sarcasmo_con_emoji():
    s = analyze("Sí sí, estoy súper feliz de haber perdido todo mi trabajo 🙄")
    assert s.sarcasm_probability >= 0.6
    assert s.primary_emotion not in (Emotion.JOY, Emotion.EXCITEMENT)
    assert s.primary_emotion in (Emotion.FRUSTRATION, Emotion.DISAPPOINTMENT)
    assert s.valence < 0


def test_sarcasmo_SIN_emoji():
    """La detección no puede depender de emojis."""
    s = analyze("Qué maravilla, se borraron seis horas de trabajo.")
    assert s.sarcasm_probability >= 0.5
    assert s.primary_emotion != Emotion.JOY
    assert s.valence < 0


def test_sarcasmo_otro_sin_emoji():
    s = analyze("Qué genial, perdí todo mi trabajo.")
    assert s.sarcasm_probability >= 0.5
    assert s.valence < 0


def test_alegria_sincera_no_es_sarcasmo():
    s = analyze("¡Lo conseguí! Me aceptaron 🎉")
    assert s.sarcasm_probability < 0.4
    assert s.valence > 0.3


def test_sarcasmo_detecta_contradiccion_intrafrase():
    señal = sarcasm.detect("Perfecto, justo lo que necesitaba: otro error")
    assert señal.probability > 0.3


# ======================================================================
# 5 · LÍMITES EXPLÍCITOS
# ======================================================================
def test_limite_no_consejos():
    s = analyze("No quiero consejos, solo necesitaba contárselo a alguien.")
    assert Boundary.NO_ADVICE in s.explicit_boundary


def test_limite_espacio():
    s = analyze("Déjame solo un rato, luego hablamos.")
    assert Boundary.WANTS_SPACE in s.explicit_boundary


def test_limite_no_hablar_del_tema():
    s = analyze("Prefiero no hablar de eso ahora")
    assert Boundary.DOES_NOT_WANT_TO_TALK in s.explicit_boundary


def test_sin_limite_no_se_inventa():
    """Un límite jamás se intuye: o se dice o no existe."""
    s = analyze("Hoy fue un día raro")
    assert s.explicit_boundary == ()


# ======================================================================
# 6 · CONFIANZA E INCERTIDUMBRE (la honestidad del sistema)
# ======================================================================
def test_declaracion_da_mas_confianza_que_indicio():
    directo = analyze("Estoy muy triste")
    indirecto = analyze("Bueno, al final no vino")
    assert directo.confidence > indirecto.confidence


def test_mensaje_ambiguo_tiene_incertidumbre_alta():
    s = analyze("No estoy triste")
    assert s.uncertainty >= 0.5, "sabe qué NO siente, no qué sí"
    assert s.confidence < 0.6


def test_rangos_siempre_validos():
    """Ningún consumidor debe defenderse de valores fuera de rango."""
    for texto in ["", "a", "!!!!!!", "😭😭😭", "x" * 500]:
        s = analyze(texto)
        assert -1.0 <= s.valence <= 1.0
        assert 0.0 <= s.arousal <= 1.0
        assert 0.0 <= s.confidence <= 1.0
        assert 0.0 <= s.sarcasm_probability <= 1.0


# ======================================================================
# 7 · CONTEXTO TEMPORAL
# ======================================================================
def test_contexto_detecta_tendencia():
    it = AffectiveInterpreter(engine=None, use_semantic=False)
    for m in ["Estoy fatal", "Todo me sale mal", "No puedo con esto",
              "Bueno, algo mejor", "Ya estoy más tranquilo"]:
        it.interpret(m)
    assert it.context.trend in ("mejorando", "estable")
    assert len(it.context) == 5


def test_contexto_malestar_sostenido():
    it = AffectiveInterpreter(engine=None, use_semantic=False)
    for m in ["Estoy muy triste", "Sigo hundido", "No mejora nada"]:
        it.interpret(m)
    assert it.context.sustained, "tres turnos negativos = malestar sostenido"


def test_contexto_arrastra_estado_anterior():
    """Un mensaje escueto tras uno doloroso no reinicia la lectura a cero."""
    it = AffectiveInterpreter(engine=None, use_semantic=False)
    it.interpret("Estoy destrozado, no puedo más con esto")
    s = it.interpret("ya")
    assert s.primary_emotion != Emotion.NEUTRAL or s.confidence < 0.5


def test_reset_limpia_contexto():
    it = AffectiveInterpreter(engine=None, use_semantic=False)
    it.interpret("Estoy triste")
    it.reset()
    assert len(it.context) == 0


# ======================================================================
# 8 · SISTEMA HÍBRIDO
# ======================================================================
def test_no_llama_al_modelo_en_casos_claros():
    """Un mensaje inequívoco NO debe gastar una llamada de red."""
    llamadas = []

    class MotorFalso:
        def chat(self, messages, timeout=None):
            llamadas.append(messages)
            return '{"primary_emotion": "joy", "confidence": 0.9}'

    it = AffectiveInterpreter(engine=MotorFalso(), use_semantic=True)
    it.interpret("Estoy muy triste, de verdad me siento fatal")
    assert not llamadas, "las reglas ya lo resolvían: no debía consultar"


def test_llama_al_modelo_en_casos_ambiguos():
    llamadas = []

    class MotorFalso:
        def chat(self, messages, timeout=None):
            llamadas.append(messages)
            return ('{"primary_emotion": "disappointment", "valence": -0.6, '
                    '"arousal": 0.3, "confidence": 0.8}')

    it = AffectiveInterpreter(engine=MotorFalso(), use_semantic=True)
    s = it.interpret("Total, que ya está, cosas que pasan y nada más")
    assert llamadas, "un mensaje ambiguo sí debería escalar"
    assert s.source in ("hybrid", "semantic")


def test_limite_explicito_no_gasta_llamada():
    llamadas = []

    class MotorFalso:
        def chat(self, messages, timeout=None):
            llamadas.append(messages)
            return "{}"

    it = AffectiveInterpreter(engine=MotorFalso(), use_semantic=True)
    it.interpret("No quiero consejos, solo necesitaba contárselo a alguien")
    assert not llamadas, "con un límite claro ya está todo lo importante dicho"


def test_motor_caido_no_rompe_nada():
    class MotorRoto:
        def chat(self, messages, timeout=None):
            raise RuntimeError("sin red")

    it = AffectiveInterpreter(engine=MotorRoto(), use_semantic=True)
    s = it.interpret("Total, que ya está, cosas que pasan y nada más")
    assert isinstance(s, AffectiveState), "debe degradar a reglas locales"


def test_cache_evita_trabajo_repetido():
    it = AffectiveInterpreter(engine=None, use_semantic=False)
    a = it.interpret("Estoy triste")
    b = it.interpret("estoy TRISTE")
    assert a is b, "la caché normaliza y reutiliza"


# ======================================================================
# 9 · FUSIÓN LOCAL + SEMÁNTICA
# ======================================================================
def test_merge_respeta_limites_locales():
    """Un LLM que ignore un «no quiero consejos» no puede borrarlo."""
    local = analyze("No quiero consejos, solo necesitaba contárselo")
    remoto = AffectiveState(primary_emotion=Emotion.SADNESS, confidence=0.9,
                            source="semantic")
    fusion = merge(local, remoto)
    assert Boundary.NO_ADVICE in fusion.explicit_boundary


def test_merge_no_repone_emocion_negada():
    local = analyze("No estoy enojado, solo cansado")
    remoto = AffectiveState(primary_emotion=Emotion.ANGER, confidence=0.95,
                            source="semantic")
    fusion = merge(local, remoto)
    assert fusion.primary_emotion != Emotion.ANGER


def test_merge_baja_confianza_si_hay_desacuerdo():
    local = AffectiveState(primary_emotion=Emotion.SADNESS, confidence=0.8)
    remoto = AffectiveState(primary_emotion=Emotion.ANGER, confidence=0.85,
                            source="semantic")
    fusion = merge(local, remoto)
    assert fusion.confidence <= 0.75, "el desacuerdo ES información"


def test_parse_json_tolera_ruido():
    crudo = '```json\n{"primary_emotion": "sadness", "valence": -0.7}\n```'
    s = parse_json_state(crudo)
    assert s is not None and s.primary_emotion == Emotion.SADNESS


def test_parse_json_invalido_devuelve_none():
    assert parse_json_state("no soy json") is None
    assert parse_json_state("") is None


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
    print(f"{total - len(fallos)}/{total} pruebas afectivas OK")
    for f in fallos:
        print("  FALLO ·", f)
    sys.exit(1 if fallos else 0)
