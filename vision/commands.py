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
            return _privacy_text(system)
        # ---------------- ADITIVO: comandos de la visión avanzada --------
        if arg in {"leer", "lee", "texto", "ocr"}:
            resultado = system.read_text()
            if resultado is None or not getattr(resultado, "text", ""):
                return ("No consigo leer nada estable. Acerca el papel, sujétalo "
                        "quieto y procura que tenga luz.")
            return f'Leo: "{resultado.text}" (confianza {resultado.confidence:.0%})'
        if arg in {"titulo", "título"}:
            resultado = system.read_text(only_title=True)
            if resultado is None or not getattr(resultado, "text", ""):
                return "No distingo un título claro ahora mismo."
            return f'El título dice: "{resultado.text}"'
        if arg in {"habitacion", "habitación", "cuarto", "describir", "describe"}:
            return system.describe_room()
        if arg in {"accion", "acción", "acciones", "que-hago"}:
            return system.what_am_i_doing()
        if arg in {"dedos", "fingers"}:
            return system.count_fingers()
        if arg in {"sostengo", "holding", "mano"}:
            return system.what_am_i_holding()
        if arg in {"animo", "ánimo", "emocion", "emoción"}:
            est = system.affective_estimate()
            if est.get("affective_state", "undetermined") == "undetermined":
                return "No tengo una estimación clara de tu estado de ánimo."
            return (f"{est.get('safe_description', '')} "
                    f"(confianza {est.get('confidence', 0):.0%}, "
                    f"certeza {est.get('certainty', 'baja')})")
        if arg in {"capacidades", "capabilities"}:
            return system.capabilities_report()
        if arg in {"version", "versión", "integridad"}:
            try:
                from vision import version as version_mod
                return version_mod.informe()
            except Exception as exc:
                return ("No encuentro vision/version.py: estás ejecutando una "
                        f"copia antigua del proyecto. ({exc})")
        if arg in {"modelos", "models"}:
            return system.models_report()
        if arg in {"olvida", "olvidar", "forget"}:
            n = system.forget_observations()
            return f"Olvidé lo observado ({n} anotaciones borradas)."
        if arg.startswith("privado") or arg.startswith("privacidad on"):
            return system.set_privacy_mode(True)
        if arg.startswith("publico") or arg.startswith("público"):
            return system.set_privacy_mode(False)
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


def _privacy_text(system) -> str:
    """Informe de privacidad; usa el gestor nuevo si existe."""
    try:
        return system.privacy_report()
    except Exception:
        return ("Privacidad: procesamiento 100% local, no guardo ni envío "
                "imágenes, y libero la cámara al cerrar.")
