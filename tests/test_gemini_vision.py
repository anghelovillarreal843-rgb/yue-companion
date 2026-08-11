"""Pruebas de la visión de PANTALLA con Gemini. SIN red y SIN clave real.

Se comprueban las tres cosas que de verdad importan:

  1. Que la CÁMARA no puede llegar a Gemini de ninguna manera.
  2. Que YUE solo captura la pantalla cuando la orden lo justifica (si no, no
     gastaría cuota en cada frase del chat).
  3. Que cada error de la API se traduce a un motivo y a una frase que YUE
     pueda decir en voz alta.

Ejecuta:  python tests/test_gemini_vision.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import gemini_vision, screen_vision  # noqa: E402


_fallos = []


def comprobar(condicion, descripcion):
    if condicion:
        print(f"  ✅ {descripcion}")
    else:
        print(f"  ❌ {descripcion}")
        _fallos.append(descripcion)


# ---------------------------------------------------------------------------
def test_camara_bloqueada():
    print("\n1) La cámara NO puede llegar a Gemini")
    for origen in ("camara", "cámara", "webcam", "camera_service", "frame_hub",
                   "CAMERA", "face_detector", "camera_observer"):
        try:
            gemini_vision.analizar_imagen_b64("QUJD", "hola", origen=origen)
            comprobar(False, f"origen «{origen}» debía ser rechazado")
        except gemini_vision.GeminiVisionError as exc:
            comprobar("BLOQUEADO" in str(exc) or exc.kind == "config",
                      f"origen «{origen}» rechazado")
        except Exception as exc:
            comprobar(False, f"origen «{origen}»: error raro {exc}")

    # Un origen inventado tampoco pasa: la lista es blanca, no negra.
    try:
        gemini_vision.analizar_imagen_b64("QUJD", "hola", origen="lo_que_sea")
        comprobar(False, "un origen desconocido debía ser rechazado")
    except gemini_vision.GeminiVisionError:
        comprobar(True, "un origen desconocido queda fuera de la lista blanca")

    comprobar("camara" not in gemini_vision.FUENTES_PERMITIDAS
              and "webcam" not in gemini_vision.FUENTES_PERMITIDAS,
              "la lista blanca no contiene ningún origen de cámara")


# ---------------------------------------------------------------------------
def test_decide_cuando_mirar():
    print("\n2) YUE solo mira cuando hace falta")

    si_mira = [
        ("mira mi pantalla", "pantalla"),
        ("YUE, ¿qué hay en mi pantalla?", "pantalla"),
        ("lee lo que aparece aquí", "pantalla"),
        ("¿qué error aparece?", "pantalla"),
        ("dime qué aplicación tengo abierta", "pantalla"),
        ("explícame esta imagen", "imagen"),
        ("analiza este PDF", "pdf"),
        ("¿de qué trata este documento?", "pdf"),
        ("resume este documento", "pdf"),
        ("¿qué está pasando en este video?", "video"),
        ("describe esta escena", "video"),
        ("analiza esta página", "pantalla"),
        ("¿qué dice ese documento?", "pdf"),
    ]
    for frase, tipo_esperado in si_mira:
        res = screen_vision.clasificar(frase)
        comprobar(res["necesita"], f"«{frase}» → necesita visión")
        if res["necesita"]:
            comprobar(res["tipo"] == tipo_esperado,
                      f"«{frase}» → tipo {res['tipo']} (esperado {tipo_esperado})")

    no_mira = [
        "hola YUE, ¿cómo estás?",
        "cuéntame un chiste",
        "¿qué hora es?",
        "recuérdame comprar pan",
        "me siento cansado hoy",
        "¿cuántos años tienes?",
        "pon música tranquila",
        "gracias, eres un cielo",
        "¿me ves?",
        "¿qué opinas de la canción?",
    ]
    for frase in no_mira:
        res = screen_vision.clasificar(frase)
        comprobar(not res["necesita"], f"«{frase}» → NO captura nada")


# ---------------------------------------------------------------------------
def test_errores_traducidos():
    print("\n3) Los errores se traducen a algo que YUE puede decir")
    casos = [
        (401, "API key not valid", "clave"),
        (403, "permission denied", "clave"),
        (429, "Resource has been exhausted (quota)", "cuota"),
        (404, "models/xxx is not found", "modelo"),
        (413, "request payload size exceeds the limit", "tamano"),
        (400, "Unsupported MIME type: application/zip", "formato"),
        (500, "internal error", "error"),
    ]
    for status, cuerpo, kind_esperado in casos:
        err = gemini_vision._clasificar_http(status, cuerpo)
        comprobar(err.kind == kind_esperado,
                  f"HTTP {status} → motivo «{err.kind}» (esperado «{kind_esperado}»)")
        comprobar(bool(err.hablado) and len(err.hablado) > 10,
                  f"HTTP {status} tiene frase hablada en español")

    # Sin clave, la llamada falla ANTES de tocar la red (no cuelga sin internet).
    guardadas = {}
    for nombre in ("VISION_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        guardadas[nombre] = os.environ.pop(nombre, None)
    try:
        import config as _config
        antiguos = {}
        for nombre in ("VISION_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
            antiguos[nombre] = getattr(_config, nombre, None)
            setattr(_config, nombre, "")
        try:
            gemini_vision._post([{"text": "hola"}])
            comprobar(False, "sin clave debía lanzar error de configuración")
        except gemini_vision.GeminiVisionError as exc:
            comprobar(exc.kind == "config", "sin clave → error de configuración, sin red")
        finally:
            for nombre, valor in antiguos.items():
                if valor is not None:
                    setattr(_config, nombre, valor)
    finally:
        for nombre, valor in guardadas.items():
            if valor is not None:
                os.environ[nombre] = valor


# ---------------------------------------------------------------------------
def test_respuesta_vacia():
    print("\n4) Respuestas raras de Gemini no dejan a YUE muda")
    try:
        gemini_vision._texto_de_respuesta({"candidates": []})
        comprobar(False, "sin candidatos debía lanzar error")
    except gemini_vision.GeminiVisionError as exc:
        comprobar(exc.kind == "respuesta", "sin candidatos → motivo «respuesta»")

    try:
        gemini_vision._texto_de_respuesta(
            {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}]})
        comprobar(False, "respuesta cortada debía lanzar error")
    except gemini_vision.GeminiVisionError as exc:
        comprobar("espacio" in exc.hablado or exc.kind == "respuesta",
                  "respuesta cortada → aviso claro")

    texto = gemini_vision._texto_de_respuesta(
        {"candidates": [{"content": {"parts": [{"text": "Veo "}, {"text": "un error."}]}}]})
    comprobar(texto == "Veo un error.", "junta bien las partes de texto")


# ---------------------------------------------------------------------------
def test_nombre_de_documento():
    print("\n5) Se reconoce el PDF abierto por el título de la ventana")
    casos = [
        ("informe_final.pdf - Adobe Acrobat Reader", "informe_final.pdf"),
        ("tesis - Google Chrome", "tesis"),
        ("(3) manual.pdf — Microsoft Edge", "manual.pdf"),
    ]
    for titulo, esperado in casos:
        obtenido = screen_vision._nombre_desde_titulo(titulo)
        comprobar(obtenido == esperado,
                  f"«{titulo}» → «{obtenido}» (esperado «{esperado}»)")


# ---------------------------------------------------------------------------
def main():
    print("=" * 62)
    print("  PRUEBAS · VISIÓN DE PANTALLA CON GEMINI (sin red)")
    print("=" * 62)
    test_camara_bloqueada()
    test_decide_cuando_mirar()
    test_errores_traducidos()
    test_respuesta_vacia()
    test_nombre_de_documento()
    print("\n" + "=" * 62)
    if _fallos:
        print(f"  ❌ {len(_fallos)} comprobaciones fallaron:")
        for f in _fallos:
            print(f"     · {f}")
        sys.exit(1)
    print("  ✅ TODO CORRECTO")
    print("=" * 62)


if __name__ == "__main__":
    main()
