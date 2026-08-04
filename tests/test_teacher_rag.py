"""Pruebas del RAG del Modo Profesora (FASE 6/7).

    python -m pytest tests/test_teacher_rag.py
    python tests/test_teacher_rag.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.teacher_ext import DocumentRAG, chunk_text

DOC = """
La fotosíntesis es el proceso por el cual las plantas transforman la luz solar en energía.
Ocurre principalmente en los cloroplastos, gracias a un pigmento verde llamado clorofila.

Las plantas absorben dióxido de carbono del aire y agua del suelo. Con la energía de la luz,
producen glucosa y liberan oxígeno como subproducto.

La respiración celular es distinta: las células usan glucosa y oxígeno para obtener energía,
liberando dióxido de carbono. Es casi el proceso inverso de la fotosíntesis.
"""


def test_chunk_text_respeta_parrafos():
    chunks = chunk_text(DOC, max_chars=500)
    assert len(chunks) >= 3  # hay tres párrafos claros
    assert all(isinstance(c, str) and c for c in chunks)


def test_chunk_text_parte_parrafos_largos():
    largo = " ".join([f"Oración número {i} sobre el tema." for i in range(60)])
    chunks = chunk_text(largo, max_chars=120)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)  # ninguno se dispara de tamaño


def test_indexa_y_cuenta_fragmentos():
    rag = DocumentRAG()
    n = rag.index_document("bio1", DOC, title="Biología")
    assert n >= 3
    docs = rag.documents()
    assert docs and docs[0]["doc_id"] == "bio1"


def test_busqueda_recupera_fragmento_relevante():
    rag = DocumentRAG()
    rag.index_document("bio1", DOC)
    hits = rag.search("¿qué pigmento hace la fotosíntesis?", top_k=2)
    assert hits
    assert "clorofila" in hits[0]["content"].lower()


def test_busqueda_distingue_temas():
    rag = DocumentRAG()
    rag.index_document("bio1", DOC)
    hits = rag.search("cómo obtienen energía las células respirando", top_k=1)
    assert hits
    assert "respiración" in hits[0]["content"].lower() or "glucosa" in hits[0]["content"].lower()


def test_answer_context_encontrado_trae_citas():
    rag = DocumentRAG()
    rag.index_document("bio1", DOC)
    ctx = rag.answer_context("¿qué liberan las plantas?", top_k=2)
    assert ctx["found"] is True
    assert "Fragmento 1" in ctx["context"]
    assert ctx["citations"] and ctx["citations"][0]["doc_id"] == "bio1"
    assert "apoy" in ctx["instruction"].lower()


def test_answer_context_sin_material_avisa():
    rag = DocumentRAG()
    rag.index_document("bio1", DOC)
    ctx = rag.answer_context("cuál es la capital de Australia y su moneda oficial")
    assert ctx["found"] is False
    assert ctx["context"] == ""
    assert "no aparece en el documento" in ctx["instruction"].lower()


def test_reindexar_no_duplica():
    rag = DocumentRAG()
    rag.index_document("bio1", DOC)
    rag.index_document("bio1", DOC)  # segunda vez
    docs = rag.documents()
    solo_bio1 = [d for d in docs if d["doc_id"] == "bio1"]
    assert len(solo_bio1) == 1


def test_filtra_por_documento():
    rag = DocumentRAG()
    rag.index_document("bio1", DOC)
    rag.index_document("otro", "Este documento habla de historia romana y el coliseo.")
    hits = rag.search("coliseo romano", doc_id="bio1")
    # Buscando solo en bio1, no debe salir el contenido del otro documento.
    assert all(h["doc_id"] == "bio1" for h in hits)


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
