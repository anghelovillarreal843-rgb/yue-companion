"""Motor de OCR para la cámara (punto 5 del pedido).

Cadena de motores LOCALES, en orden de calidad y sin ningún servicio de pago:

    PaddleOCR  ->  EasyOCR  ->  Tesseract (pytesseract)  ->  motor nulo

Se elige el primero que esté instalado. Si no hay ninguno, el motor nulo deja
todo funcionando: `available=False` y el sistema informa con claridad de que
falta instalar un OCR, sin romper YUE.

Idioma principal ESPAÑOL (`VISION_OCR_LANGUAGES=es`), con inglés añadible sin
tocar código (`VISION_OCR_LANGUAGES=es,en`).

Cada motor devuelve una lista de bloques:
    {"text": str, "confidence": 0..1, "box": {x,y,w,h} normalizado}

Los modelos se cargan UNA sola vez (caché de instancia) y de forma perezosa: si
nadie usa OCR, no se carga nada ni se gasta RAM.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("vision.ocr")


@dataclass
class OCRBlock:
    text: str
    confidence: float = 0.0
    box: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"text": self.text, "confidence": round(self.confidence, 3), "box": dict(self.box)}


# ---------------------------------------------------------------------------
# Motores
# ---------------------------------------------------------------------------
class BaseOCR:
    name = "base"

    def __init__(self, languages: tuple[str, ...] = ("es",)) -> None:
        self.languages = tuple(languages)
        self._impl: Any = None
        self._failed = False

    @property
    def available(self) -> bool:
        return not self._failed and self._load() is not None

    def _load(self) -> Any:  # pragma: no cover - lo implementa cada motor
        raise NotImplementedError

    def read(self, image) -> list[OCRBlock]:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        self._impl = None


class PaddleOCREngine(BaseOCR):
    """Mejor calidad en español y funciona bien con fotos de webcam."""

    name = "paddleocr"

    def _load(self):
        if self._impl is not None or self._failed:
            return self._impl
        try:
            from paddleocr import PaddleOCR
            lang = "es" if "es" in self.languages else (self.languages[0] if self.languages else "en")
            self._impl = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
            log.info("OCR: PaddleOCR cargado (idioma %s).", lang)
        except Exception as exc:
            self._failed = True
            log.debug("PaddleOCR no disponible: %s", exc)
            self._impl = None
        return self._impl

    def read(self, image) -> list[OCRBlock]:
        impl = self._load()
        if impl is None or image is None:
            return []
        try:
            h, w = image.shape[:2]
            raw = impl.ocr(image, cls=True)
        except Exception as exc:
            log.debug("PaddleOCR falló: %s", exc)
            return []
        blocks: list[OCRBlock] = []
        for page in (raw or []):
            for item in (page or []):
                try:
                    quad, (text, conf) = item[0], item[1]
                    xs = [float(p[0]) for p in quad]
                    ys = [float(p[1]) for p in quad]
                    blocks.append(OCRBlock(
                        text=str(text).strip(),
                        confidence=float(conf),
                        box={"x": min(xs) / w, "y": min(ys) / h,
                             "w": (max(xs) - min(xs)) / w, "h": (max(ys) - min(ys)) / h},
                    ))
                except Exception:
                    continue
        return blocks


class EasyOCREngine(BaseOCR):
    """Buena alternativa; primer arranque descarga pesos (una sola vez)."""

    name = "easyocr"

    def _load(self):
        if self._impl is not None or self._failed:
            return self._impl
        try:
            import easyocr
            langs = list(self.languages) or ["es"]
            self._impl = easyocr.Reader(langs, gpu=False, verbose=False)
            log.info("OCR: EasyOCR cargado (%s).", ",".join(langs))
        except Exception as exc:
            self._failed = True
            log.debug("EasyOCR no disponible: %s", exc)
            self._impl = None
        return self._impl

    def read(self, image) -> list[OCRBlock]:
        impl = self._load()
        if impl is None or image is None:
            return []
        try:
            h, w = image.shape[:2]
            raw = impl.readtext(image)
        except Exception as exc:
            log.debug("EasyOCR falló: %s", exc)
            return []
        blocks = []
        for quad, text, conf in raw or []:
            try:
                xs = [float(p[0]) for p in quad]
                ys = [float(p[1]) for p in quad]
                blocks.append(OCRBlock(
                    text=str(text).strip(),
                    confidence=float(conf),
                    box={"x": min(xs) / w, "y": min(ys) / h,
                         "w": (max(xs) - min(xs)) / w, "h": (max(ys) - min(ys)) / h},
                ))
            except Exception:
                continue
        return blocks


class TesseractOCREngine(BaseOCR):
    """Respaldo: ya viene en las dependencias de YUE para el OCR de pantalla."""

    name = "tesseract"

    def _load(self):
        if self._impl is not None or self._failed:
            return self._impl
        try:
            import pytesseract
            # Reutiliza la ruta que YUE ya resuelve para el OCR de pantalla.
            try:
                from core.screen_text import TesseractEngine as _ScreenTess
                ruta = _ScreenTess._buscar_binario()
                if ruta:
                    pytesseract.pytesseract.tesseract_cmd = ruta
            except Exception:
                ruta = os.environ.get("OCR_TESSERACT_CMD", "")
                if ruta:
                    pytesseract.pytesseract.tesseract_cmd = ruta
            pytesseract.get_tesseract_version()
            self._impl = pytesseract
            log.info("OCR: Tesseract cargado.")
        except Exception as exc:
            self._failed = True
            log.debug("Tesseract no disponible: %s", exc)
            self._impl = None
        return self._impl

    def read(self, image) -> list[OCRBlock]:
        impl = self._load()
        if impl is None or image is None:
            return []
        lang_map = {"es": "spa", "en": "eng"}
        lang = "+".join(lang_map.get(l, l) for l in self.languages) or "spa"
        try:
            from pytesseract import Output
            h, w = image.shape[:2]
            try:
                data = impl.image_to_data(image, lang=lang, output_type=Output.DICT)
            except Exception:
                data = impl.image_to_data(image, output_type=Output.DICT)
        except Exception as exc:
            log.debug("Tesseract falló: %s", exc)
            return []

        # Agrupa palabras por línea (block/par/line) para dar bloques legibles.
        lines: dict[tuple, list] = {}
        n = len(data.get("text", []))
        for i in range(n):
            text = (data["text"][i] or "").strip()
            if not text:
                continue
            try:
                conf = float(data["conf"][i])
            except Exception:
                conf = -1.0
            if conf < 0:
                continue
            key = (data.get("block_num", [0] * n)[i],
                   data.get("par_num", [0] * n)[i],
                   data.get("line_num", [0] * n)[i])
            lines.setdefault(key, []).append((text, conf / 100.0,
                                              data["left"][i], data["top"][i],
                                              data["width"][i], data["height"][i]))
        blocks = []
        for parts in lines.values():
            text = " ".join(p[0] for p in parts)
            conf = sum(p[1] for p in parts) / len(parts)
            x1 = min(p[2] for p in parts)
            y1 = min(p[3] for p in parts)
            x2 = max(p[2] + p[4] for p in parts)
            y2 = max(p[3] + p[5] for p in parts)
            blocks.append(OCRBlock(
                text=text, confidence=conf,
                box={"x": x1 / w, "y": y1 / h, "w": (x2 - x1) / w, "h": (y2 - y1) / h},
            ))
        return blocks


class NullOCREngine(BaseOCR):
    """Sin OCR instalado: no rompe nada y lo dice claro."""

    name = "ninguno"

    def _load(self):
        return None

    @property
    def available(self) -> bool:
        return False

    def read(self, image) -> list[OCRBlock]:
        return []


_ENGINE_CLASSES = {
    "paddleocr": PaddleOCREngine,
    "easyocr": EasyOCREngine,
    "tesseract": TesseractOCREngine,
    "pytesseract": TesseractOCREngine,
    "ninguno": NullOCREngine,
    "none": NullOCREngine,
}

_PREFERRED_ORDER = ("paddleocr", "easyocr", "tesseract")


# ---------------------------------------------------------------------------
@dataclass
class OCRResult:
    """Resultado con la forma EXACTA que pide el documento."""

    text: str = ""
    confidence: float = 0.0
    language: str = "es"
    stable: bool = False
    region: list = field(default_factory=list)
    timestamp: float = 0.0
    engine: str = ""
    blocks: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "confidence": round(float(self.confidence), 3),
            "language": self.language,
            "stable": bool(self.stable),
            "region": list(self.region),
            "timestamp": round(float(self.timestamp), 3),
        }

    def __bool__(self) -> bool:
        return bool(self.text.strip())


class OCREngineChain:
    """Elige el mejor motor disponible y expone una API única."""

    def __init__(self, languages: tuple[str, ...] = ("es",), preferred: str = "auto",
                 min_confidence: float = 0.35) -> None:
        self.languages = tuple(l.strip().lower() for l in languages if l.strip()) or ("es",)
        self.preferred = (preferred or "auto").strip().lower()
        self.min_confidence = float(min_confidence)
        self._engine: BaseOCR | None = None
        self._resolved = False

    # ------------------------------------------------------------------
    def engine(self) -> BaseOCR:
        if self._resolved and self._engine is not None:
            return self._engine
        self._resolved = True
        order = list(_PREFERRED_ORDER)
        if self.preferred not in {"auto", ""} and self.preferred in _ENGINE_CLASSES:
            order = [self.preferred] + [o for o in order if o != self.preferred]
        for key in order:
            cls = _ENGINE_CLASSES.get(key)
            if cls is None:
                continue
            candidate = cls(self.languages)
            if candidate.available:
                self._engine = candidate
                log.info("Motor OCR de cámara: %s", candidate.name)
                return candidate
        self._engine = NullOCREngine(self.languages)
        log.warning("No hay ningún motor OCR instalado. Instala 'paddleocr', "
                    "'easyocr' o Tesseract para que YUE pueda leer textos.")
        return self._engine

    @property
    def available(self) -> bool:
        return self.engine().available

    @property
    def engine_name(self) -> str:
        return self.engine().name

    # ------------------------------------------------------------------
    def read(self, image, region: list | None = None, language: str | None = None) -> OCRResult:
        """Lee la imagen ya preparada y devuelve el texto ordenado por posición."""
        now = time.time()
        eng = self.engine()
        if not eng.available or image is None:
            return OCRResult(text="", confidence=0.0, language=language or self.languages[0],
                             timestamp=now, engine=eng.name)
        blocks = [b for b in eng.read(image) if b.confidence >= self.min_confidence and b.text]
        if not blocks:
            return OCRResult(text="", confidence=0.0, language=language or self.languages[0],
                             timestamp=now, engine=eng.name)
        # Orden de lectura natural: de arriba abajo, de izquierda a derecha.
        blocks.sort(key=lambda b: (round(b.box.get("y", 0.0), 2), b.box.get("x", 0.0)))
        text = "\n".join(b.text for b in blocks).strip()
        conf = sum(b.confidence for b in blocks) / len(blocks)
        if region is None:
            xs1 = min(b.box.get("x", 0.0) for b in blocks)
            ys1 = min(b.box.get("y", 0.0) for b in blocks)
            xs2 = max(b.box.get("x", 0.0) + b.box.get("w", 0.0) for b in blocks)
            ys2 = max(b.box.get("y", 0.0) + b.box.get("h", 0.0) for b in blocks)
            region = [round(xs1, 4), round(ys1, 4), round(xs2, 4), round(ys2, 4)]
        return OCRResult(
            text=text,
            confidence=round(conf, 3),
            language=language or self.languages[0],
            stable=False,               # la estabilidad la decide TextStabilizer
            region=list(region),
            timestamp=now,
            engine=eng.name,
            blocks=[b.as_dict() for b in blocks],
        )

    def first_line(self, result: OCRResult) -> str:
        """Primera línea: sirve para "lee solamente el título"."""
        if not result or not result.text:
            return ""
        return result.text.splitlines()[0].strip()

    def close(self) -> None:
        if self._engine is not None:
            try:
                self._engine.close()
            except Exception:
                pass
        self._engine = None
        self._resolved = False
