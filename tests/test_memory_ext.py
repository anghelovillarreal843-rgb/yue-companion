"""Pruebas de la capa aditiva de memoria (FASE 3).

    python -m pytest tests/test_memory_ext.py
    python tests/test_memory_ext.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.memory_ext import MemoryExtension


def _tmp_db():
    d = tempfile.mkdtemp(prefix="yue_mem_")
    return str(Path(d) / "yue.db"), d


def test_crea_tablas_nuevas():
    db, _ = _tmp_db()
    MemoryExtension(db)
    con = sqlite3.connect(db)
    tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {"conversations", "conv_messages", "mem_backups"} <= tablas


def test_es_aditivo_no_borra_tablas_existentes():
    """Si la base ya tenía tablas de la memoria vieja, siguen intactas."""
    db, _ = _tmp_db()
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, content TEXT)")
    con.execute("INSERT INTO messages (content) VALUES ('hola vieja')")
    con.commit(); con.close()

    MemoryExtension(db)  # añade lo suyo sin tocar 'messages'

    con = sqlite3.connect(db)
    fila = con.execute("SELECT content FROM messages").fetchone()
    tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert fila[0] == "hola vieja"
    assert "messages" in tablas and "conversations" in tablas


def test_conversacion_y_mensajes():
    db, _ = _tmp_db()
    m = MemoryExtension(db)
    cid = m.start_conversation(mode="companera", device="pc", topic="mascotas")
    m.add_message(cid, "user", "Tengo un gato llamado Michi", source="voz",
                  emotion="feliz", importance=0.8)
    m.add_message(cid, "assistant", "¡Qué lindo! Cuéntame de Michi")
    m.end_conversation(cid, summary="El usuario habló de su gato Michi", emotion="feliz")

    msgs = m.conversation_messages(cid)
    assert len(msgs) == 2
    assert msgs[0]["source"] == "voz"
    assert msgs[0]["emotion"] == "feliz"


def test_busqueda_por_significado_encuentra_original():
    db, _ = _tmp_db()
    m = MemoryExtension(db)
    cid = m.start_conversation(mode="companera")
    m.add_message(cid, "user", "Estoy aprendiendo a tocar la guitarra española")
    m.add_message(cid, "user", "Ayer comí una pizza enorme con mis amigos")
    m.add_message(cid, "user", "El proyecto de programación en Python va avanzando")

    # Consulta que NO usa las mismas palabras exactas, pero el tema coincide.
    hits = m.search("instrumentos musicales y guitarra", top_k=3)
    assert hits, "debería encontrar algo"
    assert "guitarra" in hits[0]["texto"].lower()
    assert 0.0 < hits[0]["score"] <= 1.0
    assert hits[0]["grado"] in ("exacto", "aproximado", "inferido")
    assert hits[0]["fecha"]  # trae la fecha del mensaje original


def test_remember_da_frase_con_fecha():
    db, _ = _tmp_db()
    m = MemoryExtension(db)
    cid = m.start_conversation()
    m.add_message(cid, "user", "Mi cumpleaños es el 12 de marzo y me gusta el chocolate")
    r = m.remember("cuando es tu cumpleanos y que dulces te gustan")
    assert r is not None
    assert "chocolate" in r["texto"].lower()
    assert r["frase"].startswith(("Me hablaste", "Creo que", "No estoy"))
    assert len(r["fecha_corta"]) == 10  # YYYY-MM-DD


def test_borrado_logico_excluye_de_busqueda():
    db, _ = _tmp_db()
    m = MemoryExtension(db)
    cid = m.start_conversation()
    mid = m.add_message(cid, "user", "Password secreto: quiero que borres esto")
    m.mark_deleted(mid)
    hits = m.search("password secreto borrar")
    assert all("secreto" not in h["texto"].lower() for h in hits)


def test_backup_verifica_integridad():
    db, d = _tmp_db()
    m = MemoryExtension(db)
    cid = m.start_conversation()
    m.add_message(cid, "user", "dato importante para respaldar")

    info = m.backup(Path(d) / "backups")
    assert info["ok"] is True
    assert info["integrity"] == "ok"
    assert Path(info["path"]).exists()
    assert m.list_backups()[0]["integrity"] == "ok"


def test_restore_respalda_antes_y_recupera():
    db, d = _tmp_db()
    m = MemoryExtension(db)
    cid = m.start_conversation()
    m.add_message(cid, "user", "version A")
    info = m.backup(Path(d) / "backups")

    # Cambiamos la base después del backup.
    cid2 = m.start_conversation()
    m.add_message(cid2, "user", "version B agregada después del backup")

    res = m.restore(info["path"])
    assert res["ok"] is True
    assert res["safety_copy"] is not None  # se guardó copia previa
    # Tras restaurar, "version B" ya no está (volvimos al backup).
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM conv_messages WHERE content LIKE '%version B%'").fetchone()[0]
    con.close()
    assert n == 0


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
