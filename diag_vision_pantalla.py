"""Diagnostico de la VISION DE PANTALLA de YUE.

    python diag_vision_pantalla.py

Comprueba, por separado y sin abrir la interfaz:
  1. Que monitores hay y si la captura funciona en cada uno.
  2. Que motores OCR estan instalados.
  3. Que proveedores multimodales tienen clave y estan disponibles.
  4. Una mirada REAL de principio a fin (captura -> clasificacion -> vision/OCR).

No imprime ninguna clave. Si algo falla, dice exactamente que y como arreglarlo.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def titulo(texto):
    print()
    print("=" * 72)
    print(texto)
    print("=" * 72)


def main():
    fallos = []

    # ---------------------------------------------------------- 1) monitores
    titulo("1. MONITORES Y CAPTURA")
    from core import screen_capture
    monitores = screen_capture.list_monitors()
    if not monitores:
        print("  [X] No detecte ningun monitor.")
        print("      Instala mss y pillow:  pip install mss pillow")
        fallos.append("captura")
    for mon in monitores:
        print(f"  - Monitor {mon['index']}: {mon['width']}x{mon['height']} "
              f"en ({mon['left']},{mon['top']})"
              f"{'  [principal]' if mon.get('primary') else ''}")

    for mon in monitores:
        info = screen_capture.capture_info(monitor=mon["index"])
        estado = "OK " if info["ok"] else "X  "
        print(f"  [{estado}] Captura monitor {mon['index']}: "
              f"{info['width']}x{info['height']}px, {info['bytes']} bytes, "
              f"frame_age={info['frame_age']}s, metodo={info['method']}")
        if not info["ok"]:
            print(f"        motivo: {info['note']}")
            if "negra" in info["note"].lower():
                print("        -> suele ser video protegido (DRM) o aceleracion por "
                      "hardware. Desactivala en esa aplicacion.")
            fallos.append(f"captura monitor {mon['index']}")

    # --------------------------------------------------------------- 2) OCR
    titulo("2. MOTORES OCR (el respaldo final)")
    from core import screen_ocr
    if screen_ocr.available():
        print(f"  [OK ] Hay OCR disponible. Motor elegido: {screen_ocr.engine_name()}")
    else:
        print("  [X ] No hay ningun motor OCR instalado.")
        print("       Instala UNO de estos (cualquiera sirve):")
        print("         winget install UB-Mannheim.TesseractOCR")
        print("         pip install easyocr")
        print("         pip install paddleocr")
        print("       Sin OCR, si la vision multimodal se cae YUE se queda ciega.")
        fallos.append("ocr")

    # ------------------------------------------------------- 3) proveedores
    titulo("3. FILA DE PROVEEDORES VISUALES (VisionRouter)")
    import config
    print(f"  VISION_PROVIDER = {config.VISION_PROVIDER}")
    if config.VISION_PROVIDER == "none":
        print("  [!] La vision multimodal esta APAGADA a proposito.")
        print("      YUE solo mirara la pantalla por OCR. Para encenderla, pon")
        print("      VISION_PROVIDER=auto en el .env.")
    if getattr(config, "VISION_OCR_ONLY", False):
        print("  [!] VISION_OCR_ONLY=true: se ignora todo modelo multimodal.")

    from core import vision_router
    router = vision_router.build_default_vision_router(log=lambda m: None)
    disponibles = 0
    for p in router.report():
        if p["disponible"]:
            disponibles += 1
            print(f"  [OK ] {p['nombre']:11} -> {p['modelo']}")
        else:
            motivo = p["ultimo_error"] or ("sin clave configurada"
                                           if not p["tiene_clave"] else "no disponible")
            print(f"  [-- ] {p['nombre']:11} -> {p['modelo'][:46]}  ({motivo})")
    print()
    if disponibles:
        print(f"  {disponibles} proveedor(es) multimodal(es) en fila. Si uno falla por "
              "cuota,\n  429, timeout o 5xx, se pasa al siguiente automaticamente.")
    else:
        print("  [X ] Ningun proveedor multimodal disponible.")
        print("       Pon al menos una clave en el .env: GEMINI_API_KEY,")
        print("       OPENROUTER_API_KEY, GROQ_API_KEY, TOGETHER_API_KEY u OPENAI_API_KEY.")
        if screen_ocr.available():
            print("       (Aun asi YUE puede leer la pantalla por OCR.)")
        fallos.append("proveedores visuales")

    # ------------------------------------------------------- 4) mirada real
    titulo("4. MIRADA REAL DE PRINCIPIO A FIN")
    if "captura" in fallos:
        print("  Se omite: la captura no funciona.")
    else:
        print("  Mirando la pantalla ahora mismo...")
        inicio = time.time()
        try:
            from core.ai_engine import AIEngine
            obs = AIEngine().look_screen(
                question="Describe brevemente que hay en la pantalla.",
                force_fresh=True,
            )
        except Exception as exc:
            print(f"  [X ] La mirada fallo por completo: {exc}")
            fallos.append("mirada")
            obs = None

        if obs is not None:
            print()
            print(f"  Monitor .............. {obs.monitor}/{obs.monitors_total}")
            print(f"  Resolucion ........... {obs.width}x{obs.height}")
            print(f"  Antiguedad del frame . {obs.frame_age:.2f}s")
            print(f"  Contenido detectado .. {obs.detected_content_type} "
                  f"(confianza {obs.confidence:.2f})")
            print(f"  Estrategia ........... {obs.strategy}")
            print(f"  Vision multimodal .... "
                  f"{'SI (' + obs.provider_used + '/' + obs.model_used + ')' if obs.has_vision() else 'NO'}")
            print(f"  Texto por OCR ........ {obs.ocr_chars} caracteres")
            print(f"  Tiempo total ......... {time.time() - inicio:.1f}s")
            if obs.visual_description:
                print()
                print("  Descripcion:")
                for linea in obs.visual_description.splitlines()[:6]:
                    print("    " + linea[:100])
            if obs.errors:
                print()
                print("  Incidencias:")
                for e in obs.errors[:8]:
                    print("    - " + str(e)[:110])
            print()
            if obs:
                print("  [OK ] YUE puede describir tu pantalla.")
            else:
                print("  [X ] YUE no obtuvo ni descripcion ni texto.")
                fallos.append("mirada")

    # -------------------------------------------------------------- resumen
    titulo("RESUMEN")
    if not fallos:
        print("  Todo correcto. La vision de pantalla de YUE esta operativa.")
        return 0
    print("  Puntos a revisar: " + ", ".join(dict.fromkeys(fallos)))
    print()
    print("  Recuerda: mientras haya AL MENOS un motor OCR, YUE puede seguir")
    print("  leyendo la pantalla aunque toda la vision multimodal este caida.")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelado.")
        raise SystemExit(130)
