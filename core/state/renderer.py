"""AvatarRenderer — EL ÚNICO SITIO que toca el avatar.

Esta es la regla que sostiene toda la arquitectura:

    módulo → StateManager → YUE STATE → renderer → avatar

Nunca:

    sensor  → avatar
    cámara  → avatar
    media   → avatar
    teacher → avatar

Antes había llamadas sueltas a `pet.set_emotion(...)` repartidas por la
conversación, la música, la cámara y los dos sistemas de visión. Ganaba la
última en llegar, así que el avatar cambiaba de cara por motivos que nadie podía
reconstruir después. Ahora todas esas rutas terminan aquí, y aquí solo llega el
ganador del arbitraje.

HILO DE LA INTERFAZ
-------------------
El estado se calcula en el hilo que lo provocara (visión, audio, red...), pero
las ventanas de YUE las creó el hilo de Qt y solo él puede tocarlas. Tocar un
widget desde un hilo de trabajo en Windows no da un error limpio: cuelga la
aplicación (ver la explicación larga en `core/ui_bridge.py`). Por eso todo se
marshalla con `post_to_ui_thread`, que ya existía y está probado. Si el puente
no está instalado (pruebas headless), se llama directo.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("yue.state.renderer")


class AvatarRenderer:
    """Aplica el `YueBehaviorState` sobre el avatar, y nada más.

    No decide: ejecuta. Si algo no le gusta de lo que recibe, el sitio donde
    hay que arreglarlo es el arbitraje, no aquí.
    """

    def __init__(self, pet=None, *, speaker=None, use_ui_thread: bool = True):
        self.pet = pet
        self.speaker = speaker
        self.use_ui_thread = use_ui_thread
        self._lock = threading.RLock()
        #: Lo último que se APLICÓ de verdad. Evita repintar cien veces la
        #: misma cara: el avatar VRM reinicia la animación en cada llamada, y
        #: eso se ve como un parpadeo constante.
        self._ultimo: tuple | None = None
        self._ultimo_gesto: str = ""
        self._unsubscribe = None
        #: Contador para el panel de depuración.
        self.aplicados = 0
        self.omitidos = 0

    # ------------------------------------------------------------------ API
    def bind(self, state_manager) -> "AvatarRenderer":
        """Se engancha al gestor de estado. A partir de aquí, todo pasa por él."""
        if state_manager is None:
            return self
        self._unsubscribe = state_manager.subscribe_global(self.render)
        try:
            self.render(state_manager.global_state())
        except Exception as exc:
            log.debug("primer renderizado omitido: %s", exc)
        return self

    def unbind(self) -> None:
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            except Exception:
                pass
            self._unsubscribe = None

    def render(self, estado) -> None:
        """Punto de entrada: recibe el estado global y pinta lo que toca."""
        if estado is None or self.pet is None:
            return
        yue = getattr(estado, "yue", None)
        if yue is None:
            return

        clave = (yue.emotion, round(yue.intensity, 2), yue.duration_ms)
        with self._lock:
            repetido = (clave == self._ultimo)
            if not repetido:
                self._ultimo = clave
            gesto = yue.gesture or ""
            gesto_nuevo = bool(gesto) and gesto != self._ultimo_gesto
            if gesto_nuevo:
                self._ultimo_gesto = gesto
            elif not gesto:
                self._ultimo_gesto = ""

        if repetido and not gesto_nuevo:
            self.omitidos += 1
            return

        def _pintar():
            # ==============================================================
            # ÚNICA llamada a pet.set_emotion() en todo el proyecto.
            # Si aparece otra en cualquier módulo, es un error de diseño:
            # ese módulo debería llamar a state_manager.propose().
            # ==============================================================
            try:
                if not repetido:
                    self.pet.set_emotion(yue.emotion, yue.intensity, yue.duration_ms)
                    self.aplicados += 1
            except Exception as exc:
                log.warning("no pude aplicar la emoción al avatar: %s", exc)
            if gesto_nuevo:
                try:
                    if hasattr(self.pet, "play_gesture"):
                        self.pet.play_gesture(gesto, min(1.0, yue.intensity))
                except Exception as exc:
                    log.debug("no pude reproducir el gesto %s: %s", gesto, exc)

        self._en_hilo_ui(_pintar)

    def render_gesture(self, nombre: str, ganancia: float = 1.0) -> None:
        """Gesto puntual, también marshalado al hilo correcto."""
        if not nombre or self.pet is None:
            return

        def _pintar():
            try:
                if hasattr(self.pet, "play_gesture"):
                    self.pet.play_gesture(str(nombre), float(ganancia))
            except Exception as exc:
                log.debug("gesto %s no aplicado: %s", nombre, exc)

        self._en_hilo_ui(_pintar)

    def stats(self) -> dict:
        return {"aplicados": self.aplicados, "omitidos": self.omitidos,
                "ultimo": self._ultimo}

    # -------------------------------------------------------------- interno
    def _en_hilo_ui(self, tarea) -> None:
        """Ejecuta la tarea en el hilo de la interfaz. Nunca bloquea al que llama."""
        if not self.use_ui_thread:
            tarea()
            return
        try:
            from core import ui_bridge
            if ui_bridge.post_to_ui_thread(tarea):
                return
        except Exception:
            pass
        # Sin puente (pruebas headless, o aún no instalado): directo. En ese
        # escenario no hay ventanas Qt de por medio, así que es seguro.
        try:
            tarea()
        except Exception as exc:
            log.debug("renderizado directo falló: %s", exc)


def attach_renderer(state_manager, pet, *, speaker=None,
                    use_ui_thread: bool = True) -> AvatarRenderer:
    """Crea y engancha el renderer de una vez. Lo llama `main.py` al arrancar."""
    renderer = AvatarRenderer(pet, speaker=speaker, use_ui_thread=use_ui_thread)
    return renderer.bind(state_manager)
