"""Sello de versión del sistema de visión de YUE.

Existe por un motivo muy concreto: al actualizar el proyecto es fácil acabar
ejecutando una copia ANTIGUA sin darse cuenta —por ejemplo si el zip se
descomprime dentro de la carpeta que ya existía y queda anidado
(`yue_companion/yue_companion/…`), o si quedan dos carpetas y se lanza la que
no toca—. El síntoma es desconcertante: los arreglos "no funcionan" aunque el
código nuevo esté en el disco.

Con esto, el diagnóstico dice desde la primera línea qué versión se está
ejecutando y avisa si faltan archivos que deberían estar.
"""
from __future__ import annotations

from pathlib import Path

# Se sube al cambiar algo que el usuario deba poder verificar.
VERSION = "2.4.0"
FECHA = "2026-08-07"

CAMBIOS = {
    "2.0.0": "Sistema de visión avanzada: OCR, habitación, acciones, emociones.",
    "2.1.0": "Corregido is_speaking(), FPS=0 y el apagado de vision_mp.",
    "2.2.0": "Backends de cámara con validación (arregla '1 fotograma y muda').",
    "2.3.0": "Control por cabeza sobre el motor nuevo; corregidos Thread._stop "
             "tapado y los módulos añadidos en caliente que no arrancaban.",
    "2.4.0": "CAMERA_ENABLED ya no apaga el motor nuevo al migrar; comprobación "
             "de coherencia del .env en el diagnóstico.",
}

# Archivos que DEBEN existir en esta versión. Si falta alguno, la copia que se
# está ejecutando es antigua o está incompleta.
ARCHIVOS_CLAVE = (
    "vision/camera_backend.py",
    "vision/perception_engine.py",
    "vision/privacy_manager.py",
    "vision/event_manager.py",
    "vision/context_builder.py",
    "vision/capabilities.py",
    "vision/voice_intents.py",
    "vision/legacy_adapter.py",
    "vision/camera_manager.py",
    "vision/tracking/hand_tracker.py",
    "vision/analyzers/action_analyzer.py",
    "vision/ocr/ocr_engine.py",
    "vision/models/model_registry.py",
    "tools/download_vision_models.py",
    "tests/test_vision_avanzada.py",
    "docs/VISION_AVANZADA.md",
)

# Marcas internas: además de existir, el archivo tiene que contener el arreglo.
MARCAS = (
    ("vision/vision_scheduler.py", "_stop_event",
     "corrección del Thread._stop tapado"),
    ("vision/vision_scheduler.py", "añadido en caliente",
     "arranque de módulos registrados en caliente"),
    ("vision/perception_engine.py", "_job_head_landmarks",
     "control del cursor por cabeza sobre el motor nuevo"),
    ("vision/camera_service.py", "CaptureOpener",
     "apertura de cámara con validación de backend"),
    ("vision/settings.py", "def get_fps",
     "convención FPS=0 = usa el perfil"),
    ("vision/settings.py", "VISION_CAMERA_ENABLED",
     "interruptor de cámara propio del motor nuevo"),
    ("diag_vision_avanzada.py", "Coherencia de la configuración",
     "comprobación de coherencia del .env"),
)


def raiz() -> Path:
    """Carpeta del proyecto (dos niveles arriba: vision/ -> raíz)."""
    return Path(__file__).resolve().parent.parent


def comprobar() -> tuple[list[str], list[str]]:
    """Devuelve (archivos_que_faltan, arreglos_que_no_estan)."""
    base = raiz()
    faltan = [ruta for ruta in ARCHIVOS_CLAVE if not (base / ruta).is_file()]

    sin_arreglo: list[str] = []
    for ruta, marca, descripcion in MARCAS:
        destino = base / ruta
        if not destino.is_file():
            sin_arreglo.append(f"{descripcion} (falta {ruta})")
            continue
        try:
            contenido = destino.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if marca not in contenido:
            sin_arreglo.append(descripcion)
    return faltan, sin_arreglo


def al_dia() -> bool:
    faltan, sin_arreglo = comprobar()
    return not faltan and not sin_arreglo


def informe() -> str:
    """Texto listo para imprimir en el diagnóstico."""
    base = raiz()
    lineas = [f"Versión de visión: {VERSION}  ({FECHA})",
              f"Ejecutando desde: {base}"]
    faltan, sin_arreglo = comprobar()

    if not faltan and not sin_arreglo:
        lineas.append("Integridad: correcta, esta copia está al día.")
        return "\n".join(lineas)

    lineas.append("")
    lineas.append("  AVISO: esta copia NO está al día.")
    if faltan:
        lineas.append("  Archivos que faltan:")
        for ruta in faltan[:10]:
            lineas.append(f"    · {ruta}")
    if sin_arreglo:
        lineas.append("  Arreglos que NO están en el código:")
        for descripcion in sin_arreglo[:10]:
            lineas.append(f"    · {descripcion}")

    lineas.append("")
    lineas.append("  Suele pasar al descomprimir el zip DENTRO de la carpeta que")
    lineas.append("  ya existía: queda anidado en yue_companion\\yue_companion\\ y")
    lineas.append("  se acaba ejecutando la copia vieja de fuera.")
    lineas.append("")
    lineas.append("  Comprueba si existe una carpeta anidada:")
    lineas.append(f"    dir \"{base}\\yue_companion\"")
    lineas.append("  Si existe, ejecuta desde ahí, o descomprime en una carpeta")
    lineas.append("  NUEVA y vacía y copia solo tu .env a la nueva.")
    return "\n".join(lineas)


if __name__ == "__main__":
    print(informe())
