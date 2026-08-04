"""Recuperación de documentos para el Modo Profesora (FASE 6/7).

La auditoría marcó que el modo profesora lee documentos pero no los "aprovecha"
para responder con base en ellos: explica de memoria del modelo, no citando el
material. Esta capa lo arregla con un RAG (Retrieval-Augmented Generation) LOCAL:

  1) Trocea el documento en fragmentos (chunks) y los indexa.
  2) Ante una pregunta, recupera los fragmentos más relevantes por significado
     (TF-IDF + coseno, en español, SIN internet ni modelo pesado).
  3) Arma un CONTEXTO citable para pasárselo al modelo, de modo que la profesora
     responda "según el material..." y pueda señalar de qué fragmento salió.

Esto es la base de responder, explicar y evaluar pegado al documento (y de tomar
en serio "reemplazar temporalmente al profesor": sin recuperación, el modelo
inventa; con recuperación, se apoya en el texto real del alumno).

ADITIVO: no toca teacher/*.py. El motor de profesora puede llamar a
`answer_context()` antes de responder. Solo librería estándar; funciona offline.
"""
from __future__ import annotations

import math
import re
import sqlite3
import unicodedata
from collections import Counter
from pathlib import Path

# Palabras vacías del español (misma idea que en memory_ext, mantenidas aparte
# para que este módulo sea autónomo).
_STOP = {
    "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por",
    "un", "para", "con", "no", "una", "su", "al", "lo", "como", "mas", "pero",
    "sus", "le", "ya", "o", "este", "si", "porque", "esta", "entre", "cuando",
    "muy", "sin", "sobre", "tambien", "me", "hasta", "hay", "donde", "quien",
    "desde", "todo", "nos", "durante", "todos", "uno", "les", "ni", "contra",
    "otros", "ese", "eso", "ante", "ellos", "e", "esto", "mi", "antes", "unos",
    "yo", "otro", "otras", "otra", "tanto", "esa", "estos", "mucho", "nada",
    "cual", "poco", "ella", "estar", "estas", "algunas", "algo", "tu", "te",
    "ti", "es", "son", "the", "of", "and",
}


def _normalize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    tokens = re.findall(r"[a-zñ0-9]+", text)
    return [t for t in tokens if len(t) > 2 and t not in _STOP]


def chunk_text(text: str, max_chars: int = 500) -> list[str]:
    """Divide un texto en fragmentos legibles, respetando párrafos y oraciones.

    Junta oraciones hasta acercarse a `max_chars`, sin cortar a mitad de frase.
    """
    text = (text or "").strip()
    if not text:
        return []
    # Primero por párrafos; luego por oraciones dentro de párrafos largos.
    parrafos = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    for p in parrafos:
        p = p.strip()
        if not p:
            continue
        if len(p) <= max_chars:
            chunks.append(p)
            continue
        oraciones = re.split(r"(?<=[\.\?\!])\s+", p)
        actual = ""
        for o in oraciones:
            if not actual:
                actual = o
            elif len(actual) + 1 + len(o) <= max_chars:
                actual += " " + o
            else:
                chunks.append(actual.strip())
                actual = o
        if actual.strip():
            chunks.append(actual.strip())
    return chunks


class DocumentRAG:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = ":memory:" if db_path is None else str(db_path)
        self._mem_conn = sqlite3.connect(self.db_path) if self.db_path == ":memory:" else None
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _ensure_schema(self):
        c = self._conn()
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS rag_documents (
                doc_id TEXT PRIMARY KEY,
                title TEXT DEFAULT '',
                added_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS rag_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT,
                idx INTEGER,
                content TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_rag_chunks_doc ON rag_chunks(doc_id);
            """
        )
        c.commit()
        if self._mem_conn is None:
            c.close()

    # ---- Indexado ---------------------------------------------------------
    def index_document(self, doc_id: str, text: str, title: str = "",
                       max_chars: int = 500) -> int:
        """Indexa (o reindexa) un documento. Devuelve cuántos fragmentos generó."""
        chunks = chunk_text(text, max_chars=max_chars)
        c = self._conn()
        c.execute("DELETE FROM rag_chunks WHERE doc_id=?", (doc_id,))
        c.execute(
            "INSERT INTO rag_documents (doc_id, title) VALUES (?,?) "
            "ON CONFLICT(doc_id) DO UPDATE SET title=excluded.title",
            (doc_id, title),
        )
        for i, ch in enumerate(chunks):
            c.execute(
                "INSERT INTO rag_chunks (doc_id, idx, content) VALUES (?,?,?)",
                (doc_id, i, ch),
            )
        c.commit()
        if self._mem_conn is None:
            c.close()
        return len(chunks)

    def _all_chunks(self, doc_id: str | None = None):
        c = self._conn()
        if doc_id:
            rows = c.execute(
                "SELECT id, doc_id, idx, content FROM rag_chunks WHERE doc_id=? ORDER BY id",
                (doc_id,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, doc_id, idx, content FROM rag_chunks ORDER BY id"
            ).fetchall()
        out = [{"id": r[0], "doc_id": r[1], "idx": r[2], "content": r[3]} for r in rows]
        if self._mem_conn is None:
            c.close()
        return out

    # ---- Recuperación (TF-IDF + coseno) ----------------------------------
    def search(self, question: str, top_k: int = 3, doc_id: str | None = None,
               min_score: float = 0.02) -> list[dict]:
        """Recupera los fragmentos más relevantes para la pregunta."""
        q_tokens = _normalize(question)
        if not q_tokens:
            return []
        docs = self._all_chunks(doc_id=doc_id)
        if not docs:
            return []

        doc_tokens = [_normalize(d["content"]) for d in docs]
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
            common = set(qv) & set(dv)
            if not common:
                continue
            dot = sum(qv[t] * dv[t] for t in common)
            score = dot / (qn * dn)
            if score >= min_score:
                resultados.append({**d, "score": round(score, 4)})

        resultados.sort(key=lambda r: r["score"], reverse=True)
        return resultados[:top_k]

    def answer_context(self, question: str, top_k: int = 3,
                       doc_id: str | None = None) -> dict:
        """Prepara el CONTEXTO citable para que la profesora responda con base real.

        Devuelve:
          - context: bloque de texto con los fragmentos numerados, listo para el
            prompt del modelo.
          - citations: de qué documento y fragmento salió cada cita.
          - found: si hubo material relevante (si no, la profesora debe avisar que
            no está en el documento en vez de inventar).
        """
        hits = self.search(question, top_k=top_k, doc_id=doc_id)
        if not hits:
            return {
                "found": False,
                "context": "",
                "citations": [],
                "instruction": (
                    "No hay fragmentos del material que respondan a esto. Dile al "
                    "alumno, con amabilidad, que eso no aparece en el documento y "
                    "ofrécele explicárselo por tu cuenta si quiere, aclarando que ya "
                    "no es del texto."
                ),
            }
        bloques = []
        citas = []
        for n, h in enumerate(hits, start=1):
            bloques.append(f"[Fragmento {n}] {h['content']}")
            citas.append({"n": n, "doc_id": h["doc_id"], "fragmento": h["idx"],
                          "score": h["score"]})
        contexto = "\n\n".join(bloques)
        instr = (
            "Responde la pregunta del alumno APOYÁNDOTE en los fragmentos de arriba "
            "(son del material que trajo). Cita el número de fragmento cuando uses "
            "algo, y si el material no alcanza para responder del todo, dilo con "
            "honestidad en vez de inventar."
        )
        return {"found": True, "context": contexto, "citations": citas,
                "instruction": instr}

    def documents(self) -> list[dict]:
        c = self._conn()
        rows = c.execute(
            "SELECT d.doc_id, d.title, COUNT(ch.id) AS n "
            "FROM rag_documents d LEFT JOIN rag_chunks ch ON ch.doc_id=d.doc_id "
            "GROUP BY d.doc_id ORDER BY d.added_at DESC"
        ).fetchall()
        out = [{"doc_id": r[0], "title": r[1], "fragmentos": r[2]} for r in rows]
        if self._mem_conn is None:
            c.close()
        return out
