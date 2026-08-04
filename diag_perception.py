"""Diagnóstico del YUE V3 PERCEPTION SYSTEM.

Ejecuta:   python diag_perception.py

Comprueba, en orden y sin gastar nada:
  1) Config del V3 leída del .env.
  2) Dependencias: OpenCV, MediaPipe (FaceDetection), DeepFace, psutil.
  3) Perfil de rendimiento resuelto (LOW/MEDIUM/HIGH) y sus cadencias.
  4) Aviso de posible conflicto de cámara con el observador clásico.
  5) Veredicto con el siguiente paso.

Es de solo lectura: NO abre la cámara, NO analiza imágenes, NO modifica archivos.
"""
from __future__ import annotations

import importlib
import sys


def _mark(ok: bool) -> str:
    return "✅" if ok else "❌"


def _has(mod: str) -> bool:
    try:
        importlib.import_module(mod)
        return True
    except Exception:
        return False


def main() -> None:
    print("=" * 62)
    print("  DIAGNÓSTICO DEL YUE V3 PERCEPTION SYSTEM")
    print("=" * 62)

    try:
        import config
    except Exception as exc:
        print("No pude importar config.py (¿ejecutas esto desde yue_companion/?):", exc)
        sys.exit(1)

    # 1) Config
    print("\n1) Config")
    enabled = bool(getattr(config, "VISION_V3_ENABLED", False))
    print(f"   VISION_V3_ENABLED         = {enabled}")
    print(f"   VISION_PERF_PROFILE       = {getattr(config, 'VISION_PERF_PROFILE', 'auto')}")
    print(f"   CAMERA_INDEX              = {getattr(config, 'CAMERA_INDEX', 0)}")
    print(f"   CAMERA_PRIVACY_MODE       = {getattr(config, 'CAMERA_PRIVACY_MODE', True)}")
    print(f"   VISION_EMOTION_PERSONA    = {getattr(config, 'VISION_EMOTION_PERSONA', 'YUE')}")
    print(f"   MAX_EMOTION_RESPONSES/H   = {getattr(config, 'MAX_EMOTION_RESPONSES_PER_HOUR', 4)}")

    # 2) Dependencias
    print("\n2) Dependencias")
    cv2_ok = _has("cv2")
    # MediaPipe FaceDetection: importamos el submódulo REAL (no hasattr, que da
    # falsos negativos por el lazy-loading) e informamos el motivo si falla.
    mp_ok = False
    mp_err = ""
    try:
        import mediapipe as mp  # noqa
        # Ruta EXACTA que usa core/vision/face_detector.py en ejecución, para
        # que el diagnóstico coincida con lo que pasará de verdad.
        _fd = mp.solutions.face_detection.FaceDetection  # noqa
        mp_ok = True
    except Exception as exc:
        mp_ok = False
        mp_err = f"{type(exc).__name__}: {exc}"
    deep_ok = _has("deepface")
    psutil_ok = _has("psutil")
    print(f"   {_mark(cv2_ok)} OpenCV (opencv-python)")
    if mp_ok:
        print(f"   {_mark(True)} MediaPipe FaceDetection")
    else:
        print(f"   {_mark(False)} MediaPipe FaceDetection  -> se usará Haar de OpenCV (presencia sí, funciona)")
        if mp_err:
            print(f"        motivo: {mp_err}")
    print(f"   {_mark(deep_ok)} DeepFace" + ("" if deep_ok else "  -> pip install deepface tf-keras (sin él NO hay emociones)"))
    print(f"   {_mark(psutil_ok)} psutil" + ("" if psutil_ok else "  -> pip install psutil (para auto-detectar la gama; si falta, MEDIUM)"))

    # 3) Perfil
    print("\n3) Perfil de rendimiento")
    try:
        from core.vision.perf_profile import resolve_profile, describe
        prof = resolve_profile()
        print("   " + describe(prof))
    except Exception as exc:
        print("   ❌ No pude resolver el perfil:", exc)

    # 4) Conflicto de cámara
    print("\n4) Convivencia con el observador clásico")
    legacy = bool(getattr(config, "CAMERA_ENABLED", True))
    if enabled and legacy:
        print("   ⚠  El observador clásico (CAMERA_ENABLED=true) y el V3 están ambos activos.")
        print("      Comparten webcam: pon CAMERA_ENABLED=false o dale a V3 otra CAMERA_INDEX.")
    elif enabled and not legacy:
        print("   ✅ V3 activo y observador clásico apagado: sin conflicto de cámara.")
    else:
        print("   ℹ  V3 desactivado: el observador clásico funciona como siempre.")

    # 5) Veredicto
    print("\n5) Veredicto")
    if not enabled:
        print("   El V3 está APAGADO. Para probarlo: VISION_V3_ENABLED=true en el .env")
        print("   (recuerda CAMERA_ENABLED=false o un CAMERA_INDEX distinto).")
    elif not cv2_ok:
        print("   Falta OpenCV: la cámara no puede iniciarse. pip install opencv-python")
    elif not deep_ok:
        print("   Listo para presencia y atención, pero SIN emociones (falta DeepFace).")
        print("   Instala:  pip install deepface tf-keras")
    else:
        print("   Todo en su sitio: presencia, atención y emociones deberían funcionar.")
    print()


if __name__ == "__main__":
    main()
