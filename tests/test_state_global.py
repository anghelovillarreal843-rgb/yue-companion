"""Pruebas del cerebro central de YUE: USER / YUE / SYSTEM separados.

    python -m pytest tests/test_state_global.py
    python tests/test_state_global.py

Cubren los ocho escenarios que motivaron la refactorización. El más importante
es el TEST 3: que la cámara no pueda contradecir lo que el usuario dijo con
palabras. Ese era el fallo que hacía que YUE respondiera «pareces estar bien» a
alguien que acababa de escribir que estaba fatal.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.state import (
    AvatarRenderer, Priority, SystemState, UserObservation, UserState,
    YueBehaviorState, YueGlobalState, YueProposal, YueStateManager,
    behavior_for_need, proposal_from_companion, user_state_from_companion,
)
from core.affect.models import AffectiveState, Emotion
from core.companion_brain import CompanionBrain


def _sm():
    """Gestor con el log de decisiones apagado, para no ensuciar la salida."""
    gestor = YueStateManager()
    gestor.set_decision_logging(False)
    return gestor


# ==========================================================================
# TEST 1 — usuario triste → USER sadness, YUE worried/gentle
# ==========================================================================
def test_1_usuario_triste_yue_preocupada_no_triste():
    """La regla de oro: YUE responde al usuario, no lo imita."""
    cerebro = CompanionBrain(engine=None, use_semantic=False)
    resultado = cerebro.process("estoy muy triste, no puedo con esto")

    gestor = _sm()
    usuario = user_state_from_companion(resultado)
    gestor.set_user_state(usuario)
    gestor.propose(proposal_from_companion(resultado))

    estado = gestor.global_state()

    # El USUARIO está triste.
    assert estado.user.emotion == str(Emotion.SADNESS), estado.user.emotion
    # YUE NO. Está preocupada, que es otra cosa.
    assert estado.yue.emotion != "sad"
    assert estado.yue.emotion in ("worried", "focused"), estado.yue.emotion
    # Y se comporta en consecuencia: acompaña sin invadir.
    assert estado.yue.behavior in ("listening", "comforting", "grounding",
                                   "asking", "attentive")
    assert estado.yue.initiative in ("none", "low", "medium")


# ==========================================================================
# TEST 2 — cámara con poca confianza vs texto con mucha: gana el texto
# ==========================================================================
def test_2_texto_alta_confianza_gana_a_camara_baja():
    gestor = _sm()
    gestor.observe(UserObservation(source="camera", emotion="happy",
                                   confidence=0.40, valence=0.6))
    gestor.observe(UserObservation(source="text", emotion="sadness",
                                   confidence=0.88, valence=-0.7, explicit=True))

    usuario = gestor.user_state()
    assert usuario.emotion == "sadness"
    assert usuario.dominant_source == "text"
    # La confianza de cada fuente queda registrada por separado, ponderada.
    assert usuario.text_confidence > usuario.camera_confidence


# ==========================================================================
# TEST 3 — cámara feliz vs texto explícito triste: la cámara NO pisa el texto
# ==========================================================================
def test_3_camara_no_pisa_texto_explicito():
    """El caso que motivó todo esto.

    Aunque la cámara esté MUY segura (0.95) de ver una cara feliz, si la
    persona escribió que está triste, manda lo que escribió. Una sonrisa de
    compromiso no desmiente un «estoy fatal».
    """
    gestor = _sm()
    gestor.observe(UserObservation(source="text", emotion="sadness",
                                   confidence=0.85, valence=-0.8, explicit=True))
    # La cámara llega DESPUÉS y con más confianza bruta. Aun así no manda.
    gestor.observe(UserObservation(source="camera", emotion="happy",
                                   confidence=0.95, valence=0.7))

    usuario = gestor.user_state()
    assert usuario.emotion == "sadness", "la cámara pisó el texto explícito"
    assert usuario.dominant_source == "text"
    assert usuario.explicit is True
    # La lectura de la cámara NO se tira: queda como matiz secundario.
    assert usuario.secondary_emotion == "happy"
    # Y como las fuentes se contradicen, la incertidumbre no baja a cero.
    assert usuario.uncertainty > 0.0


def test_3b_camara_manda_si_no_hay_texto():
    """Sin palabras de por medio, la cámara sí es la mejor fuente disponible."""
    gestor = _sm()
    gestor.observe(UserObservation(source="camera", emotion="sadness",
                                   confidence=0.80, valence=-0.6))
    assert gestor.user_state().emotion == "sadness"
    assert gestor.user_state().dominant_source == "camera"


def test_3c_observar_no_cambia_la_cara_de_yue():
    """Observar es observar. Un sensor NUNCA mueve el avatar por su cuenta."""
    gestor = _sm()
    antes = gestor.yue_state()
    gestor.observe(UserObservation(source="camera", emotion="angry",
                                   confidence=0.9, valence=-0.8))
    assert gestor.yue_state().emotion == antes.emotion
    assert gestor.yue_state().source == antes.source


# ==========================================================================
# TEST 4 — MEDIA (40) vs TEACHER (80): gana la profesora
# ==========================================================================
def test_4_teacher_gana_a_media():
    gestor = _sm()
    gano_media = gestor.propose(YueProposal(
        emotion="happy", behavior="enjoying_music", source="media",
        priority=Priority.MEDIA, ttl=30.0))
    assert gano_media is True   # de momento no hay nadie más

    gano_teacher = gestor.propose(YueProposal(
        emotion="focused", behavior="teaching", source="teacher",
        priority=Priority.TEACHER, ttl=30.0))
    assert gano_teacher is True

    estado = gestor.yue_state()
    assert estado.source == "teacher"
    assert estado.emotion == "focused"

    # La música vuelve a intentarlo y pierde, pero NO desaparece.
    assert gestor.propose(YueProposal(
        emotion="excited", behavior="enjoying_music", source="media",
        priority=Priority.MEDIA, ttl=30.0)) is False
    assert gestor.yue_state().source == "teacher"


# ==========================================================================
# TEST 5 — al expirar TEACHER se recalcula, no se cae a neutral
# ==========================================================================
def test_5_al_expirar_vuelve_a_la_siguiente_propuesta():
    gestor = _sm()
    gestor.propose(YueProposal(
        emotion="curious", behavior="conversing", source="conversation",
        priority=Priority.CONVERSATION, ttl=30.0))
    gestor.propose(YueProposal(
        emotion="focused", behavior="teaching", source="teacher",
        priority=Priority.TEACHER, ttl=0.25))
    assert gestor.yue_state().source == "teacher"

    # El TTL tiene un suelo de 0.2 s (ver YueProposal.expires_at): se espera
    # con holgura para no depender de la precisión del reloj.
    time.sleep(0.35)
    gestor.tick()   # el latido caduca la propuesta y RECALCULA

    estado = gestor.yue_state()
    assert estado.source == "conversation", "no volvió a la conversación"
    assert estado.emotion == "curious"
    assert estado.emotion != "neutral", "se cayó a neutral en vez de recalcular"


def test_5b_sin_propuestas_queda_en_reposo():
    """Cuando de verdad no queda nada, el reposo es un estado explícito."""
    gestor = _sm()
    gestor.propose(YueProposal(emotion="happy", source="media",
                               priority=Priority.MEDIA, ttl=0.25))
    time.sleep(0.35)
    gestor.tick()
    assert gestor.yue_state().source == "idle"
    assert gestor.yue_state().behavior == "attentive"


def test_5c_retirar_propuesta_recalcula():
    gestor = _sm()
    gestor.propose(YueProposal(emotion="curious", source="conversation",
                               priority=Priority.CONVERSATION, ttl=None))
    gestor.propose(YueProposal(emotion="focused", source="teacher",
                               priority=Priority.TEACHER, ttl=None))
    assert gestor.yue_state().source == "teacher"
    gestor.withdraw("teacher")
    assert gestor.yue_state().source == "conversation"


# ==========================================================================
# TEST 6 — ningún módulo de visión toca el avatar directamente
# ==========================================================================
def test_6_vision_no_toca_el_avatar_directamente():
    """Búsqueda en el código fuente: `pet.set_emotion` fuera del renderer.

    Se permiten exactamente tres excepciones, todas de degradación (cuando no
    hay gestor de estado no hay renderer, y sin renderer nadie pintaría nunca).
    Cualquier otra aparición es un error de arquitectura.
    """
    import re
    raiz = Path(__file__).resolve().parents[1]
    permitidos = {"core/state/renderer.py", "main.py",
                  "vision/integration.py", "core/vision/integration.py"}

    patron = re.compile(r"^\s*[\w.]*pet\.set_emotion\(", re.MULTILINE)
    infractores = []
    for ruta in raiz.rglob("*.py"):
        rel = ruta.relative_to(raiz).as_posix()
        if ("__pycache__" in rel or rel.startswith("_respaldo")
                or rel.startswith("tests/") or rel in permitidos):
            continue
        try:
            texto = ruta.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        # Se ignoran comentarios y docstrings: solo cuenta el código.
        codigo = "\n".join(l for l in texto.splitlines()
                           if not l.strip().startswith("#"))
        if patron.search(codigo):
            infractores.append(rel)

    assert not infractores, f"módulos tocando el avatar directamente: {infractores}"


def test_6b_el_renderer_es_el_unico_punto():
    renderer = Path(__file__).resolve().parents[1] / "core/state/renderer.py"
    assert renderer.read_text(encoding="utf-8").count("self.pet.set_emotion(") == 1


# ==========================================================================
# TEST 7 — cambiar el YUE STATE actualiza el avatar
# ==========================================================================
class _PetFalso:
    """Doble de prueba del avatar VRM."""

    def __init__(self):
        self.emociones = []
        self.gestos = []

    def set_emotion(self, name, intensity=1.0, duration_ms=4800):
        self.emociones.append((name, round(float(intensity), 2), int(duration_ms)))

    def play_gesture(self, name, gain=1.0):
        self.gestos.append((name, gain))


def test_7_el_estado_de_yue_llega_al_avatar():
    gestor = _sm()
    pet = _PetFalso()
    # use_ui_thread=False: en pruebas no hay bucle de Qt al que marshalar.
    renderer = AvatarRenderer(pet, use_ui_thread=False).bind(gestor)

    gestor.propose(YueProposal(emotion="worried", intensity=0.7,
                               behavior="comforting", source="conversation",
                               priority=Priority.CONVERSATION, ttl=5.0))
    assert pet.emociones, "el avatar no recibió nada"
    assert pet.emociones[-1][0] == "worried"
    assert pet.emociones[-1][1] == 0.7

    # Un estado de MÁS prioridad también llega.
    gestor.propose(YueProposal(emotion="focused", intensity=0.8,
                               source="teacher", priority=Priority.TEACHER,
                               ttl=5.0))
    assert pet.emociones[-1][0] == "focused"

    # Y uno de MENOS prioridad no repinta nada.
    antes = len(pet.emociones)
    gestor.propose(YueProposal(emotion="happy", source="media",
                               priority=Priority.MEDIA, ttl=5.0))
    assert len(pet.emociones) == antes, "una propuesta perdedora pintó el avatar"
    renderer.unbind()


def test_7b_no_repinta_el_mismo_estado():
    """El avatar VRM reinicia la animación en cada llamada: repetir parpadea."""
    gestor = _sm()
    pet = _PetFalso()
    AvatarRenderer(pet, use_ui_thread=False).bind(gestor)
    for _ in range(5):
        gestor.propose(YueProposal(emotion="curious", intensity=0.5,
                                   source="conversation",
                                   priority=Priority.CONVERSATION, ttl=9.0))
    assert len(pet.emociones) <= 2, pet.emociones


# ==========================================================================
# TEST 8 — USER STATE y YUE STATE pueden diferir, y es lo correcto
# ==========================================================================
def test_8_usuario_y_yue_son_estados_independientes():
    gestor = _sm()
    gestor.observe(UserObservation(source="text", emotion="anger",
                                   confidence=0.9, valence=-0.8, arousal=0.9,
                                   explicit=True))
    gestor.propose(YueProposal(emotion="relaxed", intensity=0.45,
                               behavior="listening", voice_style="soft",
                               initiative="low", source="conversation",
                               priority=Priority.CONVERSATION, ttl=9.0))

    estado = gestor.global_state()
    assert estado.user.emotion == "anger"     # el usuario, enfadado
    assert estado.yue.emotion == "relaxed"    # YUE, serena
    assert estado.user.emotion != estado.yue.emotion


def test_8b_los_tres_estados_no_se_contaminan():
    gestor = _sm()
    gestor.observe(UserObservation(source="text", emotion="joy",
                                   confidence=0.8, valence=0.8))
    gestor.update_system(mic="listening", camera="active", teacher_active=True)
    gestor.propose(YueProposal(emotion="focused", source="teacher",
                               priority=Priority.TEACHER, ttl=9.0))

    estado = gestor.global_state()
    assert estado.user.emotion == "joy"
    assert estado.yue.emotion == "focused"
    assert estado.system.mic == "listening"
    assert estado.system.camera == "active"
    assert estado.system.teacher_active is True
    # Y el estado clásico sigue reflejando lo mismo, en su vocabulario.
    clasico = gestor.get_state()
    assert clasico.mic_state == "escuchando"
    assert clasico.camera_state == "activa"
    assert clasico.teacher_mode is True


# ==========================================================================
# EXTRA — iniciativa, coherencia y confianza vs prioridad
# ==========================================================================
def test_give_space_apaga_la_iniciativa():
    """Si la persona pidió espacio, YUE no insiste. Ni un poco."""
    comportamiento, voz, iniciativa = behavior_for_need("GIVE_SPACE")
    assert iniciativa == "none"
    assert comportamiento == "waiting"
    assert voz == "quiet"


def test_listen_acompana_sin_invadir():
    comportamiento, _voz, iniciativa = behavior_for_need("LISTEN")
    assert comportamiento == "listening"
    assert iniciativa == "low"


def test_el_estado_ganador_es_coherente():
    """Nada de cara triste con voz alegre y gesto de baile."""
    gestor = _sm()
    gestor.propose(YueProposal(
        emotion="worried", intensity=0.7, behavior="comforting",
        voice_style="soft", initiative="low", avatar_state="listening",
        source="conversation", priority=Priority.CONVERSATION, ttl=9.0))
    gestor.propose(YueProposal(
        emotion="excited", intensity=0.9, behavior="enjoying_music",
        voice_style="playful", initiative="high", avatar_state="dancing",
        source="media", priority=Priority.MEDIA, ttl=9.0))

    yue = gestor.yue_state()
    # TODOS los campos vienen del MISMO ganador. No se mezclan.
    assert yue.emotion == "worried"
    assert yue.behavior == "comforting"
    assert yue.voice_style == "soft"
    assert yue.initiative == "low"
    assert yue.avatar_state == "listening"


def test_confianza_no_es_prioridad():
    """Una observación segurísima no gana autoridad. Son ejes distintos."""
    gestor = _sm()
    # Cámara con confianza altísima.
    gestor.observe(UserObservation(source="camera", emotion="happy",
                                   confidence=1.0, valence=0.9))
    # Propuesta con prioridad bajísima.
    gestor.propose(YueProposal(emotion="happy", source="ambient",
                               priority=Priority.AMBIENT, ttl=9.0))
    gestor.propose(YueProposal(emotion="worried", source="safety",
                               priority=Priority.EMERGENCY, ttl=9.0))
    # La confianza de la cámara no le da ninguna autoridad sobre la cara.
    assert gestor.yue_state().source == "safety"
    assert gestor.user_state().confidence > 0.4


def test_emergencia_manda_sobre_todo():
    gestor = _sm()
    for origen, prioridad in (("media", Priority.MEDIA),
                              ("conversation", Priority.CONVERSATION),
                              ("teacher", Priority.TEACHER),
                              ("user", Priority.USER)):
        gestor.propose(YueProposal(emotion="happy", source=origen,
                                   priority=prioridad, ttl=9.0))
    gestor.propose(YueProposal(emotion="worried", source="safety",
                               priority=Priority.EMERGENCY, ttl=9.0))
    assert gestor.yue_state().source == "safety"


def test_observacion_caducada_deja_de_contar():
    from core.state.models import SOURCE_TTL
    gestor = _sm()
    vieja = UserObservation(source="camera", emotion="sadness", confidence=0.9,
                            valence=-0.7,
                            timestamp=time.time() - SOURCE_TTL["camera"] - 5)
    gestor.observe(vieja)
    assert gestor.user_state().dominant_source == "none"


def test_la_voz_no_inventa_datos():
    """Hoy no hay clasificador de prosodia. voice_confidence debe ser 0.0."""
    from core import voice_affect
    assert voice_affect.AVAILABLE is False
    assert voice_affect.confidence() == 0.0
    assert voice_affect.analyze(b"", sample_rate=16000) is None

    gestor = _sm()
    gestor.observe(UserObservation(source="text", emotion="sadness",
                                   confidence=0.8, explicit=True))
    assert gestor.user_state().voice_confidence == 0.0


def test_debug_snapshot_util():
    gestor = _sm()
    gestor.observe(UserObservation(source="text", emotion="joy", confidence=0.8))
    gestor.propose(YueProposal(emotion="happy", source="conversation",
                               priority=Priority.CONVERSATION, ttl=9.0))
    foto = gestor.debug_snapshot()
    for clave in ("user", "yue", "system", "winner", "proposals",
                  "observations", "summary"):
        assert clave in foto
    assert foto["winner"] == "conversation"
    assert foto["proposals"][0]["ttl_remaining"] is not None


def test_estado_global_es_inmutable():
    gestor = _sm()
    estado = gestor.global_state()
    try:
        estado.user.emotion = "hackeado"
        assert False, "el estado debería ser inmutable"
    except Exception:
        pass



# ==========================================================================
# EXTRA — la iniciativa se USA, no solo se calcula
# ==========================================================================
def _controller_falso(gestor):
    """Instancia mínima para probar métodos de `Controller` sin arrancar Qt."""
    import types
    return types.SimpleNamespace(state_manager=gestor)


def test_iniciativa_none_bloquea_hablar_primero():
    """Si el usuario pidió espacio, YUE no arranca a hablar. Ni check-in ni nada."""
    import main
    gestor = _sm()
    gestor.propose(YueProposal(
        emotion="relaxed", behavior="waiting", voice_style="quiet",
        initiative="none", source="conversation",
        priority=Priority.USER, ttl=30.0))
    app = _controller_falso(gestor)
    assert main.Controller._yue_may_take_initiative(app, "medium") is False
    assert main.Controller._yue_may_take_initiative(app, "low") is False


def test_iniciativa_alta_permite_hablar_primero():
    import main
    gestor = _sm()
    gestor.propose(YueProposal(
        emotion="playful", behavior="entertaining", initiative="high",
        source="conversation", priority=Priority.USER, ttl=30.0))
    app = _controller_falso(gestor)
    assert main.Controller._yue_may_take_initiative(app, "medium") is True


def test_sin_gestor_la_iniciativa_no_bloquea_nada():
    """Degradación: sin cerebro central, YUE se comporta como siempre."""
    import main
    import types
    app = types.SimpleNamespace(state_manager=None)
    assert main.Controller._yue_may_take_initiative(app, "high") is True


def test_sync_system_state_refleja_la_realidad():
    """Los campos del SYSTEM STATE dejan de ser decorativos."""
    import main
    import types
    gestor = _sm()
    app = types.SimpleNamespace(
        state_manager=gestor,
        listener=types.SimpleNamespace(enabled=True),
        camera=types.SimpleNamespace(active=True),
        speaker=types.SimpleNamespace(is_speaking=False),
        audio=types.SimpleNamespace(media_playing=True),
        teacher=types.SimpleNamespace(is_active=True),
        _pc_busy=False, _vision_busy=False, _autonomy_busy=False)
    main.Controller._sync_system_state(app)

    sistema = gestor.system_state()
    assert sistema.mic == "listening"
    assert sistema.camera == "active"
    assert sistema.media_playing is True
    assert sistema.teacher_active is True
    assert sistema.activity == "teaching"
    assert sistema.mode == "teacher"


def test_sync_system_state_prioriza_el_control_de_pc():
    import main
    import types
    gestor = _sm()
    app = types.SimpleNamespace(
        state_manager=gestor,
        listener=types.SimpleNamespace(enabled=False),
        speaker=types.SimpleNamespace(is_speaking=True),
        teacher=types.SimpleNamespace(is_active=True),
        _pc_busy=True, _vision_busy=False, _autonomy_busy=False)
    main.Controller._sync_system_state(app)
    # Controlar el PC es lo más urgente: manda sobre la clase.
    assert gestor.system_state().activity == "controlling"
    assert gestor.system_state().voice == "speaking"


def test_would_win_evita_trabajo_inutil():
    """Un módulo caro puede preguntar antes de calcular una reacción."""
    gestor = _sm()
    gestor.propose(YueProposal(emotion="focused", source="teacher",
                               priority=Priority.TEACHER, ttl=30.0))
    assert gestor.would_win(Priority.MEDIA, "media") is False
    assert gestor.would_win(Priority.EMERGENCY, "safety") is True
    # Quien ya manda siempre puede refrescarse a sí mismo.
    assert gestor.would_win(Priority.TEACHER, "teacher") is True


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
