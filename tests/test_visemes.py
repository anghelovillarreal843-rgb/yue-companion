"""Pruebas del lip-sync por visemas (FASE 4).

    python -m pytest tests/test_visemes.py
    python tests/test_visemes.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.voice_ext import (
    VISEMES, text_to_visemes, visemes_from_envelope, merge_shape_with_envelope,
)


def test_vocales_dan_visema_correcto():
    seq = text_to_visemes("aeiou")
    formas = [v["viseme"] for v in seq]
    assert formas == ["AA", "E", "I", "O", "U"]


def test_tildes_no_rompen_la_forma():
    # "canción" -> las tildes se ignoran para la forma.
    seq = text_to_visemes("canción")
    formas = [v["viseme"] for v in seq]
    assert "O" in formas and "AA" in formas
    assert all(f in VISEMES for f in formas)


def test_bilabiales_cierran_boca():
    seq = text_to_visemes("mama papa")
    formas = [v["viseme"] for v in seq]
    assert formas.count("MBP") >= 4  # m, m, p, p


def test_labiodentales_fv():
    seq = text_to_visemes("favor")
    formas = [v["viseme"] for v in seq]
    assert "FV" in formas


def test_h_muda_se_ignora():
    con_h = [v["viseme"] for v in text_to_visemes("hola")]
    sin_h = [v["viseme"] for v in text_to_visemes("ola")]
    assert con_h == sin_h  # la h no aporta forma


def test_digrafo_qu_es_una_consonante():
    # "queso" -> q(u) como /k/ (CONS), luego e, s, o. La u es muda.
    formas = [v["viseme"] for v in text_to_visemes("queso")]
    assert formas == ["CONS", "E", "CONS", "O"]


def test_espacios_insertan_reposo():
    seq = text_to_visemes("si no")
    formas = [v["viseme"] for v in seq]
    assert "REST" in formas  # hay una pausa entre palabras


def test_target_duration_escala_la_suma():
    seq = text_to_visemes("hola mundo", target_duration=2.0)
    total = sum(v["dur"] for v in seq)
    # Se redondea cada duración a 4 decimales, así que la suma cae muy cerca de 2 s.
    assert abs(total - 2.0) < 0.01


def test_texto_vacio_da_lista_vacia():
    assert text_to_visemes("") == []
    assert text_to_visemes("   ") == []


def test_envelope_abre_y_cierra():
    # Volumen: bajo, alto, alto, bajo -> reposo, abierto, abierto, reposo.
    env = [0.0, 1.0, 0.9, 0.0]
    frames = visemes_from_envelope(env, fps=10.0, threshold=0.1)
    formas = [f["viseme"] for f in frames]
    assert formas[0] == "REST"
    assert formas[1] == "AA"
    assert formas[-1] == "REST"


def test_merge_alinea_a_duracion_del_audio():
    env = [0.0, 0.8, 0.9, 0.7, 0.0]  # 5 frames
    frames = merge_shape_with_envelope("hola", env, fps=10.0)
    assert len(frames) == len(env)
    # El último frame (volumen 0) debe cerrar la boca.
    assert frames[-1]["viseme"] == "REST"
    # En los frames con volumen alto, la apertura es > 0.
    assert any(f["openness"] > 0 for f in frames)


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
