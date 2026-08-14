"""Smoke de core/camera_observer.py (PR 1, REFACTOR_SPEC §6).

Determinista: sin cámara, sin MediaPipe. Gate obligatorio antes de tocar el
módulo (PR 5: VisionDirector se hace cargo del ciclo de vida).
"""
import sys

from core.camera_observer import (
    CameraObservation,
    CameraObserver,
    _score_map,
    summarize_blendshapes,
    summarize_pose,
)


def test_helpers_neutrales_sin_rostros():
    assert _score_map({}) == {}
    assert summarize_blendshapes({}) == ()
    assert summarize_pose([]) == ()


def test_observation_neutra_por_defecto():
    obs = CameraObservation(timestamp=0.0, camera_index=-1)
    assert obs.faces == 0
    assert obs.people == 0


def test_degrada_sin_opencv(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)
    obs = CameraObserver()
    obs._run()  # import cv2 falla -> estado "Falta opencv-python" y retorna
    assert obs._active is False
    assert obs._latest is None
