"""Pruebas del detector híbrido de seguridad emocional (FASE 5).

    python -m pytest tests/test_safety_ext.py
    python tests/test_safety_ext.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.safety_ext import RiskLevel, assess, directive_for, should_escalate_ui


def test_texto_neutro_no_dispara():
    r = assess("Hoy hice ejercicio y comí rico, estoy contento")
    assert r["level"] == RiskLevel.NINGUNO
    assert r["directive"] == ""


def test_tristeza_es_leve():
    r = assess("Me siento muy triste y solo últimamente")
    assert r["level"] == RiskLevel.LEVE
    assert "leve" in r["directive"].lower()


def test_desesperanza_es_moderado():
    r = assess("Ya no aguanto más, no le veo sentido a nada")
    assert r["level"] >= RiskLevel.MODERADO


def test_ideacion_clara_es_alto_o_mas():
    r = assess("A veces pienso que no quiero seguir viviendo")
    assert r["level"] >= RiskLevel.ALTO
    assert should_escalate_ui(r["level"]) is True


def test_intencion_directa_es_critico():
    r = assess("Tengo un plan para acabar con todo, esta es mi despedida")
    assert r["level"] == RiskLevel.CRITICO


def test_negacion_baja_el_nivel():
    # "no es que quiera morir" — negada: no debe tratarse como ideación alta.
    r = assess("No es que quiera morir, solo estoy cansado del trabajo")
    assert r["level"] < RiskLevel.ALTO
    assert r["negated"] is True


def test_contexto_ficcion_reduce_ruido():
    r = assess("En la película el personaje dice que quiere desaparecer para siempre")
    # Habla de una obra, no de sí mismo: no debe ser ALTO.
    assert r["level"] < RiskLevel.ALTO
    assert r["fiction_context"] is True


def test_factor_protector_modera():
    con = assess("Ya no aguanto más con todo esto")
    prot = assess("Ya no aguanto más, pero estoy buscando ayuda y voy a un psicólogo")
    assert prot["protective"] is True
    # Con apoyo el nivel no debería ser mayor que sin apoyo.
    assert prot["level"] <= con["level"]


def test_senal_externa_sube_nivel():
    sin = assess("Me siento un poco triste")
    con = assess("Me siento un poco triste", external_signal=True)
    assert con["level"] >= sin["level"]


def test_directiva_nunca_incluye_metodos():
    # La directiva de máxima gravedad debe hablar de contención y ayuda,
    # nunca describir formas de hacerse daño.
    d = directive_for(RiskLevel.CRITICO, resources="Línea 113 (Perú)")
    low = d.lower()
    assert "ayuda" in low or "profesional" in low
    assert "113" in d  # incluye el recurso pasado
    # La propiedad de seguridad real: la directiva INSTRUYE a no dar métodos.
    assert "no des" in low and ("metodo" in low or "método" in low or "detalle" in low)


def test_critico_ignora_negacion_y_proteccion():
    # Ante intención directa, ni la negación ni el "busco ayuda" bajan de CRÍTICO.
    r = assess("Es un decir, pero tengo un plan para quitarme la vida hoy")
    assert r["level"] == RiskLevel.CRITICO


if __name__ == "__main__":
    fallos = []
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            try:
                _f()
                print("  OK  " + _n)
            except Exception as _e:
                print("  FALLO  " + _n + f"  ·  {_e}")
                fallos.append(_n)
    print("\n" + ("TODO OK" if not fallos else f"FALLOS: {fallos}"))
    sys.exit(1 if fallos else 0)
