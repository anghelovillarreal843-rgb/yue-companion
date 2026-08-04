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
}

# Sustantivo para la mencion aparte "N momentos de ___".
_MOOD_NOUN = {
    "preocupada": "preocupación", "decaída": "tristeza", "irritada": "enojo",
    "tensa": "tensión", "confundida": "confusión", "aburrida": "desgana",
    "sorprendida": "sorpresa", "cansada": "cansancio",
}

# Emociones de tono dificil que merecen resaltarse como "momentos".
_MOOD_NEGATIVAS = ("preocupada", "decaída", "irritada", "tensa", "confundida")

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

                -- Limpieza de la funcion retirada, sin tocar conversaciones,
                -- hechos, metas ni el nivel de vinculo.
                DROP TABLE IF EXISTS media;
                DROP TABLE IF EXISTS diary;
                """
            )
            connection.commit()

    def add_message(self, role, content):
        with closing(self._conn()) as connection:
            connection.execute(
                "INSERT INTO messages(role,content,ts) VALUES(?,?,?)",
                (role, content, time.time()),
            )
            connection.commit()

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
        """Mensajes crudos (role, content) en el rango [ts_inicio, ts_fin], en
        orden cronológico. Mismo patrón que recent_messages pero filtrando por ts
        en vez de LIMIT. Lo usa la consolidación para resumir un periodo."""
        with closing(self._conn()) as connection:
            rows = connection.execute(
                "SELECT role,content FROM messages WHERE ts>=? AND ts<=? ORDER BY id ASC",
                (float(ts_inicio), float(ts_fin)),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

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
