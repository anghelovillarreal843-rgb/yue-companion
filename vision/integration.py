"""Enganche OPT-IN del sistema de visión con YUE, sin tocar el núcleo (paso 16/17).

Desde `main.py`, en el arranque, basta con:

    from vision import integration as vision_mp
    self.vision_mp = vision_mp.attach(self)   # None si VISION_MP_ENABLED=false

`attach(app)` conecta:
  - gestos del avatar   -> `app.pet.set_emotion(...)`
  - frases opcionales   -> `app._yue_say(...)`
  - estado de cámara    -> callback de estado (log/estado en chat)

Los callbacks de visión ocurren en HILOS de trabajo; se marshalan al hilo de Qt
con un pequeño QObject de señales (igual patrón que el resto de YUE). Si PyQt5 no
está (pruebas), se llaman directo.

Devuelve el `VisionSystem` (o None). Totalmente aditivo y degradable.
"""
from __future__ import annotations

import logging

from vision import settings as settings_mod

log = logging.getLogger("vision.integration")


def _make_qt_bridge():
    try:
        from PyQt5.QtCore import QObject, pyqtSignal

        class _VisionBridge(QObject):
            avatar = pyqtSignal(str, float, int)
            speak = pyqtSignal(str)
            status = pyqtSignal(str, bool)

        return _VisionBridge()
    except Exception:
        return None


def attach(app):
    """Engancha el sistema de visión MP a la app real. None si está apagado."""
    cfg = settings_mod.load()
    if not cfg.enabled:
        log.info("Visión MP desactivada; no se engancha.")
        return None

    from vision.controller import VisionSystem

    bridge = _make_qt_bridge()

    def _avatar(name, intensity, ms):
        try:
            if bridge is not None:
                bridge.avatar.emit(name, float(intensity), int(ms))
            elif getattr(app, "pet", None) is not None:
                app.pet.set_emotion(name, float(intensity), int(ms))
        except Exception:
            pass

    def _speak(text):
        try:
            if bridge is not None:
                bridge.speak.emit(text)
            elif hasattr(app, "_yue_say"):
                app._yue_say(text)
        except Exception:
            pass

    def _status(text, active):
        try:
            if bridge is not None:
                bridge.status.emit(text, bool(active))
        except Exception:
            pass

    if bridge is not None:
        try:
            if getattr(app, "pet", None) is not None:
                bridge.avatar.connect(app.pet.set_emotion)
            if hasattr(app, "_yue_say"):
                bridge.speak.connect(app._yue_say)
            if hasattr(app, "_on_vision_status"):
                bridge.status.connect(app._on_vision_status)
        except Exception as exc:
            log.warning("No pude conectar las señales de visión a la app: %s", exc)

    system = VisionSystem(on_avatar_emotion=_avatar, on_speak=_speak, on_status=_status,
                          settings=cfg)
    try:
        system.start()
    except Exception as exc:
        log.warning("No pude iniciar la visión MP: %s", exc)
    return system
