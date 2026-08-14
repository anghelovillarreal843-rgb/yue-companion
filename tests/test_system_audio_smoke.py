"""Smoke de core/system_audio.py (PR 1, REFACTOR_SPEC §6).

Determinista: funciones puras con arrays sintéticos, sin PyAudio ni hardware.
Gate obligatorio antes de tocar el módulo (PR 5: MediaDirector).
"""
import numpy as np

from core.system_audio import (
    AudioObservation,
    Reaction,
    ReactorState,
    analyze_block,
    classify,
    decide_reaction,
)


def test_analyze_block_silencio_y_vacio():
    feats = analyze_block(np.zeros(2048, dtype=np.float32))
    assert feats["rms"] == 0.0
    assert feats["bass"] == 0.0
    assert set(feats) >= {"rms", "zcr", "centroid", "brightness", "bass", "flux"}

    vacio = analyze_block(np.array([], dtype=np.float32))
    assert vacio["rms"] == 0.0


def test_classify_silencio_por_rms_bajo():
    kind, energetic = classify(analyze_block(np.zeros(2048, dtype=np.float32)))
    assert kind == "silencio"
    assert energetic is False


def test_decide_reaction_no_lanza_con_estado_vacio():
    obs = AudioObservation(
        timestamp=0.0, rms=0.001, kind="silencio", energetic=False,
        tempo_bpm=0.0, brightness=0.0, bass=0.0, onset=False,
    )
    res = decide_reaction(obs, ReactorState(), now=100.0)
    assert res is None or isinstance(res, Reaction)


def test_decide_reaction_musica_fuerte_reacciona():
    obs = AudioObservation(
        timestamp=0.0, rms=0.5, kind="musica", energetic=True,
        tempo_bpm=120.0, brightness=0.3, bass=0.6, onset=False,
    )
    res = decide_reaction(obs, ReactorState(), now=100.0)
    assert isinstance(res, Reaction)
