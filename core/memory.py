"""Memoria evolutiva de YUE: conversaciones, hechos, metas y vinculo."""
import json
import sqlite3
import time
from collections import defaultdict
from contextlib import closing


# --- Historial emocional (mood_log): de etiquetas crudas a un recuerdo en
# español. Une el vocabulario del analisis de texto (ingles, de core.emotion) y
# el de la camara (ya en espanol, de core.face_emotion) en un idioma comun. ---
_MOOD_ADJ = {
    # texto (core.emotion.EmotionState.name)
    "happy": "contenta", "excited": "animada", "love": "cariñosa",
    "shy": "tímida", "proud": "orgullosa", "playful": "juguetona",
    "curious": "curiosa", "confused": "confundida", "worried": "preocupada",
    "sad": "decaída", "angry": "irritada", "surprised": "sorprendida",
    "relaxed": "tranquila", "focused": "concentrada", "bored": "aburrida",
    "sleepy": "cansada",
    # camara (core.face_emotion.PersonEmotion.key)
    "feliz": "contenta", "triste": "decaída", "sorprendida": "sorprendida",
    "molesta": "tensa", "pensativa": "pensativa", "tranquila": "tranquila",
    # NUEVO · taxonomia de core.affect (core.affect.models.Emotion). Se anaden
    # SIN quitar las anteriores, para que las bases existentes sigan leyendose
    # igual y el resumen semanal entienda ambos vocabularios a la vez.
    "joy": "contenta", "excitement": "animada", "pride": "orgullosa",
    "relief": "tranquila", "affection": "cariñosa",
    "sadness": "decaída", "disappointment": "decepcionada",
    "loneliness": "sola", "anger": "irritada", "frustration": "frustrada",
    "fear": "asustada", "anxiety": "preocupada", "guilt": "culpable",
    "embarrassment": "avergonzada", "confusion": "confundida",
    "tiredness": "cansada",
}

# Sustantivo para la mencion aparte "N momentos de ___".
_MOOD_NOUN = {
    "preocupada": "preocupación", "decaída": "tristeza", "irritada": "enojo",
    "tensa": "tensión", "confundida": "confusión", "aburrida": "desgana",
    "sorprendida": "sorpresa", "cansada": "cansancio",
    # NUEVO · taxonomia de core.affect
    "decepcionada": "decepción", "sola": "soledad", "frustrada": "frustración",
    "asustada": "miedo", "culpable": "culpa", "avergonzada": "vergüenza",
}

# Emociones de tono dificil que merecen resaltarse como "momentos".
_MOOD_NEGATIVAS = (
    "preocupada", "decaída", "irritada", "tensa", "confundida",
    "decepcionada", "sola", "frustrada", "asustada", "culpable",
)

_DIAS_SEMANA = (
    "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo",
)

_NUM_PALABRA = {1: "un", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco", 6: "seis"}


class Memory:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        self._init_db()

    def _conn(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self):
        with closing(self._conn()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT, content TEXT, ts REAL);
                CREATE TABLE IF NOT EXISTS facts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT, ts REAL);
                CREATE TABLE IF NOT EXISTS goals(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT, status TEXT DEFAULT 'activa',
                    created REAL, done REAL);
                CREATE TABLE IF NOT EXISTS state(
                    key TEXT PRIMARY KEY, value TEXT);

                -- NUEVO: historial emocional de fondo. 'fuente' es 'texto'
                -- (lo que escribe el usuario) o 'camara' (expresion leida). Para
                -- 'camara' NO se guarda ninguna imagen ni dato identificable:
                -- solo la etiqueta, su intensidad y la hora.
                CREATE TABLE IF NOT EXISTS mood_log(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL,
                    fuente TEXT,
                    emocion TEXT,
                    intensidad REAL,
                    texto_origen TEXT);

                -- NUEVO: eventos de riesgo textual (cuando safety.detect_risk
                -- salta sobre un mensaje). NO se guarda el texto, solo la hora,
                -- para poder notar un patron reciente sin conservar nada sensible.
                CREATE TABLE IF NOT EXISTS risk_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL);

                -- NUEVO: rutinas guardadas. 'pasos_json' es el plan de acciones
                -- de pc_control (el mismo bloque que usa "deshacer"/"repetir")
                -- serializado, para reejecutarlo con una sola frase corta.
                CREATE TABLE IF NOT EXISTS routines(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre TEXT,
                    pasos_json TEXT,
                    creado REAL);

                -- NUEVO: bitacora de actividades. Solo una DESCRIPCION CORTA de
                -- lo que hizo YUE (nunca contenido sensible ni archivos). 'tipo'
                -- es la categoria (musica, video, imagen, control_pc, autonomia,
                -- clase…), 'con_usuario' 1 si fue pedido/compartido y 0 si YUE lo
                -- hizo sola, y 'origen' el modulo que la genero.
                CREATE TABLE IF NOT EXISTS activity_log(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL,
                    tipo TEXT,
                    detalle TEXT,
                    con_usuario INTEGER,
                    origen TEXT);

                -- NUEVO (memoria a largo plazo): resúmenes CONSOLIDADOS del
                -- historial viejo. La sesión/semana funcionan con mensajes crudos
                -- recientes; a escala de meses, esta tabla guarda 3-5 líneas por
                -- periodo (temas, tono, algún hecho notable) que vuelven al prompt.
                -- Es ADITIVA: no sustituye ni borra nada de las tablas de arriba.
                CREATE TABLE IF NOT EXISTS memoria_larga(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    periodo_inicio REAL,
                    periodo_fin REAL,
                    resumen TEXT,
                    ts REAL);

                -- Memoria multimedia específica: sesiones completas, momentos
                -- importantes y títulos vistos/escuchados. Solo se guarda texto
                -- descriptivo; nunca frames, audio, rostros ni capturas.
                CREATE TABLE IF NOT EXISTS multimedia_sessions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started REAL,
                    ended REAL,
                    source TEXT,
                    app TEXT,
                    title TEXT,
                    media_type TEXT,
                    dominant_emotion TEXT,
                    peak_intensity REAL DEFAULT 0,
                    summary TEXT);
                CREATE TABLE IF NOT EXISTS multimedia_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER,
                    ts REAL,
                    event_kind TEXT,
                    label TEXT,
                    emotion TEXT,
                    intensity REAL DEFAULT 0,
                    detail TEXT,
                    favorite INTEGER DEFAULT 0,
                    comment TEXT);
                CREATE INDEX IF NOT EXISTS idx_multimedia_events_session
                    ON multimedia_events(session_id, ts);
                CREATE TABLE IF NOT EXISTS multimedia_items(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_kind TEXT,
                    title TEXT,
                    title_key TEXT,
                    source TEXT,
                    app TEXT,
                    first_seen REAL,
                    last_seen REAL,
                    times_seen INTEGER DEFAULT 1,
                    last_emotion TEXT,
                    last_intensity REAL DEFAULT 0,
                    preference_score REAL DEFAULT 0,
                    explicit_preference INTEGER DEFAULT 0,
                    last_session_id INTEGER,
                    metadata_json TEXT,
                    UNIQUE(media_kind, title_key, source, app));
                CREATE INDEX IF NOT EXISTS idx_multimedia_items_recent
                    ON multimedia_items(last_seen DESC);

                -- NUEVO (memoria afectiva estructurada): sustituye al uso de
                -- 'texto_origen' de mood_log. Guarda la LECTURA (emoción,
                -- valencia, activación, malestar, qué necesitaba, confianza),
                -- nunca el mensaje: ese ya vive en 'messages' y duplicarlo era
                -- copiar información privada sin ninguna ganancia.
                -- 'trigger_category' es una CATEGORÍA corta (trabajo, estudios,
                -- relaciones, salud, dinero, otro), jamás la frase literal.
                CREATE TABLE IF NOT EXISTS affect_log(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL,
                    emotion TEXT,
                    secondary_emotion TEXT,
                    valence REAL,
                    arousal REAL,
                    distress REAL,
                    support_need TEXT,
                    confidence REAL,
                    sarcasm REAL DEFAULT 0,
                    safety_level INTEGER DEFAULT 0,
                    trigger_category TEXT,
                    source TEXT);
                CREATE INDEX IF NOT EXISTS idx_affect_log_ts
                    ON affect_log(ts DESC);

                -- NUEVO (memoria EPISÓDICA emocional): acontecimientos CONCRETOS
                -- de la vida del usuario ligados a una emoción. NO es otro
                -- affect_log: aquel responde "¿qué siente ahora?", este responde
                -- "¿qué está viviendo, por qué le importa, qué hicimos al
                -- respecto y cómo terminó?". Ambos conviven.
                --
                -- OJO con los dos estados, que son distintos a propósito:
                --   status           -> del ACONTECIMIENTO (unresolved/resolved/
                --                       cancelled/expired)
                --   follow_up_state  -> de la PREGUNTA de YUE (pending/asked/
                --                       skipped/cancelled)
                -- Un episodio puede seguir 'unresolved' con la pregunta ya
                -- 'asked': YUE preguntó una vez y no insiste.
                --
                -- Aquí SÍ se guarda texto corto (etiqueta, causa, desenlace)
                -- porque sin él no hay recuerdo posible; nunca la conversación
                -- entera ni la respuesta completa de YUE.
                CREATE TABLE IF NOT EXISTS emotional_episodes(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,

                    source_message_id INTEGER,

                    event_type TEXT,
                    event_label TEXT,

                    event_at REAL,
                    date_precision TEXT,

                    emotion TEXT,
                    intensity REAL,

                    reason_summary TEXT,

                    importance REAL,

                    support_mode TEXT,
                    yue_action TEXT,

                    follow_up_at REAL,
                    follow_up_state TEXT DEFAULT 'pending',
                    follow_up_count INTEGER DEFAULT 0,

                    status TEXT DEFAULT 'unresolved',

                    outcome_summary TEXT,
                    outcome_emotion TEXT,

                    confidence REAL,

                    first_mentioned_at REAL,
                    last_mentioned_at REAL,

                    mention_count INTEGER DEFAULT 1);
                CREATE INDEX IF NOT EXISTS idx_episodes_status
                    ON emotional_episodes(status);
                CREATE INDEX IF NOT EXISTS idx_episodes_followup
                    ON emotional_episodes(follow_up_state, follow_up_at);
                CREATE INDEX IF NOT EXISTS idx_episodes_event_at
                    ON emotional_episodes(event_at);
                CREATE INDEX IF NOT EXISTS idx_episodes_last_mentioned
                    ON emotional_episodes(last_mentioned_at DESC);

                -- ===========================================================
                -- NUEVO · STORY MEMORY (memoria narrativa)
                -- ===========================================================
                -- `emotional_episodes` responde «¿qué le pasó?». Estas tres
                -- tablas responden «¿qué HISTORIA sigue viva?»: un hilo de su
                -- vida que evoluciona durante semanas o meses (una amistad, una
                -- meta, un proyecto) y que se compone de varios episodios.
                --
                -- Es ADITIVA: no sustituye a `memoria_larga` (resumen histórico
                -- difuso) ni a `emotional_episodes` (acontecimientos sueltos).
                -- Cuando puede, NO duplica: `story_events.source_type` +
                -- `source_id` apuntan al episodio o mensaje original.
                --
                -- `story_key` es una clave ESTABLE y normalizada (sin tildes,
                -- en minúsculas: "person:andrea"). No depende del título que
                -- genere un LLM, que es lo que provocaría historias duplicadas.
                CREATE TABLE IF NOT EXISTS memory_stories(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    story_type TEXT NOT NULL,
                    story_key TEXT NOT NULL UNIQUE,
                    title TEXT,
                    status TEXT DEFAULT 'active',
                    summary TEXT,
                    current_state TEXT,
                    emotional_significance REAL DEFAULT 0.0,
                    confidence REAL DEFAULT 0.5,
                    -- Solo para historias de META/PROYECTO. `progress` es NULL
                    -- mientras no haya EVIDENCIA: preferimos no saber a inventar.
                    motivation TEXT,
                    progress REAL,
                    mention_count INTEGER DEFAULT 1,
                    created_at REAL,
                    updated_at REAL,
                    last_evidence_at REAL);
                CREATE INDEX IF NOT EXISTS idx_stories_type
                    ON memory_stories(story_type);
                CREATE INDEX IF NOT EXISTS idx_stories_status
                    ON memory_stories(status);
                CREATE INDEX IF NOT EXISTS idx_stories_updated
                    ON memory_stories(updated_at DESC);

                -- Acontecimientos de una historia, en orden. NUNCA se pisan:
                -- una reconciliación se AÑADE, la discusión anterior se queda
                -- (marcada como resuelta). Así la historia conserva su pasado.
                CREATE TABLE IF NOT EXISTS story_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    story_id INTEGER NOT NULL,
                    source_type TEXT,
                    source_id INTEGER,
                    source_message_id INTEGER,
                    event_type TEXT,
                    summary TEXT,
                    user_feeling TEXT,
                    significance REAL DEFAULT 0.0,
                    intensity REAL DEFAULT 0.0,
                    confidence REAL DEFAULT 0.5,
                    unresolved INTEGER DEFAULT 0,
                    resolved_at REAL,
                    yue_action TEXT,
                    happened_at REAL,
                    created_at REAL);
                CREATE INDEX IF NOT EXISTS idx_story_events_story
                    ON story_events(story_id, happened_at);
                CREATE INDEX IF NOT EXISTS idx_story_events_source
                    ON story_events(source_type, source_id);
                CREATE INDEX IF NOT EXISTS idx_story_events_unresolved
                    ON story_events(story_id, unresolved);

                -- Quién/qué aparece en la historia. `normalized_name` es lo que
                -- permite reconocer a «Andrea», «andrea» y «ANDREA» como la
                -- misma persona. `relation` solo se rellena si él lo DIJO: YUE
                -- no decide por su cuenta que alguien es su pareja o su madre.
                CREATE TABLE IF NOT EXISTS story_entities(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    story_id INTEGER NOT NULL,
                    entity_type TEXT,
                    name TEXT,
                    normalized_name TEXT,
                    relation TEXT,
                    relation_confidence REAL DEFAULT 0.0,
                    confidence REAL DEFAULT 0.5,
                    mention_count INTEGER DEFAULT 1,
                    first_seen REAL,
                    last_seen REAL);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_story_entities_unica
                    ON story_entities(story_id, entity_type, normalized_name);
                CREATE INDEX IF NOT EXISTS idx_story_entities_nombre
                    ON story_entities(normalized_name);

                -- Limpieza de la funcion retirada, sin tocar conversaciones,
                -- hechos, metas ni el nivel de vinculo.
                DROP TABLE IF EXISTS media;
                DROP TABLE IF EXISTS diary;
                """
            )
            connection.commit()
        self._migrate_episodes()
        self._migrate_stories()

    def _migrate_episodes(self):
        """Migración TOLERANTE de `emotional_episodes` para bases ya existentes.

        `CREATE TABLE IF NOT EXISTS` cubre el caso de una base antigua sin la
        tabla, pero no el de una base creada con una versión anterior de la
        tabla a la que luego se le añadió una columna. Aquí se añaden las que
        falten, una a una y sin tocar los datos. Nunca borra ni recrea nada.
        """
        columnas_extra = (
            ("mention_count", "INTEGER DEFAULT 1"),
        )
        try:
            with closing(self._conn()) as connection:
                existentes = {
                    fila["name"] for fila in connection.execute(
                        "PRAGMA table_info(emotional_episodes)").fetchall()
                }
                if not existentes:
                    return
                for nombre, tipo in columnas_extra:
                    if nombre not in existentes:
                        connection.execute(
                            f"ALTER TABLE emotional_episodes ADD COLUMN {nombre} {tipo}")
                connection.commit()
        except Exception as exc:  # pragma: no cover
            print("[episodic-memory] no pude migrar emotional_episodes:", exc)

    def _migrate_stories(self):
        """Migración TOLERANTE de las tablas de Story Memory.

        Mismo criterio que `_migrate_episodes`: una base creada con una versión
        anterior de estas tablas recibe las columnas que le falten, una a una.
        NUNCA borra, recrea ni cambia columnas existentes; si algo va mal, la
        aplicación sigue funcionando sin memoria narrativa.
        """
        extra = {
            "memory_stories": (
                ("current_state", "TEXT"),
                ("mention_count", "INTEGER DEFAULT 1"),
                ("last_evidence_at", "REAL"),
                ("confidence", "REAL DEFAULT 0.5"),
                ("emotional_significance", "REAL DEFAULT 0.0"),
                ("motivation", "TEXT"),
                ("progress", "REAL"),
            ),
            "story_events": (
                ("source_message_id", "INTEGER"),
                ("confidence", "REAL DEFAULT 0.5"),
                ("resolved_at", "REAL"),
                ("yue_action", "TEXT"),
                ("significance", "REAL DEFAULT 0.0"),
                ("intensity", "REAL DEFAULT 0.0"),
            ),
            "story_entities": (
                ("relation_confidence", "REAL DEFAULT 0.0"),
                ("mention_count", "INTEGER DEFAULT 1"),
                ("confidence", "REAL DEFAULT 0.5"),
            ),
        }
        try:
            with closing(self._conn()) as connection:
                for tabla, columnas in extra.items():
                    existentes = {
                        fila["name"] for fila in connection.execute(
                            f"PRAGMA table_info({tabla})").fetchall()
                    }
                    if not existentes:
                        continue  # la tabla aún no existe: ya la crea _init_db
                    for nombre, tipo in columnas:
                        if nombre not in existentes:
                            connection.execute(
                                f"ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo}")
                connection.commit()
        except Exception as exc:  # pragma: no cover
            print("[story-memory] no pude migrar las tablas de historias:", exc)

    # ================================================================
    # STORY MEMORY · persistencia pura (la LÓGICA vive en
    # core/story_memory.py, igual que episodic_memory con los episodios)
    # ================================================================
    def create_story(self, *, story_type, story_key, title="", summary="",
                     status="active", emotional_significance=0.0,
                     confidence=0.5, current_state="", motivation="",
                     now=None):
        """Crea una historia. Si la clave ya existe NO duplica: devuelve la suya.

        La unicidad de `story_key` es la primera barrera antideduplicado; la
        segunda (reconocer que «Andre» y «Andrea» son la misma) vive arriba.
        """
        ahora = time.time() if now is None else float(now)
        clave = str(story_key or "").strip().lower()
        if not clave:
            return None
        try:
            with closing(self._conn()) as connection:
                fila = connection.execute(
                    "SELECT id FROM memory_stories WHERE story_key=?",
                    (clave,)).fetchone()
                if fila:
                    return int(fila["id"])
                cursor = connection.execute(
                    "INSERT INTO memory_stories(story_type,story_key,title,status,"
                    "summary,current_state,emotional_significance,confidence,"
                    "motivation,progress,mention_count,created_at,updated_at,"
                    "last_evidence_at) VALUES(?,?,?,?,?,?,?,?,?,NULL,1,?,?,?)",
                    (str(story_type or "other"), clave, str(title or "")[:120],
                     str(status or "active"), str(summary or "")[:600],
                     str(current_state or "")[:200],
                     _clamp01(emotional_significance), _clamp01(confidence, 0.5),
                     (str(motivation)[:200] if motivation else None),
                     ahora, ahora, ahora))
                connection.commit()
                return int(cursor.lastrowid)
        except Exception as exc:
            print("[story-memory] no pude crear la historia:", exc)
            return None

    def get_story(self, story_id):
        try:
            with closing(self._conn()) as connection:
                fila = connection.execute(
                    "SELECT * FROM memory_stories WHERE id=?",
                    (int(story_id),)).fetchone()
            return dict(fila) if fila else None
        except Exception:
            return None

    def find_story_by_key(self, story_key):
        """La consulta que evita duplicados. Clave normalizada, sin tildes."""
        try:
            with closing(self._conn()) as connection:
                fila = connection.execute(
                    "SELECT * FROM memory_stories WHERE story_key=?",
                    (str(story_key or "").strip().lower(),)).fetchone()
            return dict(fila) if fila else None
        except Exception:
            return None

    def update_story(self, story_id, *, now=None, **campos):
        """Actualiza SOLO los campos indicados. Ignora los desconocidos."""
        permitidos = {
            "story_type", "title", "status", "summary", "current_state",
            "emotional_significance", "confidence", "mention_count",
            "last_evidence_at", "motivation", "progress",
        }
        datos = {k: v for k, v in campos.items() if k in permitidos and v is not None}
        if not datos:
            return False
        for clave in ("emotional_significance", "confidence"):
            if clave in datos:
                datos[clave] = _clamp01(datos[clave], 0.5)
        if "progress" in datos:
            datos["progress"] = _clamp01(datos["progress"], 0.0)
        datos["updated_at"] = time.time() if now is None else float(now)
        sets = ", ".join(f"{k}=?" for k in datos)
        try:
            with closing(self._conn()) as connection:
                cursor = connection.execute(
                    f"UPDATE memory_stories SET {sets} WHERE id=?",
                    tuple(datos.values()) + (int(story_id),))
                connection.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print("[story-memory] no pude actualizar la historia:", exc)
            return False

    def touch_story(self, story_id, *, now=None, evidence=True):
        """Marca que la historia volvió a aparecer (una mención más)."""
        ahora = time.time() if now is None else float(now)
        try:
            with closing(self._conn()) as connection:
                if evidence:
                    connection.execute(
                        "UPDATE memory_stories SET mention_count="
                        "COALESCE(mention_count,0)+1, updated_at=?, "
                        "last_evidence_at=? WHERE id=?",
                        (ahora, ahora, int(story_id)))
                else:
                    connection.execute(
                        "UPDATE memory_stories SET mention_count="
                        "COALESCE(mention_count,0)+1, updated_at=? WHERE id=?",
                        (ahora, int(story_id)))
                connection.commit()
                return True
        except Exception:
            return False

    def add_story_event(self, story_id, *, source_type="", source_id=None,
                        source_message_id=None, event_type="", summary="",
                        user_feeling="", significance=0.0, intensity=0.0,
                        confidence=0.5, unresolved=False, yue_action="",
                        happened_at=None, now=None):
        """Añade un acontecimiento a la historia. NUNCA pisa los anteriores.

        Si ya hay un evento con el mismo (source_type, source_id) NO se
        duplica: se devuelve el que ya estaba. Así un mismo episodio emocional
        no entra dos veces aunque la consolidación lo mire varias veces.
        """
        ahora = time.time() if now is None else float(now)
        try:
            with closing(self._conn()) as connection:
                if source_type and source_id is not None:
                    fila = connection.execute(
                        "SELECT id FROM story_events WHERE story_id=? AND "
                        "source_type=? AND source_id=?",
                        (int(story_id), str(source_type), int(source_id))).fetchone()
                    if fila:
                        return int(fila["id"])
                cursor = connection.execute(
                    "INSERT INTO story_events(story_id,source_type,source_id,"
                    "source_message_id,event_type,summary,user_feeling,"
                    "significance,intensity,confidence,unresolved,yue_action,"
                    "happened_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (int(story_id), str(source_type or ""),
                     None if source_id is None else int(source_id),
                     None if source_message_id is None else int(source_message_id),
                     str(event_type or "")[:40], str(summary or "")[:300],
                     str(user_feeling or "")[:40], _clamp01(significance),
                     _clamp01(intensity),
                     _clamp01(confidence, 0.5), 1 if unresolved else 0,
                     str(yue_action or "")[:80],
                     ahora if happened_at is None else float(happened_at), ahora))
                connection.execute(
                    "UPDATE memory_stories SET updated_at=?, last_evidence_at=? "
                    "WHERE id=?", (ahora, ahora, int(story_id)))
                connection.commit()
                return int(cursor.lastrowid)
        except Exception as exc:
            print("[story-memory] no pude añadir el acontecimiento:", exc)
            return None

    def get_story_events(self, story_id, limit=20, only_unresolved=False):
        """Acontecimientos en orden CRONOLÓGICO (viejo → nuevo)."""
        try:
            consulta = "SELECT * FROM story_events WHERE story_id=?"
            if only_unresolved:
                consulta += " AND unresolved=1"
            consulta += " ORDER BY happened_at ASC, id ASC LIMIT ?"
            with closing(self._conn()) as connection:
                filas = connection.execute(
                    consulta, (int(story_id), int(limit))).fetchall()
            return [dict(f) for f in filas]
        except Exception:
            return []

    def resolve_story_event(self, event_id, *, yue_action=None, now=None):
        """Cierra un acontecimiento SIN borrarlo: `unresolved` pasa a 0.

        Esto es lo que permite que la discusión de agosto siga en la historia
        cuando en septiembre llega la reconciliación.
        """
        ahora = time.time() if now is None else float(now)
        try:
            with closing(self._conn()) as connection:
                cursor = connection.execute(
                    "UPDATE story_events SET unresolved=0, resolved_at=? "
                    "WHERE id=?", (ahora, int(event_id)))
                if yue_action:
                    connection.execute(
                        "UPDATE story_events SET yue_action=? WHERE id=? AND "
                        "(yue_action IS NULL OR yue_action='')",
                        (str(yue_action)[:80], int(event_id)))
                connection.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print("[story-memory] no pude resolver el acontecimiento:", exc)
            return False

    def set_story_event_action(self, event_id, action, only_if_empty=True):
        """Anota QUÉ hizo YUE en ese momento de la historia."""
        try:
            with closing(self._conn()) as connection:
                if only_if_empty:
                    cursor = connection.execute(
                        "UPDATE story_events SET yue_action=? WHERE id=? AND "
                        "(yue_action IS NULL OR yue_action='')",
                        (str(action or "")[:80], int(event_id)))
                else:
                    cursor = connection.execute(
                        "UPDATE story_events SET yue_action=? WHERE id=?",
                        (str(action or "")[:80], int(event_id)))
                connection.commit()
                return cursor.rowcount > 0
        except Exception:
            return False

    def upsert_story_entity(self, story_id, *, entity_type="person", name="",
                            normalized_name="", relation=None,
                            relation_confidence=0.0, confidence=0.5, now=None):
        """Crea o refuerza una entidad de la historia.

        Reglas al reencontrarla:
          * `last_seen` y `mention_count` siempre se actualizan.
          * `relation` SOLO se pisa si la nueva evidencia es MÁS fiable. Un
            «creo que estudia conmigo» (0.55) no puede borrar un «es mi mejor
            amiga de la universidad» (0.92).
        """
        ahora = time.time() if now is None else float(now)
        clave = str(normalized_name or "").strip().lower()
        if not clave:
            return None
        try:
            with closing(self._conn()) as connection:
                fila = connection.execute(
                    "SELECT * FROM story_entities WHERE story_id=? AND "
                    "entity_type=? AND normalized_name=?",
                    (int(story_id), str(entity_type), clave)).fetchone()
                if fila is None:
                    cursor = connection.execute(
                        "INSERT INTO story_entities(story_id,entity_type,name,"
                        "normalized_name,relation,relation_confidence,confidence,"
                        "mention_count,first_seen,last_seen) "
                        "VALUES(?,?,?,?,?,?,?,1,?,?)",
                        (int(story_id), str(entity_type), str(name or "")[:80],
                         clave, (str(relation)[:60] if relation else None),
                         _clamp01(relation_confidence), _clamp01(confidence, 0.5),
                         ahora, ahora))
                    connection.commit()
                    return int(cursor.lastrowid)

                nueva_rel = fila["relation"]
                nueva_rel_conf = float(fila["relation_confidence"] or 0.0)
                if relation and _clamp01(relation_confidence) >= nueva_rel_conf:
                    nueva_rel = str(relation)[:60]
                    nueva_rel_conf = _clamp01(relation_confidence)
                # La confianza en la entidad sube con la corroboración, nunca baja.
                conf = max(float(fila["confidence"] or 0.0), _clamp01(confidence, 0.5))
                connection.execute(
                    "UPDATE story_entities SET name=?, relation=?, "
                    "relation_confidence=?, confidence=?, "
                    "mention_count=COALESCE(mention_count,0)+1, last_seen=? "
                    "WHERE id=?",
                    (str(name or fila["name"] or "")[:80], nueva_rel,
                     nueva_rel_conf, conf, ahora, int(fila["id"])))
                connection.commit()
                return int(fila["id"])
        except Exception as exc:
            print("[story-memory] no pude guardar la entidad:", exc)
            return None

    def get_story_entities(self, story_id):
        try:
            with closing(self._conn()) as connection:
                filas = connection.execute(
                    "SELECT * FROM story_entities WHERE story_id=? "
                    "ORDER BY mention_count DESC, id ASC",
                    (int(story_id),)).fetchall()
            return [dict(f) for f in filas]
        except Exception:
            return []

    def find_stories_by_entity(self, normalized_name, entity_type=None, limit=5):
        """Historias en las que aparece esa entidad. La otra vía antiduplicado."""
        clave = str(normalized_name or "").strip().lower()
        if not clave:
            return []
        try:
            consulta = ("SELECT s.* FROM memory_stories s "
                        "JOIN story_entities e ON e.story_id=s.id "
                        "WHERE e.normalized_name=?")
            parametros = [clave]
            if entity_type:
                consulta += " AND e.entity_type=?"
                parametros.append(str(entity_type))
            consulta += " ORDER BY s.updated_at DESC LIMIT ?"
            parametros.append(int(limit))
            with closing(self._conn()) as connection:
                filas = connection.execute(consulta, tuple(parametros)).fetchall()
            return [dict(f) for f in filas]
        except Exception:
            return []

    def get_active_stories(self, limit=20, story_type=None, now=None):
        """Historias vivas (`active`/`dormant`), de la más reciente a la más vieja."""
        try:
            consulta = ("SELECT * FROM memory_stories WHERE status IN "
                        "('active','dormant')")
            parametros = []
            if story_type:
                consulta += " AND story_type=?"
                parametros.append(str(story_type))
            consulta += (" ORDER BY emotional_significance DESC, "
                         "updated_at DESC LIMIT ?")
            parametros.append(int(limit))
            with closing(self._conn()) as connection:
                filas = connection.execute(consulta, tuple(parametros)).fetchall()
            return [dict(f) for f in filas]
        except Exception:
            return []

    def story_candidates(self, limit=40):
        """Todas las historias no archivadas: materia prima de la recuperación."""
        try:
            with closing(self._conn()) as connection:
                filas = connection.execute(
                    "SELECT * FROM memory_stories WHERE status!='archived' "
                    "ORDER BY updated_at DESC LIMIT ?", (int(limit),)).fetchall()
            return [dict(f) for f in filas]
        except Exception:
            return []

    def count_stories(self, status=None):
        try:
            with closing(self._conn()) as connection:
                if status:
                    fila = connection.execute(
                        "SELECT COUNT(*) AS n FROM memory_stories WHERE status=?",
                        (str(status),)).fetchone()
                else:
                    fila = connection.execute(
                        "SELECT COUNT(*) AS n FROM memory_stories").fetchone()
            return int(fila["n"]) if fila else 0
        except Exception:
            return 0

    def add_message(self, role, content):
        with closing(self._conn()) as connection:
            connection.execute(
                "INSERT INTO messages(role,content,ts) VALUES(?,?,?)",
                (role, content, time.time()),
            )
            connection.commit()

    def last_message_id(self):
        """Id del último mensaje insertado. None si aún no hay ninguno.

        Lo usa la memoria episódica para dejar constancia de QUÉ mensaje originó
        un recuerdo, sin tener que duplicar su texto.
        """
        try:
            with closing(self._conn()) as connection:
                fila = connection.execute(
                    "SELECT id FROM messages ORDER BY id DESC LIMIT 1").fetchone()
            return int(fila["id"]) if fila else None
        except Exception:
            return None

    def recent_messages(self, n=12):
        with closing(self._conn()) as connection:
            rows = connection.execute(
                "SELECT role,content FROM messages ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    def add_fact(self, text):
        with closing(self._conn()) as connection:
            connection.execute("INSERT INTO facts(text,ts) VALUES(?,?)", (text, time.time()))
            connection.commit()

    def get_facts(self, n=20):
        with closing(self._conn()) as connection:
            rows = connection.execute(
                "SELECT text FROM facts ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
        return [row["text"] for row in rows]

    # ---------- memoria a largo plazo (resúmenes consolidados) ----------
    def messages_between(self, ts_inicio, ts_fin):
        """Mensajes crudos en el rango [ts_inicio, ts_fin], orden cronológico.

        Mismo patrón que recent_messages pero filtrando por ts en vez de LIMIT.
        Lo usa la consolidación para resumir un periodo.

        NUEVO (trazabilidad): además de `role` y `content` se devuelven `id` y
        `ts`. Es ADITIVO —las claves de siempre están intactas y en el mismo
        sitio—, y permite que Story Memory guarde de QUÉ mensaje salió una
        afirmación (`source_message_id`) en vez de copiar su texto.
        """
        with closing(self._conn()) as connection:
            rows = connection.execute(
                "SELECT id,role,content,ts FROM messages WHERE ts>=? AND ts<=? "
                "ORDER BY id ASC",
                (float(ts_inicio), float(ts_fin)),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"],
                 "id": row["id"], "ts": row["ts"]} for row in rows]

    def add_long_term_summary(self, periodo_inicio, periodo_fin, resumen):
        """Guarda un resumen consolidado de un periodo. Aditivo: no borra nada."""
        with closing(self._conn()) as connection:
            connection.execute(
                "INSERT INTO memoria_larga(periodo_inicio,periodo_fin,resumen,ts) "
                "VALUES(?,?,?,?)",
                (float(periodo_inicio), float(periodo_fin), str(resumen), time.time()),
            )
            connection.commit()

    def get_long_term_summaries(self, n=5):
        """Últimos N resúmenes consolidados, en orden CRONOLÓGICO (viejo→nuevo),
        para que entren al prompt como 'de hace tiempo recuerdas: …'."""
        with closing(self._conn()) as connection:
            rows = connection.execute(
                "SELECT resumen FROM memoria_larga ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
        return [row["resumen"] for row in reversed(rows)]

    def add_goal(self, text):
        with closing(self._conn()) as connection:
            connection.execute("INSERT INTO goals(text,created) VALUES(?,?)", (text, time.time()))
            connection.commit()

    def list_goals(self, only_active=True):
        query = "SELECT id,text,status FROM goals"
        if only_active:
            query += " WHERE status='activa'"
        query += " ORDER BY id"
        with closing(self._conn()) as connection:
            return [dict(row) for row in connection.execute(query).fetchall()]

    def complete_goal(self, goal_id):
        with closing(self._conn()) as connection:
            connection.execute(
                "UPDATE goals SET status='completada',done=? WHERE id=?",
                (time.time(), goal_id),
            )
            connection.commit()

    # ---------- rutinas guardadas (planes de pc_control reutilizables) ----------
    def add_routine(self, nombre, pasos):
        """Guarda (o reemplaza) una rutina: un plan de acciones bajo un nombre.

        `pasos` es la lista de acciones tal cual la ejecutó pc_control. El nombre
        es único sin distinguir mayúsculas/acentos: re-guardar con el mismo
        nombre actualiza la rutina en vez de duplicarla.
        """
        nombre = (nombre or "").strip()
        if not nombre:
            raise ValueError("La rutina necesita un nombre.")
        pasos_json = json.dumps(pasos or [], ensure_ascii=False)
        with closing(self._conn()) as connection:
            connection.execute(
                "DELETE FROM routines WHERE lower(nombre)=lower(?)", (nombre,)
            )
            connection.execute(
                "INSERT INTO routines(nombre,pasos_json,creado) VALUES(?,?,?)",
                (nombre, pasos_json, time.time()),
            )
            connection.commit()

    def get_routine(self, nombre):
        """Devuelve {id, nombre, pasos, creado} o None. Coincide sin acentos/caso."""
        nombre = (nombre or "").strip()
        if not nombre:
            return None
        with closing(self._conn()) as connection:
            row = connection.execute(
                "SELECT id,nombre,pasos_json,creado FROM routines "
                "WHERE lower(nombre)=lower(?) ORDER BY id DESC LIMIT 1",
                (nombre,),
            ).fetchone()
        if not row:
            return None
        try:
            pasos = json.loads(row["pasos_json"])
        except Exception:
            pasos = []
        return {"id": row["id"], "nombre": row["nombre"], "pasos": pasos, "creado": row["creado"]}

    def list_routines(self):
        """Lista las rutinas: [{id, nombre, pasos (nº), creado}], por nombre."""
        with closing(self._conn()) as connection:
            rows = connection.execute(
                "SELECT id,nombre,pasos_json,creado FROM routines ORDER BY nombre"
            ).fetchall()
        salida = []
        for row in rows:
            try:
                n = len(json.loads(row["pasos_json"]))
            except Exception:
                n = 0
            salida.append({
                "id": row["id"], "nombre": row["nombre"],
                "pasos": n, "creado": row["creado"],
            })
        return salida

    def delete_routine(self, nombre):
        """Borra una rutina por nombre. Devuelve True si borró algo."""
        nombre = (nombre or "").strip()
        if not nombre:
            return False
        with closing(self._conn()) as connection:
            cur = connection.execute(
                "DELETE FROM routines WHERE lower(nombre)=lower(?)", (nombre,)
            )
            connection.commit()
            return cur.rowcount > 0

    # ---------- bitácora de actividades ----------
    def add_activity(self, tipo, detalle, con_usuario, origen, dedup_seg=60.0):
        """Registra una actividad breve. Solo una descripción corta, sin contenido
        sensible ni archivos.

        Lleva un pequeño antirrebote: ignora la MISMA actividad (mismo tipo,
        origen y detalle) si se repite en pocos segundos, para que reacciones
        continuas (p. ej. a la música) no inflen la bitácora.
        """
        tipo = str(tipo or "").strip().lower()
        detalle = str(detalle or "").strip()[:300]
        origen = str(origen or "").strip()[:60]
        con = 1 if con_usuario else 0
        ahora = time.time()
        with closing(self._conn()) as connection:
            if dedup_seg and dedup_seg > 0:
                row = connection.execute(
                    "SELECT ts FROM activity_log WHERE tipo=? AND origen=? AND detalle=? "
                    "ORDER BY id DESC LIMIT 1",
                    (tipo, origen, detalle),
                ).fetchone()
                if row and (ahora - float(row["ts"])) < float(dedup_seg):
                    return
            connection.execute(
                "INSERT INTO activity_log(ts,tipo,detalle,con_usuario,origen) "
                "VALUES(?,?,?,?,?)",
                (ahora, tipo, detalle, con, origen),
            )
            connection.commit()

    def get_recent_activities(self, n=20, solo_con_usuario=None):
        """Últimas actividades (más recientes primero).

        solo_con_usuario: None = todas; True = solo las compartidas con el
        usuario; False = solo las que YUE hizo sola.
        """
        query = "SELECT id,ts,tipo,detalle,con_usuario,origen FROM activity_log"
        params = []
        if solo_con_usuario is not None:
            query += " WHERE con_usuario=?"
            params.append(1 if solo_con_usuario else 0)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(int(n))
        with closing(self._conn()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {
                "id": r["id"], "ts": r["ts"], "tipo": r["tipo"],
                "detalle": r["detalle"], "con_usuario": bool(r["con_usuario"]),
                "origen": r["origen"],
            }
            for r in rows
        ]

    def get_activity_summary(self, dias=7):
        """Resumen legible en español de la actividad reciente.

        Ej.: "esta semana escuchamos música tres veces, vi dos videos que me
        pediste y armé un plan por mi cuenta el martes". "" si no hay nada.
        """
        try:
            dias = max(1, int(dias))
        except (TypeError, ValueError):
            dias = 7
        desde = time.time() - dias * 86400.0
        with closing(self._conn()) as connection:
            rows = connection.execute(
                "SELECT ts,tipo,con_usuario FROM activity_log WHERE ts>=? ORDER BY ts",
                (desde,),
            ).fetchall()
        if not rows:
            return ""

        conteo = defaultdict(int)     # (tipo, con_usuario bool) -> n
        ult_ts = {}                   # (tipo, con_usuario bool) -> ultimo ts
        for r in rows:
            key = (str(r["tipo"] or ""), bool(r["con_usuario"]))
            conteo[key] += 1
            ult_ts[key] = float(r["ts"])
        if not conteo:
            return ""

        def cw(n):  # palabra del número para n>=2
            return _NUM_PALABRA.get(n, str(n))

        def dia_de(ts):
            return _DIAS_SEMANA[time.localtime(ts).tm_wday]

        partes = []

        def add(key, singular, plural, sufijo="", con_dia=False):
            if key not in conteo:
                return
            n = conteo[key]
            frase = singular if n == 1 else plural.format(n=cw(n))
            if sufijo:
                frase += " " + sufijo
            if con_dia:
                frase += f" el {dia_de(ult_ts[key])}"
            partes.append(frase)

        # Orden natural. Música y búsquedas web se juntan sin distinguir origen.
        # --- música ---
        n_mus_c = conteo.get(("musica", True), 0)
        n_mus_s = conteo.get(("musica", False), 0)
        if n_mus_c:
            partes.append("escuchamos música una vez" if n_mus_c == 1
                          else f"escuchamos música {cw(n_mus_c)} veces")
        if n_mus_s:
            partes.append("puse música por mi cuenta una vez" if n_mus_s == 1
                          else f"puse música {cw(n_mus_s)} veces por mi cuenta")
        # --- video ---
        add(("video", True), "vi un video que me pediste", "vi {n} videos que me pediste")
        add(("video", False), "vi un video por mi cuenta", "vi {n} videos por mi cuenta")
        # --- imagen ---
        add(("imagen", True), "generé una imagen para ti", "generé {n} imágenes para ti")
        add(("imagen", False), "generé una imagen por mi cuenta", "generé {n} imágenes por mi cuenta")
        # --- búsquedas web (se suman ambas) ---
        n_web = conteo.get(("busqueda_web", True), 0) + conteo.get(("busqueda_web", False), 0)
        if n_web:
            partes.append("hice una búsqueda web" if n_web == 1 else f"hice {cw(n_web)} búsquedas web")
        # --- control del PC ---
        add(("control_pc", True), "controlé tu PC una vez", "controlé tu PC {n} veces")
        add(("control_pc", False), "toqué el PC por mi cuenta una vez", "toqué el PC {n} veces por mi cuenta")
        # --- clases ---
        n_clase = conteo.get(("clase", True), 0) + conteo.get(("clase", False), 0)
        if n_clase:
            partes.append("dimos una clase" if n_clase == 1 else f"dimos {cw(n_clase)} clases")
        # --- autonomía (lo que hizo sola): al final y con el día ---
        add(("autonomia", False), "armé un plan por mi cuenta", "armé {n} planes por mi cuenta", con_dia=True)
        add(("autonomia", True), "armamos un plan juntos", "armamos {n} planes juntos")

        if not partes:
            return ""

        prefijo = "esta semana" if dias == 7 else ("hoy" if dias == 1 else f"en los últimos {dias} días")
        if len(partes) == 1:
            cuerpo = partes[0]
        else:
            cuerpo = ", ".join(partes[:-1]) + " y " + partes[-1]
        return f"{prefijo} {cuerpo}."

    def get_bond_points(self):
        with closing(self._conn()) as connection:
            row = connection.execute("SELECT value FROM state WHERE key='bond'").fetchone()
        return int(row["value"]) if row else 0

    def add_bond_points(self, points):
        new_value = self.get_bond_points() + points
        with closing(self._conn()) as connection:
            connection.execute(
                "INSERT INTO state(key,value) VALUES('bond',?) "
                "ON CONFLICT(key) DO UPDATE SET value=?",
                (str(new_value), str(new_value)),
            )
            connection.commit()
        return new_value

    # ---------- memoria multimedia ----------
    @staticmethod
    def _media_key(text):
        import unicodedata
        value = unicodedata.normalize("NFD", str(text or "").lower())
        value = "".join(c for c in value if unicodedata.category(c) != "Mn")
        import re
        return re.sub(r"\s+", " ", value).strip()

    def start_multimedia_session(self, source="audio_sistema", app="", title="",
                                 media_type="indefinido"):
        """Abre una sesión multimedia y devuelve su id."""
        now = time.time()
        with closing(self._conn()) as connection:
            cur = connection.execute(
                "INSERT INTO multimedia_sessions(started,source,app,title,media_type) "
                "VALUES(?,?,?,?,?)",
                (now, str(source or ""), str(app or ""), str(title or "")[:240],
                 str(media_type or "indefinido")),
            )
            connection.commit()
            return int(cur.lastrowid)

    def update_multimedia_session(self, session_id, source=None, app=None,
                                  title=None, media_type=None):
        fields, values = [], []
        for name, value in (("source", source), ("app", app), ("title", title),
                            ("media_type", media_type)):
            if value is not None:
                fields.append(f"{name}=?")
                values.append(str(value)[:240])
        if not fields:
            return
        values.append(int(session_id))
        with closing(self._conn()) as connection:
            connection.execute(
                "UPDATE multimedia_sessions SET " + ",".join(fields) + " WHERE id=?",
                tuple(values),
            )
            connection.commit()

    def end_multimedia_session(self, session_id, dominant_emotion="neutral",
                               peak_intensity=0.0, summary=""):
        try:
            peak = max(0.0, min(1.0, float(peak_intensity)))
        except (TypeError, ValueError):
            peak = 0.0
        with closing(self._conn()) as connection:
            connection.execute(
                "UPDATE multimedia_sessions SET ended=?,dominant_emotion=?,"
                "peak_intensity=?,summary=? WHERE id=?",
                (time.time(), str(dominant_emotion or "neutral"), peak,
                 str(summary or "")[:600], int(session_id)),
            )
            connection.commit()

    def add_multimedia_event(self, session_id, event_kind, label="", emotion="neutral",
                             intensity=0.0, detail="", favorite=False, comment=""):
        try:
            level = max(0.0, min(1.0, float(intensity)))
        except (TypeError, ValueError):
            level = 0.0
        with closing(self._conn()) as connection:
            cur = connection.execute(
                "INSERT INTO multimedia_events(session_id,ts,event_kind,label,emotion,"
                "intensity,detail,favorite,comment) VALUES(?,?,?,?,?,?,?,?,?)",
                (int(session_id) if session_id is not None else None, time.time(),
                 str(event_kind or ""), str(label or "")[:120],
                 str(emotion or "neutral"), level, str(detail or "")[:700],
                 1 if favorite else 0, str(comment or "")[:300]),
            )
            connection.commit()
            return int(cur.lastrowid)

    def remember_multimedia_item(self, media_kind, title, source="", app="",
                                 emotion="neutral", intensity=0.0, session_id=None,
                                 metadata=None):
        """Inserta/actualiza un título sin guardar contenido multimedia."""
        title = str(title or "").strip()
        if not title:
            return None
        key = self._media_key(title)
        now = time.time()
        try:
            level = max(0.0, min(1.0, float(intensity)))
        except (TypeError, ValueError):
            level = 0.0
        meta = json.dumps(metadata or {}, ensure_ascii=False)
        args = (str(media_kind or "contenido"), title[:240], key[:240],
                str(source or "")[:80], str(app or "")[:100])
        with closing(self._conn()) as connection:
            row = connection.execute(
                "SELECT id,times_seen,preference_score FROM multimedia_items "
                "WHERE media_kind=? AND title_key=? AND source=? AND app=?",
                (args[0], args[2], args[3], args[4]),
            ).fetchone()
            if row:
                # La repetición aumenta suavemente la afinidad implícita, pero nunca
                # suplanta una preferencia explícita del usuario.
                score = min(1.0, float(row["preference_score"] or 0.0) + 0.04)
                connection.execute(
                    "UPDATE multimedia_items SET title=?,last_seen=?,times_seen=?,"
                    "last_emotion=?,last_intensity=?,preference_score=?,"
                    "last_session_id=?,metadata_json=? WHERE id=?",
                    (title[:240], now, int(row["times_seen"] or 0) + 1,
                     str(emotion or "neutral"), level, score,
                     int(session_id) if session_id is not None else None,
                     meta, int(row["id"])),
                )
                item_id = int(row["id"])
            else:
                cur = connection.execute(
                    "INSERT INTO multimedia_items(media_kind,title,title_key,source,app,"
                    "first_seen,last_seen,times_seen,last_emotion,last_intensity,"
                    "preference_score,explicit_preference,last_session_id,metadata_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    args + (now, now, 1, str(emotion or "neutral"), level,
                            0.0, 0, int(session_id) if session_id is not None else None,
                            meta),
                )
                item_id = int(cur.lastrowid)
            connection.commit()
            return item_id

    def recent_multimedia_items(self, n=10, media_kind=None, since=None, until=None):
        where, values = ["title<>''"], []
        if media_kind:
            where.append("media_kind=?")
            values.append(str(media_kind))
        if since is not None:
            where.append("last_seen>=?")
            values.append(float(since))
        if until is not None:
            where.append("last_seen<?")
            values.append(float(until))
        values.append(max(1, int(n)))
        query = (
            "SELECT id,media_kind,title,source,app,first_seen,last_seen,times_seen,"
            "last_emotion,last_intensity,preference_score,explicit_preference,"
            "last_session_id FROM multimedia_items WHERE " + " AND ".join(where) +
            " ORDER BY last_seen DESC LIMIT ?"
        )
        with closing(self._conn()) as connection:
            rows = connection.execute(query, tuple(values)).fetchall()
        return [dict(row) for row in rows]

    def resolve_multimedia_reference(self, text):
        """Resuelve «la canción de ayer/anterior» contra la memoria reciente."""
        import datetime as _dt
        low = self._media_key(text)
        reference = any(token in low for token in (
            "de ayer", "anoche", "anterior", "ultima cancion", "ultimo video",
            "de hace rato", "que escuchamos", "que vimos", "la de antes",
        ))
        if not reference:
            return None
        kind = None
        if any(k in low for k in ("cancion", "musica", "tema", "opening")):
            kind = "musica"
        elif any(k in low for k in ("video", "pelicula", "serie", "escena")):
            kind = "video"
        since = until = None
        now = _dt.datetime.now()
        if "ayer" in low or "anoche" in low:
            day = (now - _dt.timedelta(days=1)).date()
            since = _dt.datetime.combine(day, _dt.time.min).timestamp()
            until = _dt.datetime.combine(day + _dt.timedelta(days=1), _dt.time.min).timestamp()
        items = self.recent_multimedia_items(20, media_kind=kind, since=since, until=until)
        if not items and (since is not None or kind is not None):
            items = self.recent_multimedia_items(20, media_kind=kind)
        return items[0] if items else None

    def learn_multimedia_preference(self, text):
        """Aprende gustos explícitos referidos al contenido actual o reciente."""
        low = self._media_key(text)
        positive = any(p in low for p in (
            "me gusta", "me encanto", "me fascina", "mi favorita", "mi favorito",
            "esta buenisima", "esta buenisimo", "me gusto esa parte",
        ))
        negative = any(p in low for p in (
            "no me gusta", "no me gusto", "la odio", "lo odio", "que aburrida",
            "que aburrido", "no la pongas", "no lo pongas",
        ))
        if not positive and not negative:
            return None
        # Las frases negativas contienen a veces una positiva como subcadena
        # ("no me gusta" contiene "me gusta"); la negación tiene prioridad.
        if negative:
            positive = False
        items = self.recent_multimedia_items(1)
        if not items:
            return None
        item = items[0]
        score = 1.0 if positive else -1.0
        with closing(self._conn()) as connection:
            connection.execute(
                "UPDATE multimedia_items SET preference_score=?,explicit_preference=1 "
                "WHERE id=?", (score, int(item["id"])),
            )
            if positive and "parte" in low:
                connection.execute(
                    "UPDATE multimedia_events SET favorite=1 WHERE id=(SELECT id FROM "
                    "multimedia_events ORDER BY ts DESC LIMIT 1)"
                )
            connection.commit()
        item["preference_score"] = score
        item["explicit_preference"] = 1
        return item

    def multimedia_context(self, n=5):
        items = self.recent_multimedia_items(n)
        if not items:
            return ""
        parts = []
        for item in items:
            kind = "canción" if item["media_kind"] == "musica" else "video"
            suffix = ""
            score = float(item.get("preference_score") or 0.0)
            if int(item.get("explicit_preference") or 0):
                suffix = " (le gustó)" if score > 0 else " (no le gustó)"
            parts.append(f"{kind}: {item['title']}{suffix}")
        return "Contenido multimedia reciente: " + "; ".join(parts) + "."

    # ---------- estado generico clave/valor ----------
    def get_state(self, key, default=None):
        with closing(self._conn()) as connection:
            row = connection.execute(
                "SELECT value FROM state WHERE key=?", (str(key),)
            ).fetchone()
        return row["value"] if row else default

    def set_state(self, key, value):
        with closing(self._conn()) as connection:
            connection.execute(
                "INSERT INTO state(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=?",
                (str(key), str(value), str(value)),
            )
            connection.commit()

    # ---------- señales para el recordatorio de bienestar ----------
    def add_risk_event(self):
        """Registra que el detector de riesgo textual salto ahora (solo la hora)."""
        with closing(self._conn()) as connection:
            connection.execute(
                "INSERT INTO risk_events(ts) VALUES(?)", (time.time(),)
            )
            connection.commit()

    def count_risk_events(self, dias=7):
        """Cuantas veces salto el riesgo textual en los ultimos `dias` dias."""
        try:
            dias = max(1, int(dias))
        except (TypeError, ValueError):
            dias = 7
        desde = time.time() - dias * 86400.0
        with closing(self._conn()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM risk_events WHERE ts>=?", (desde,)
            ).fetchone()
        return int(row["n"]) if row else 0

    def session_span_seconds(self, max_gap=1800.0, limit=400):
        """Duracion de la sesion CONTINUA que termina en el ultimo mensaje.

        Recorre los mensajes del mas nuevo al mas viejo y corta en cuanto hay un
        hueco mayor que `max_gap` (por defecto 30 min): eso marca el fin de la
        sesion. Devuelve los segundos entre el primer y el ultimo mensaje de esa
        racha continua. Sirve para detectar uso muy intensivo (sesiones largas).
        """
        try:
            max_gap = float(max_gap)
        except (TypeError, ValueError):
            max_gap = 1800.0
        with closing(self._conn()) as connection:
            rows = connection.execute(
                "SELECT ts FROM messages WHERE ts IS NOT NULL ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        marcas = [float(r["ts"]) for r in rows]  # del mas nuevo al mas viejo
        if len(marcas) < 2:
            return 0.0
        mas_nuevo = marcas[0]
        inicio_sesion = marcas[0]
        anterior = marcas[0]
        for t in marcas[1:]:
            if (anterior - t) > max_gap:
                break
            inicio_sesion = t
            anterior = t
        return max(0.0, mas_nuevo - inicio_sesion)

    # ---------- historial emocional (mood_log) ----------
    def add_mood(self, fuente, emocion, intensidad, texto_origen=None):
        """Registra un momento emocional en el historial de animo.

        fuente: 'texto' (lo que escribe el usuario) o 'camara' (expresion leida
        por la camara). Para 'camara' NO se guarda ninguna imagen ni dato
        identificable: solo la etiqueta emocional, su intensidad y la hora.
        """
        try:
            intensidad = float(intensidad)
        except (TypeError, ValueError):
            intensidad = 0.0
        intensidad = max(0.0, min(1.0, intensidad))
        with closing(self._conn()) as connection:
            connection.execute(
                "INSERT INTO mood_log(ts,fuente,emocion,intensidad,texto_origen) "
                "VALUES(?,?,?,?,?)",
                (time.time(), str(fuente), str(emocion), intensidad, texto_origen),
            )
            connection.commit()

    def get_mood_summary(self, dias=7):
        """Resumen legible en espanol del animo reciente (frecuencia + intensidad).

        Devuelve algo como "esta semana predomino tranquila y curiosa, con dos
        momentos de preocupacion el martes", o "" si aun no hay registro
        suficiente. Esta pensado como MEMORIA de fondo para YUE, no como informe.
        """
        try:
            dias = max(1, int(dias))
        except (TypeError, ValueError):
            dias = 7
        desde = time.time() - dias * 86400.0
        with closing(self._conn()) as connection:
            rows = connection.execute(
                "SELECT ts,emocion,intensidad FROM mood_log WHERE ts>=? ORDER BY ts",
                (desde,),
            ).fetchall()
        if not rows:
            return ""

        # Normalizamos a un vocabulario comun y acumulamos frecuencia,
        # intensidad y en que dia se concentro cada emocion.
        conteo = defaultdict(int)
        intensidad_total = defaultdict(float)
        por_dia = defaultdict(lambda: defaultdict(int))  # adj -> weekday -> conteo
        for row in rows:
            adj = _MOOD_ADJ.get(str(row["emocion"] or "").strip().lower())
            if not adj:  # neutral u otra etiqueta desconocida: se ignora
                continue
            try:
                inten = float(row["intensidad"])
            except (TypeError, ValueError):
                inten = 0.5
            conteo[adj] += 1
            intensidad_total[adj] += max(0.0, min(1.0, inten))
            por_dia[adj][time.localtime(float(row["ts"])).tm_wday] += 1

        if not conteo:
            return ""

        # Puntuacion que combina cuantas veces aparecio y con que intensidad.
        def _score(adj):
            n = conteo[adj]
            media = intensidad_total[adj] / n if n else 0.0
            return n * (0.5 + media)

        ordenadas = sorted(conteo, key=_score, reverse=True)

        if dias == 7:
            prefijo = "esta semana"
        elif dias == 1:
            prefijo = "hoy"
        else:
            prefijo = f"en los últimos {dias} días"

        dominantes = ordenadas[:2]
        if len(dominantes) == 1:
            cuerpo = f"{prefijo} predominó {dominantes[0]}"
        else:
            cuerpo = f"{prefijo} predominó {dominantes[0]} y {dominantes[1]}"

        # Mencion aparte: la emocion de tono dificil mas presente que no sea ya
        # dominante, con el dia en que mas se concentro.
        resalte = ""
        for adj in ordenadas:
            if adj in dominantes or adj not in _MOOD_NEGATIVAS:
                continue
            n = conteo[adj]
            noun = _MOOD_NOUN.get(adj, adj)
            dias_adj = por_dia[adj]
            wday = max(dias_adj, key=dias_adj.get) if dias_adj else None
            num = _NUM_PALABRA.get(n, str(n))
            palabra = "momento" if n == 1 else "momentos"
            if wday is not None:
                resalte = f", con {num} {palabra} de {noun} el {_DIAS_SEMANA[wday]}"
            else:
                resalte = f", con {num} {palabra} de {noun}"
            break

        return cuerpo + resalte + "."

    # ---------- memoria afectiva estructurada (affect_log) ----------
    #: Categorías de disparador. Se guarda la CATEGORÍA, nunca la frase: así el
    #: recuerdo sirve ("suele bajonearse por temas de estudios") sin conservar
    #: lo que la persona contó, que ya está en el historial de mensajes.
    _TRIGGER_CATEGORIAS = (
        ("trabajo", ("trabajo", "jefe", "oficina", "empleo", "curro", "despid",
                     "contrato", "sueldo", "cliente", "proyecto")),
        ("estudios", ("examen", "clase", "profesor", "universidad", "instituto",
                      "nota", "aprob", "reprob", "suspend", "tarea", "tesis",
                      "entrevista")),
        ("relaciones", ("amig", "novi", "pareja", "familia", "madre", "padre",
                        "herman", "discut", "pelea", "plantad", "vino")),
        ("salud", ("medic", "hospital", "dolor", "enferm", "operacion", "dormir",
                   "cansancio")),
        ("dinero", ("dinero", "plata", "deuda", "pagar", "alquiler", "banco")),
        ("tecnico", ("codigo", "error", "bug", "programa", "computadora", "pc",
                     "archivo", "borr", "servidor")),
    )

    @classmethod
    def _categorizar_trigger(cls, texto):
        """Reduce un disparador a una categoría corta. "" si no encaja en ninguna."""
        base = str(texto or "").lower()
        if not base:
            return ""
        for categoria, claves in cls._TRIGGER_CATEGORIAS:
            if any(k in base for k in claves):
                return categoria
        return "otro"

    def add_affect(self, *, emotion, secondary_emotion="", valence=0.0, arousal=0.0,
                   distress=0.0, support_need="", confidence=0.0, sarcasm=0.0,
                   safety_level=0, trigger="", source="texto"):
        """Registra una lectura afectiva ESTRUCTURADA.

        A diferencia del viejo `add_mood(..., texto_origen=...)`, aquí NO entra
        ni un carácter del mensaje del usuario: solo la interpretación. El
        disparador se guarda categorizado ("estudios", "relaciones"), nunca
        literal. Tolerante a fallos: si algo va mal, no rompe la conversación.
        """
        def _f(valor, minimo, maximo, defecto=0.0):
            try:
                return max(minimo, min(maximo, float(valor)))
            except (TypeError, ValueError):
                return defecto

        try:
            with closing(self._conn()) as connection:
                connection.execute(
                    "INSERT INTO affect_log(ts,emotion,secondary_emotion,valence,"
                    "arousal,distress,support_need,confidence,sarcasm,safety_level,"
                    "trigger_category,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (time.time(), str(emotion or "neutral"),
                     str(secondary_emotion or ""),
                     _f(valence, -1.0, 1.0), _f(arousal, 0.0, 1.0),
                     _f(distress, 0.0, 1.0), str(support_need or ""),
                     _f(confidence, 0.0, 1.0), _f(sarcasm, 0.0, 1.0),
                     int(safety_level or 0),
                     self._categorizar_trigger(trigger), str(source or "texto")),
                )
                connection.commit()
            return True
        except Exception as exc:
            print("[affect-memory] no pude registrar la lectura afectiva:", exc)
            return False

    def recent_affect(self, limite=10):
        """Últimas lecturas afectivas, de la más reciente a la más antigua."""
        try:
            with closing(self._conn()) as connection:
                filas = connection.execute(
                    "SELECT ts,emotion,secondary_emotion,valence,arousal,distress,"
                    "support_need,confidence,sarcasm,safety_level,trigger_category,"
                    "source FROM affect_log ORDER BY ts DESC LIMIT ?",
                    (max(1, int(limite)),),
                ).fetchall()
            return [dict(f) for f in filas]
        except Exception:
            return []

    def affect_trend(self, horas=24):
        """Tendencia afectiva reciente: media de valencia, malestar y dominante.

        Sirve como MEMORIA de fondo, no como informe clínico. Devuelve {} si
        todavía no hay datos suficientes.
        """
        try:
            desde = time.time() - max(1, float(horas)) * 3600.0
            with closing(self._conn()) as connection:
                filas = connection.execute(
                    "SELECT emotion,valence,distress,trigger_category FROM affect_log "
                    "WHERE ts>=? ORDER BY ts", (desde,),
                ).fetchall()
        except Exception:
            return {}
        if len(filas) < 2:
            return {}

        valencias = [float(f["valence"] or 0.0) for f in filas]
        malestares = [float(f["distress"] or 0.0) for f in filas]
        conteo = defaultdict(int)
        causas = defaultdict(int)
        for f in filas:
            emocion = str(f["emotion"] or "")
            if emocion and emocion != "neutral":
                conteo[emocion] += 1
            categoria = str(f["trigger_category"] or "")
            if categoria and categoria != "otro":
                causas[categoria] += 1

        return {
            "n": len(filas),
            "valence_media": round(sum(valencias) / len(valencias), 3),
            "distress_medio": round(sum(malestares) / len(malestares), 3),
            "dominante": max(conteo, key=conteo.get) if conteo else "neutral",
            "causa_frecuente": max(causas, key=causas.get) if causas else "",
        }

    def purge_mood_texts(self):
        """MIGRACIÓN de privacidad: borra los textos guardados en `mood_log`.

        Las versiones anteriores copiaban en `mood_log.texto_origen` el mensaje
        completo del usuario, que ya estaba en `messages`. Era una duplicación
        de información privada sin ninguna utilidad. Esta migración vacía esa
        columna SIN tocar el resto de la fila (hora, emoción e intensidad se
        conservan, así que `get_mood_summary` y `mood_risk_signal` siguen
        funcionando exactamente igual).

        La columna se mantiene en el esquema para no romper bases antiguas ni
        código que aún la lea. Devuelve cuántas filas se limpiaron.
        """
        try:
            with closing(self._conn()) as connection:
                cur = connection.execute(
                    "UPDATE mood_log SET texto_origen=NULL "
                    "WHERE texto_origen IS NOT NULL AND texto_origen<>''"
                )
                limpiadas = cur.rowcount or 0
                connection.commit()
            return int(limpiadas)
        except Exception as exc:
            print("[memoria] no pude limpiar los textos de mood_log:", exc)
            return 0

    def mood_risk_signal(self, dias=1, min_eventos=4, min_ratio=0.5, min_span_seg=3600.0):
        """True si el historial reciente muestra un animo negativo SOSTENIDO.

        Mira mood_log (texto + camara) en la ventana dada y exige, a la vez:
          - varias marcas negativas (min_eventos),
          - que sean fraccion relevante del total registrado (min_ratio),
          - y que esten repartidas en el tiempo (min_span_seg): asi un unico
            bajon puntual no cuenta.
        Pensado como CORROBORACION de la senal visual sostenida, no como gatillo
        por si solo. Es tolerante: si aun no hay datos, devuelve False.
        """
        try:
            dias = max(1, int(dias))
        except (TypeError, ValueError):
            dias = 1
        desde = time.time() - dias * 86400.0
        with closing(self._conn()) as connection:
            rows = connection.execute(
                "SELECT ts,emocion FROM mood_log WHERE ts>=? ORDER BY ts",
                (desde,),
            ).fetchall()
        if not rows:
            return False

        total = 0
        neg_ts = []
        for row in rows:
            adj = _MOOD_ADJ.get(str(row["emocion"] or "").strip().lower())
            if not adj:  # neutral / desconocida: no cuenta
                continue
            total += 1
            if adj in _MOOD_NEGATIVAS:
                neg_ts.append(float(row["ts"]))

        if total <= 0 or len(neg_ts) < int(min_eventos):
            return False
        if (len(neg_ts) / total) < float(min_ratio):
            return False
        return (max(neg_ts) - min(neg_ts)) >= float(min_span_seg)

    # ======================================================================
    # MEMORIA EPISÓDICA EMOCIONAL (emotional_episodes)
    # ======================================================================
    # Esta sección es PERSISTENCIA PURA: crear, leer, actualizar y caducar.
    # Toda la inteligencia (detectar candidatos, extraer, puntuar importancia,
    # deduplicar, decidir cuándo preguntar) vive en `core.episodic_memory`.
    # Mantenerlo separado es lo que evita que memory.py acabe siendo otro
    # cajón de sastre de 3000 líneas.
    #
    # Todos los métodos son tolerantes a fallos: ante cualquier error devuelven
    # un valor neutro y dejan constancia por consola. Una conversación nunca se
    # cae porque la memoria episódica tenga un mal día.

    #: Campos que `update_emotional_episode` acepta. Lista blanca explícita:
    #: así ningún llamador puede inyectar un nombre de columna arbitrario.
    _EPISODE_FIELDS = (
        "source_message_id", "event_type", "event_label", "event_at",
        "date_precision", "emotion", "intensity", "reason_summary", "importance",
        "support_mode", "yue_action", "follow_up_at", "follow_up_state",
        "follow_up_count", "status", "outcome_summary", "outcome_emotion",
        "confidence", "first_mentioned_at", "last_mentioned_at", "mention_count",
    )

    _EPISODE_STATUS = ("unresolved", "resolved", "cancelled", "expired")
    _EPISODE_FOLLOWUP_STATES = ("pending", "asked", "skipped", "cancelled")

    def add_emotional_episode(self, *, event_type="other", event_label="",
                              source_message_id=None, event_at=None,
                              date_precision="unknown", emotion="neutral",
                              intensity=0.0, reason_summary="", importance=0.0,
                              support_mode="", yue_action=None, follow_up_at=None,
                              follow_up_state="pending", status="unresolved",
                              confidence=0.0, now=None):
        """Crea un episodio. Devuelve su id, o None si no se pudo.

        `yue_action` entra como NULL a propósito: el episodio se detecta ANTES
        de que YUE termine de responder, así que en este momento todavía no se
        sabe qué hizo ella. Se completa después con `update_yue_action`.
        """
        ahora = time.time() if now is None else float(now)
        if status not in self._EPISODE_STATUS:
            status = "unresolved"
        if follow_up_state not in self._EPISODE_FOLLOWUP_STATES:
            follow_up_state = "pending"
        try:
            with closing(self._conn()) as connection:
                cursor = connection.execute(
                    "INSERT INTO emotional_episodes("
                    "created_at,updated_at,source_message_id,event_type,event_label,"
                    "event_at,date_precision,emotion,intensity,reason_summary,"
                    "importance,support_mode,yue_action,follow_up_at,follow_up_state,"
                    "follow_up_count,status,outcome_summary,outcome_emotion,"
                    "confidence,first_mentioned_at,last_mentioned_at,mention_count)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,'','',?,?,?,1)",
                    (ahora, ahora,
                     int(source_message_id) if source_message_id else None,
                     str(event_type or "other"), str(event_label or "")[:120],
                     float(event_at) if event_at else None,
                     str(date_precision or "unknown"),
                     str(emotion or "neutral"),
                     _clamp01(intensity), str(reason_summary or "")[:200],
                     _clamp01(importance), str(support_mode or ""),
                     (str(yue_action)[:80] if yue_action else None),
                     float(follow_up_at) if follow_up_at else None,
                     follow_up_state, status, _clamp01(confidence), ahora, ahora),
                )
                connection.commit()
                return int(cursor.lastrowid)
        except Exception as exc:
            print("[episodic-memory] no pude crear el episodio:", exc)
            return None

    def get_emotional_episode(self, episode_id):
        """Un episodio por id, como dict. None si no existe."""
        try:
            with closing(self._conn()) as connection:
                fila = connection.execute(
                    "SELECT * FROM emotional_episodes WHERE id=?",
                    (int(episode_id),)).fetchone()
            return dict(fila) if fila else None
        except Exception as exc:
            print("[episodic-memory] no pude leer el episodio:", exc)
            return None

    def update_emotional_episode(self, episode_id, *, now=None, **campos):
        """Actualiza los campos indicados. Ignora los que no estén en la lista blanca."""
        cambios = {k: v for k, v in campos.items() if k in self._EPISODE_FIELDS}
        if not cambios:
            return False
        if "status" in cambios and cambios["status"] not in self._EPISODE_STATUS:
            cambios.pop("status")
        if ("follow_up_state" in cambios
                and cambios["follow_up_state"] not in self._EPISODE_FOLLOWUP_STATES):
            cambios.pop("follow_up_state")
        for numerico in ("intensity", "importance", "confidence"):
            if numerico in cambios:
                cambios[numerico] = _clamp01(cambios[numerico])
        for texto, tope in (("event_label", 120), ("reason_summary", 200),
                            ("outcome_summary", 300), ("yue_action", 80)):
            if texto in cambios and cambios[texto] is not None:
                cambios[texto] = str(cambios[texto])[:tope]
        cambios["updated_at"] = time.time() if now is None else float(now)
        try:
            sets = ", ".join(f"{k}=?" for k in cambios)
            with closing(self._conn()) as connection:
                connection.execute(
                    f"UPDATE emotional_episodes SET {sets} WHERE id=?",
                    tuple(cambios.values()) + (int(episode_id),))
                connection.commit()
            return True
        except Exception as exc:
            print("[episodic-memory] no pude actualizar el episodio:", exc)
            return False

    def open_episodes(self, since=None, limit=40):
        """Episodios ABIERTOS (status='unresolved'), del más reciente al más viejo.

        `since` acota por última mención, para no arrastrar cosas de hace meses
        al comparar duplicados.
        """
        try:
            with closing(self._conn()) as connection:
                if since is None:
                    filas = connection.execute(
                        "SELECT * FROM emotional_episodes WHERE status='unresolved' "
                        "ORDER BY last_mentioned_at DESC LIMIT ?",
                        (int(limit),)).fetchall()
                else:
                    filas = connection.execute(
                        "SELECT * FROM emotional_episodes WHERE status='unresolved' "
                        "AND COALESCE(last_mentioned_at, created_at)>=? "
                        "ORDER BY last_mentioned_at DESC LIMIT ?",
                        (float(since), int(limit))).fetchall()
            return [dict(f) for f in filas]
        except Exception as exc:
            print("[episodic-memory] no pude listar episodios abiertos:", exc)
            return []

    def find_due_followup(self, now=None, max_asked=1):
        """El episodio pendiente MÁS importante cuyo seguimiento ya toca.

        Condiciones: sigue sin resolverse, la pregunta está en 'pending', ya
        pasó `follow_up_at` y no se ha preguntado más veces de la cuenta. El
        orden es por importancia y luego por antigüedad del vencimiento: si hay
        varios, primero lo que más le pesa.
        """
        ahora = time.time() if now is None else float(now)
        try:
            with closing(self._conn()) as connection:
                fila = connection.execute(
                    "SELECT * FROM emotional_episodes "
                    "WHERE status='unresolved' AND follow_up_state='pending' "
                    "AND follow_up_at IS NOT NULL AND follow_up_at<=? "
                    "AND follow_up_count<? "
                    "ORDER BY importance DESC, follow_up_at ASC LIMIT 1",
                    (ahora, int(max_asked))).fetchone()
            return dict(fila) if fila else None
        except Exception as exc:
            print("[episodic-memory] no pude buscar seguimientos vencidos:", exc)
            return None

    def mark_followup_asked(self, episode_id, now=None):
        """YUE ya preguntó por este episodio. No volverá a hacerlo por su cuenta.

        El estado del EVENTO no cambia: sigue 'unresolved' hasta que se sepa
        cómo terminó. Lo que se cierra es la iniciativa de YUE.
        """
        ahora = time.time() if now is None else float(now)
        try:
            with closing(self._conn()) as connection:
                connection.execute(
                    "UPDATE emotional_episodes SET follow_up_state='asked', "
                    "follow_up_count=follow_up_count+1, updated_at=? WHERE id=?",
                    (ahora, int(episode_id)))
                connection.commit()
            return True
        except Exception as exc:
            print("[episodic-memory] no pude marcar el seguimiento:", exc)
            return False

    def resolve_episode(self, episode_id, *, outcome_summary="",
                        outcome_emotion="", now=None):
        """Cierra el episodio: ya se sabe cómo terminó."""
        ahora = time.time() if now is None else float(now)
        return self.update_emotional_episode(
            episode_id, now=ahora, status="resolved",
            outcome_summary=str(outcome_summary or "")[:300],
            outcome_emotion=str(outcome_emotion or ""),
            follow_up_state="cancelled", last_mentioned_at=ahora)

    def cancel_episode(self, episode_id, now=None):
        """Descarta el episodio (falsa alarma, el usuario lo canceló…)."""
        ahora = time.time() if now is None else float(now)
        return self.update_emotional_episode(
            episode_id, now=ahora, status="cancelled",
            follow_up_state="cancelled")

    def update_yue_action(self, episode_id, action, now=None, only_if_empty=True):
        """Anota QUÉ hizo YUE en ese episodio, en una etiqueta corta.

        Con `only_if_empty` no se pisa lo que ya había: lo que YUE hizo la
        primera vez que él lo contó es lo que da sentido al recuerdo.
        """
        ahora = time.time() if now is None else float(now)
        try:
            with closing(self._conn()) as connection:
                if only_if_empty:
                    cursor = connection.execute(
                        "UPDATE emotional_episodes SET yue_action=?, updated_at=? "
                        "WHERE id=? AND (yue_action IS NULL OR yue_action='')",
                        (str(action)[:80], ahora, int(episode_id)))
                else:
                    cursor = connection.execute(
                        "UPDATE emotional_episodes SET yue_action=?, updated_at=? "
                        "WHERE id=?", (str(action)[:80], ahora, int(episode_id)))
                connection.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            print("[episodic-memory] no pude anotar la acción de YUE:", exc)
            return False

    def recently_touched_episodes(self, since, limit=5):
        """Episodios mencionados hace poco. Respaldo para completar `yue_action`."""
        try:
            with closing(self._conn()) as connection:
                filas = connection.execute(
                    "SELECT * FROM emotional_episodes "
                    "WHERE COALESCE(last_mentioned_at, created_at)>=? "
                    "ORDER BY last_mentioned_at DESC LIMIT ?",
                    (float(since), int(limit))).fetchall()
            return [dict(f) for f in filas]
        except Exception:
            return []

    def get_relevant_episodes(self, now=None, recent_days=21.0, limit=25):
        """Candidatos para el contexto del prompt.

        Devuelve los abiertos y los resueltos recientemente; la PRIORIZACIÓN
        fina (relación con lo que se habla, cercanía del evento…) la hace
        `core.episodic_memory`, que es quien tiene el mensaje delante.
        """
        ahora = time.time() if now is None else float(now)
        desde = ahora - max(1.0, float(recent_days)) * 86400.0
        try:
            with closing(self._conn()) as connection:
                filas = connection.execute(
                    "SELECT * FROM emotional_episodes WHERE "
                    "(status='unresolved' AND COALESCE(last_mentioned_at,created_at)>=?) "
                    "OR (status='resolved' AND updated_at>=?) "
                    "ORDER BY importance DESC, updated_at DESC LIMIT ?",
                    (desde, desde, int(limit))).fetchall()
            return [dict(f) for f in filas]
        except Exception as exc:
            print("[episodic-memory] no pude recuperar episodios relevantes:", exc)
            return []

    def expire_old_episodes(self, retention_days=90.0, now=None):
        """Caduca lo que lleva demasiado tiempo abierto sin saberse nada.

        No borra: marca 'expired'. Un episodio caducado deja de estorbar en el
        contexto y en los seguimientos, pero sigue ahí por si algún día la
        consolidación quiere mirarlo.
        """
        ahora = time.time() if now is None else float(now)
        limite = ahora - max(1.0, float(retention_days)) * 86400.0
        try:
            with closing(self._conn()) as connection:
                cursor = connection.execute(
                    "UPDATE emotional_episodes SET status='expired', "
                    "follow_up_state='cancelled', updated_at=? "
                    "WHERE status='unresolved' AND "
                    "COALESCE(last_mentioned_at, created_at)<?", (ahora, limite))
                connection.commit()
                return int(cursor.rowcount or 0)
        except Exception as exc:
            print("[episodic-memory] no pude caducar episodios:", exc)
            return 0

    def purge_trivial_episodes(self, retention_days=90.0, min_importance=0.55,
                               now=None):
        """Borra episodios VIEJOS y POCO importantes. Evita el crecimiento infinito.

        Solo toca lo que ya está cerrado o caducado y quedó por debajo del
        umbral de importancia. Los recuerdos que importan no se tiran nunca.
        """
        ahora = time.time() if now is None else float(now)
        limite = ahora - max(1.0, float(retention_days)) * 86400.0
        try:
            with closing(self._conn()) as connection:
                cursor = connection.execute(
                    "DELETE FROM emotional_episodes WHERE status IN "
                    "('expired','cancelled') AND updated_at<? AND importance<?",
                    (limite, float(min_importance)))
                connection.commit()
                return int(cursor.rowcount or 0)
        except Exception as exc:
            print("[episodic-memory] no pude purgar episodios triviales:", exc)
            return 0

    def episode_emotion_counts(self, now=None, days=180.0):
        """Cuántas veces se repite cada par (tipo de evento, emoción).

        Materia prima para que la consolidación pueda observar algo PRUDENTE
        («las entrevistas suelen ponerle nervioso»). Nunca para diagnosticar.
        """
        ahora = time.time() if now is None else float(now)
        desde = ahora - max(1.0, float(days)) * 86400.0
        try:
            with closing(self._conn()) as connection:
                filas = connection.execute(
                    "SELECT event_type, emotion, COUNT(*) AS n FROM emotional_episodes "
                    "WHERE created_at>=? AND status!='cancelled' AND emotion!='neutral' "
                    "GROUP BY event_type, emotion ORDER BY n DESC LIMIT 10",
                    (desde,)).fetchall()
            return [dict(f) for f in filas]
        except Exception:
            return []

    def count_episodes(self, status=None):
        """Cuántos episodios hay (opcionalmente filtrando por estado)."""
        try:
            with closing(self._conn()) as connection:
                if status:
                    fila = connection.execute(
                        "SELECT COUNT(*) AS n FROM emotional_episodes WHERE status=?",
                        (str(status),)).fetchone()
                else:
                    fila = connection.execute(
                        "SELECT COUNT(*) AS n FROM emotional_episodes").fetchone()
            return int(fila["n"]) if fila else 0
        except Exception:
            return 0


def _clamp01(valor, defecto=0.0):
    """Recorta a [0,1] sin reventar con basura (None, texto, NaN)."""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return defecto
    if numero != numero:  # NaN
        return defecto
    return max(0.0, min(1.0, numero))
