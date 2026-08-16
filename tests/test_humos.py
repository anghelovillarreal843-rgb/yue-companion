"""Test de humo: arranque REAL de `Controller()` completo.

Detecta regresiones de cableado que la suite de módulos aislados no ve:
atributos del maestro que otros pasos movieron al ctx (self.camera), orden
de conexión de señales (state_tick conectado antes de crear el orquestador),
canales prometidos en docstrings pero nunca publicados (ctx.system_context).

Se parachea SOLO el arranque físico de hardware (webcam, micrófono, audio);
todo el wiring, la creación de directores y el `__init__` del maestro se
ejecutan de verdad.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtWidgets import QApplication


@pytest.fixture(scope="module")
def controller():
    import main as m
    from core.camera_observer import CameraObserver

    # Solo el disparo físico del hardware: cero dispositivo, cero tarjeta.
    CameraObserver.start = lambda self: None
    CameraObserver.stop = lambda self: None
    m.VoiceListener.start = lambda self: None
    m.SystemAudioReactor.start = lambda self: None

    app = QApplication.instance() or QApplication([])
    ctrl = None
    try:
        ctrl = m.Controller()
        yield ctrl
    finally:
        if ctrl is not None:
            ctrl.shutdown()
        app.processEvents()


def test_humano_controller_levanta_sin_attribute_error(controller):
    assert controller is not None
    assert controller.controller_ctx is not None
    assert controller.emotion_orchestrator is not None
    assert controller.vision_director is not None


def test_humano_camara_vive_en_el_ctx_y_es_la_del_director(controller):
    """self.camera del maestro ya no existe; el maestro usa el registro del ctx."""
    assert not hasattr(controller, "camera")
    camara = controller.controller_ctx.camera
    assert camara is not None
    assert camara is controller.vision_director._camera
    for metodo in ("start", "stop", "set_landmark_consumer",
                   "context_for_ai", "describe"):
        assert callable(getattr(camara, metodo, None)), f"falta {metodo}"


def test_humano_system_context_publicado_y_consistente(controller):
    """Un canal que los docstrings prometen y que el paso 7 perdió al cablear."""
    canal = controller.controller_ctx.system_context
    assert callable(canal), "ctx.system_context no está publicado"
    assert canal() == controller.emotion_orchestrator.refresh_bond()


def test_humano_avatar_emotion_publicado_por_el_orquestador(controller):
    canal = controller.controller_ctx.avatar_emotion
    assert callable(canal)
    assert getattr(canal, "__self__", None) is controller.emotion_orchestrator


def test_humano_latido_conectado_y_sin_estallar(controller):
    """El timer del maestro dispara el latido del orquestador cada tick."""
    assert controller._state_timer.isActive()
    controller.emotion_orchestrator.state_tick()  # no debe lanzar


def test_humano_glance_llega_al_director(controller):
    """El puente del maestro (ctx.glance -> _glance -> director) sigue vivo."""
    puente = controller.controller_ctx.glance
    assert callable(puente)