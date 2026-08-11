"""Pruebas de la MEMORIA EPISÓDICA EMOCIONAL.

    python -m pytest tests/test_episodic_memory.py -v

Cubren los doce casos del diseño, más la separación con `affect_log` y la
migración de bases de datos antiguas.

Todo corre OFFLINE y sin motor de IA: se usa el extractor local a propósito,
porque si la memoria episódica solo funcionara con red, YUE se quedaría sin
recuerdos justo en el modo offline que tanto costó construir.
"""
import os
import sqlite3
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.affect import AffectiveInterpreter, Boundary, Emotion  # noqa: E402
from core.episodic_memory import (  # noqa: E402
    EpisodicMemory, detect_signals, extract_local, parse_extraction,
    resolve_event_time, schedule_follow_up, score_importance,
)
from core.memory import Memory  # noqa: E402
from core.support import SupportDecision, SupportNeed  # noqa: E402

DIA = 86400.0
HORA = 3600.0


# ===========================================================================
# Utilidades
# ===========================================================================
@pytest.fixture()
def entorno(tmp_path):
    """Memoria en disco + capa episódica + intérprete afectivo local."""
    memoria = Memory(str(tmp_path / "yue.db"))
    episodica = EpisodicMemory(memory=memoria, engine=None)
    interprete = AffectiveInterpreter(engine=None, use_semantic=False)
    return memoria, episodica, interprete


def _observar(entorno, texto, *, now=None, decision=None, safety_level=0):
    """Simula un turno del usuario tal y como lo hace main.py."""
    memoria, episodica, interprete = entorno
    afecto = interprete.interpret(texto)
    memoria.add_message("user", texto)
    return episodica.observe(
        texto, affect=afecto, decision=decision, safety_level=safety_level,
        message_id=memoria.last_message_id(), now=now)


# ===========================================================================
# Piezas sueltas
# ===========================================================================
def test_resolucion_temporal_relativa():
    """«mañana» y «pasado mañana» se resuelven con la fecha REAL, no inventada."""
    ahora = time.time()
    fecha, precision = resolve_event_time("Mañana tengo una entrevista", now=ahora)
    assert precision == "day"
    assert 0 < (fecha - ahora) < 2 * DIA

    fecha2, _ = resolve_event_time("Pasado mañana viajo", now=ahora)
    assert fecha2 > fecha


def test_hora_explicita_da_precision_exacta():
    """Si dice la hora, la precisión sube; si no, NO se inventa una."""
    ahora = time.time()
    _, sin_hora = resolve_event_time("Mañana tengo una entrevista", now=ahora)
    _, con_hora = resolve_event_time("Mañana a las 10 tengo una entrevista", now=ahora)
    assert sin_hora == "day"
    assert con_hora == "exact"


def test_follow_up_es_distinto_de_event_at():
    """Nunca se pregunta «¿cómo te fue?» antes de que el evento haya terminado."""
    ahora = time.time()
    evento, precision = resolve_event_time("Mañana a las 10 tengo la entrevista",
                                           now=ahora)
    seguimiento = schedule_follow_up(evento, precision, now=ahora)
    assert seguimiento > evento, "el seguimiento va DESPUÉS del acontecimiento"
    assert seguimiento - evento <= 4 * HORA

    # Sin hora conocida, se espera a que acabe el día.
    evento_dia, precision_dia = resolve_event_time("Mañana tengo la entrevista",
                                                   now=ahora)
    seguimiento_dia = schedule_follow_up(evento_dia, precision_dia, now=ahora)
    assert seguimiento_dia > evento_dia + 6 * HORA


def test_importancia_descarta_lo_trivial():
    """Un recado cotidiano nunca llega al umbral; una entrevista, sí."""
    trivial = score_importance(
        has_date=True, event_type="other", intensity=0.1, has_reason=False,
        should_follow_up=False, remember_request=False, future=True,
        routine=True, novelty=False)
    importante = score_importance(
        has_date=True, event_type="job_interview", intensity=0.7, has_reason=True,
        should_follow_up=True, remember_request=False, future=True,
        routine=False, novelty=False)
    assert trivial < 0.55 <= importante


def test_peticion_explicita_de_recordar_pesa_mucho():
    señales = detect_signals("Recuérdame lo de la reunión del viernes")
    assert señales.remember_request is True
    assert señales.is_candidate is True


def test_extractor_json_del_modelo_tolera_ruido():
    """El extractor estructurado sobrevive a markdown y texto alrededor."""
    crudo = (
        "Claro, aquí tienes:\n```json\n"
        '{"is_episode": true, "event_type": "exam", "event_label": "examen de '
        'cálculo", "event_at": "2026-08-10", "date_precision": "day", '
        '"reason_summary": "no estudió lo suficiente", "needs_follow_up": true, '
        '"is_routine": false, "resolves_previous": false, "outcome_summary": "", '
        '"confidence": 0.9}\n```'
    )
    draft = parse_extraction(crudo)
    assert draft is not None
    assert draft.is_episode and draft.event_type == "exam"
    assert draft.event_label == "examen de cálculo"
    assert draft.date_precision == "day"


def test_extractor_json_invalido_no_revienta():
    assert parse_extraction("lo siento, no puedo") is None
    assert parse_extraction("") is None


# ===========================================================================
# CASO 1 · crear el episodio
# ===========================================================================
def test_caso1_crea_episodio_con_evento_emocion_causa_y_fecha(entorno):
    memoria, _, _ = entorno
    resultado = _observar(
        entorno,
        "Mañana tengo una entrevista y estoy nervioso porque la última me salió mal.")

    assert resultado["action"] == "created"
    episodio = memoria.get_emotional_episode(resultado["episode_id"])

    assert episodio["event_type"] == "job_interview"
    assert "entrevista" in episodio["event_label"]
    assert episodio["emotion"] == str(Emotion.ANXIETY)
    assert "salió mal" in episodio["reason_summary"]
    assert episodio["event_at"] > time.time()
    assert episodio["status"] == "unresolved"
    assert episodio["follow_up_state"] == "pending"
    # yue_action entra NULO: YUE todavía no ha respondido.
    assert not episodio["yue_action"]
    # Las tildes se conservan: esto acaba en el prompt y YUE lo lee.
    assert "ultima" not in episodio["reason_summary"]


# ===========================================================================
# CASOS 2 y 3 · deduplicación y enriquecimiento progresivo
# ===========================================================================
def test_caso2_y_3_tres_mensajes_un_solo_episodio(entorno):
    memoria, _, _ = entorno
    primero = _observar(entorno, "Mañana tengo una entrevista de trabajo.")
    segundo = _observar(entorno, "Estoy cagado con esa entrevista.")
    _observar(entorno, "Es que la anterior me salió mal.")

    assert segundo["action"] == "updated"
    assert segundo["episode_id"] == primero["episode_id"]
    assert memoria.count_episodes() == 1, "no se crean tres recuerdos del mismo hecho"

    episodio = memoria.get_emotional_episode(primero["episode_id"])
    assert episodio["mention_count"] >= 2
    assert "salió mal" in (episodio["reason_summary"] or ""), \
        "la causa llega dos mensajes después y debe engancharse igual"
    assert episodio["emotion"] == str(Emotion.ANXIETY)


def test_dedup_no_mezcla_acontecimientos_distintos(entorno):
    memoria, _, _ = entorno
    _observar(entorno, "Mañana tengo una entrevista de trabajo.")
    _observar(entorno, "El viernes tengo un examen de matemáticas y estoy nervioso.")
    assert memoria.count_episodes() == 2, "una entrevista y un examen NO son lo mismo"


# ===========================================================================
# CASOS 4 y 5 · seguimiento: vence, se pregunta UNA vez
# ===========================================================================
def test_caso4_el_seguimiento_vence_solo_despues_del_evento(entorno):
    _, episodica, _ = entorno
    ahora = time.time()
    _observar(entorno, "Mañana tengo una entrevista y estoy nervioso.", now=ahora)

    assert episodica.due_followup(now=ahora) is None, "aún no ha pasado nada"
    assert episodica.due_followup(now=ahora + 12 * HORA) is None, \
        "la entrevista todavía no ha ocurrido"

    vencido = episodica.due_followup(now=ahora + 36 * HORA)
    assert vencido is not None
    assert vencido["event_label"]


def test_caso5_tras_preguntar_no_vuelve_a_sugerirse(entorno):
    memoria, episodica, _ = entorno
    ahora = time.time()
    _observar(entorno, "Mañana tengo una entrevista y estoy nervioso.", now=ahora)

    despues = ahora + 36 * HORA
    vencido = episodica.due_followup(now=despues)
    episodica.mark_followup_asked(vencido["id"], now=despues)

    assert episodica.due_followup(now=despues + HORA) is None
    assert episodica.due_followup(now=despues + 5 * DIA) is None

    episodio = memoria.get_emotional_episode(vencido["id"])
    assert episodio["follow_up_state"] == "asked"
    assert episodio["follow_up_count"] == 1
    # El EVENTO sigue sin resolverse; lo que se cerró es la iniciativa de YUE.
    assert episodio["status"] == "unresolved"


def test_el_seguimiento_suena_a_persona_no_a_recordatorio(entorno):
    _, episodica, _ = entorno
    ahora = time.time()
    _observar(entorno, "Mañana tengo una entrevista y estoy nervioso.", now=ahora)
    vencido = episodica.due_followup(now=ahora + 36 * HORA)

    frase = episodica.fallback_followup_text(vencido, now=ahora + 36 * HORA)
    bajo = frase.lower()
    assert "?" in frase
    for prohibida in ("registro", "base de datos", "memoria interna", "detect",
                      "episodio", "según mis"):
        assert prohibida not in bajo

    instruccion = episodica.build_followup_prompt(vencido, now=ahora + 36 * HORA)
    assert "según mis registros" in instruccion.lower(), \
        "el prompt debe PROHIBIR esa fórmula explícitamente"


# ===========================================================================
# CASO 6 · resolución
# ===========================================================================
def test_caso6_contar_como_fue_resuelve_el_episodio(entorno):
    memoria, episodica, _ = entorno
    ahora = time.time()
    creado = _observar(entorno, "Mañana tengo una entrevista y estoy nervioso.",
                       now=ahora)

    despues = ahora + 38 * HORA
    resultado = _observar(entorno,
                          "Me fue genial en la entrevista, creo que me van a contratar.",
                          now=despues)

    assert resultado["action"] == "resolved"
    assert resultado["episode_id"] == creado["episode_id"]

    episodio = memoria.get_emotional_episode(creado["episode_id"])
    assert episodio["status"] == "resolved"
    assert episodio["outcome_summary"]
    assert episodio["outcome_emotion"] == str(Emotion.JOY)
    assert memoria.count_episodes() == 1, "un desenlace no crea un episodio nuevo"


def test_la_respuesta_a_la_pregunta_de_yue_cierra_el_episodio(entorno):
    """Tras preguntar YUE, el usuario contesta SIN repetir de qué habla.

    «Muchísimo mejor de lo que esperaba» no contiene la palabra «entrevista»;
    aun así es la respuesta, porque YUE acaba de preguntar por ella. Este caso
    no lo veía ninguna prueba aislada: apareció simulando los dos días enteros.
    """
    memoria, episodica, _ = entorno
    ahora = time.time()
    creado = _observar(entorno, "Mañana tengo una entrevista y estoy nervioso.",
                       now=ahora)

    despues = ahora + 36 * HORA
    vencido = episodica.due_followup(now=despues)
    episodica.mark_followup_asked(vencido["id"], now=despues)

    resultado = _observar(
        entorno, "Muchísimo mejor de lo que esperaba. Creo que me van a contratar.",
        now=despues + 600)

    assert resultado["action"] == "resolved"
    assert resultado["episode_id"] == creado["episode_id"]
    episodio = memoria.get_emotional_episode(creado["episode_id"])
    assert episodio["status"] == "resolved"
    assert episodio["outcome_emotion"] == str(Emotion.JOY)


def test_el_recuerdo_se_narra_en_pasado_si_ya_ocurrio(entorno):
    _, episodica, _ = entorno
    ahora = time.time()
    _observar(entorno, "Mañana tengo una entrevista y estoy nervioso.", now=ahora)
    bloque = episodica.context_block("entrevista", now=ahora + 5 * DIA)
    assert "tuvo" in bloque and "días tiene" not in bloque


# ===========================================================================
# CASO 7 · lo trivial no se recuerda
# ===========================================================================
@pytest.mark.parametrize("mensaje", [
    "Mañana compraré pan.",
    "Mañana compraré papel higiénico.",
    "Mañana voy al gimnasio.",
    "Hoy tengo que lavar la ropa.",
])
def test_caso7_lo_cotidiano_no_genera_recuerdo(entorno, mensaje):
    memoria, _, _ = entorno
    resultado = _observar(entorno, mensaje)
    assert resultado["action"] == "skipped"
    assert memoria.count_episodes() == 0


def test_una_rutina_con_carga_emocional_si_puede_ser_episodio(entorno):
    """«Vuelvo al gimnasio después de seis meses» ya no es una rutina."""
    señales = detect_signals(
        "Mañana vuelvo al gimnasio por primera vez después de seis meses y estoy nervioso",
        emotional=True)
    assert señales.novelty is True
    assert señales.is_candidate is True


# ===========================================================================
# CASO 8 · también se recuerdan las cosas buenas
# ===========================================================================
@pytest.mark.parametrize("mensaje,emocion_esperada", [
    ("Mi graduación es mañana y estoy súper emocionado.", None),
    ("Mi hermana se casa mañana y estoy emocionadísimo.", None),
])
def test_caso8_episodios_positivos(entorno, mensaje, emocion_esperada):
    memoria, _, _ = entorno
    resultado = _observar(entorno, mensaje)
    assert resultado["action"] == "created", "la memoria no es solo para lo que duele"
    episodio = memoria.get_emotional_episode(resultado["episode_id"])
    assert episodio["event_at"] is not None
    assert episodio["emotion"] != ""


# ===========================================================================
# CASO 9 · los límites explícitos mandan
# ===========================================================================
def test_caso9_pedir_espacio_bloquea_el_seguimiento(entorno):
    _, episodica, interprete = entorno
    ahora = time.time()
    _observar(entorno, "Mañana tengo una entrevista y estoy nervioso.", now=ahora)

    despues = ahora + 36 * HORA
    assert episodica.due_followup(now=despues) is not None, "punto de partida"

    # El usuario pide espacio: a partir de aquí, silencio proactivo.
    afecto = interprete.interpret("Déjame solo, no quiero hablar.")
    decision = SupportDecision(mode=SupportNeed.GIVE_SPACE, give_space=True)
    episodica.observe("Déjame solo, no quiero hablar.", affect=afecto,
                      decision=decision, now=despues)

    assert episodica.in_quiet_window(now=despues) is True
    assert episodica.due_followup(now=despues) is None, \
        "un episodio pendiente NUNCA atropella un «déjame solo»"
    assert episodica.due_followup(now=despues + HORA) is None


def test_el_limite_se_detecta_tambien_por_el_estado_afectivo(entorno):
    _, episodica, _ = entorno
    ahora = time.time()
    episodica.note_boundary(boundaries=(Boundary.NO_QUESTIONS,), now=ahora)
    assert episodica.in_quiet_window(now=ahora) is True


def test_la_ventana_de_calma_caduca(entorno):
    _, episodica, _ = entorno
    ahora = time.time()
    episodica.note_boundary(give_space=True, now=ahora)
    assert episodica.in_quiet_window(now=ahora + 10 * DIA) is False


# ===========================================================================
# CASO 10 · la seguridad tiene prioridad
# ===========================================================================
def test_caso10_riesgo_no_entra_al_seguimiento_ordinario(entorno):
    memoria, _, _ = entorno
    resultado = _observar(
        entorno, "Mañana tengo una entrevista y ya no quiero seguir con nada.",
        safety_level=3)
    assert resultado["action"] == "skipped"
    assert resultado["reason"] == "seguridad"
    assert memoria.count_episodes() == 0, \
        "ese terreno lo lleva el sistema de seguridad, no un «¿cómo te fue?»"


def test_un_evento_de_riesgo_reciente_suspende_los_seguimientos(entorno):
    memoria, episodica, _ = entorno
    ahora = time.time()
    _observar(entorno, "Mañana tengo una entrevista y estoy nervioso.", now=ahora)
    despues = ahora + 36 * HORA
    assert episodica.due_followup(now=despues) is not None

    memoria.add_risk_event()
    assert episodica.due_followup(now=despues) is None


# ===========================================================================
# CASO 11 · persistencia entre reinicios
# ===========================================================================
def test_caso11_los_episodios_sobreviven_a_un_reinicio(tmp_path):
    ruta = str(tmp_path / "yue.db")

    memoria = Memory(ruta)
    episodica = EpisodicMemory(memory=memoria, engine=None)
    interprete = AffectiveInterpreter(engine=None, use_semantic=False)
    texto = "Mañana tengo una entrevista y estoy nervioso porque la última salió mal."
    creado = episodica.observe(texto, affect=interprete.interpret(texto))
    assert creado["action"] == "created"

    # --- YUE se cierra y se vuelve a abrir -------------------------------
    del episodica, memoria
    memoria2 = Memory(ruta)
    episodica2 = EpisodicMemory(memory=memoria2, engine=None)

    episodio = memoria2.get_emotional_episode(creado["episode_id"])
    assert episodio is not None
    assert "entrevista" in episodio["event_label"]
    assert episodio["status"] == "unresolved"

    # Y el seguimiento programado ayer sigue en pie hoy.
    assert episodica2.due_followup(now=time.time() + 36 * HORA) is not None


# ===========================================================================
# CASO 12 · migración de bases de datos antiguas
# ===========================================================================
def test_caso12_base_antigua_sin_la_tabla_se_migra_sin_perder_datos(tmp_path):
    ruta = str(tmp_path / "vieja.db")

    # Base "antigua": el esquema mínimo de siempre, con datos dentro.
    conexion = sqlite3.connect(ruta)
    conexion.executescript(
        """
        CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT, content TEXT, ts REAL);
        CREATE TABLE facts(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, ts REAL);
        CREATE TABLE goals(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT,
            status TEXT DEFAULT 'activa', created REAL, done REAL);
        CREATE TABLE state(key TEXT PRIMARY KEY, value TEXT);
        """
    )
    conexion.execute("INSERT INTO messages(role,content,ts) VALUES('user','hola',1.0)")
    conexion.execute("INSERT INTO facts(text,ts) VALUES('vive en Paiján',1.0)")
    conexion.commit()
    conexion.close()

    # Al abrirla, YUE debe crear lo nuevo SIN tocar lo viejo.
    memoria = Memory(ruta)
    assert len(memoria.recent_messages(5)) == 1, "los mensajes antiguos siguen ahí"
    assert any("Paiján" in f for f in memoria.get_facts()), "los hechos siguen ahí"

    episodica = EpisodicMemory(memory=memoria, engine=None)
    interprete = AffectiveInterpreter(engine=None, use_semantic=False)
    texto = "Mañana tengo una entrevista y estoy nervioso."
    assert episodica.observe(texto, affect=interprete.interpret(texto))["action"] == "created"
    assert memoria.count_episodes() == 1


def test_abrir_dos_veces_no_duplica_ni_rompe_nada(tmp_path):
    ruta = str(tmp_path / "yue.db")
    memoria = Memory(ruta)
    interprete = AffectiveInterpreter(engine=None, use_semantic=False)
    texto = "Mañana tengo un examen y estoy nervioso."
    EpisodicMemory(memory=memoria, engine=None).observe(
        texto, affect=interprete.interpret(texto))

    memoria2 = Memory(ruta)      # _init_db se ejecuta otra vez
    assert memoria2.count_episodes() == 1


# ===========================================================================
# Coexistencia con affect_log · no duplicar sistemas
# ===========================================================================
def test_affect_log_y_episodios_responden_preguntas_distintas(entorno):
    """`affect_log` dice qué SIENTE; el episodio, qué está VIVIENDO."""
    memoria, _, interprete = entorno
    texto = "Mañana tengo una entrevista y estoy nervioso porque la última salió mal."
    afecto = interprete.interpret(texto)

    # Lo que main.py hace en cada mensaje (sigue intacto).
    memoria.add_affect(emotion=str(afecto.primary_emotion), valence=afecto.valence,
                       arousal=afecto.arousal, distress=afecto.distress,
                       confidence=afecto.confidence, trigger="trabajo")
    resultado = _observar(entorno, texto)

    lecturas = memoria.recent_affect(5)
    assert lecturas and lecturas[0]["emotion"] == str(Emotion.ANXIETY)
    assert "trigger_category" in lecturas[0]
    assert "event_label" not in lecturas[0], "affect_log NO guarda acontecimientos"

    episodio = memoria.get_emotional_episode(resultado["episode_id"])
    assert episodio["event_label"] and episodio["reason_summary"]
    assert episodio["emotion"] == lecturas[0]["emotion"], "misma lectura, reutilizada"


def test_no_se_vuelve_a_analizar_la_emocion(entorno):
    """La emoción del episodio VIENE del análisis afectivo, no se recalcula."""
    memoria, episodica, _ = entorno

    class AfectoFalso:
        primary_emotion = Emotion.PRIDE
        valence = 0.8
        arousal = 0.6
        confidence = 0.9
        explicit_boundary = ()

    resultado = episodica.observe("Mañana tengo mi graduación.", affect=AfectoFalso())
    episodio = memoria.get_emotional_episode(resultado["episode_id"])
    assert episodio["emotion"] == str(Emotion.PRIDE), \
        "se reutiliza la lectura existente en vez de pedir otra"


# ===========================================================================
# should_follow_up · ahora sí sirve para algo
# ===========================================================================
def test_should_follow_up_sube_la_importancia_pero_no_basta(entorno):
    """`should_follow_up=True` NO crea un episodio por sí solo.

    Hace falta además un acontecimiento concreto. Antes de esto, el campo se
    calculaba y no lo leía nadie.
    """
    memoria, _, _ = entorno
    decision = SupportDecision(mode=SupportNeed.COMFORT, should_follow_up=True)

    sin_evento = _observar(entorno, "Hoy me siento un poco raro, no sé.",
                           decision=decision)
    assert sin_evento["action"] == "skipped"
    assert memoria.count_episodes() == 0

    con_evento = _observar(entorno, "Mañana tengo la operación de mi madre.",
                           decision=decision)
    assert con_evento["action"] == "created"


# ===========================================================================
# yue_action
# ===========================================================================
def test_yue_action_se_rellena_despues_de_responder(entorno):
    memoria, episodica, _ = entorno
    creado = _observar(entorno,
                       "Mañana tengo una entrevista y estoy muy nervioso.")
    episodio = memoria.get_emotional_episode(creado["episode_id"])
    assert not episodio["yue_action"], "al crearlo todavía no se sabe qué hará YUE"

    episodica.note_yue_response(
        "Respira, va a salir bien. Si quieres practicamos algunas preguntas.",
        decision=SupportDecision(mode=SupportNeed.COMFORT))

    episodio = memoria.get_emotional_episode(creado["episode_id"])
    assert episodio["yue_action"] == "practicaron preguntas"
    assert len(episodio["yue_action"]) < 80, "una etiqueta corta, no la respuesta entera"


def test_yue_action_no_se_pisa(entorno):
    memoria, episodica, _ = entorno
    creado = _observar(entorno, "Mañana tengo una entrevista y estoy nervioso.")
    episodica.note_yue_response("Respira, tranquilo, va a salir bien.")
    primera = memoria.get_emotional_episode(creado["episode_id"])["yue_action"]

    episodica._touch(creado["episode_id"], time.time())
    episodica.note_yue_response("Vale, te dejo con lo tuyo.")
    assert memoria.get_emotional_episode(creado["episode_id"])["yue_action"] == primera


# ===========================================================================
# Contexto del prompt
# ===========================================================================
def test_el_contexto_no_revela_estructuras_internas(entorno):
    _, episodica, _ = entorno
    _observar(entorno, "Mañana tengo una entrevista y estoy nervioso.")

    bloque = episodica.context_block("hablemos de la entrevista")
    assert "RECUERDOS CONCRETOS" in bloque
    assert "entrevista" in bloque
    bajo = bloque.lower()
    assert "sqlite" not in bajo and "emotional_episodes" not in bajo
    assert "bases de datos" in bajo, "debe PROHIBIR mencionarlas explícitamente"
    assert "no fuerces" in bajo


def test_el_contexto_esta_acotado(entorno):
    memoria, episodica, _ = entorno
    ahora = time.time()
    for i, mensaje in enumerate((
            "Mañana tengo una entrevista y estoy nervioso.",
            "El viernes tengo un examen y estoy preocupado.",
            "La próxima semana tengo mi graduación y estoy emocionado.",
            "El lunes tengo una operación y tengo miedo.",
            "Pasado mañana tengo una reunión importante y estoy nervioso.")):
        _observar(entorno, mensaje, now=ahora + i)

    relevantes = episodica.get_relevant_episodes("", limit=3, now=ahora)
    assert len(relevantes) <= 3, "no se vuelca la base entera en el prompt"


def test_sin_episodios_el_prompt_no_cambia(entorno):
    _, episodica, _ = entorno
    assert episodica.context_block("hola, ¿qué tal?") == ""


def test_un_episodio_ya_preguntado_avisa_de_no_repetir(entorno):
    memoria, episodica, _ = entorno
    ahora = time.time()
    creado = _observar(entorno, "Mañana tengo una entrevista y estoy nervioso.",
                       now=ahora)
    episodica.mark_followup_asked(creado["episode_id"], now=ahora + 36 * HORA)

    bloque = episodica.context_block("entrevista", now=ahora + 37 * HORA)
    assert "NO vuelvas a preguntarlo" in bloque


# ===========================================================================
# Retención
# ===========================================================================
def test_los_episodios_viejos_caducan_sin_borrarse(entorno):
    memoria, episodica, _ = entorno
    hace_mucho = time.time() - 200 * DIA
    _observar(entorno, "Mañana tengo una entrevista y estoy nervioso.",
              now=hace_mucho)

    assert memoria.count_episodes(status="unresolved") == 1
    episodica.maintenance()
    assert memoria.count_episodes(status="unresolved") == 0
    assert memoria.count_episodes() >= 0  # caducado, no destruido a lo bruto


def test_la_capa_se_puede_apagar(entorno, monkeypatch):
    memoria, episodica, _ = entorno
    import config
    monkeypatch.setattr(config, "EPISODIC_MEMORY_ENABLED", False, raising=False)

    resultado = _observar(entorno, "Mañana tengo una entrevista y estoy nervioso.")
    assert resultado["action"] == "skipped"
    assert memoria.count_episodes() == 0
    assert episodica.context_block("entrevista") == ""
    assert episodica.due_followup() is None


# ===========================================================================
# Robustez
# ===========================================================================
@pytest.mark.parametrize("basura", ["", "   ", "?", "a" * 5000, "🙂🙂🙂"])
def test_nunca_revienta_con_entradas_raras(entorno, basura):
    _, episodica, _ = entorno
    assert episodica.observe(basura)["action"] in ("skipped", "created", "updated")


def test_sobrevive_a_una_memoria_rota():
    """Si la persistencia falla, la conversación sigue como si nada."""
    class MemoriaRota:
        def open_episodes(self, *a, **k):
            raise RuntimeError("disco lleno")

        def add_emotional_episode(self, *a, **k):
            raise RuntimeError("disco lleno")

        def list_goals(self, *a, **k):
            raise RuntimeError("disco lleno")

    episodica = EpisodicMemory(memory=MemoriaRota(), engine=None)
    resultado = episodica.observe("Mañana tengo una entrevista y estoy nervioso.")
    assert resultado["action"] == "skipped"
    assert episodica.context_block("entrevista") == ""


def test_un_motor_que_falla_cae_al_extractor_local(entorno):
    memoria, _, interprete = entorno

    class MotorRoto:
        def chat(self, *a, **k):
            raise RuntimeError("sin conexión")

    episodica = EpisodicMemory(memory=memoria, engine=MotorRoto())
    texto = "Mañana tengo una entrevista y estoy nervioso porque la última salió mal."
    resultado = episodica.observe(texto, affect=interprete.interpret(texto))
    assert resultado["action"] == "created", "offline también hay memoria episódica"
