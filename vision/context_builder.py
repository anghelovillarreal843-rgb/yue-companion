"""VisionContextBuilder: de las detecciones al contexto del chat (punto 11).

Al modelo conversacional NUNCA se le manda una lista enorme ni coordenadas. Se
le mandan cuatro o cinco frases en español, cortas y útiles, como las del
ejemplo del documento:

    Contexto visual reciente:
    - Se observa una persona frente a la cámara.
    - Parece estar mostrando una hoja con texto.
    - El texto estable detectado dice: "Reunión viernes 7 de agosto".
    - Hay una laptop y un libro sobre una mesa.
    - La persona levantó el pulgar derecho.
    - Su expresión parece positiva, con confianza media.

Filtros obligatorios: solo lo RECIENTE, ESTABLE, RELEVANTE, con CONFIANZA
suficiente y NO REPETIDO.

Distingue tres cosas distintas:
  - `state`   : lo persistente (hay una persona, hay una laptop),
  - `event`   : lo puntual (levantó el pulgar, apareció una persona),
  - `request` : lo que la persona pidió expresamente ("lee esto").

Y decide con `should_react()` si YUE debería comentar algo por su cuenta. Por
defecto la respuesta es NO: YUE no narra todo lo que ve.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from vision.analyzers.action_analyzer import ACTIONS_ES
from vision.tracking.hand_tracker import GESTURES_ES
from vision.tracking.object_tracker import label_es

_CERTAINTY_ES = {"high": "alta", "medium": "media", "low": "baja"}

# Motivos válidos para que YUE hable sin que le pregunten (punto 11).
REACT_REASONS = ("pregunta_usuario", "gesto_intencional", "riesgo", "cambio_importante")

# Gestos que son claramente intencionales (dirigidos a YUE).
INTENTIONAL_GESTURES = {"thumbs_up", "thumbs_down", "victory", "wave", "ok", "pointing"}


@dataclass
class VisualContext:
    lines: list = field(default_factory=list)
    text: str = ""
    has_content: bool = False
    react: bool = False
    reason: str = ""

    def as_prompt(self) -> str:
        return self.text


class VisionContextBuilder:
    def __init__(
        self,
        *,
        max_lines: int = 6,
        max_age: float = 12.0,
        min_confidence: float = 0.5,
        repeat_gap: float = 60.0,
    ) -> None:
        self.max_lines = int(max_lines)
        self.max_age = float(max_age)
        self.min_confidence = float(min_confidence)
        self.repeat_gap = float(repeat_gap)
        self._said: dict[str, float] = {}

    # ------------------------------------------------------------------
    def build(self, snapshot: dict, *, events=None, force: bool = False,
              now: float | None = None) -> VisualContext:
        """Contexto visual listo para inyectar al prompt del modelo."""
        now = time.time() if now is None else float(now)
        ctx = VisualContext()
        if not snapshot:
            return ctx

        cam = snapshot.get("camera", {})
        privacy = snapshot.get("privacy", {})
        if privacy.get("modo_privacidad"):
            return ctx
        if not cam.get("active"):
            return ctx

        lines: list[str] = []
        presence = snapshot.get("presence", {}) or {}
        count = int(presence.get("count", 0))

        # --- estado persistente ----------------------------------------
        if count == 0:
            lines.append("La cámara está activa, pero no se ve a nadie ahora mismo.")
        elif count == 1:
            lines.append("Se observa una persona frente a la cámara.")
        else:
            lines.append(f"Se observan {count} personas frente a la cámara.")

        att = snapshot.get("attention", {}) or {}
        if count and att.get("state") == "distracted":
            lines.append("No está mirando directamente a la cámara.")

        # --- acción en curso -------------------------------------------
        acciones = snapshot.get("actions", []) or []
        for a in acciones[:1]:
            if float(a.get("confidence", 0)) >= self.min_confidence:
                nombre = ACTIONS_ES.get(a.get("action", ""), a.get("action", ""))
                lines.append(f"Parece estar en la acción: {nombre}.")

        # --- texto leído -----------------------------------------------
        texto = snapshot.get("text") or {}
        if texto.get("stable") and texto.get("text"):
            edad = now - float(texto.get("timestamp", now))
            if edad <= self.max_age * 3 and float(texto.get("confidence", 0)) >= 0.5:
                fragmento = texto["text"].replace("\n", " / ")[:120]
                lines.append(f'El texto estable detectado dice: "{fragmento}".')

        # --- objetos ----------------------------------------------------
        objetos = snapshot.get("objects", []) or []
        nombres = []
        for o in objetos[:5]:
            if float(o.get("confidence", 0)) >= self.min_confidence:
                nombres.append(o.get("translated_label") or label_es(o.get("label", "")))
        if nombres:
            lines.append("Objetos visibles: " + ", ".join(dict.fromkeys(nombres)) + ".")

        # --- habitación --------------------------------------------------
        room = snapshot.get("room", {}) or {}
        if room.get("scene_type") and room.get("scene_type") != "desconocido":
            if float(room.get("scene_confidence", 0)) >= 0.5:
                lines.append(f"El lugar parece {room['scene_type']}.")
        if room.get("lighting") in {"baja", "muy_baja"}:
            lines.append("La iluminación se ve baja.")

        # --- gestos y eventos recientes ---------------------------------
        for ev in self._recent_events(events, now):
            line = self._event_line(ev)
            if line and self._fresh(line, now):
                lines.append(line)

        # --- estimación afectiva (siempre prudente) ---------------------
        afecto = snapshot.get("affective", {}) or {}
        estado = afecto.get("affective_state", "undetermined")
        if estado != "undetermined" and float(afecto.get("confidence", 0)) >= self.min_confidence:
            certeza = _CERTAINTY_ES.get(afecto.get("certainty", "low"), "baja")
            lines.append(
                f"Estimación de ánimo (NO es un hecho, solo una impresión): "
                f"{afecto.get('safe_description', '')} Confianza {certeza}."
            )

        lines = [l for l in dict.fromkeys(lines) if l][: self.max_lines]
        if not lines:
            return ctx

        ctx.lines = lines
        ctx.has_content = True
        ctx.text = "Contexto visual reciente:\n" + "\n".join(f"- {l}" for l in lines)
        if force:
            ctx.react = True
            ctx.reason = "pregunta_usuario"
        return ctx

    # ------------------------------------------------------------------
    def should_react(self, snapshot: dict, events=None, *, user_asked: bool = False,
                     now: float | None = None) -> tuple[bool, str]:
        """¿Debe YUE comentar algo por su cuenta? Por defecto, NO."""
        now = time.time() if now is None else float(now)
        if user_asked:
            return True, "pregunta_usuario"
        if not snapshot or not (snapshot.get("camera", {}) or {}).get("active"):
            return False, ""
        if (snapshot.get("privacy", {}) or {}).get("modo_privacidad"):
            return False, ""

        # Riesgo evidente: posible caída con confianza alta.
        for a in snapshot.get("actions", []) or []:
            if a.get("action") == "possible_fall" and float(a.get("confidence", 0)) >= 0.85:
                return True, "riesgo"

        for ev in self._recent_events(events, now, seconds=8.0):
            kind = getattr(ev, "event", "")
            key = getattr(ev, "key", "")
            if kind == "gesture_detected" and key in INTENTIONAL_GESTURES:
                return True, "gesto_intencional"
            if kind == "scene_change" and "apareció una persona" in key:
                return True, "cambio_importante"
        return False, ""

    # ------------------------------------------------------------------
    def _recent_events(self, events, now: float, seconds: float | None = None) -> list:
        if events is None:
            return []
        try:
            return events.recent(seconds if seconds is not None else self.max_age)
        except Exception:
            return []

    @staticmethod
    def _event_line(ev) -> str:
        kind = getattr(ev, "event", "")
        key = getattr(ev, "key", "")
        data = getattr(ev, "data", {}) or {}
        if kind == "gesture_detected":
            nombre = data.get("gesture_es") or GESTURES_ES.get(key, key)
            lado = {"left": "izquierda", "right": "derecha"}.get(data.get("hand", ""), "")
            return f"Hizo un gesto: {nombre}" + (f" con la mano {lado}." if lado else ".")
        if kind == "action_detected":
            nombre = data.get("action_es") or ACTIONS_ES.get(key, key)
            return f"Acción detectada: {nombre}."
        if kind == "scene_change":
            return f"Cambio en la escena: {key}."
        if kind == "text_visible":
            return "Parece estar mostrando algo con texto."
        return ""

    def _fresh(self, line: str, now: float) -> bool:
        """Evita repetir la misma frase una y otra vez."""
        last = self._said.get(line, 0.0)
        if last and (now - last) < self.repeat_gap:
            return False
        self._said[line] = now
        # Limpieza barata del historial.
        if len(self._said) > 200:
            cutoff = now - self.repeat_gap * 2
            self._said = {k: v for k, v in self._said.items() if v >= cutoff}
        return True

    def reset(self) -> None:
        self._said.clear()


# Función de conveniencia, por simetría con `dialogue_context.build_visual_context`.
def build_context(snapshot: dict, events=None, force: bool = False) -> str:
    return VisionContextBuilder().build(snapshot, events=events, force=force).text
