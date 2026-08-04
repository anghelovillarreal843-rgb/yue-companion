"""Pruebas de detección de comandos, foco en preguntas sobre la PANTALLA.

Cubre el arreglo de percepción de pantalla v3: preguntas naturales que antes
NO disparaban vision_look (y caían al LLM, que inventaba "solo veo por la
cámara"), y la nota de respaldo screen_prompt_note() del prompt del sistema.

Se puede correr de dos formas:
    python -m pytest tests/test_commands.py
    python tests/test_commands.py            # suelto, sin pytest (runner de abajo)
"""
import sys
from pathlib import Path

# Sin esto, «python tests/test_commands.py» a secas falla con «No module named 'core'».
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import commands, vision_awareness


# ---------------------------------------------------------------------------
# vision_look: las preguntas nuevas SÍ deben matchear.
# ---------------------------------------------------------------------------
def test_cuantos_iconos_ves_matchea_vision_look():
    assert commands.match("cuántos iconos ves en mi pantalla")[0] == "vision_look"


def test_que_colores_hay_en_pantalla_matchea():
    assert commands.match("qué colores hay en pantalla")[0] == "vision_look"


def test_cuantas_ventanas_ves_abiertas_matchea():
    assert commands.match("cuántas ventanas ves abiertas")[0] == "vision_look"


def test_hay_algun_error_en_pantalla_matchea():
    assert commands.match("hay algún error en la pantalla")[0] == "vision_look"


def test_de_que_color_en_pantalla_matchea():
    assert commands.match("de qué color es la barra en pantalla")[0] == "vision_look"


def test_donde_esta_el_icono_matchea():
    assert commands.match("dónde está el icono en la pantalla")[0] == "vision_look"


def test_no_se_rompe_el_patron_rigido_previo():
    # La regla clásica (verbo exacto + referencia) debe seguir funcionando.
    assert commands.match("qué ves en mi pantalla")[0] == "vision_look"
    assert commands.match("mira la pantalla")[0] == "vision_look"
    assert commands.match("lee este documento")[0] == "vision_look"


# ---------------------------------------------------------------------------
# vision_look: frases de control (sin referencia visual) NO deben matchear.
# ---------------------------------------------------------------------------
def test_pregunta_de_musica_no_matchea_vision():
    assert commands.match("qué opinas de esta canción")[0] != "vision_look"
    assert commands.match("qué te pareció la canción")[0] != "vision_look"


def test_pregunta_de_camara_no_cae_en_vision_look():
    # "qué ves por la cámara" es de cámara (camera_status), no de pantalla.
    accion = commands.match("qué ves por la cámara")[0]
    assert accion != "vision_look"


def test_pregunta_de_actividad_no_matchea_vision():
    assert commands.match("qué hemos hecho hoy")[0] != "vision_look"


def test_ves_solo_sin_referencia_no_dispara_las_alts_nuevas():
    # "cuántos años tienes" contiene "cuántos" pero NINGUNA referencia visual:
    # las alternativas nuevas exigen pantalla/ventana/icono/... obligatoria.
    assert commands.match("cuántos años tienes")[0] != "vision_look"
    # "cuántas personas hay en la sala" tampoco (sin referencia a pantalla).
    assert commands.match("cuántas personas hay en la sala")[0] != "vision_look"


# ---------------------------------------------------------------------------
# screen_prompt_note(): respaldo del prompt del sistema.
# ---------------------------------------------------------------------------
def test_screen_prompt_note_texto_cuando_captura_disponible(monkeypatch=None):
    # Forzamos "disponible" para probar la rama afirmativa de forma determinista.
    orig = vision_awareness._screen_capture_available
    vision_awareness._screen_capture_available = lambda: True
    try:
        nota = vision_awareness.screen_prompt_note()
        assert isinstance(nota, str) and nota.strip()
        assert "pantalla" in nota.lower()
    finally:
        vision_awareness._screen_capture_available = orig


def test_screen_prompt_note_vacio_cuando_no_disponible():
    # Sin captura disponible, devuelve cadena vacía (no afirma algo falso).
    orig = vision_awareness._screen_capture_available
    vision_awareness._screen_capture_available = lambda: False
    try:
        assert vision_awareness.screen_prompt_note() == ""
    finally:
        vision_awareness._screen_capture_available = orig


def test_screen_prompt_note_siempre_es_str():
    # Sea cual sea el entorno real, nunca lanza y siempre devuelve str.
    assert isinstance(vision_awareness.screen_prompt_note(), str)


if __name__ == "__main__":
    # Runner mínimo para correr sin pytest: ejecuta cada test_* y resume.
    fallos = []
    for _nombre, _fn in sorted(globals().items()):
        if _nombre.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("  OK  " + _nombre)
            except Exception as _exc:
                print("  FALLO  " + _nombre + f"  ·  {_exc}")
                fallos.append(_nombre)
    print("\n" + ("TODO OK" if not fallos else f"FALLOS: {fallos}"))
    sys.exit(1 if fallos else 0)
