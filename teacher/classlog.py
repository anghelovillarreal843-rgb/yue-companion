"""Memoria de clases y estadísticas educativas del Modo Profesora (§5, §6, §13, §14).

k_same_thrAlmacén ESPECIALIZADO del Teacher Mode: registra cada clase impartida u observada
(tema, duración, materiales, dudas, preguntas, errores, conceptos difíciles y
observaciones) y permite consultarla después para mejorar futuras sesiones y
generar estadísticas. Es memoria de aprendizaje docente, NO la memoria emocional
de YUE (requisito de seguridad §15): vive en su propia base SQLite.

Diseño:
    - SQLite propio (thread-safe con lock + checead=False), como el
      resto de YUE. Si no hay ruta, funciona en memoria (útil en pruebas).
    - Aditivo y degradable: si algo falla, avisa por consola y no rompe la clase.
    - Puro: sin Qt, sin IA. Solo guarda y consulta hechos de la clase.

Uso típico:
    log = ClassLog(path)                       # abre/crea la BD
    cid = log.start_class("leyes de newton", student="alumno", mode="autonoma")
    log.add_question(cid, "¿qué es la inercia?", correct=True, score=8)
    log.add_doubt(cid, "no entiende la 2a ley")
    log.add_material(cid, "fisica.pdf", kind="pdf")
    log.add_difficult_concept(cid, "aceleración")
    log.note(cid, "el alumno mejoró al final")
    log.end_class(cid)                          # fija duración
    resumen = log.recall("leyes de newton")     # para inyectar en la próxima clase
    stats = log.stats()                          # estadísticas globales
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time


_SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    topic        TEXT    NOT NULL DEFAULT '',
    student      TEXT    NOT NULL DEFAULT 'alumno',
    mode         TEXT    NOT NULL DEFAULT '',      -- autonoma | asistida | observada
    started_at   REAL    NOT NULL DEFAULT 0,
    ended_at     REAL    NOT NULL DEFAULT 0,
    duration_s   REAL    NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id  INTEGER NOT NULL,
    kind      TEXT    NOT NULL,   -- question | doubt | material | difficult | error | note
    text      TEXT    NOT NULL DEFAULT '',
    correct   INTEGER,            -- 1 | 0 | NULL
    score     REAL,
    meta      TEXT    NOT NULL DEFAULT '',
    ts        REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_events_class ON events(class_id);
CREATE INDEX IF NOT EXISTS ix_events_kind  ON events(kind);
CREATE INDEX IF NOT EXISTS ix_classes_topic ON classes(topic);
"""


class ClassLog:
    def __init__(self, path: str | None = None):
        self._lock = threading.RLock()
        self._path = path
        target = path or ":memory:"
        if path:
            try:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            except Exception as exc:
                print("[profesora/log] no pude crear la carpeta:", exc)
        self._db = sqlite3.connect(target, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    # ------------------------------------------------------------- ciclo
    def start_class(self, topic: str = "", student: str = "alumno",
                    mode: str = "") -> int:
        """Abre una clase y devuelve su id. `mode`: autonoma|asistida|observada."""
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO classes (topic, student, mode, started_at) "
                "VALUES (?,?,?,?)",
                (topic or "", student or "alumno", mode or "", time.time()),
            )
            self._db.commit()
            return int(cur.lastrowid)

    def update_topic(self, class_id: int, topic: str) -> None:
        """Fija el tema de una clase abierta (cuando se detecta más tarde)."""
        if not topic:
            return
        with self._lock:
            self._db.execute(
                "UPDATE classes SET topic=? WHERE id=?", (topic, class_id))
            self._db.commit()

    def end_class(self, class_id: int) -> float:
        """Cierra una clase y fija su duración en segundos. Devuelve la duración."""
        now = time.time()
        with self._lock:
            row = self._db.execute(
                "SELECT started_at FROM classes WHERE id=?", (class_id,)
            ).fetchone()
            started = float(row["started_at"]) if row else now
            dur = max(0.0, now - started)
            self._db.execute(
                "UPDATE classes SET ended_at=?, duration_s=? WHERE id=?",
                (now, dur, class_id),
            )
            self._db.commit()
            return dur

    # ------------------------------------------------------------- eventos
    def _add(self, class_id: int, kind: str, text: str = "",
             correct=None, score=None, meta: str = "") -> None:
        c = None if correct is None else (1 if correct else 0)
        with self._lock:
            self._db.execute(
                "INSERT INTO events (class_id, kind, text, correct, score, meta, ts) "
                "VALUES (?,?,?,?,?,?,?)",
                (class_id, kind, text or "", c, score, meta or "", time.time()),
            )
            self._db.commit()

    def add_question(self, class_id: int, text: str, correct=None,
                     score=None) -> None:
        self._add(class_id, "question", text, correct=correct, score=score)

    def add_doubt(self, class_id: int, text: str) -> None:
        self._add(class_id, "doubt", text)

    def add_material(self, class_id: int, origin: str, kind: str = "") -> None:
        self._add(class_id, "material", origin, meta=kind)

    def add_difficult_concept(self, class_id: int, concept: str) -> None:
        self._add(class_id, "difficult", concept)

    def add_error(self, class_id: int, text: str) -> None:
        self._add(class_id, "error", text)

    def note(self, class_id: int, text: str) -> None:
        self._add(class_id, "note", text)

    # ------------------------------------------------------------- consulta
    def recall(self, topic: str, limit: int = 3) -> str:
        """Resumen breve, en español, de clases pasadas sobre un tema.

        Se inyecta en el prompt de la próxima clase para no repetir errores y
        retomar dudas/conceptos difíciles ya detectados (§14 aprendizaje permanente).
        """
        topic = (topic or "").strip()
        if not topic:
            return ""
        with self._lock:
            clases = self._db.execute(
                "SELECT id, topic, duration_s FROM classes "
                "WHERE topic LIKE ? ORDER BY started_at DESC LIMIT ?",
                (f"%{topic}%", limit),
            ).fetchall()
            if not clases:
                return ""
            ids = [c["id"] for c in clases]
            marcas = ",".join("?" * len(ids))
            dudas = [r["text"] for r in self._db.execute(
                f"SELECT text FROM events WHERE kind='doubt' AND class_id IN ({marcas})",
                ids).fetchall()]
            dificiles = [r["text"] for r in self._db.execute(
                f"SELECT text FROM events WHERE kind='difficult' AND class_id IN ({marcas})",
                ids).fetchall()]
            errores = [r["text"] for r in self._db.execute(
                f"SELECT text FROM events WHERE kind='error' AND class_id IN ({marcas})",
                ids).fetchall()]
        partes = [f"Ya diste {len(clases)} clase(s) sobre «{topic}»."]
        if dificiles:
            partes.append("Conceptos que costaron: " + ", ".join(_uniq(dificiles)[:6]) + ".")
        if dudas:
            partes.append("Dudas frecuentes anteriores: " + "; ".join(_uniq(dudas)[:4]) + ".")
        if errores:
            partes.append("Errores a prevenir: " + "; ".join(_uniq(errores)[:4]) + ".")
        return " ".join(partes)

    def frequent_doubts(self, topic: str | None = None, limit: int = 8) -> list:
        with self._lock:
            if topic:
                rows = self._db.execute(
                    "SELECT e.text FROM events e JOIN classes c ON c.id=e.class_id "
                    "WHERE e.kind='doubt' AND c.topic LIKE ?",
                    (f"%{topic}%",)).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT text FROM events WHERE kind='doubt'").fetchall()
        return _rank([r["text"] for r in rows])[:limit]

    def difficult_concepts(self, limit: int = 10) -> list:
        with self._lock:
            rows = self._db.execute(
                "SELECT text FROM events WHERE kind='difficult'").fetchall()
        return _rank([r["text"] for r in rows])[:limit]

    def stats(self, student: str | None = None) -> dict:
        """Estadísticas educativas globales o por alumno (§13)."""
        with self._lock:
            if student:
                clases = self._db.execute(
                    "SELECT * FROM classes WHERE student=?", (student,)).fetchall()
            else:
                clases = self._db.execute("SELECT * FROM classes").fetchall()
            ids = [c["id"] for c in clases]
            preguntas = evaluadas = aciertos = 0
            suma_score = 0.0
            tiempo_por_tema: dict = {}
            if ids:
                marcas = ",".join("?" * len(ids))
                qs = self._db.execute(
                    f"SELECT correct, score FROM events "
                    f"WHERE kind='question' AND class_id IN ({marcas})", ids).fetchall()
                preguntas = len(qs)
                for q in qs:
                    if q["correct"] is not None:
                        evaluadas += 1
                        aciertos += 1 if q["correct"] else 0
                    if q["score"] is not None:
                        suma_score += float(q["score"])
            for c in clases:
                t = (c["topic"] or "—")
                tiempo_por_tema[t] = tiempo_por_tema.get(t, 0.0) + float(c["duration_s"] or 0)
        n_eval = evaluadas or 1
        return {
            "clases": len(clases),
            "preguntas": preguntas,
            "evaluadas": evaluadas,
            "aciertos": aciertos,
            "fallos": max(0, evaluadas - aciertos),
            "porcentaje_acierto": round(100.0 * aciertos / n_eval, 1) if evaluadas else 0.0,
            "promedio": round(suma_score / (preguntas or 1), 2) if preguntas else 0.0,
            "tiempo_por_tema_min": {k: round(v / 60.0, 1) for k, v in tiempo_por_tema.items()},
            "conceptos_dificiles": self.difficult_concepts(),
        }

    def stats_text_es(self, student: str | None = None) -> str:
        s = self.stats(student)
        if not s["clases"]:
            return "Todavía no tengo clases registradas en mi memoria de profesora."
        quien = f" de {student}" if student else ""
        temas = sorted(s["tiempo_por_tema_min"].items(), key=lambda kv: kv[1], reverse=True)
        top = ", ".join(f"{t} ({m} min)" for t, m in temas[:4]) or "—"
        dif = ", ".join(s["conceptos_dificiles"][:5]) or "ninguno claro aún"
        return (
            f"Estadísticas educativas{quien}: {s['clases']} clase(s), "
            f"{s['preguntas']} pregunta(s), {s['porcentaje_acierto']}% de acierto "
            f"(promedio {s['promedio']}/10). Tiempo por tema: {top}. "
            f"Conceptos más difíciles: {dif}."
        )

    def close(self):
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass


# ------------------------------------------------------------------ helpers
def _uniq(seq) -> list:
    vistos, out = set(), []
    for s in seq:
        k = (s or "").strip().lower()
        if k and k not in vistos:
            vistos.add(k)
            out.append(s.strip())
    return out


def _rank(seq) -> list:
    """Ordena por frecuencia (más repetido primero), sin distinguir may/min."""
    from collections import Counter
    norm = [(s or "").strip() for s in seq if (s or "").strip()]
    cont = Counter(s.lower() for s in norm)
    # Recupera una forma legible por cada clave, ordenando por frecuencia.
    forma = {}
    for s in norm:
        forma.setdefault(s.lower(), s)
    return [forma[k] for k, _ in cont.most_common()]
