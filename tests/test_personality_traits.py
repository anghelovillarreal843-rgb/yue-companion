"""Pruebas de rasgos de personalidad ajustables (FASE 5).

    python -m pytest tests/test_personality_traits.py
    python tests/test_personality_traits.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.personality_traits import TraitProfile, AJUSTES_RAPIDOS


def test_defaults_cargan():
    tp = TraitProfile()
    assert 0.0 <= tp.get("sarcasmo") <= 1.0
    assert tp.get("carino") > 0.5  # YUE es cariñosa por debajo del pique


def test_set_y_clamp():
    tp = TraitProfile()
    assert tp.set("sarcasmo", 2.0) == 1.0     # se recorta a 1
    assert tp.set("carino", -1.0) == 0.0      # se recorta a 0


def test_nudge_sube_y_baja():
    tp = TraitProfile()
    base = tp.get("humor")
    subido = tp.nudge("humor", 0.2)
    assert subido >= base or subido == 1.0


def test_rasgo_desconocido_falla():
    tp = TraitProfile()
    try:
        tp.set("teletransporte", 0.5)
        assert False, "debió lanzar KeyError"
    except KeyError:
        pass


def test_fragmento_menciona_seguridad():
    tp = TraitProfile()
    frag = tp.to_prompt_fragment()
    # La regla de oro debe estar SIEMPRE en el fragmento.
    assert "crisis" in frag.lower() or "malestar" in frag.lower()
    assert "contención" in frag.lower() or "calidez" in frag.lower()


def test_fragmento_refleja_valores():
    tp = TraitProfile()
    tp.set("sarcasmo", 0.0)
    tp.set("carino", 1.0)
    frag = tp.to_prompt_fragment().lower()
    assert "casi sin sarcasmo" in frag or "sé directa" in frag


def test_persistencia_json():
    d = tempfile.mkdtemp(prefix="yue_traits_")
    path = str(Path(d) / "traits.json")
    tp = TraitProfile(path)
    tp.set("formalidad", 0.9)
    # Nueva instancia lee el mismo archivo.
    tp2 = TraitProfile(path)
    assert tp2.get("formalidad") == 0.9


def test_reset_vuelve_a_defaults():
    tp = TraitProfile()
    tp.set("sarcasmo", 0.1)
    tp.reset()
    assert tp.get("sarcasmo") == 0.70


def test_panel_state_completo():
    tp = TraitProfile()
    panel = tp.panel_state()
    claves = {p["clave"] for p in panel}
    assert {"sarcasmo", "carino", "humor", "iniciativa"} <= claves
    assert all("etiqueta" in p and "valor" in p for p in panel)


def test_ajustes_rapidos_apuntan_a_rasgos_validos():
    tp = TraitProfile()
    for _frase, (rasgo, delta) in AJUSTES_RAPIDOS.items():
        # Cada atajo debe referirse a un rasgo real y aplicarse sin error.
        antes = tp.get(rasgo)
        despues = tp.nudge(rasgo, delta)
        assert 0.0 <= despues <= 1.0
        assert despues != antes or despues in (0.0, 1.0)


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
