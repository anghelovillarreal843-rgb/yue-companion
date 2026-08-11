"""Pruebas de las tres correcciones: una sola cuenta, sin inglés raro, rápido."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core import groq_tuning, text_sanitizer
from core.ai_router import default_providers


# ---------- 1) UNA SOLA CUENTA DE GROQ ----------
def test_solo_usa_la_clave_principal():
    env = {
        "GROQ_API_KEY": "principal",
        "GROQ_API_KEY_2": "otra",
        "GROQ_API_KEY_3": "otra_mas",
        "GROQ_API_KEYS": "c,d",
    }
    fila = default_providers(getenv=lambda k, d="": env.get(k, d))
    assert [p.api_key for p in fila] == ["principal"], "debe quedarse con una sola cuenta"


def test_modo_antiguo_sigue_disponible():
    env = {
        "GROQ_UNA_SOLA_CUENTA": "false",
        "GROQ_API_KEY": "a",
        "GROQ_API_KEY_2": "b",
        "GROQ_API_KEYS": "c",
    }
    fila = default_providers(getenv=lambda k, d="": env.get(k, d))
    assert [p.api_key for p in fila] == ["a", "c", "b"]


def test_sin_clave_no_hay_proveedores():
    fila = default_providers(getenv=lambda k, d="": {}.get(k, d))
    assert fila == []


# ---------- 2) NADA DE INGLÉS RARO ----------
def test_quita_bloque_think():
    crudo = ("<think>The user greets me. I should answer in a tsundere tone, "
             "keep it short.</think>\nAh... eres tú. ¿Qué quieres ahora?")
    assert text_sanitizer.limpiar(crudo) == "Ah... eres tú. ¿Qué quieres ahora?"


def test_quita_think_sin_cerrar():
    crudo = "Bueno, ya voy.\n<think>Okay so the user wants me to"
    assert text_sanitizer.limpiar(crudo) == "Bueno, ya voy."


def test_todo_razonamiento_devuelve_vacio():
    crudo = "<think>The user is asking about the weather and I need to"
    assert text_sanitizer.limpiar(crudo) == ""


def test_cierre_huerfano():
    crudo = "I need to reply in Spanish now.</think> Claro que sí, tonto."
    assert text_sanitizer.limpiar(crudo) == "Claro que sí, tonto."


def test_canales_gpt_oss():
    crudo = ("<|channel|>analysis<|message|>The user said hi<|end|>"
             "<|channel|>final<|message|>Hola, ¿qué tal?")
    assert text_sanitizer.limpiar(crudo) == "Hola, ¿qué tal?"


def test_no_toca_una_respuesta_normal():
    bueno = "No es que me importe, pero deberías descansar un poco."
    assert text_sanitizer.limpiar(bueno) == bueno


def test_detecta_ingles_de_verdad():
    assert text_sanitizer.parece_ingles(
        "The user wants me to explain that, so I should probably answer with this.")


def test_no_confunde_espanol_con_ingles():
    assert not text_sanitizer.parece_ingles("Hazme un backup del proyecto, por favor.")
    assert not text_sanitizer.parece_ingles("Claro, ya te abro el navegador.")


# ---------- 3) RAPIDEZ ----------
def test_ajustes_de_velocidad():
    payload = {"model": "qwen/qwen3.6-27b", "messages": []}
    groq_tuning.aplicar_ajustes(payload)
    assert payload["reasoning_format"] == "hidden"
    assert payload["reasoning_effort"] == "none"
    assert payload["max_completion_tokens"] > 0


def test_retira_parametro_no_soportado():
    payload = {"model": "x", "reasoning_effort": "none", "reasoning_format": "hidden"}
    retirado = groq_tuning.soltar_parametro_no_soportado(
        payload, '{"error":{"message":"reasoning_effort is not supported for this model"}}')
    assert retirado == "reasoning_effort"
    assert "reasoning_effort" not in payload
    assert "reasoning_format" in payload, "solo se retira el parámetro señalado"


def test_sesion_se_reutiliza():
    assert groq_tuning.sesion() is groq_tuning.sesion()


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(list(globals().items())):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  OK   {nombre}")
            except AssertionError as exc:
                fallos += 1
                print(f"  FALLA {nombre}: {exc}")
    print("\nTodo correcto." if not fallos else f"\n{fallos} prueba(s) fallaron.")
    sys.exit(1 if fallos else 0)
