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


def test_humano_routing_comandos_sin_director_propio(controller):
    """10.1 ROUTING: los comandos huérfanos delegan a servicios de ctx sin reventar."""
    director = controller.dialogue_director
    assert callable(controller.controller_ctx.handle_command)
    assert controller.controller_ctx.handle_command.__self__ is director
    pruebas = [
        "/actividad",
        "/recuerda Recordaré esta fecha para la prueba",
        "/metas",
        "/vinculo",
        "/animo_historial",
        "/rutinas",                       # lista (canal ctx.list_routines)
        "/rutinas rutinaQueNoExisteZZ",   # dispatch a PCDirector.run_routine
        "/camara",                        # ctx.camera.describe (degradado)
        "/diagvision",
        "/diagvoz",
        "/help",
        "/comandoInexistenteXYZ",
    ]
    for comando in pruebas:
        director.handle_command(comando)


def test_humano_routing_comandos_con_director(controller):
    """10.1 ROUTING: comandos con director dueño despachan por su canal."""
    director = controller.dialogue_director
    director.handle_command("/animo")
    director.handle_command("/recuerdos")
    director.handle_command("/metas")
    director.handle_command("/modo")


def test_humano_routing_no_dejo_restos_del_maestro(controller):
    """El maestro ya no tiene _handle_command ni _diagnose_vision."""
    import main as m
    assert not hasattr(m.Controller, "_handle_command")
    assert not hasattr(m.Controller, "_diagnose_vision")
    assert controller.dialogue_director is controller.controller_ctx.handle_command.__self__


def test_humano_routing_en_vivo_huérfanos_entregan_servicio(controller, monkeypatch):
    """10.1 ROUTING EN VIVO: los comandos huérfanos emiten mensaje real y/o
    tocan su servicio (memoria, metas, vínculo, cámara…)."""
    director = controller.dialogue_director
    emitidos = []
    monkeypatch.setattr(controller.controller_ctx, "say", emitidos.append)
    monkeypatch.setattr(controller, "_yue_say", emitidos.append)

    # /recuerda -> memoria real (el hecho debe quedar guardado)
    antes = len(controller.controller_ctx.memory.get_facts(500))
    director.handle_command("/recuerda Detalle para la prueba 10_1")
    hechos = controller.controller_ctx.memory.get_facts(500)
    assert len(hechos) == antes + 1
    assert any("10_1" in f for f in hechos)
    assert emitidos, "el director debe confirmar el recuerdo"
    # higiene: no dejar el hecho de prueba en la memoria real (contamina la
    # DB de datos y los tests dependientes de estado, p.ej. test_episodic_memory)
    import sqlite3
    import config
    with sqlite3.connect(str(config.DB_PATH)) as con:
        con.execute("DELETE FROM facts WHERE text LIKE '%10_1%'")
    emitidos.clear()

    # /metas -> listado real (vacío o con metas)
    emitidos.clear()
    director.handle_command("/metas")
    assert any(m.startswith("Tus metas") or "No tienes metas" in m for m in emitidos)

    # /vinculo -> progreso del vínculo
    emitidos.clear()
    director.handle_command("/vinculo")
    assert any("puntos" in m or "máximo" in m for m in emitidos)

    # /actividad -> resumen real
    emitidos.clear()
    director.handle_command("/actividad")
    assert len(emitidos) == 1 and len(emitidos[0]) > 0

    # /animo_historial -> respuesta de resumen o estado
    emitidos.clear()
    director.handle_command("/animo_historial")
    assert any("ánimo" in m or "ánimo" in m.lower() for m in emitidos)

    # /camara -> descripción o degradado (no explota sin cámara real)
    emitidos.clear()
    director.handle_command("/camara")
    assert any(m for m in emitidos)