"""Memoria 2.0 — capa ADITIVA sobre la base de datos actual (FASE 3).

No reemplaza core/memory.py ni toca sus tablas. Añade, con CREATE TABLE IF NOT
EXISTS, lo que faltaba según la auditoría:

  - conversations      : cada charla con id, tema, modo, dispositivo, resumen…
  - conv_messages       : mensajes ligados a su conversación, con emoción,
                          importancia, confianza, marca de editado/borrado.
  - mem_backups         : registro de copias de seguridad hechas.

Y encima ofrece:

  1) Búsqueda "por significado" SIN internet ni modelo pesado: un TF-IDF con
     coseno sobre el texto en español (minúsculas, sin tildes, sin palabras
     vacías). Devuelve el mensaje ORIGINAL, su FECHA y un nivel de CONFIANZA,
     para que YUE pueda decir «me hablaste de esto el día X» y distinguir un
     recuerdo exacto de uno aproximado o inferido.
  2) Copias de seguridad seguras con verificación de integridad y restauración
     (nunca se pisa una base sin respaldo previo).

Solo usa la librería estándar (sqlite3, re, math, unicodedata, shutil), así que
funciona en modo totalmente offline y no añade dependencias.
"""
from __future__ import annotations

import math
import re
import shutil
import sqlite3
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

# Palabras vacías del español (lista corta pero útil para el TF-IDF).
_STOP = {
    "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por",
    "un", "para", "con", "no", "una", "su", "al", "lo", "como", "mas", "pero",
    "sus", "le", "ya", "o", "este", "si", "porque", "esta", "entre", "cuando",
    "muy", "sin", "sobre", "tambien", "me", "hasta", "hay", "donde", "quien",
    "desde", "todo", "nos", "durante", "todos", "uno", "les", "ni", "contra",
    "otros", "ese", "eso", "ante", "ellos", "e", "esto", "mi", "antes", "algunos",
    "que", "unos", "yo", "otro", "otras", "otra", "el", "tanto", "esa", "estos",
    "mucho", "quienes", "nada", "muchos", "cual", "poco", "ella", "estar", "estas",
    "algunas", "algo", "nosotros", "tu", "te", "ti", "es", "soy", "era", "son",
}


def _normalize(text: str) -> list[str]:
    """Minúsculas, sin tildes, solo palabras, sin palabras vacías ni cortas."""
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    tokens = re.findall(r"[a-zñ0-9]+", text)
    return [t for t in tokens if len(t) > 2 and t not in _STOP]


class MemoryExtension:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _ensure_schema(self):
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT DEFAULT 'default',
                    title TEXT DEFAULT '',
                    topic TEXT DEFAULT '',
                    mode TEXT DEFAULT '',
                    device TEXT DEFAULT '',
                    started_at TEXT,
                    ended_at TEXT,
                    summary TEXT DEFAULT '',
                    emotion TEXT DEFAULT 'neutral',
                    tags TEXT DEFAULT '',
                    privacy TEXT DEFAULT 'normal',
                    status TEXT DEFAULT 'abierta'
                );
                CREATE TABLE IF NOT EXISTS conv_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER,
                    user_id TEXT DEFAULT 'default',
                    role TEXT,
                    content TEXT,
                    ts TEXT,
                    source TEXT DEFAULT 'teclado',
                    emotion TEXT DEFAULT '',
                    importance REAL DEFAULT 0.5,
                    confidence REAL DEFAULT 1.0,
                    edited INTEGER DEFAULT 0,
                    deleted INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_conv_msg_conv
                    ON conv_messages(conversation_id);
                CREATE INDEX IF NOT EXISTS idx_conv_msg_user
                    ON conv_messages(user_id);
                CREATE TABLE IF NOT EXISTS mem_backups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT,
                    path TEXT,
                    size_bytes INTEGER,
                    integrity TEXT
                );
                """
            )

    # ---- Ciclo de vida de una conversación --------------------------------
    def start_conversation(self, user_id="default", title="", topic="",
                           mode="", device="") -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO conversations (user_id,title,topic,mode,device,started_at,status)"
                " VALUES (?,?,?,?,?,?, 'abierta')",
                (user_id, title, topic, mode, device, now),
            )
            return cur.lastrowid

    def add_message(self, conversation_id: int, role: str, content: str,
                    user_id="default", source="teclado", emotion="",
                    importance=0.5, confidence=1.0) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO conv_messages "
                "(conversation_id,user_id,role,content,ts,source,emotion,importance,confidence)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (conversation_id, user_id, role, content, now, source,
                 emotion, importance, confidence),
            )
            return cur.lastrowid

    def end_conversation(self, conversation_id: int, summary="",
                         emotion="neutral", tags="", status="cerrada"):
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute(
                "UPDATE conversations SET ended_at=?, summary=?, emotion=?, tags=?, status=?"
                " WHERE id=?",
                (now, summary, emotion, tags, status, conversation_id),
            )

    def conversation_messages(self, conversation_id: int, include_deleted=False):
        q = "SELECT * FROM conv_messages WHERE conversation_id=?"
        if not include_deleted:
            q += " AND deleted=0"
        q += " ORDER BY id"
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, (conversation_id,))]

    def mark_deleted(self, message_id: int):
        """Borrado lógico: el usuario puede eliminar un recuerdo sin romper la charla."""
        with self._conn() as c:
            c.execute("UPDATE conv_messages SET deleted=1 WHERE id=?", (message_id,))

    # ---- Búsqueda "por significado" (TF-IDF + coseno, local) --------------
    def _corpus(self, user_id="default"):
        """Trae mensajes no borrados + resúmenes de conversación como documentos."""
        docs = []
        with self._conn() as c:
            for r in c.execute(
                "SELECT id, conversation_id, role, content, ts FROM conv_messages"
                " WHERE deleted=0 AND user_id=?", (user_id,)
            ):
                docs.append({
                    "tipo": "mensaje", "id": r["id"], "conv": r["conversation_id"],
                    "role": r["role"], "texto": r["content"], "fecha": r["ts"],
                })
            for r in c.execute(
                "SELECT id, summary, ended_at, started_at FROM conversations"
                " WHERE user_id=? AND summary != ''", (user_id,)
            ):
                docs.append({
                    "tipo": "resumen", "id": r["id"], "conv": r["id"],
                    "role": "resumen", "texto": r["summary"],
                    "fecha": r["ended_at"] or r["started_at"],
                })
        return docs

    def search(self, query: str, top_k=5, min_score=0.05, user_id="default"):
        """Devuelve los recuerdos más parecidos al 'query' por significado.

        Cada resultado trae: texto original, fecha, conversación, rol, score
        (0..1) y 'grado' (exacto/aproximado/inferido) según la confianza.
        """
        q_tokens = _normalize(query)
        if not q_tokens:
            return []
        docs = self._corpus(user_id=user_id)
        if not docs:
            return []

        # Tokenizar corpus y calcular IDF.
        doc_tokens = [_normalize(d["texto"]) for d in docs]
        N = len(docs)
        df = Counter()
        for toks in doc_tokens:
            for t in set(toks):
                df[t] += 1
        idf = {t: math.log((N + 1) / (df_t + 1)) + 1.0 for t, df_t in df.items()}

        def vec(tokens):
            tf = Counter(tokens)
            v = {t: (tf[t] / len(tokens)) * idf.get(t, math.log(N + 1) + 1.0)
                 for t in tf}
            norm = math.sqrt(sum(w * w for w in v.values())) or 1.0
            return v, norm

        qv, qn = vec(q_tokens)

        resultados = []
        for d, toks in zip(docs, doc_tokens):
            if not toks:
                continue
            dv, dn = vec(toks)
            # Coseno solo sobre términos comunes.
            common = set(qv) & set(dv)
            if not common:
                continue
            dot = sum(qv[t] * dv[t] for t in common)
            score = dot / (qn * dn)
            if score >= min_score:
                resultados.append({**d, "score": round(score, 4),
                                   "grado": _grado(score)})

        resultados.sort(key=lambda r: r["score"], reverse=True)
        return resultados[:top_k]

    def remember(self, query: str, user_id="default") -> dict | None:
        """Atajo: el mejor recuerdo con una frase lista para que YUE la diga."""
        hits = self.search(query, top_k=1, user_id=user_id)
        if not hits:
            return None
        h = hits[0]
        fecha = (h["fecha"] or "")[:10]
        frase = {
            "exacto": f"Me hablaste de esto el {fecha}",
            "aproximado": f"Creo que algo de esto salió alrededor del {fecha}",
            "inferido": f"No estoy del todo segura, pero por el {fecha} tocamos algo parecido",
        }[h["grado"]]
        return {**h, "frase": frase, "fecha_corta": fecha}

    # ---- Copias de seguridad + integridad ---------------------------------
    def backup(self, dest_dir: str | Path) -> dict:
        """Copia consistente de la base (API backup de SQLite) + verificación."""
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = dest / f"yue_backup_{stamp}.db"

        src = sqlite3.connect(self.db_path)
        try:
            dst = sqlite3.connect(str(out))
            with dst:
                src.backup(dst)
            dst.close()
        finally:
            src.close()

        integridad = self.verify_integrity(out)
        size = out.stat().st_size
        with self._conn() as c:
            c.execute(
                "INSERT INTO mem_backups (created_at,path,size_bytes,integrity)"
                " VALUES (?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), str(out), size, integridad),
            )
        return {"path": str(out), "size_bytes": size, "integrity": integridad,
                "ok": integridad == "ok"}

    @staticmethod
    def verify_integrity(db_path: str | Path) -> str:
        """Devuelve 'ok' si la base está sana, o el mensaje de error de SQLite."""
        try:
            c = sqlite3.connect(str(db_path))
            row = c.execute("PRAGMA integrity_check").fetchone()
            c.close()
            return row[0] if row else "desconocido"
        except sqlite3.Error as e:
            return f"error: {e}"

    def restore(self, backup_path: str | Path) -> dict:
        """Restaura desde un backup, PERO respalda primero la base actual.

        Nunca se pierde el estado previo: si algo sale mal, queda la copia de
        seguridad '.pre_restore' para volver atrás.
        """
        backup_path = Path(backup_path)
        if not backup_path.exists():
            return {"ok": False, "error": "el backup no existe"}
        if self.verify_integrity(backup_path) != "ok":
            return {"ok": False, "error": "el backup está corrupto; no se restaura"}

        safety = Path(self.db_path).with_suffix(
            Path(self.db_path).suffix + f".pre_restore_{datetime.now():%Y%m%d_%H%M%S}"
        )
        if Path(self.db_path).exists():
            shutil.copy2(self.db_path, safety)
        shutil.copy2(backup_path, self.db_path)
        ok = self.verify_integrity(self.db_path) == "ok"
        return {"ok": ok, "safety_copy": str(safety) if safety.exists() else None}

    def list_backups(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM mem_backups ORDER BY id DESC")]


def _grado(score: float) -> str:
    if score >= 0.45:
        return "exacto"
    if score >= 0.20:
        return "aproximado"
    return "inferido"
