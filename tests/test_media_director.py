import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.media_director import MediaCompanionDirector


def test_texto_vacio_se_ignora():
    d = MediaCompanionDirector()
    d.remember_heard("   ")
    d.remember_heard("")
    assert d.recent_lyrics() == ""


def test_rolling_corta_lo_oido_y_limita_fragmentos():
    import time
    d = MediaCompanionDirector()
    ahora = time.time()
    d._buffer = [(ahora - 10.0 - i, f"fragmento {i}") for i in range(30)]
    d.remember_heard("fragmento 31")
    assert len(d._buffer) <= 25
    assert all(ahora - t <= 180.0 for (t, _) in d._buffer)


def test_recent_lyrics_respeta_max_age():
    d = MediaCompanionDirector()
    import time
    ahora = time.time()
    d._buffer = [(ahora - 1000.0, "viejo"), (ahora - 10.0, "reciente")]
    out = d.recent_lyrics(max_age=150.0)
    assert "reciente" in out
    assert "viejo" not in out


def test_recent_lyrics_reverso_lo_mas_nuevo_primero_y_limite():
    d = MediaCompanionDirector()
    import time
    ahora = time.time()
    d._buffer = [(ahora - 40.0, "uno"), (ahora - 5.0, "dos")]
    out = d.recent_lyrics(max_age=150.0)
    assert out == "uno … dos"
    corto = d.recent_lyrics(max_age=150.0, max_chars=5)
    assert corto == "dos"


def test_sin_letra_captada_devuelve_vacio():
    d = MediaCompanionDirector()
    assert d.recent_lyrics() == ""