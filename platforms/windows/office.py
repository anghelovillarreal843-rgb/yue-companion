"""Automatización nativa de Microsoft Office mediante COM (Windows).

El módulo no importa pywin32 al cargar para que YUE siga arrancando en equipos
sin Office, sin pywin32 o fuera de Windows. Cada operación inicializa COM en el
hilo que la ejecuta (PCWorker es un QThread) y degrada mediante OfficeUnavailable.
"""
from __future__ import annotations

import platform
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class OfficeError(RuntimeError):
    """Error general de automatización de Office."""


class OfficeUnavailable(OfficeError):
    """Office/COM/pywin32 no están disponibles; el llamador puede usar fallback."""


@dataclass
class OfficeResult:
    app: str
    operation: str
    detail: str
    path: str = ""
    content: str = ""


_APP_MAP = {
    "word": "Word.Application",
    "winword": "Word.Application",
    "excel": "Excel.Application",
    "powerpoint": "PowerPoint.Application",
    "power point": "PowerPoint.Application",
    "powerpnt": "PowerPoint.Application",
}
_EXTENSIONS = {"word": ".docx", "excel": ".xlsx", "powerpoint": ".pptx"}


def _norm_app(app: str) -> str:
    value = re.sub(r"\s+", " ", (app or "").strip().lower())
    if "power" in value or "ppt" in value:
        return "powerpoint"
    if "excel" in value or "hoja" in value:
        return "excel"
    if "word" in value or "documento" in value:
        return "word"
    return value


def available() -> bool:
    if platform.system().lower() != "windows":
        return False
    try:
        import win32com.client  # noqa: F401
        import pythoncom  # noqa: F401
        return True
    except Exception:
        return False


class OfficeController:
    """Controla Word, Excel y PowerPoint usando sus modelos de objetos COM."""

    def __init__(self, visible: bool = True):
        self.visible = bool(visible)

    @staticmethod
    def _com():
        if platform.system().lower() != "windows":
            raise OfficeUnavailable("La automatización COM de Office solo funciona en Windows.")
        try:
            import pythoncom
            import win32com.client
        except Exception as exc:
            raise OfficeUnavailable(
                "Falta pywin32. Instálalo con: pip install pywin32"
            ) from exc
        try:
            pythoncom.CoInitialize()
        except Exception:
            pass
        return pythoncom, win32com.client

    def _app(self, app: str, create: bool = True):
        normalized = _norm_app(app)
        progid = _APP_MAP.get(normalized)
        if not progid:
            raise OfficeError(f"Aplicación de Office no compatible: {app}")
        _pythoncom, client = self._com()
        try:
            # GetActiveObject evita abrir instancias duplicadas; Dispatch crea una
            # cuando no existe. Algunas instalaciones no exponen GetActiveObject.
            try:
                obj = client.GetActiveObject(progid)
            except Exception:
                if not create:
                    raise
                obj = client.Dispatch(progid)
            try:
                obj.Visible = self.visible
            except Exception:
                pass
            return normalized, obj
        except Exception as exc:
            raise OfficeUnavailable(
                f"No pude conectar con {normalized.title()}; puede no estar instalado."
            ) from exc

    @staticmethod
    def _default_path(app: str, requested: str | Path | None = None) -> Path:
        ext = _EXTENSIONS[app]
        if requested:
            path = Path(requested).expanduser()
            if path.suffix.lower() != ext:
                path = path.with_suffix(ext)
        else:
            root = Path.home() / "Documents"
            if not root.exists():
                root = Path.home()
            path = root / f"YUE_{app}_{time.strftime('%Y%m%d_%H%M%S')}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.resolve()

    def create_document(self, app: str) -> OfficeResult:
        normalized, office = self._app(app)
        try:
            if normalized == "word":
                office.Documents.Add()
                detail = "documento de Word nuevo"
            elif normalized == "excel":
                office.Workbooks.Add()
                detail = "libro de Excel nuevo"
            else:
                office.Presentations.Add()
                detail = "presentación de PowerPoint nueva"
            return OfficeResult(normalized, "create", detail)
        except Exception as exc:
            raise OfficeError(f"No pude crear un archivo nuevo en {normalized}: {exc}") from exc

    def insert_text(
        self,
        app: str,
        text: str,
        *,
        title: str = "",
        bold: bool = False,
        new_document: bool = False,
    ) -> OfficeResult:
        normalized, office = self._app(app)
        text = str(text or "")
        title = str(title or "").strip()
        try:
            if normalized == "word":
                docs = office.Documents
                doc = docs.Add() if new_document or docs.Count == 0 else office.ActiveDocument
                rng = doc.Content
                rng.Collapse(0)  # wdCollapseEnd
                if title:
                    start = rng.End
                    rng.InsertAfter(title + "\r")
                    title_rng = doc.Range(start, start + len(title))
                    try:
                        title_rng.Style = "Título 1"
                    except Exception:
                        title_rng.Font.Bold = True
                        title_rng.Font.Size = 16
                if text:
                    start = doc.Content.End - 1
                    doc.Range(start, start).InsertAfter(text)
                    if bold:
                        doc.Range(start, start + len(text)).Font.Bold = True
                detail = f"inserté {len(text)} caracteres en Word"

            elif normalized == "excel":
                books = office.Workbooks
                book = books.Add() if new_document or books.Count == 0 else office.ActiveWorkbook
                sheet = book.ActiveSheet
                row = 1
                if title:
                    sheet.Cells(row, 1).Value = title
                    sheet.Cells(row, 1).Font.Bold = True
                    sheet.Cells(row, 1).Font.Size = 14
                    row += 2
                lines = text.splitlines() or [text]
                for line in lines:
                    columns = line.split("\t")
                    for col, value in enumerate(columns, 1):
                        sheet.Cells(row, col).Value = value
                        if bold and row == (3 if title else 1):
                            sheet.Cells(row, col).Font.Bold = True
                    row += 1
                try:
                    sheet.UsedRange.Columns.AutoFit()
                except Exception:
                    pass
                detail = f"inserté {len(lines)} fila(s) en Excel"

            else:
                presentations = office.Presentations
                pres = presentations.Add() if new_document or presentations.Count == 0 else office.ActivePresentation
                # 2 = ppLayoutText (título + cuerpo). Si ya hay diapositiva y no
                # se pidió nueva presentación, se añade otra para no pisar contenido.
                slide = pres.Slides.Add(pres.Slides.Count + 1, 2)
                slide.Shapes.Title.TextFrame.TextRange.Text = title or "Documento de YUE"
                body = slide.Shapes.Placeholders(2).TextFrame.TextRange
                body.Text = text
                if bold:
                    body.Font.Bold = True
                detail = "creé una diapositiva con título y contenido"

            return OfficeResult(normalized, "insert", detail)
        except Exception as exc:
            raise OfficeError(f"No pude insertar contenido en {normalized}: {exc}") from exc

    def save_document(self, app: str, path: str | Path | None = None) -> OfficeResult:
        normalized, office = self._app(app, create=False)
        target = self._default_path(normalized, path)
        try:
            if normalized == "word":
                if office.Documents.Count == 0:
                    raise OfficeError("No hay un documento de Word abierto.")
                office.ActiveDocument.SaveAs2(str(target), FileFormat=16)  # docx
            elif normalized == "excel":
                if office.Workbooks.Count == 0:
                    raise OfficeError("No hay un libro de Excel abierto.")
                office.ActiveWorkbook.SaveAs(str(target), FileFormat=51)  # xlsx
            else:
                if office.Presentations.Count == 0:
                    raise OfficeError("No hay una presentación abierta.")
                office.ActivePresentation.SaveAs(str(target), FileFormat=24)  # pptx
            return OfficeResult(normalized, "save", f"guardé {target.name}", str(target))
        except OfficeError:
            raise
        except Exception as exc:
            raise OfficeError(f"No pude guardar el archivo de {normalized}: {exc}") from exc

    def read_open_document(self, app: str) -> OfficeResult:
        normalized, office = self._app(app, create=False)
        try:
            if normalized == "word":
                if office.Documents.Count == 0:
                    raise OfficeError("No hay un documento de Word abierto.")
                content = str(office.ActiveDocument.Content.Text or "").rstrip("\r\x07")
            elif normalized == "excel":
                if office.Workbooks.Count == 0:
                    raise OfficeError("No hay un libro de Excel abierto.")
                used = office.ActiveWorkbook.ActiveSheet.UsedRange.Value
                if used is None:
                    content = ""
                elif not isinstance(used, tuple):
                    content = str(used)
                else:
                    rows = []
                    for row in used:
                        if not isinstance(row, tuple):
                            row = (row,)
                        rows.append("\t".join("" if v is None else str(v) for v in row))
                    content = "\n".join(rows)
            else:
                if office.Presentations.Count == 0:
                    raise OfficeError("No hay una presentación abierta.")
                chunks = []
                for slide in office.ActivePresentation.Slides:
                    texts = []
                    for shape in slide.Shapes:
                        try:
                            if shape.HasTextFrame and shape.TextFrame.HasText:
                                texts.append(str(shape.TextFrame.TextRange.Text))
                        except Exception:
                            continue
                    chunks.append("\n".join(texts))
                content = "\n\n".join(chunks)
            return OfficeResult(normalized, "read", f"leí {len(content)} caracteres", content=content)
        except OfficeError:
            raise
        except Exception as exc:
            raise OfficeError(f"No pude leer el contenido de {normalized}: {exc}") from exc

    def write_document(
        self,
        app: str,
        text: str,
        *,
        title: str = "",
        bold: bool = False,
        save_path: str | Path | None = None,
        new_document: bool = True,
    ) -> OfficeResult:
        result = self.insert_text(
            app, text, title=title, bold=bold, new_document=new_document
        )
        if save_path:
            saved = self.save_document(app, save_path)
            result.path = saved.path
            result.detail += f" y lo guardé como {Path(saved.path).name}"
        return result
