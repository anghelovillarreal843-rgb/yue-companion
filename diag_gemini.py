"""Diagnóstico de los OJOS DE PANTALLA de YUE (Google Gemini).

Ejecuta:   python diag_gemini.py
           python diag_gemini.py --real        (hace UNA llamada real de prueba)
           python diag_gemini.py --pdf ruta.pdf

Comprueba, en orden:
  1) Configuración leída del .env (sin enseñar la clave entera).
  2) Que la CÁMARA está aislada: intenta colar una imagen de cámara y verifica
     que el módulo la rechaza.
  3) Captura de pantalla (mss / Pillow).
  4) Clave y modelo de Gemini contra su /models (NO gasta tokens).
  5) Opcional: una mirada real a tu pantalla.

Es de solo lectura: no modifica ningún archivo.
"""
from __future__ import annotations

import sys

try:
    import config  # noqa: F401  (carga el .env)
except Exception as exc:
    print("No pude importar config.py (¿ejecutas esto desde la carpeta "
          "yue_companion?):", exc)
    sys.exit(1)

try:
    from core import gemini_vision
except Exception as exc:
    print("No pude importar core/gemini_vision.py:", exc)
    sys.exit(1)


def _titulo(texto: str) -> None:
    print("\n" + texto)
    print("-" * len(texto))


def main() -> None:
    real = "--real" in sys.argv
    pdf = ""
    if "--pdf" in sys.argv:
        i = sys.argv.index("--pdf")
        if i + 1 < len(sys.argv):
            pdf = sys.argv[i + 1]

    print("=" * 64)
    print("  DIAGNÓSTICO · OJOS DE PANTALLA DE YUE (Google Gemini)")
    print("=" * 64)

    # ---------------------------------------------------------------- 1
    _titulo("1) Configuración (.env)")
    prov = str(getattr(config, "VISION_PROVIDER", "none"))
    activo = gemini_vision.activo()
    print(f"   VISION_PROVIDER   = {prov}   {'✅' if activo else '❌ (debe ser google)'}")
    clave = gemini_vision.api_key()
    if clave:
        print(f"   Clave             = ✅ cargada del entorno "
              f"({len(clave)} caracteres, empieza por «{clave[:4]}…»)")
    else:
        print("   Clave             = ❌ NO configurada")
        print("      -> Pon GEMINI_API_KEY=... en el .env "
              "(o VISION_API_KEY). Consíguela en https://aistudio.google.com/apikey")
    print(f"   Modelo            = {gemini_vision.modelo()}")
    print(f"   Host              = {gemini_vision.base_url()}")
    print(f"   VISION_OCR_ONLY   = {getattr(config, 'VISION_OCR_ONLY', False)}"
          + ("   ⚠️ en true, Gemini NO se usa" if getattr(config, "VISION_OCR_ONLY", False) else ""))
    print(f"   VISION_SCREEN_AUTO= {getattr(config, 'VISION_SCREEN_AUTO', True)}")

    # ---------------------------------------------------------------- 2
    _titulo("2) Aislamiento de la CÁMARA (lo más importante)")
    fallos = 0
    for origen in ("camara", "webcam", "camera_service", "frame_hub", "face_detector"):
        try:
            gemini_vision.analizar_imagen_b64("AAAA", "prueba", origen=origen)
            print(f"   ❌ origen «{origen}» NO fue bloqueado")
            fallos += 1
        except gemini_vision.GeminiVisionError:
            print(f"   ✅ origen «{origen}» bloqueado antes de tocar la red")
        except Exception as exc:
            print(f"   ⚠️ origen «{origen}»: error inesperado ({exc})")
            fallos += 1
    print(f"   Orígenes permitidos: {', '.join(sorted(gemini_vision.FUENTES_PERMITIDAS))}")
    print("   " + gemini_vision.politica_camara())
    if fallos:
        print("   ❌ EL CANDADO TIENE UN AGUJERO. No uses la visión hasta revisarlo.")

    # ---------------------------------------------------------------- 3
    _titulo("3) Captura de pantalla")
    try:
        from core import screen_capture
        info = screen_capture.capture_info()
        if info.get("ok"):
            print(f"   ✅ {info['width']}x{info['height']}px, {info['bytes']} bytes")
        else:
            print(f"   ❌ {info.get('note')}")
            print("      -> pip install mss pillow")
    except Exception as exc:
        print(f"   ❌ Error en la captura: {exc}")

    # ---------------------------------------------------------------- 4
    _titulo("4) Clave y modelo contra Gemini (no gasta tokens)")
    res = gemini_vision.preflight()
    if res.get("ok"):
        print(f"   ✅ Conectado. Modelo «{res.get('model')}» disponible "
              f"({res.get('modelos', '?')} modelos en la cuenta).")
    else:
        motivo = res.get("reason", "?")
        print(f"   ❌ {motivo}: {str(res.get('detail', ''))[:300]}")
        ayuda = {
            "disabled": "Pon VISION_PROVIDER=google en el .env.",
            "no_key": "Pon GEMINI_API_KEY=... en el .env.",
            "invalid_key": "La clave no vale. Genera otra en https://aistudio.google.com/apikey",
            "model_missing": "Cambia VISION_MODEL/GEMINI_VISION_MODEL por uno de los listados arriba.",
            "unreachable": "Revisa tu conexión o el proxy/firewall.",
        }.get(motivo, "")
        if ayuda:
            print(f"      -> {ayuda}")

    # ---------------------------------------------------------------- 5
    if pdf:
        _titulo(f"5) Prueba REAL con el PDF «{pdf}»")
        try:
            texto = gemini_vision.analizar_pdf(
                pdf, "¿De qué trata este documento? Responde en dos frases.")
            print("   ✅ Gemini leyó el PDF:")
            print("      " + texto.replace("\n", "\n      ")[:600])
        except Exception as exc:
            print(f"   ❌ {exc}")
    elif real:
        _titulo("5) Prueba REAL: una mirada a tu pantalla")
        print("   (esto SÍ consume una llamada de tu cuota)")
        try:
            texto = gemini_vision.analizar_pantalla(
                "¿Qué se ve en la pantalla? Responde en una sola frase.")
            print("   ✅ Gemini respondió:")
            print("      " + texto.replace("\n", "\n      ")[:600])
        except Exception as exc:
            print(f"   ❌ {exc}")
            hablado = getattr(exc, "hablado", "")
            if hablado:
                print(f"      YUE diría: «{hablado}»")
    else:
        _titulo("5) Prueba real")
        print("   (omitida) Añade --real para que YUE mire tu pantalla de verdad,")
        print("   o --pdf C:\\ruta\\documento.pdf para probar la lectura de un PDF.")

    print("\n" + "=" * 64)
    print("  Fin del diagnóstico")
    print("=" * 64)


if __name__ == "__main__":
    main()
