"""Enganche OPT-IN del sistema de visión con YUE, sin tocar el núcleo (paso 16/17).

Desde `main.py`, en el arranque, basta con:

    from vision import integration as vision_mp
    self.vision_mp = vision_mp.attach(self)   # None si VISION_MP_ENABLED=false

`attach(app)` conecta:
  - expresión del avatar  -> `app.pet.set_emotion(...)`
  - gesto del avatar      -> `app.pet.play_gesture(...)`   (ADITIVO)
  - frases opcionales     -> `app._yue_say(...)`
  - estado de cámara      -> `app._on_vision_status(...)`

y, en sentido contrario, le pasa a la visión dos GUARDAS que solo la app sabe
responder (ADITIVO, y es lo que evita que YUE se vuelva insoportable):

  - `can_speak()`   : False si el usuario escribe, si YUE ya habla, o si hay una
                      orden de PC/visión/autonomía en curso.
  - `can_animate()` : False si la cara del avatar la gobierna otra cosa ahora
                      mismo (música o vídeo sonando, YUE hablando).

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
            gesture = pyqtSignal(str, float)     # ADITIVO
            speak = pyqtSignal(str)
            status = pyqtSignal(str, bool)

        return _VisionBridge()
    except Exception:
        return None


def _make_guards(app):
    """Construye `can_speak` y `can_animate` a partir del estado real de la app.

    Se leen con `getattr` y todo va dentro de try: si `main.py` cambia un
    atributo, la visión no se rompe, simplemente se vuelve más prudente.
    """

    def _ocupado() -> bool:
        return bool(
            getattr(app, "_pc_busy", False)
            or getattr(app, "_vision_busy", False)
            or getattr(app, "_autonomy_busy", False)
        )

    def can_speak() -> bool:
        try:
            chat = getattr(app, "chat", None)
            if chat is not None and chat.is_user_composing():
                return False
            speaker = getattr(app, "speaker", None)
            # OJO: `is_speaking` es una PROPIEDAD, no un método. Llamarla como
            # función devuelve el objeto método (siempre verdadero) y bloquearía
            # a YUE para siempre. Ya nos pasó antes; queda anotado.
            if speaker is not None and bool(getattr(speaker, "is_speaking", False)):
                return False
            if _ocupado():
                return False
            # Si el modo enseñanza está activo, YUE no interrumpe la clase.
            teacher = getattr(app, "teacher", None)
            if teacher is not None and bool(getattr(teacher, "is_active", False)):
                return False
            # NUEVO: respeta la INICIATIVA del cerebro central. Si el usuario
            # pidió espacio, la visión tampoco habla: da igual lo interesante
            # que le parezca lo que acaba de ver.
            comprobar = getattr(app, "_yue_may_take_initiative", None)
            if callable(comprobar) and not comprobar("medium"):
                return False
        except Exception:
            return False
        return True

    def can_animate() -> bool:
        """¿Merece la pena que la visión proponga una cara?

        Ya NO es una decisión: es un ATAJO. El arbitraje decidiría lo mismo de
        todos modos, pero preguntar antes evita construir y enviar una
        propuesta que iba a perder. La lógica antigua (música sonando, YUE
        hablando, orden en curso) se conserva como red de seguridad para cuando
        no haya gestor de estado.
        """
        try:
            gestor = getattr(app, "state_manager", None)
            if gestor is not None:
                try:
                    from core.state import Priority
                    return bool(gestor.would_win(int(Priority.EMOTION), "vision"))
                except Exception:
                    pass
            audio = getattr(app, "audio", None)
            if audio is not None and bool(getattr(audio, "media_playing", False)):
                return False   # la cara la gobierna el acompañamiento musical
            speaker = getattr(app, "speaker", None)
            if speaker is not None and bool(getattr(speaker, "is_speaking", False)):
                return False   # mientras habla, la emoción la marca la frase
            if _ocupado():
                return False
        except Exception:
            return True
        return True

    return can_speak, can_animate


def attach(app):
    """Engancha el sistema de visión MP a la app real. None si está apagado."""
    cfg = settings_mod.load()
    if not cfg.enabled:
        log.info("Visión MP desactivada; no se engancha.")
        return None

    from vision.controller import VisionSystem

    bridge = _make_qt_bridge()

    def _avatar(name, intensity, ms):
        """La visión PROPONE una cara. Ya no la impone.

        Antes esto acababa en `pet.set_emotion(...)` por dos caminos (la señal
        Qt conectada al avatar, y la llamada directa sin Qt). Eran dos de las
        fugas que permitían que un sensor cambiara el estado de YUE saltándose
        el arbitraje. Ahora ambos caminos terminan en el gestor de estado, que
        decide si esa cara gana y deja que el renderer la pinte.
        """
        try:
            gestor = getattr(app, "state_manager", None)
            if gestor is not None:
                try:
                    from core.state import Priority
                    prioridad = int(Priority.EMOTION)
                except Exception:
                    prioridad = 60
                gestor.request_emotion(str(name), float(intensity), int(ms),
                                       priority=prioridad, source="vision")
                return
            # Sin gestor de estado no hay renderer: se conserva el camino
            # antiguo para no dejar el avatar congelado en un arranque degradado.
            if bridge is not None:
                bridge.avatar.emit(name, float(intensity), int(ms))
            elif getattr(app, "pet", None) is not None:
                app.pet.set_emotion(name, float(intensity), int(ms))
        except Exception:
            pass

    def _observe(name, confidence, valence=0.0, arousal=0.3):
        """La visión REPORTA lo que cree ver en el usuario (evidencia, no verdad).

        Esta es la vía correcta para un sensor: no cambia nada de YUE, solo
        alimenta el USER STATE con su confianza ponderada (cámara = 0.55).
        """
        try:
            gestor = getattr(app, "state_manager", None)
            if gestor is not None:
                gestor.observe_emotion("camera", str(name), float(confidence),
                                       valence=float(valence),
                                       arousal=float(arousal), explicit=False,
                                       detail="visión MP")
        except Exception:
            pass

    def _gesture(name, gain=1.0):
        try:
            if bridge is not None:
                bridge.gesture.emit(str(name), float(gain))
            elif getattr(app, "pet", None) is not None:
                app.pet.play_gesture(str(name), float(gain))
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
            pet = getattr(app, "pet", None)
            if pet is not None:
                # `bridge.avatar` YA NO se conecta a `pet.set_emotion`. Esa
                # conexión era una de las fugas: dejaba que la visión pintara el
                # avatar sin pasar por el arbitraje. Ahora `_avatar()` manda una
                # propuesta al gestor y el renderer se encarga de pintar.
                # Los GESTOS sí siguen aquí: son animaciones puntuales que no
                # compiten por el estado emocional (un saludo no es una emoción).
                if hasattr(pet, "play_gesture"):
                    bridge.gesture.connect(pet.play_gesture)
            if hasattr(app, "_yue_say"):
                bridge.speak.connect(app._yue_say)
            if hasattr(app, "_on_vision_status"):
                bridge.status.connect(app._on_vision_status)
        except Exception as exc:
            log.warning("No pude conectar las señales de visión a la app: %s", exc)

    can_speak, can_animate = _make_guards(app)

    system = VisionSystem(
        on_avatar_emotion=_avatar,
        on_avatar_gesture=_gesture,
        on_speak=_speak,
        on_status=_status,
        can_speak=can_speak,
        can_animate=can_animate,
        settings=cfg,
    )
    # El puente de señales debe sobrevivir a esta función: sin una referencia
    # fuerte, Python lo recolecta y las señales dejan de llegar en silencio.
    system._qt_bridge = bridge
    try:
        system.start()
    except Exception as exc:
        log.warning("No pude iniciar la visión MP: %s", exc)
    return system
