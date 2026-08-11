"""Capa reactiva: convierte lo que YUE VE en lo que YUE HACE.

Este es el eslabón que faltaba. Todo lo demás ya existía:

    CameraManager -> PerceptionEngine -> EventManager   (eventos filtrados)
                                      -> snapshot()     (estado completo)

…pero nadie estaba suscrito al `EventManager` cuando el motor de percepción
tomaba el mando, y el `AvatarBridge` clásico escuchaba a `VisionState`, que en
ese modo se queda vacío. Resultado: la visión funcionaba perfectamente y no
movía absolutamente nada.

Esta capa hace tres cosas, y solo tres:

  1. **Bombea el estado**: en un hilo propio a ~4 Hz toma `snapshot()` y lo
     vuelca en `LiveVisionState` (el `vision_state` que consulta toda YUE).
  2. **Traduce eventos a conducta**: gesto -> expresión + gesto del avatar,
     emoción -> espejo empático, objeto nuevo -> comentario, texto -> lectura.
  3. **Decide cuándo callar**, que es lo más importante. Sin esto YUE narraría
     cada fotograma y sería insoportable.

REGLAS DE SILENCIO (deliberadas, no negociables):
  - nunca habla si el usuario está escribiendo, si YUE ya habla, o si hay una
    orden de PC/visión en curso -> lo decide `can_speak()`, que pone la app,
  - cada tipo de reacción tiene su propio enfriamiento,
  - hay un tope global de frases por minuto,
  - en modo privacidad no reacciona a nada,
  - la cara del avatar se cede cuando la gobierna otra cosa (música, vídeo).

Es Python puro: no importa Qt, ni OpenCV, ni MediaPipe. Se prueba entero sin
cámara inyectando un motor falso.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from vision import vision_log
from vision.live_state import LiveVisionState

log = logging.getLogger("vision.reactive")


# ===========================================================================
# Vocabulario: del gesto DEL USUARIO a la reacción DEL AVATAR
# ===========================================================================
# Las claves son las que emite `GestureAnalyzer` (snake_case). El puente clásico
# usaba los nombres CamelCase de MediaPipe ("Thumb_Up", "Open_Palm"), que este
# motor NO produce: por eso ni con los eventos enchufados habría coincidido nada.
#
# Valor: (emoción_avatar, intensidad, duración_ms, gesto_avatar, frase|None)
# La frase es OPCIONAL y aun así pasa por todos los filtros de silencio.
GESTO_A_REACCION: dict[str, tuple[str, float, int, str | None, str | None]] = {
    "wave":        ("happy",     0.85, 4200, "saludar",   "Hola."),
    "open_palm":   ("happy",     0.70, 3500, "saludar",   "Hola."),
    "thumbs_up":   ("happy",     0.90, 4000, "asentir",   None),
    "thumbs_down": ("confused",  0.60, 4000, "negar",     None),
    "victory":     ("excited",   0.95, 4500, "celebrar",  None),
    "ok":          ("happy",     0.70, 3500, "asentir",   None),
    "pointing":    ("curious",   0.70, 3500, "asomarse",  None),
    "fist":        ("focused",   0.60, 3000, None,        None),
    "pinch":       ("curious",   0.50, 3000, None,        None),
    "call_me":     ("playful",   0.70, 3500, "ladear",    None),
}

# Emoción estimada del usuario -> reacción del avatar.
# Filosofía ESPEJO EMPÁTICO: la alegría se acompaña, el malestar se atiende (no
# se imita). Por eso "sad" no pone al avatar triste: lo pone preocupado.
EMOCION_A_REACCION: dict[str, tuple[str, float, int]] = {
    "happy":     ("happy",     0.85, 5000),
    "sad":       ("worried",   0.80, 6000),
    "surprised": ("surprised", 0.85, 4200),
    "angry":     ("worried",   0.70, 5000),
    "tense":     ("worried",   0.70, 5000),
    "tired":     ("relaxed",   0.50, 4500),
    "confused":  ("curious",   0.65, 4200),
    "focused":   ("focused",   0.60, 5000),
    # "neutral" y "undetermined" a propósito fuera: no merecen reacción.
}

# Objetos que dan pie a un comentario natural. El resto se ve, se guarda en el
# estado y se calla: YUE no es un inventario parlante.
OBJETO_A_FRASE: dict[str, str] = {
    "libro":     "Veo que tienes un libro.",
    "laptop":    "Te veo con la laptop.",
    "celular":   "Veo que tienes el celular a mano.",
    "taza":      "¿Café? Buena idea.",
    "botella":   "Bien, te veo hidratándote.",
    "teclado":   "Te veo en modo teclado.",
    "guitarra":  "¿Vas a tocar algo?",
    "mochila":   "¿Saliendo o llegando?",
}


class ReactiveLayer:
    """Puente entre la percepción y la conducta de YUE.

    Parámetros de conexión (todos opcionales; sin ellos la capa es inerte):
      `on_avatar_emotion(nombre, intensidad, ms)` -> pet.set_emotion
      `on_avatar_gesture(nombre, ganancia)`       -> pet.play_gesture
      `on_speak(texto)`                           -> app._yue_say
      `can_speak()  -> bool`                      -> guardas de la app
      `can_animate() -> bool`                     -> ¿la cara está libre?
    """

    def __init__(
        self,
        perception,
        events,
        *,
        privacy=None,
        state: LiveVisionState | None = None,
        on_avatar_emotion: Callable[[str, float, int], None] | None = None,
        on_avatar_gesture: Callable[[str, float], None] | None = None,
        on_speak: Callable[[str], None] | None = None,
        can_speak: Callable[[], bool] | None = None,
        can_animate: Callable[[], bool] | None = None,
        settings=None,
    ) -> None:
        self.perception = perception
        self.events = events
        self.privacy = privacy
        self.state = state or LiveVisionState()
        cfg = settings

        self._avatar = on_avatar_emotion or (lambda *_a, **_k: None)
        self._gesture = on_avatar_gesture or (lambda *_a, **_k: None)
        self._speak_cb = on_speak or (lambda *_a, **_k: None)
        self._can_speak = can_speak or (lambda: True)
        self._can_animate = can_animate or (lambda: True)

        # --- cadencia del bombeo de estado -----------------------------
        self.poll_hz = float(_get(cfg, "reactive_poll_hz", 4.0))

        # --- enfriamientos (segundos) ----------------------------------
        self.cd_saludo = float(_get(cfg, "reactive_greet_cooldown", 45.0))
        self.cd_emocion = float(_get(cfg, "reactive_emotion_cooldown", 25.0))
        self.cd_comentario = float(_get(cfg, "reactive_comment_cooldown", 90.0))
        self.cd_objeto = float(_get(cfg, "reactive_object_cooldown", 240.0))
        self.cd_mirada = float(_get(cfg, "reactive_gaze_cooldown", 180.0))

        # --- contacto visual sostenido ---------------------------------
        self.gaze_seconds = float(_get(cfg, "reactive_gaze_seconds", 3.5))
        self.gaze_opens_chat = bool(_get(cfg, "reactive_gaze_opens_chat", True))

        # --- tope global de frases -------------------------------------
        self.max_frases_min = float(_get(cfg, "reactive_max_phrases_per_minute", 2.0))

        # --- estado interno --------------------------------------------
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ultimo: dict[str, float] = {}       # clave de enfriamiento -> ts
        self._frases: list[float] = []            # marcas de tiempo de lo dicho
        self._habia_persona = False
        self._objetos_comentados: dict[str, float] = {}
        self._suscrito = False
        self._saludo_pendiente = 0.0

        vision_log.configurar(
            consola=bool(_get(cfg, "debug", False)),
            min_gap=float(_get(cfg, "reactive_log_gap", 1.5)),
        )

    # ==================================================================
    # Ciclo de vida
    # ==================================================================
    def start(self) -> bool:
        """Arranca el bombeo y se suscribe a los eventos. Nunca lanza."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stop.clear()
            if self.events is not None and not self._suscrito:
                try:
                    self.events.subscribe(self.on_event)
                    self._suscrito = True
                except Exception as exc:
                    log.warning("No pude suscribirme a los eventos de visión: %s", exc)
            self._thread = threading.Thread(
                target=self._loop, name="vision-reactive", daemon=True
            )
            self._thread.start()
        log.info("Capa reactiva de visión activa (%.1f Hz).", self.poll_hz)
        vision_log.log_evento("Percepción conectada al comportamiento de YUE.")
        return True

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        if self.events is not None and self._suscrito:
            try:
                self.events.unsubscribe(self.on_event)
            except Exception:
                pass
            self._suscrito = False

    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    # ==================================================================
    # Bombeo de estado (hilo propio, jamás toca Qt)
    # ==================================================================
    def _loop(self) -> None:
        periodo = 1.0 / max(0.5, self.poll_hz)
        while not self._stop.is_set():
            inicio = time.monotonic()
            try:
                self._tick()
            except Exception as exc:
                # Un fallo aquí NUNCA puede tumbar la percepción ni la interfaz.
                log.debug("fallo en el ciclo reactivo: %s", exc)
            restante = periodo - (time.monotonic() - inicio)
            if restante > 0:
                self._stop.wait(restante)

    def _tick(self) -> None:
        if self.perception is None:
            return
        try:
            snapshot = self.perception.snapshot()
        except Exception as exc:
            log.debug("no pude leer el snapshot: %s", exc)
            return

        estado = self.state.update_from_snapshot(snapshot)
        if self._privado():
            return

        self._log_estado(estado)
        self._reaccionar_presencia(estado)
        self._reaccionar_emocion(estado)
        self._reaccionar_mirada(estado)
        self._reaccionar_objetos(estado)

    # ==================================================================
    # Logs (por cambio, nunca por fotograma)
    # ==================================================================
    @staticmethod
    def _log_estado(estado: dict) -> None:
        personas = estado.get("personas", {})
        if personas.get("hay_persona"):
            principal = personas.get("principal") or {}
            n = personas.get("count", 1)
            vision_log.log_vision(
                "rostro",
                f"Rostro detectado: {n} rostro{'s' if n != 1 else ''} · "
                f"{principal.get('posicion', 'al centro')} · {principal.get('distancia', '')}".strip(" ·"),
            )
        else:
            vision_log.log_vision("rostro", "Sin rostros en el encuadre.")

        emo = estado.get("emociones", {})
        if emo.get("emocion_raw") not in ("undetermined", None):
            vision_log.log_vision(
                "emocion", f"Emoción: {emo.get('emocion')} ({emo.get('confianza', 0):.2f})"
            )

        postura = estado.get("postura", {})
        if postura.get("visible"):
            vision_log.log_vision("postura", f"Postura: {postura.get('estado')}")

        objetos = [o["etiqueta"] for o in estado.get("objetos", [])]
        if objetos:
            vision_log.log_vision("objetos", "Objeto: " + ", ".join(objetos[:6]))

        mirada = estado.get("mirada", {})
        vision_log.log_vision(
            "mirada",
            f"Mirada: {'a YUE' if mirada.get('mira_a_yue') else 'fuera de cámara'}"
            f" ({mirada.get('estado_es')})",
        )

    # ==================================================================
    # Reacciones periódicas (del estado, no de eventos puntuales)
    # ==================================================================
    def _reaccionar_presencia(self, estado: dict) -> None:
        """Alguien aparece o se va. Es la reacción más básica y la más notable."""
        hay = bool(estado.get("personas", {}).get("hay_persona"))
        if hay == self._habia_persona:
            return
        self._habia_persona = hay
        if hay:
            vision_log.log_evento("Persona detectada.")
            if self._enfriar("presencia", self.cd_saludo):
                self._animar("happy", 0.7, 3500, gesto="saludar")
        else:
            vision_log.log_evento("La persona salió del encuadre.")
            # Sin gesto: irse no merece aspaviento, solo bajar a modo espera.
            self._animar("relaxed", 0.4, 4000)

    def _reaccionar_emocion(self, estado: dict) -> None:
        """Espejo empático. Solo cuando la emoción CAMBIA y es creíble."""
        emo = estado.get("emociones", {})
        clave = emo.get("emocion_raw", "undetermined")
        if clave not in EMOCION_A_REACCION:
            return
        if float(emo.get("confianza", 0.0)) < 0.5:
            return
        if not emo.get("cambio") and float(emo.get("desde_hace", 0.0)) > 2.0:
            return
        if not self._enfriar(f"emocion:{clave}", self.cd_emocion):
            return
        nombre, intensidad, ms = EMOCION_A_REACCION[clave]
        self._animar(nombre, intensidad, ms)
        vision_log.log_evento(f"Reacción del avatar: {nombre} (por emoción {emo.get('emocion')})")

    def _reaccionar_mirada(self, estado: dict) -> None:
        """Contacto visual sostenido -> YUE puede iniciar la conversación.

        Es el punto 3 del pedido. Deliberadamente conservador: exige varios
        segundos SEGUIDOS y tiene un enfriamiento largo, porque alguien que
        trabaja frente a la pantalla mira a la cámara constantemente sin querer
        hablar con nadie.
        """
        if not self.gaze_opens_chat:
            return
        mirada = estado.get("mirada", {})
        if not mirada.get("mira_a_yue"):
            return
        if float(mirada.get("segundos_mirando", 0.0)) < self.gaze_seconds:
            return
        if not self._enfriar("mirada", self.cd_mirada):
            return
        self._animar("curious", 0.65, 4000, gesto="ladear")
        self._decir("¿Necesitas algo? Te noto pendiente de mí.")

    def _reaccionar_objetos(self, estado: dict) -> None:
        """Comenta un objeto NUEVO y relevante, como mucho uno por vez."""
        for etiqueta in estado.get("objetos_nuevos", []):
            frase = OBJETO_A_FRASE.get(str(etiqueta).lower())
            if not frase:
                continue
            if not self._enfriar(f"objeto:{etiqueta}", self.cd_objeto):
                continue
            vision_log.log_evento(f"Objeto nuevo: {etiqueta}")
            self._animar("curious", 0.55, 3500)
            self._decir(frase)
            return   # uno por ciclo: no se enumera la habitación entera

    # ==================================================================
    # Eventos puntuales (llegan del EventManager, en el hilo del detector)
    # ==================================================================
    def on_event(self, evento) -> None:
        """Punto de entrada único desde `EventManager.subscribe`. Nunca lanza."""
        try:
            self._despachar(evento)
        except Exception as exc:
            log.debug("fallo tratando el evento visual: %s", exc)

    def _despachar(self, evento) -> None:
        if self._privado():
            return
        tipo = getattr(evento, "event", "")
        clave = getattr(evento, "key", "")
        datos = getattr(evento, "data", {}) or {}

        if tipo == "gesture_detected":
            self._on_gesto(clave, datos)
        elif tipo == "affective_estimate":
            self._on_afecto(clave, datos, float(getattr(evento, "confidence", 0.0)))
        elif tipo == "action_detected":
            self._on_accion(clave, datos)
        elif tipo == "text_read":
            texto = datos.get("full_text") or clave
            vision_log.log_evento(f'Texto leído: "{str(texto)[:80]}"')
        elif tipo == "text_visible":
            vision_log.log_evento("Hay texto frente a la cámara (sin leer).")
        elif tipo == "scene_change":
            vision_log.log_evento(f"Cambio en la escena: {clave}")

    def _on_gesto(self, gesto: str, datos: dict) -> None:
        mano = datos.get("hand", "unknown")
        nombre_es = datos.get("gesture_es") or gesto
        lado = {"left": "izquierda", "right": "derecha"}.get(mano, "")
        vision_log.log_evento(
            f"Gesto: {nombre_es}" + (f" (mano {lado})" if lado else "")
        )
        self.state.registrar_gesto(nombre_es, mano)

        reaccion = GESTO_A_REACCION.get(gesto)
        if reaccion is None:
            return
        emocion, intensidad, ms, gesto_avatar, frase = reaccion

        # El saludo tiene su propio enfriamiento largo: es lo que más canta si
        # se repite. Los demás gestos ya vienen antirrebotados del analizador.
        if gesto in ("wave", "open_palm"):
            if not self._enfriar("saludo", self.cd_saludo):
                return

        self._animar(emocion, intensidad, ms, gesto=gesto_avatar)
        if frase:
            self._decir(frase)

    def _on_afecto(self, estado: str, datos: dict, confianza: float) -> None:
        descripcion = datos.get("safe_description", "")
        vision_log.log_evento(f"Estimación de ánimo: {estado} ({confianza:.2f})")
        # La reacción de la CARA la lleva `_reaccionar_emocion` desde el estado,
        # que está suavizado. Aquí solo se decide si merece una FRASE, y solo
        # para estados que conviene atender.
        if estado not in ("sad", "tense", "tired", "angry"):
            return
        if confianza < 0.6 or not descripcion:
            return
        if not self._enfriar("frase_animo", self.cd_comentario):
            return
        self._decir(descripcion)

    def _on_accion(self, accion: str, datos: dict) -> None:
        nombre = datos.get("action_es") or accion
        vision_log.log_evento(f"Acción: {nombre}")
        # Riesgo: una posible caída sí interrumpe, con confianza alta.
        if accion == "possible_fall" and float(datos.get("confidence", 0.0) or 0.0) >= 0.85:
            self._animar("surprised", 0.95, 4500, gesto="sobresalto")
            self._decir("¿Estás bien? Me pareció verte caer.", urgente=True)

    # ==================================================================
    # Salidas (avatar y voz), con todos los filtros
    # ==================================================================
    def _animar(self, emocion: str, intensidad: float, ms: int,
                gesto: str | None = None) -> None:
        """Mueve el avatar. Cede la cara si otra cosa la está gobernando."""
        try:
            if not self._can_animate():
                return
        except Exception:
            pass
        try:
            self._avatar(emocion, float(intensidad), int(ms))
        except Exception as exc:
            log.debug("no pude aplicar la emoción al avatar: %s", exc)
        if gesto:
            try:
                self._gesture(gesto, float(min(1.2, max(0.4, intensidad))))
            except Exception as exc:
                log.debug("no pude aplicar el gesto al avatar: %s", exc)

    def _decir(self, texto: str, *, urgente: bool = False) -> None:
        """Habla, si y solo si no estorba."""
        if not texto:
            return
        try:
            if not self._can_speak():
                return
        except Exception:
            return
        if not urgente and not self._hay_cupo():
            return
        with self._lock:
            self._frases.append(time.time())
        try:
            self._speak_cb(texto)
            vision_log.log_evento(f"YUE dice (por visión): {texto}")
        except Exception as exc:
            log.debug("no pude hablar: %s", exc)

    # ==================================================================
    # Filtros
    # ==================================================================
    def _enfriar(self, clave: str, segundos: float) -> bool:
        """True si toca reaccionar (y apunta la marca). False si sigue en frío."""
        now = time.time()
        with self._lock:
            ultimo = self._ultimo.get(clave, 0.0)
            if ultimo and (now - ultimo) < float(segundos):
                return False
            self._ultimo[clave] = now
            return True

    def _hay_cupo(self) -> bool:
        """Tope global: como mucho N frases por minuto salidas de la visión."""
        now = time.time()
        with self._lock:
            self._frases = [t for t in self._frases if now - t < 60.0]
            return len(self._frases) < max(1.0, self.max_frases_min)

    def _privado(self) -> bool:
        if self.privacy is None:
            return False
        try:
            return bool(self.privacy.is_private())
        except Exception:
            return False

    # ==================================================================
    # API para el resto de YUE
    # ==================================================================
    def vision_state(self) -> dict:
        return self.state.get()

    def resumen(self) -> str:
        return self.state.resumen()

    def reset(self) -> None:
        with self._lock:
            self._ultimo.clear()
            self._frases.clear()
            self._objetos_comentados.clear()
            self._habia_persona = False
        self.state.reset()
        vision_log.reset()


def _get(cfg, nombre: str, defecto):
    """Lee un ajuste con valor por defecto. `cfg` puede ser None."""
    if cfg is None:
        return defecto
    valor = getattr(cfg, nombre, None)
    return defecto if valor is None else valor
