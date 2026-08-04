"""Extensiones del Modo Profesora (FASE 6/7).

Contiene un RAG local (recuperación de documentos) para que la profesora
responda apoyándose en el material del alumno, con citas. ADITIVO.
"""
from .rag import DocumentRAG, chunk_text

__all__ = ["DocumentRAG", "chunk_text"]
