"""Análisis lingüístico estructural previo a la planificación.

No intenta convertir por sí solo cualquier frase en clics. Su función es extraer
la topología de la orden (secuencia, precedencia y paralelismo) para que el
planificador trabaje sobre el objetivo completo y no sobre palabras aisladas.
"""
from __future__ import annotations

import re
import unicodedata

from .models import GoalAnalysis, GoalClause


def _norm(text: str) -> str:
    base = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(ch for ch in base if unicodedata.category(ch) != "Mn")


class ActionParser:
    _parallel = (
        "al mismo tiempo", "simultáneamente", "simultaneamente", "en paralelo", "mientras",
        "a la vez", "meanwhile", "at the same time", "in parallel",
    )
    _sequence = (
        "después de guardar", "despues de guardar", "una vez terminado", "cuando termine",
        "al finalizar", "a continuación", "a continuacion", "finalmente",
        "después", "despues", "luego", "más tarde", "mas tarde", "entonces",
        "afterwards", "then", "finally", "once finished",
    )
    _before = ("antes de", "previamente", "before")
    _first = ("primero", "en primer lugar", "first")

    def analyze(self, instruction: str) -> GoalAnalysis:
        original = (instruction or "").strip()
        normalized = _norm(original)
        markers = [m for m in (*self._parallel, *self._sequence, *self._before, *self._first)
                   if m in normalized]
        clauses = self._split(original)
        return GoalAnalysis(
            original=original,
            objective=original,
            clauses=clauses,
            has_parallelism=any(c.relation == "parallel" for c in clauses),
            has_dependencies=len(clauses) > 1 or bool(markers),
            markers=markers,
        )

    def _split(self, instruction: str) -> list[GoalClause]:
        if not instruction.strip():
            return []

        # Se analiza fuera de comillas para que «escribe "Hola, mundo"» no
        # se convierta en dos tareas. La normalización conserva la longitud de
        # letras latinas acentuadas y permite reconocer después/despues.
        named = sorted(
            {*self._parallel, *self._sequence, *self._before, *self._first},
            key=lambda value: len(_norm(value)),
            reverse=True,
        )
        normalized = _norm(instruction)
        separators = [(_norm(value), _norm(value).strip()) for value in named]
        # Conjunciones y puntuación se consideran separadores estructurales.
        separators.extend(((" y ", "y"), (" e ", "e"), (";", ";"), (",", ",")))
        separators.sort(key=lambda item: len(item[0]), reverse=True)

        parts: list[tuple[str, str]] = []
        leading_connector = ""
        start = 0
        index = 0
        quote = ""
        opening = {'"': '"', "'": "'", "“": "”", "«": "»"}
        while index < len(instruction):
            char = instruction[index]
            if quote:
                if char == quote:
                    quote = ""
                index += 1
                continue
            if char in opening:
                quote = opening[char]
                index += 1
                continue

            matched = None
            for token, canonical in separators:
                if not normalized.startswith(token, index):
                    continue
                # Las frases sin espacios externos requieren límites de palabra.
                if token and token[0].isalnum():
                    before = normalized[index - 1] if index > 0 else " "
                    after_index = index + len(token)
                    after = normalized[after_index] if after_index < len(normalized) else " "
                    if before.isalnum() or after.isalnum():
                        continue
                matched = (token, canonical)
                break
            if matched is None:
                index += 1
                continue

            token, canonical = matched
            text = instruction[start:index].strip(" ,.;:\n\t")
            if text:
                parts.append((text, canonical))
            elif parts:
                # Dos conectores consecutivos: conserva el más específico para
                # la cláusula siguiente (por ejemplo «y luego»).
                previous_text, _previous_connector = parts[-1]
                parts[-1] = (previous_text, canonical)
            else:
                leading_connector = canonical
            start = index + len(token)
            index = start

        tail = instruction[start:].strip(" ,.;:\n\t")
        if tail:
            parts.append((tail, ""))
        if not parts:
            return [GoalClause(text=instruction.strip(), index=0)]

        normalized_parallel = {_norm(c).strip() for c in self._parallel}
        normalized_before = {_norm(c).strip() for c in self._before}
        normalized_first = {_norm(c).strip() for c in self._first}
        clauses: list[GoalClause] = []
        pending = leading_connector
        for text, connector_after in parts:
            relation = "sequence"
            if pending in normalized_parallel:
                relation = "parallel"
            elif pending in normalized_before:
                relation = "before"
            elif pending in normalized_first:
                relation = "first"
            clauses.append(GoalClause(
                text=text, relation=relation, connector=pending, index=len(clauses),
            ))
            pending = connector_after
        return clauses
