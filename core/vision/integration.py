"""Enganche OPT-IN del V3 con el resto de YUE, sin tocar el núcleo.

Desde `main.py`, en el arranque, basta una línea:

    from core.vision import integration as vision_v3
    self.vision_v3 = vision_v3.attach(self)   # inerte si VISION_V3_ENABLED=false

`attach(app)` conecta la percepción a la app real:
  - los gestos del avatar -> `app.pet.set_emotion(...)`
  - las frases de YUE      -> `app._yue_say(...)`
  - el estado de cámara    -> `app.pet.set_camera_active(...)` (si existe)

Los callbacks de la visión ocurren en el HILO de la cámara; aquí se marshalan al
hilo de Qt con un pequeño QObject de señales (igual que hace `CameraBridge` en
main.py), que es la forma segura de tocar la UI. Si PyQt5 no está, se llama
directo (útil en pruebas o entornos sin GUI).

Devuelve el `VisionController` (o None si el V3 está desactivado / no se pudo
enganchar). Totalmente aditivo y degradable.
"""
from __future__ import annotations

try:
    import config  # type: ignore
except Exception:  # pragma: no cover
    config = None  # type: ignore

from core.vision.vision_controller import VisionController


def _cfg(name: str, default):
    return getattr(config, name, default) if config is not None else default


def _make_qt_bridge():
    """Crea un puente de señales Qt o None si PyQt5 no está disponible."""
    try:
        from PyQt5.QtCore import QObject, pyqtSignal

        class _VisionBridge(QObject):
            avatar = pyqtSignal(str, float, int)
            speak = pyqtSignal(str)
            status = pyqtSignal(str, bool)

        return _VisionBridge()
    except Exception:
        return None


def attach(app) -> VisionController | None:
    """Engancha el V3 a la app. Inerte si VISION_V3_ENABLED=false."""
    if not bool(_cfg("VISION_V3_ENABLED", False)):
        return None

    bridge = _make_qt_bridge()

    if bridge is not None:
        # Conexiones a la app real (se ejecutan en el hilo de Qt).
        try:
            bridge.avatar.connect(app.pet.set_emotion)
        except Exception:
            pass
        try:
            bridge.speak.connect(app._yue_say)
        except Exception:
            pass

        def _on_status(text, active):
            try:
                if hasattr(app, "pet") and hasattr(app.pet, "set_camera_active"):
                    app.pet.set_camera_active(bool(active))
            except Exception:
                pass
            print(f"[vision.v3] estado: {text}")

        bridge.status.connect(lambda t, a: _on_status(t, a))

        on_avatar = lambda n, i, d: bridge.avatar.emit(str(n), float(i), int(d))
        on_speak = lambda t: bridge.speak.emit(str(t))
        on_status = lambda t, a: bridge.status.emit(str(t), bool(a))
    else:
        # Sin Qt: llamadas directas (p. ej. pruebas headless).
        on_avatar = lambda n, i, d: getattr(app, "pet", None) and app.pet.set_emotion(n, i, d)
        on_speak = lambda t: getattr(app, "_yue_say", lambda *_a: None)(t)
        on_status = lambda t, a: None

    controller = VisionController(
        on_avatar_emotion=on_avatar,
        on_speak=on_speak,
        on_status=on_status,
    )
    # Dejamos el bus y el puente accesibles por si algo más quiere suscribirse.
    controller._qt_bridge = bridge  # referencia viva (evita GC del QObject)
    try:
        controller.start()
    except Exception as exc:
        print("[vision.v3] no pude iniciar la percepción:", exc)
    return controller
