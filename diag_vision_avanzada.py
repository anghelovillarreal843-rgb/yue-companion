"""Diagnóstico de la VISIÓN AVANZADA de YUE.

Ejecuta desde la carpeta del proyecto:

    python diag_vision_avanzada.py

Comprueba, sin abrir la cámara salvo que se lo pidas:

  - qué librerías están instaladas (OpenCV, MediaPipe, motores OCR),
  - qué modelos hay en models/vision/ y cuáles faltan,
  - qué capacidades quedan activas y por qué las demás no,
  - qué cámaras responden,
  - la configuración de privacidad vigente,
  - si hay conflicto entre los tres sistemas de cámara.

Con `--camara` abre la webcam unos segundos y mide FPS reales.
No guarda ninguna imagen.
"""
from __future__ import annotations

import importlib.util
import sys
import time


def _hay(modulo: str) -> bool:
    try:
        return importlib.util.find_spec(modulo) is not None
    except Exception:
        return False


def titulo(texto: str) -> None:
    print()
    print("=" * 70)
    print(texto)
    print("=" * 70)


def marca(ok: bool) -> str:
    return "[ OK ]" if ok else "[FALTA]"


def main() -> int:
    titulo("YUE — Diagnóstico de visión avanzada")

    # ---------------- librerías ----------------
    titulo("1. Librerías")
    libs = {
        "opencv-python": "cv2",
        "numpy": "numpy",
        "mediapipe": "mediapipe",
        "PaddleOCR": "paddleocr",
        "EasyOCR": "easyocr",
        "pytesseract": "pytesseract",
    }
    for nombre, modulo in libs.items():
        print(f"  {marca(_hay(modulo))} {nombre}")

    if not _hay("cv2"):
        print("\n  Sin OpenCV no hay cámara. Instala:  pip install opencv-python")
    if not any(_hay(m) for m in ("paddleocr", "easyocr", "pytesseract")):
        print("\n  Sin motor OCR, YUE no podrá leer textos por cámara.")
        print("  Recomendado:  pip install paddleocr paddlepaddle")

    # ---------------- configuración ----------------
    titulo("2. Configuración (.env)")
    try:
        from vision import settings as settings_mod
        cfg = settings_mod.load()
    except Exception as exc:
        print(f"  No pude leer la configuración: {exc}")
        return 1

    print(f"  VISION_MP_ENABLED ............ {cfg.enabled}")
    print(f"  VISION_PERCEPTION_ENABLED .... {cfg.perception_enabled}")
    print(f"  CAMERA_ENABLED ............... {cfg.camera_enabled}")
    print(f"  Cámara ....................... índice {cfg.camera_index}, "
          f"{cfg.width}x{cfg.height} @ {cfg.target_fps:.0f} FPS")
    print(f"  Perfil de rendimiento ........ {cfg.performance_mode}")
    print(f"  OCR .......................... {cfg.ocr_enabled} "
          f"(motor {cfg.ocr_engine}, idiomas {','.join(cfg.ocr_languages)})")
    print(f"  Solo bajo petición (OCR) ..... {cfg.ocr_only_on_request}")
    print(f"  Acciones ..................... {cfg.actions_enabled}")
    print(f"  Emociones .................... {cfg.emotions_enabled}")
    print(f"  Escena ....................... {cfg.scene_description_enabled}")
    print(f"  Migración (REPLACE_LEGACY) ... {cfg.replace_legacy}")

    if not cfg.enabled:
        print("\n  AVISO: el sistema está APAGADO. Pon VISION_MP_ENABLED=true "
              "en el .env para usarlo.")

    # ---------------- conflicto de cámaras ----------------
    titulo("3. Conflicto entre sistemas de cámara")
    try:
        import config as yue_config
        clasico = bool(getattr(yue_config, "CAMERA_ENABLED", True))
        v3 = bool(getattr(yue_config, "VISION_V3_ENABLED", False))
        indice_clasico = int(getattr(yue_config, "CAMERA_INDEX", 0))
    except Exception:
        clasico, v3, indice_clasico = True, False, 0

    activos = []
    if clasico:
        activos.append(f"CameraObserver clásico (índice {indice_clasico})")
    if v3:
        activos.append("core.vision V3")
    if cfg.enabled:
        activos.append(f"paquete vision/ (índice {cfg.camera_index})")

    for a in activos:
        print(f"  · {a}")

    if len(activos) > 1:
        mismos = (clasico and cfg.enabled and indice_clasico == cfg.camera_index)
        if mismos or v3:
            print("\n  PROBLEMA: hay más de un sistema queriendo la MISMA webcam.")
            print("  Solución: pon CAMERA_ENABLED=false y VISION_V3_ENABLED=false,")
            print("  o dale a cada sistema un CAMERA_INDEX distinto.")
        else:
            print("\n  Hay varios sistemas activos, pero con índices distintos: correcto.")
    elif activos:
        print("\n  Un solo sistema activo: correcto.")
    else:
        print("\n  Ningún sistema de cámara activo.")

    # ---------------- modelos ----------------
    titulo("4. Modelos")
    try:
        from vision.models.model_registry import ModelRegistry
        registry = ModelRegistry(variant=cfg.model_variant,
                                 allow_download=cfg.allow_download)
        print(registry.describe())
    except Exception as exc:
        print(f"  No pude revisar los modelos: {exc}")
        registry = None

    # ---------------- capacidades ----------------
    titulo("5. Capacidades resultantes")
    try:
        from vision import capabilities as caps_mod
        from vision.ocr.ocr_engine import OCREngineChain
        cadena = OCREngineChain(languages=cfg.ocr_languages, preferred=cfg.ocr_engine)
        caps = caps_mod.detect(settings=cfg, registry=registry, ocr_chain=cadena)
        print(caps.describe_es())
        print()
        print("  Mapa (formato del pedido):")
        for clave, valor in caps.as_dict().items():
            print(f"    {clave:22s} {valor}")
    except Exception as exc:
        print(f"  No pude calcular las capacidades: {exc}")

    # ---------------- cámaras físicas ----------------
    titulo("6. Cámaras disponibles (prueba real de cada backend)")
    if not _hay("cv2"):
        print("  (necesito OpenCV para buscarlas)")
    else:
        try:
            from vision.camera_backend import backend_candidates, probe_all
            backends = [n for n, _v in backend_candidates()]
            print(f"  Backends que probaré: {', '.join(backends)}")
            print("  (esto tarda unos segundos: cada combinación se valida de verdad)")
            print()
            resultados = probe_all(range(cfg.max_camera_index),
                                   cfg.width, cfg.height)
            for r in resultados:
                print("  " + r.as_line())
            buenos = [r for r in resultados if r.usable]
            print()
            if buenos:
                mejor = max(buenos, key=lambda r: r.fps)
                print(f"  RECOMENDADO: índice {mejor.index} con {mejor.backend}")
                print(f"  Ponlo en el .env:")
                print(f"    CAMERA_INDEX={mejor.index}")
                print(f"    VISION_CAMERA_BACKEND={mejor.backend.lower()}")
            else:
                abren = [r for r in resultados if r.opened]
                if abren:
                    print("  PROBLEMA: alguna cámara ABRE pero no entrega flujo sostenido.")
                    print("  Suele ser otro programa reteniéndola (Teams, Zoom, OBS,")
                    print("  Discord, el navegador) o el driver de la webcam.")
                    print("  Prueba: cierra esos programas y vuelve a ejecutar esto.")
                else:
                    print("  No encontré ninguna cámara utilizable.")
                    print("  Revisa Configuración > Privacidad > Cámara en Windows.")
        except Exception as exc:
            print(f"  Fallo buscando cámaras: {exc}")

    # ---------------- privacidad ----------------
    titulo("7. Privacidad")
    try:
        from vision.privacy_manager import from_settings
        privacidad = from_settings(cfg)
        privacidad.set_camera(True)
        print("  " + privacidad.describe_es().replace("; ", "\n  · "))
        estado = privacidad.state()
        print()
        print(f"  Guardar fotogramas ... {estado.save_frames}  (debería ser False)")
        print(f"  Guardar eventos ...... {estado.save_events}  (debería ser False)")
        print(f"  Permitir nube ........ {estado.allow_cloud}  (debería ser False)")
        print(f"  Proceso local ........ {estado.process_local} (debería ser True)")
    except Exception as exc:
        print(f"  No pude leer la privacidad: {exc}")

    # ---------------- prueba real de cámara ----------------
    if "--camara" in sys.argv:
        titulo("8. Prueba de cámara (5 segundos, sin guardar nada)")
        try:
            from vision.camera_backend import CaptureOpener
            from vision.camera_manager import CameraManager
            from vision.frame_hub import FrameHub
            hub = FrameHub()
            abridor = CaptureOpener()
            mgr = CameraManager(hub, index=cfg.camera_index, width=cfg.width,
                                height=cfg.height, target_fps=cfg.target_fps,
                                owner="diagnostico",
                                max_index=cfg.max_camera_index,
                                capture_factory=abridor)
            if not mgr.start():
                print(f"  No pude abrir la cámara: {mgr.status().last_error}")
            else:
                print("  Cámara abierta. Midiendo…")
                inicio = time.time()
                fotogramas, ultimo = 0, -1
                while time.time() - inicio < 5.0:
                    fid = hub.frame_id()
                    if fid != ultimo:
                        ultimo = fid
                        fotogramas += 1
                    time.sleep(0.005)
                fps = fotogramas / 5.0
                print(f"  Backend en uso: {abridor.last_backend or 'desconocido'}")
                print(f"  Fotogramas recibidos: {fotogramas}  ->  {fps:.1f} FPS reales")
                frame = hub.latest_bgr()
                if frame is not None:
                    print(f"  Resolución real: {frame.shape[1]}x{frame.shape[0]}")
                print()
                if fotogramas <= 2:
                    print("  PROBLEMA GRAVE: la cámara abre pero no entrega flujo.")
                    print("  Causas más habituales, en orden:")
                    print("    1. Otro programa la tiene retenida (Teams, Zoom, OBS,")
                    print("       Discord, el navegador). Ciérralos todos y reintenta.")
                    print("    2. El backend no encaja con tu webcam. Mira la sección 6")
                    print("       y fija VISION_CAMERA_BACKEND con el que sí funcione.")
                    print("    3. El índice no es el correcto. Prueba otro CAMERA_INDEX.")
                    print("    4. Permisos: Configuración > Privacidad > Cámara.")
                elif fps < cfg.target_fps * 0.5:
                    print("  AVISO: los FPS reales están por debajo del objetivo.")
                    print("  Prueba VISION_PERFORMANCE_MODE=low o baja la resolución.")
                else:
                    print("  La cámara entrega flujo correctamente.")
                mgr.stop()
                print("  Cámara liberada correctamente.")
        except Exception as exc:
            print(f"  Fallo en la prueba: {exc}")
    else:
        titulo("8. Prueba de cámara")
        print("  Omitida. Añade  --camara  para abrirla 5 segundos y medir FPS.")

    titulo("Resumen")
    print("  Documentación completa:  docs/VISION_AVANZADA.md")
    print("  Pruebas:                 python -m unittest tests.test_vision_avanzada")
    print("  Dentro de YUE:           /vision capacidades   |   /vision modelos")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
