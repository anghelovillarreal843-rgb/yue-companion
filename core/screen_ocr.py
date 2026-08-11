"""OCR de PANTALLA unificado: reutiliza los dos motores que YUE ya tiene.

Problema real que resuelve: `core/screen_text.py` solo sabe usar **pytesseract**,
asi que en una maquina con EasyOCR o PaddleOCR instalados (pero sin el binario de
Tesseract) el OCR de pantalla se declaraba "no disponible" y YUE se quedaba
diciendo «no puedo ver» aunque tenia con que leer.

Aqui NO se reescribe ningun motor: se encadenan los dos que ya existen.

    1) core.screen_text   -> pytesseract  (ademas da cajas por palabra, que es lo
                             que necesita la accion click_text del control de PC)
    2) vision.ocr.ocr_engine.OCREngineChain -> PaddleOCR -> EasyOCR -> Tesseract

Se prueba el que este disponible; si el primero no da texto, se intenta el otro.
Ninguna funcion lanza excepcion por falta de motor: devuelven texto vacio y el
motivo, para que la capa de arriba pueda decidir.
"""
from __future__ import annotations

import time

_cadena_camara = None      # instancia cacheada de OCREngineChain
_cadena_probada = False


def _config(nombre, defecto):
    try:
        import config
        return getattr(config, nombre, defecto)
    except Exception:
        return defecto


# ------------------------------------------------------------ motor 1: tesseract
def _tesseract_disponible() -> bool:
    try:
        from core import screen_text
        return bool(screen_text.get_engine().available())
    except Exception:
        return False


def _leer_con_tesseract(image) -> str:
    try:
        from core import screen_text
        return (screen_text.read_screen(image=image) or "").strip()
    except Exception as exc:
        print(f"[VISION] OCR tesseract fallo: {exc}")
        return ""


# ------------------------------------- motor 2: cadena de la camara (paddle/easy)
def _cadena() :
    """Instancia unica de la cadena PaddleOCR -> EasyOCR -> Tesseract."""
    global _cadena_camara, _cadena_probada
    if _cadena_probada:
        return _cadena_camara
    _cadena_probada = True
    try:
        from vision.ocr.ocr_engine import OCREngineChain
        idiomas = str(_config("VISION_OCR_LANGUAGES", "es") or "es")
        langs = tuple(x.strip().lower() for x in idiomas.split(",") if x.strip()) or ("es",)
        _cadena_camara = OCREngineChain(
            languages=langs,
            preferred=str(_config("VISION_OCR_ENGINE", "auto") or "auto"),
            min_confidence=float(_config("VISION_OCR_MIN_CONFIDENCE", 0.35) or 0.35),
        )
    except Exception as exc:
        print(f"[VISION] cadena OCR de camara no disponible: {exc}")
        _cadena_camara = None
    return _cadena_camara


def _a_numpy(image):
    """PIL.Image -> numpy array RGB (lo que espera la cadena de la camara)."""
    if image is None:
        return None
    try:
        import numpy as np
        if hasattr(image, "shape"):        # ya es un array
            return image
        return np.array(image.convert("RGB"))
    except Exception:
        return None


def _leer_con_cadena(image) -> str:
    cadena = _cadena()
    if cadena is None:
        return ""
    array = _a_numpy(image)
    if array is None:
        return ""
    try:
        if not cadena.available:
            return ""
        resultado = cadena.read(array)
        return (getattr(resultado, "text", "") or "").strip()
    except Exception as exc:
        print(f"[VISION] OCR (cadena camara) fallo: {exc}")
        return ""


# ----------------------------------------------------------------- API publica
def available() -> bool:
    """Hay ALGUN motor OCR utilizable para leer la pantalla."""
    if _tesseract_disponible():
        return True
    cadena = _cadena()
    try:
        return bool(cadena is not None and cadena.available)
    except Exception:
        return False


def engine_name() -> str:
    """Nombre del motor que se usaria ahora mismo (para logs y diagnostico)."""
    if _tesseract_disponible():
        return "pytesseract"
    cadena = _cadena()
    try:
        if cadena is not None and cadena.available:
            return str(cadena.engine_name)
    except Exception:
        pass
    return "ninguno"


def read(image, max_chars: int = 8000) -> dict:
    """Lee el texto de una imagen PIL. NUNCA lanza excepcion.

    Devuelve {'text', 'chars', 'engine', 'elapsed', 'error'}.
    """
    inicio = time.time()
    texto, motor, error = "", "", ""

    if image is None:
        return {"text": "", "chars": 0, "engine": "", "elapsed": 0.0,
                "error": "no hay imagen que leer"}

    # 1) Tesseract primero: es el mas rapido en pantallas de texto nitido y ya
    #    esta integrado con click_text.
    if _tesseract_disponible():
        texto = _leer_con_tesseract(image)
        motor = "pytesseract"

    # 2) Si no habia Tesseract (o no saco nada), la cadena de la camara.
    if not texto:
        alternativo = _leer_con_cadena(image)
        if alternativo:
            texto = alternativo
            try:
                motor = str(_cadena().engine_name)
            except Exception:
                motor = "cadena"

    if not texto and not motor:
        error = ("no hay ningun motor OCR instalado (instala Tesseract, "
                 "easyocr o paddleocr)")

    texto = (texto or "")[:max_chars]
    return {
        "text": texto,
        "chars": len(texto),
        "engine": motor or engine_name(),
        "elapsed": round(time.time() - inicio, 3),
        "error": error,
    }


def read_screen_text(image=None, max_chars: int = 8000) -> str:
    """Atajo: solo el texto. Si no se pasa imagen, captura la pantalla."""
    if image is None:
        try:
            from core import screen_capture
            image = screen_capture.grab_frame().image
        except Exception as exc:
            print(f"[VISION] no pude capturar para OCR: {exc}")
            return ""
    return read(image, max_chars=max_chars).get("text", "")


def text_density(image, texto: str | None = None) -> float:
    """Cuanto texto hay respecto al tamano de la pantalla (0..1 aproximado).

    Sirve para la decision inteligente OCR / multimodal / ambos sin gastar una
    llamada a la IA. Es una heuristica barata, no una medida exacta.
    """
    if texto is None:
        texto = read(image).get("text", "")
    if not texto:
        return 0.0
    try:
        ancho, alto = image.size
    except Exception:
        ancho, alto = 1920, 1080
    # ~1 caracter legible por cada 900 px^2 se considera "pantalla llena de texto".
    referencia = max(1.0, (ancho * alto) / 900.0)
    return max(0.0, min(1.0, len(texto) / referencia))
