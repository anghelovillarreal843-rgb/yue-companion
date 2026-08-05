"""Comandos de visión de YUE (paso 27).

Devuelve texto para que YUE lo diga. `system` es un `VisionSystem` (o None). Todo
tolerante a que el sistema esté apagado.
"""
from __future__ import annotations


def _fmt_status(system) -> str:
    r = system.status_report()
    return (
        "Estado de visión:\n"
        f"Cámara: {r['camara']}\n"
        f"Rostro: {r['rostro']}\n"
        f"Postura: {r['postura']}\n"
        f"Gestos: {r['gestos']}\n"
        f"Objetos: {r['objetos']}\n"
        f"Clasificador: {r['clasificador']}\n"
        f"Segmentación: {r['segmentacion']}\n"
        f"FPS de captura: {r['fps_captura']}\n"
        f"Privacidad: {r['privacidad']}"
    )


def handle(system, command: str, arg: str = "") -> str | None:
    """Procesa /camara y /vision. Devuelve None si no reconoce el comando."""
    command = (command or "").lower().strip()
    arg = (arg or "").lower().strip()

    if system is None:
        if command in {"/vision", "/visión", "/camara", "/cámara"}:
            return ("El sistema de visión por cámara está desactivado. "
                    "Actívalo con VISION_MP_ENABLED=true en el .env.")
        return None

    if command in {"/camara", "/cámara"}:
        if arg in {"on", "activar", "activa"}:
            system.start()
            return "Encendí la cámara de visión (procesamiento local)."
        if arg in {"off", "apagar", "desactivar"}:
            system.stop()
            return "Apagué la cámara de visión."
        return system.describe()

    if command in {"/vision", "/visión"}:
        if arg in {"", "estado", "status"}:
            return _fmt_status(system)
        if arg in {"objetos", "objects"}:
            from vision.dialogue_context import describe_objects
            return describe_objects(system.snapshot())
        if arg in {"gestos", "gestures"}:
            from vision.dialogue_context import describe_gesture
            return describe_gesture(system.snapshot())
        if arg in {"escena", "scene"}:
            return system.classify_now()
        if arg in {"privacidad", "privacy"}:
            return ("Privacidad: procesamiento 100% local, no guardo ni envío "
                    "imágenes, y libero la cámara al cerrar.")
        if arg.startswith("debug"):
            estado = "activado" if "on" in arg else "desactivado" if "off" in arg else "consulta"
            return f"Overlay de depuración: {estado} (VISION_DEBUG_OVERLAY)."
        if arg in {"metricas", "métricas", "metrics"}:
            m = system.metrics()
            if not m:
                return "Aún no tengo métricas (¿la cámara está encendida?)."
            filas = [f"  {k}: {v['real_fps']} FPS, {v['infer_ms']} ms" for k, v in m.items()]
            return "Rendimiento de visión:\n" + "\n".join(filas)
        return _fmt_status(system)

    return None
