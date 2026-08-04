"""OCR de pantalla con motor enchufable (por defecto pytesseract).

Sirve para la acción click_text{text}: localizar texto visible y clicar en su
centro real, sin depender de las coordenadas que inventa el modelo de visión.

Para enchufar otro motor (EasyOCR, PaddleOCR, RapidOCR...) basta con:

    from core.screen_text import register_engine, OCREngine

    class MiMotor(OCREngine):
        name = "mi_motor"
        def words(self, image):
            return [{"text": ..., "left": ..., "top": ...,
                     "width": ..., "height": ..., "conf": ...}, ...]

    register_engine("mi_motor", MiMotor)

y poner OCR_ENGINE=mi_motor en el .env.
"""
from __future__ import annotations

import re
import threading
import unicodedata
from pathlib import Path

import config


class OCRError(RuntimeError):
    pass


def norm(text: str) -> str:
    text = (text or "").strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------- motores
class OCREngine:
    """Contrato mínimo de un motor OCR."""

    name = "base"

    def available(self) -> bool:
        return False

    def words(self, image) -> list[dict]:
        """Devuelve palabras con caja: {text,left,top,width,height,conf}."""
        raise NotImplementedError


class TesseractEngine(OCREngine):
    """Motor por defecto: pytesseract (necesita el binario de Tesseract)."""

    name = "pytesseract"

    def __init__(self):
        self._checked = False
        self._ok = False

    def available(self) -> bool:
        if self._checked:
            return self._ok
        self._checked = True
        try:
            import pytesseract
            ruta = str(getattr(config, "OCR_TESSERACT_CMD", "") or "") or self._buscar_binario()
            if ruta:
                pytesseract.pytesseract.tesseract_cmd = ruta
            pytesseract.get_tesseract_version()
            self._ok = True
        except Exception as exc:
            print("[ocr] pytesseract no disponible:", exc)
            print("[ocr] instala Tesseract y, si no queda en el PATH, pon la ruta "
                  "completa a tesseract.exe en OCR_TESSERACT_CMD (.env).")
            self._ok = False
        return self._ok

    @staticmethod
    def _buscar_binario() -> str:
        """Busca tesseract.exe donde suele instalarlo el paquete de UB-Mannheim.

        winget instala el binario pero NO refresca el PATH de la sesión abierta;
        así evitamos que el usuario tenga que configurar nada.
        """
        import os
        import shutil

        encontrado = shutil.which("tesseract")
        if encontrado:
            return encontrado
        candidatos = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
            os.path.expandvars(r"%ProgramW6432%\Tesseract-OCR\tesseract.exe"),
        ]
        for ruta in candidatos:
            if ruta and Path(ruta).is_file():
                print(f"[ocr] tesseract encontrado fuera del PATH: {ruta}")
                return ruta
        return ""

    def words(self, image) -> list[dict]:
        import pytesseract
        from pytesseract import Output

        lang = str(getattr(config, "OCR_LANG", "spa+eng"))
        try:
            data = pytesseract.image_to_data(image, lang=lang, output_type=Output.DICT)
        except Exception:
            # Si no está el paquete de idioma español, reintenta en inglés.
            data = pytesseract.image_to_data(image, output_type=Output.DICT)

        out: list[dict] = []
        total = len(data.get("text", []))
        for i in range(total):
            texto = (data["text"][i] or "").strip()
            if not texto:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1.0
            out.append({
                "text": texto,
                "left": int(data["left"][i]),
                "top": int(data["top"][i]),
                "width": int(data["width"][i]),
                "height": int(data["height"][i]),
                "conf": conf,
                "line": (int(data.get("block_num", [0] * total)[i]),
                         int(data.get("par_num", [0] * total)[i]),
                         int(data.get("line_num", [0] * total)[i])),
            })
        return out


_ENGINES: dict = {"pytesseract": TesseractEngine}
_instances: dict = {}
_lock = threading.Lock()


def register_engine(name: str, factory) -> None:
    """Registra un motor OCR alternativo."""
    with _lock:
        _ENGINES[name.strip().lower()] = factory


def get_engine(name: str | None = None) -> OCREngine:
    """Devuelve el motor pedido, el del .env, o el de por defecto."""
    key = (name or getattr(config, "OCR_ENGINE", "pytesseract") or "pytesseract").strip().lower()
    with _lock:
        if key not in _ENGINES:
            print(f"[ocr] motor «{key}» no registrado; uso pytesseract.")
            key = "pytesseract"
        if key not in _instances:
            _instances[key] = _ENGINES[key]()
        return _instances[key]


# ---------------------------------------------------------------- búsqueda
def _grab_screen():
    try:
        import pyautogui
        return pyautogui.screenshot()
    except Exception as exc:
        raise OCRError(f"No pude capturar la pantalla para el OCR: {exc}") from exc


def _group_lines(words: list[dict]) -> list[list[dict]]:
    """Agrupa palabras por línea para poder buscar frases de varias palabras."""
    lines: dict = {}
    for word in words:
        key = word.get("line") or (0, 0, round(word["top"] / 12))
        lines.setdefault(key, []).append(word)
    grouped = []
    for items in lines.values():
        grouped.append(sorted(items, key=lambda w: w["left"]))
    return grouped


def find_text(
    text: str,
    image=None,
    min_conf: float | None = None,
    engine: str | None = None,
) -> tuple[int, int, str] | None:
    """Centro (x, y, texto_encontrado) del texto visible, o None.

    Soporta frases: agrupa palabras contiguas de la misma línea hasta que la
    frase completa coincide.
    """
    objetivo = norm(text)
    if not objetivo:
        return None
    motor = get_engine(engine)
    if not motor.available():
        raise OCRError(
            f"El motor OCR «{motor.name}» no está listo. Instala pytesseract y "
            "el binario de Tesseract, o registra otro motor."
        )
    if min_conf is None:
        min_conf = float(getattr(config, "OCR_MIN_CONF", 55.0))

    image = image if image is not None else _grab_screen()
    palabras = [w for w in motor.words(image) if w["conf"] < 0 or w["conf"] >= min_conf]
    if not palabras:
        return None

    mejor = None
    for linea in _group_lines(palabras):
        for inicio in range(len(linea)):
            acumulado = ""
            for fin in range(inicio, min(inicio + 8, len(linea))):
                trozo = linea[fin]
                acumulado = (acumulado + " " + norm(trozo["text"])).strip()
                if len(acumulado) > len(objetivo) + 12:
                    break
                if acumulado == objetivo or (
                    len(objetivo) >= 4 and objetivo in acumulado
                ):
                    grupo = linea[inicio:fin + 1]
                    izq = min(w["left"] for w in grupo)
                    der = max(w["left"] + w["width"] for w in grupo)
                    arr = min(w["top"] for w in grupo)
                    aba = max(w["top"] + w["height"] for w in grupo)
                    candidato = (
                        (izq + der) // 2,
                        (arr + aba) // 2,
                        " ".join(w["text"] for w in grupo),
                    )
                    # Nos quedamos con la coincidencia más ajustada.
                    if mejor is None or len(candidato[2]) < len(mejor[2]):
                        mejor = candidato
                    break
    return mejor


def read_screen(image=None, engine: str | None = None) -> str:
    """Texto plano de la pantalla (útil para verificaciones y depuración)."""
    motor = get_engine(engine)
    if not motor.available():
        return ""
    image = image if image is not None else _grab_screen()
    return " ".join(w["text"] for w in motor.words(image))
