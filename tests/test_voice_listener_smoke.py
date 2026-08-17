"""Smoke de core/listener.py::VoiceListener (PR 1, REFACTOR_SPEC §6).

Determinista: sin micrófono real (los casos de captura se marcan @pytest.mark.hw
y no corren por defecto). Gate obligatorio antes de tocar el módulo (PR 5:
VoiceDirector).
"""
import sys

import pytest

from core.listener import VoiceListener, _norm, list_microphones


def test_norm_pura():
    assert _norm("Oye YUE") == "oye yue"
    assert _norm("Abré Chrome") == "abre chrome"
    assert _norm("Hola!") == "hola"
    # no colapsa espacios múltiples (comportamiento actual del módulo)
    assert _norm("Hola, cómo estás") == "hola  como estas"


def test_list_microphones_degrada_sin_sr(monkeypatch):
    monkeypatch.setitem(sys.modules, "speech_recognition", None)
    assert list_microphones() == []


def test_wake_word_pura():
    v = VoiceListener()
    if not v._wake_words:
        pytest.skip("config sin MIC_WAKE_WORDS")
    assert v._has_wake_word("oye yue abre chrome") is True
    assert v._has_wake_word("cuéntame un chiste") is False
