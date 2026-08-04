"""Registro de progreso de alumnos del Modo Profesora.

Guarda, por alumno, las preguntas evaluadas (acierto/fallo, puntaje, nota) y
permite exportar reportes. Es un almacén propio del Teacher Mode: NO toca la
memoria emocional de YUE ni el historial del usuario (requisito de seguridad).

Persistencia sencilla en JSON. Exporta a Markdown y CSV siempre; a Excel solo si
`openpyxl` está instalado (si no, avisa y deja el CSV, que abre igual en Excel).
"""
from __future__ import annotations

import csv
import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field


@dataclass
class EvalItem:
    topic: str = ""
    question: str = ""
    answer: str = ""
    correct: object = None       # True | False | None (no evaluado)
    score: float = 0.0           # 0..10
    note: str = ""
    ts: float = field(default_factory=time.time)


class ProgressStore:
    def __init__(self, path: str | None = None):
        self._lock = threading.RLock()
        self._path = path
        # { alumno: {"sessions": [...], "items": [ {EvalItem} ... ] } }
        self._data: dict = {}
        self._load()

    # ---- carga / guardado ----
    def _load(self):
        if self._path and os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception as exc:
                print("[profesora] no pude cargar el progreso:", exc)
                self._data = {}

    def _save(self):
        if not self._path:
            return
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            print("[profesora] no pude guardar el progreso:", exc)

    def _bucket(self, student: str) -> dict:
        return self._data.setdefault(student, {"sessions": [], "items": []})

    # ---- API ----
    def start_session(self, student: str, topic: str = "") -> None:
        with self._lock:
            self._bucket(student)["sessions"].append(
                {"topic": topic, "started_at": time.time()}
            )
            self._save()

    def record(self, student: str, item: EvalItem) -> None:
        with self._lock:
            self._bucket(student)["items"].append(asdict(item))
            self._save()

    def summary(self, student: str) -> dict:
        with self._lock:
            items = self._bucket(student)["items"]
            evaluados = [i for i in items if i.get("correct") is not None]
            aciertos = sum(1 for i in evaluados if i.get("correct"))
            total = len(evaluados)
            puntajes = [float(i.get("score", 0)) for i in evaluados]
            promedio = round(sum(puntajes) / len(puntajes), 2) if puntajes else 0.0
            temas = sorted({i.get("topic", "") for i in items if i.get("topic")})
            return {
                "student": student,
                "preguntas": len(items),
                "evaluadas": total,
                "aciertos": aciertos,
                "fallos": total - aciertos,
                "promedio": promedio,
                "temas": temas,
            }

    def students(self) -> list:
        with self._lock:
            return list(self._data.keys())

    def summary_text_es(self, student: str) -> str:
        s = self.summary(student)
        if not s["preguntas"]:
            return f"Aún no tengo preguntas registradas de {student}."
        temas = ", ".join(s["temas"]) if s["temas"] else "varios temas"
        return (
            f"{student}: {s['aciertos']}/{s['evaluadas']} aciertos, "
            f"promedio {s['promedio']}/10. Temas: {temas}."
        )

    # ---- exportación de reportes ----
    def export_markdown(self, path: str, student: str | None = None) -> str:
        with self._lock:
            alumnos = [student] if student else list(self._data.keys())
            lineas = ["# Reporte de progreso · Modo Profesora YUE", ""]
            for al in alumnos:
                s = self.summary(al)
                lineas.append(f"## {al}")
                lineas.append(
                    f"- Preguntas: {s['preguntas']} · Evaluadas: {s['evaluadas']} · "
                    f"Aciertos: {s['aciertos']} · Promedio: {s['promedio']}/10"
                )
                lineas.append(f"- Temas: {', '.join(s['temas']) or '—'}")
                lineas.append("")
                lineas.append("| Tema | Pregunta | Respuesta | Correcto | Puntaje | Nota |")
                lineas.append("|------|----------|-----------|----------|---------|------|")
                for it in self._bucket(al)["items"]:
                    corr = {True: "✔", False: "✘", None: "—"}.get(it.get("correct"), "—")
                    lineas.append(
                        f"| {it.get('topic','')} | {_md(it.get('question',''))} | "
                        f"{_md(it.get('answer',''))} | {corr} | {it.get('score',0)} | "
                        f"{_md(it.get('note',''))} |"
                    )
                lineas.append("")
            texto = "\n".join(lineas)
        _write_text(path, texto)
        return path

    def export_csv(self, path: str, student: str | None = None) -> str:
        with self._lock:
            alumnos = [student] if student else list(self._data.keys())
            filas = []
            for al in alumnos:
                for it in self._bucket(al)["items"]:
                    filas.append([
                        al, it.get("topic", ""), it.get("question", ""),
                        it.get("answer", ""), it.get("correct"), it.get("score", 0),
                        it.get("note", ""),
                        time.strftime("%Y-%m-%d %H:%M", time.localtime(it.get("ts", 0))),
                    ])
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Alumno", "Tema", "Pregunta", "Respuesta", "Correcto",
                        "Puntaje", "Nota", "Fecha"])
            w.writerows(filas)
        return path

    def export_xlsx(self, path: str, student: str | None = None) -> str | None:
        """Exporta a Excel si hay openpyxl; si no, cae a CSV y devuelve esa ruta."""
        try:
            from openpyxl import Workbook
        except Exception:
            alt = os.path.splitext(path)[0] + ".csv"
            print("[profesora] openpyxl no está; genero CSV en su lugar:", alt)
            return self.export_csv(alt, student)
        wb = Workbook()
        ws = wb.active
        ws.title = "Progreso"
        ws.append(["Alumno", "Tema", "Pregunta", "Respuesta", "Correcto",
                   "Puntaje", "Nota", "Fecha"])
        with self._lock:
            alumnos = [student] if student else list(self._data.keys())
            for al in alumnos:
                for it in self._bucket(al)["items"]:
                    ws.append([
                        al, it.get("topic", ""), it.get("question", ""),
                        it.get("answer", ""), str(it.get("correct")),
                        it.get("score", 0), it.get("note", ""),
                        time.strftime("%Y-%m-%d %H:%M", time.localtime(it.get("ts", 0))),
                    ])
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        wb.save(path)
        return path


def _md(s: str) -> str:
    return (s or "").replace("|", "/").replace("\n", " ").strip()[:180]


def _write_text(path: str, texto: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(texto)
