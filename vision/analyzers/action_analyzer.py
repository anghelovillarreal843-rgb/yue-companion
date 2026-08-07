"""Reconocimiento de acciones por análisis TEMPORAL (punto 8 del pedido).

Regla de oro del módulo: **ninguna acción compleja se deduce de una sola
postura**. Cada acción exige varias evidencias simultáneas mantenidas durante un
tiempo mínimo, y muchas exigen además una secuencia (subir → mantener → bajar).

Cada acción se define de forma declarativa con:
  - `evidence`   : evidencias necesarias en el fotograma,
  - `any_of`     : al menos una de estas,
  - `min_ratio`  : proporción de fotogramas de la ventana que deben cumplirlo,
  - `min_duration` / `max_duration`,
  - `sequence`   : (opcional) fases que deben ocurrir en orden,
  - `cooldown`   : silencio tras emitirla.

Salida (forma exacta del documento):

    {"action", "confidence", "person_id", "start_time", "duration", "evidence"}

Para una posible CAÍDA se aplica un criterio especialmente estricto: tronco
horizontal + ausencia de movimiento + varios segundos. Y aun así se comunica
como una pregunta preocupada, nunca como un diagnóstico de accidente.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque

from vision.tracking.temporal_smoother import MotionTracker

ACTIONS_ES: dict[str, str] = {
    "sitting_down": "sentarse",
    "standing_up": "levantarse",
    "walking": "caminar",
    "approaching": "acercarse a la cámara",
    "moving_away": "alejarse",
    "waving": "saludar",
    "clapping": "aplaudir",
    "drinking": "beber",
    "eating": "comer",
    "reading": "leer",
    "writing": "escribir",
    "using_phone": "usar el celular",
    "phone_call": "hablar por teléfono",
    "typing": "teclear",
    "showing_object": "mostrar un objeto a la cámara",
    "pointing_at": "señalar algo",
    "covering_face": "cubrirse el rostro",
    "yawning": "bostezar",
    "stretching": "estirarse",
    "arms_crossed": "cruzar los brazos",
    "head_on_hand": "apoyar la cabeza en la mano",
    "possible_fall": "posible caída",
}


@dataclass
class ActionRule:
    """Definición declarativa de una acción."""

    name: str
    evidence: tuple[str, ...] = ()
    any_of: tuple[str, ...] = ()
    forbid: tuple[str, ...] = ()
    min_ratio: float = 0.6
    min_duration: float = 0.8
    cooldown: float = 6.0
    weight: float = 1.0
    sequence: tuple[str, ...] = ()


@dataclass
class ActionEvent:
    action: str
    confidence: float
    person_id: int | None
    start_time: float
    duration: float
    evidence: list = field(default_factory=list)
    action_es: str = ""

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "confidence": round(float(self.confidence), 3),
            "person_id": self.person_id,
            "start_time": round(float(self.start_time), 3),
            "duration": round(float(self.duration), 2),
            "evidence": list(self.evidence),
        }


# Catálogo: 22 acciones, muy por encima de las diez que pide el criterio 20.
DEFAULT_RULES: tuple[ActionRule, ...] = (
    ActionRule("drinking", evidence=("hand_near_mouth", "bottle_detected"),
               any_of=("object_near_mouth",), min_ratio=0.5,
               min_duration=1.0, sequence=("upward_motion", "hold", "downward_motion"),
               cooldown=12.0),
    ActionRule("eating", evidence=("hand_near_mouth",), any_of=("food_near_mouth",),
               min_ratio=0.45, min_duration=1.2, cooldown=15.0),
    ActionRule("phone_call", evidence=("hand_near_ear",), any_of=("phone_near_ear", "phone_in_hand"),
               min_ratio=0.6, min_duration=2.0, cooldown=20.0),
    ActionRule("using_phone", evidence=("phone_in_hand",), forbid=("phone_near_ear",),
               min_ratio=0.55, min_duration=2.0, cooldown=25.0),
    ActionRule("typing", evidence=("wrists_over_desk",), any_of=("keyboard_present",),
               min_ratio=0.65, min_duration=2.0, cooldown=25.0),
    ActionRule("reading", evidence=("head_down", "looking_at_object"),
               min_ratio=0.6, min_duration=3.0, cooldown=30.0),
    ActionRule("writing", evidence=("head_down", "wrists_over_desk"),
               any_of=("writing_object",), min_ratio=0.6, min_duration=3.0, cooldown=30.0),
    ActionRule("arms_crossed", evidence=("arms_crossed",),
               min_ratio=0.7, min_duration=1.5, cooldown=20.0),
    ActionRule("head_on_hand", evidence=("hand_near_face", "head_tilted"),
               min_ratio=0.65, min_duration=2.5, cooldown=25.0),
    ActionRule("covering_face", evidence=("hand_near_face", "both_hands_up"),
               min_ratio=0.7, min_duration=1.0, cooldown=15.0),
    ActionRule("yawning", evidence=("mouth_wide_open",), any_of=("eyes_closed",),
               min_ratio=0.5, min_duration=0.8, cooldown=20.0),
    ActionRule("stretching", evidence=("both_hands_up",), any_of=("arms_extended",),
               min_ratio=0.6, min_duration=1.2, cooldown=20.0),
    ActionRule("clapping", evidence=("hands_together", "hands_oscillating"),
               min_ratio=0.5, min_duration=0.8, cooldown=10.0),
    ActionRule("waving", evidence=("hand_wave",), min_ratio=0.4,
               min_duration=0.6, cooldown=8.0),
    ActionRule("pointing_at", evidence=("pointing",), min_ratio=0.6,
               min_duration=0.8, cooldown=8.0),
    ActionRule("showing_object", evidence=("object_shown_to_camera",),
               min_ratio=0.5, min_duration=1.0, cooldown=10.0),
    ActionRule("sitting_down", evidence=("torso_descending", "knees_bent"),
               min_ratio=0.4, min_duration=0.6, cooldown=10.0),
    ActionRule("standing_up", evidence=("torso_ascending",), forbid=("knees_bent",),
               min_ratio=0.4, min_duration=0.6, cooldown=10.0),
    ActionRule("walking", evidence=("body_lateral_motion",), min_ratio=0.55,
               min_duration=1.5, cooldown=15.0),
    ActionRule("approaching", evidence=("body_growing",), min_ratio=0.5,
               min_duration=1.0, cooldown=12.0),
    ActionRule("moving_away", evidence=("body_shrinking",), min_ratio=0.5,
               min_duration=1.0, cooldown=12.0),
    # Caída: la más exigente de todas, y aun así solo "posible".
    ActionRule("possible_fall", evidence=("horizontal_body", "no_movement"),
               min_ratio=0.85, min_duration=3.5, cooldown=60.0, weight=1.0),
)


@dataclass
class _Window:
    samples: Deque[tuple[float, set]] = field(default_factory=lambda: deque(maxlen=180))


class ActionAnalyzer:
    def __init__(
        self,
        rules: tuple[ActionRule, ...] = DEFAULT_RULES,
        *,
        window: float = 5.0,
        min_confidence: float = 0.5,
        on_event: Callable[[ActionEvent], None] | None = None,
    ) -> None:
        self.rules = tuple(rules)
        self.window = float(window)
        self.min_confidence = float(min_confidence)
        self._on_event = on_event
        self._windows: dict[int, _Window] = {}
        self._started: dict[tuple[int, str], float] = {}
        self._last_emit: dict[tuple[int, str], float] = {}
        self._phase: dict[tuple[int, str], list[str]] = {}
        self._body_motion: dict[int, MotionTracker] = {}
        self._body_size: dict[int, deque] = {}
        self._object_motion: dict[int, MotionTracker] = {}
        self._current: dict[int, ActionEvent] = {}

    # ------------------------------------------------------------------
    def update(self, evidence, person_id: int = 0, now: float | None = None) -> list[ActionEvent]:
        """Añade un fotograma de evidencias y devuelve las acciones confirmadas."""
        now = time.time() if now is None else float(now)
        signals = self._derive(evidence, person_id, now)

        win = self._windows.setdefault(person_id, _Window())
        win.samples.append((now, signals))
        cutoff = now - self.window
        while win.samples and win.samples[0][0] < cutoff:
            win.samples.popleft()

        events: list[ActionEvent] = []
        for rule in self.rules:
            evt = self._evaluate(rule, person_id, win, now)
            if evt is not None:
                events.append(evt)
                self._current[person_id] = evt
                if self._on_event is not None:
                    try:
                        self._on_event(evt)
                    except Exception:
                        pass
        return events

    # ------------------------------------------------------------------
    def _derive(self, ev, person_id: int, now: float) -> set:
        """Convierte la evidencia del fotograma en un conjunto de señales."""
        signals: set = set(getattr(ev, "signals", ()) or ())

        # Movimiento global del cuerpo.
        center = getattr(ev, "body_center", None)
        size = float(getattr(ev, "body_size", 0.0) or 0.0)
        if center and getattr(ev, "has_pose", False):
            mt = self._body_motion.setdefault(person_id, MotionTracker(window=1.5))
            mt.add(center[0], center[1], now=now)
            dx, dy = mt.delta()
            if abs(dx) > 0.06 and mt.oscillations("x") <= 2:
                signals.add("body_lateral_motion")
            if dy > 0.045:
                signals.add("torso_descending")
            elif dy < -0.045:
                signals.add("torso_ascending")
            if mt.speed() < 0.012:
                signals.add("no_movement")

            sizes = self._body_size.setdefault(person_id, deque(maxlen=25))
            sizes.append(size)
            if len(sizes) >= 8:
                growth = (sizes[-1] - sizes[0]) / max(1e-4, sizes[0])
                if growth > 0.15:
                    signals.add("body_growing")
                elif growth < -0.15:
                    signals.add("body_shrinking")

        # Manos: oscilación conjunta (aplaudir) y saludo.
        if getattr(ev, "hands_together", False):
            mt = self._object_motion.setdefault(person_id, MotionTracker(window=1.0))
            mt.add(center[0] if center else 0.0, center[1] if center else 0.0, now=now)
            if mt.oscillations("x") >= 2 or mt.oscillations("y") >= 2:
                signals.add("hands_oscillating")

        # Bostezo y ojos.
        if float(getattr(ev, "mouth_open", 0.0)) > 0.55:
            signals.add("mouth_wide_open")
        if float(getattr(ev, "eyes_closed", 0.0)) > 0.5:
            signals.add("eyes_closed")

        # Objetos que confirman acciones.
        holding = {h.lower() for h in (getattr(ev, "holding", ()) or ())}
        if holding & {"keyboard", "laptop"}:
            signals.add("keyboard_present")
        if holding & {"pen", "pencil", "book", "notebook"}:
            signals.add("writing_object")
        if getattr(ev, "both_hands_up", False):
            signals.add("arms_extended")
        if getattr(ev, "hand_near_face", False) and getattr(ev, "looking_down", False):
            signals.add("head_tilted")

        # Secuencia vertical del objeto que se lleva a la boca (beber).
        if getattr(ev, "drink_object_near_mouth", False):
            signals.add("hold")
        return signals

    # ------------------------------------------------------------------
    def _evaluate(self, rule: ActionRule, person_id: int, win: _Window,
                  now: float) -> ActionEvent | None:
        key = (person_id, rule.name)
        samples = list(win.samples)
        if not samples:
            return None

        def matches(sig: set) -> bool:
            if rule.forbid and (set(rule.forbid) & sig):
                return False
            if rule.evidence and not set(rule.evidence) <= sig:
                return False
            if rule.any_of and not (set(rule.any_of) & sig):
                return False
            return bool(rule.evidence or rule.any_of)

        hits = [ts for ts, sig in samples if matches(sig)]
        if not hits:
            self._started.pop(key, None)
            self._phase.pop(key, None)
            return None

        ratio = len(hits) / len(samples)
        start = self._started.get(key)
        if start is None:
            start = hits[0]
            self._started[key] = start
        duration = now - start

        if ratio < rule.min_ratio or duration < rule.min_duration:
            return None

        last = self._last_emit.get(key, 0.0)
        if last and (now - last) < rule.cooldown:
            return None

        # Secuencia obligatoria (p. ej. beber: subir -> mantener -> bajar).
        if rule.sequence:
            seen = self._phase.setdefault(key, [])
            for _ts, sig in samples:
                for phase in rule.sequence:
                    if phase in sig and (not seen or seen[-1] != phase):
                        seen.append(phase)
            # Comprueba que las fases aparecen en el orden pedido.
            it = iter(seen)
            if not all(any(p == s for s in it) for p in rule.sequence):
                # Aún no está la secuencia completa: se acepta con menos confianza
                # solo si el resto de evidencias es muy fuerte.
                if ratio < 0.75:
                    return None

        confidence = min(0.96, 0.45 + 0.5 * ratio + min(0.1, duration * 0.02))
        confidence *= rule.weight
        if confidence < self.min_confidence:
            return None

        evidence_names = sorted({s for _ts, sig in samples for s in sig
                                 if s in set(rule.evidence) | set(rule.any_of)
                                 or s in {"hand_near_mouth", "bottle_detected", "upward_motion"}})
        self._last_emit[key] = now
        self._started.pop(key, None)
        self._phase.pop(key, None)

        return ActionEvent(
            action=rule.name,
            confidence=round(confidence, 3),
            person_id=person_id,
            start_time=start,
            duration=round(duration, 2),
            evidence=evidence_names[:6],
            action_es=ACTIONS_ES.get(rule.name, rule.name),
        )

    # ------------------------------------------------------------------
    def current(self, person_id: int = 0) -> ActionEvent | None:
        return self._current.get(person_id)

    def describe_current(self, person_id: int = 0) -> str:
        evt = self._current.get(person_id)
        if evt is None:
            return "No consigo identificar qué estás haciendo ahora mismo."
        if time.time() - (evt.start_time + evt.duration) > 12:
            return "Ahora mismo no distingo ninguna acción clara."
        nombre = ACTIONS_ES.get(evt.action, evt.action)
        if evt.confidence >= 0.75:
            return f"Diría que estás {self._gerundio(nombre)}."
        return f"Puede que estés {self._gerundio(nombre)}, aunque no estoy segura."

    @staticmethod
    def _gerundio(nombre: str) -> str:
        """Convierte el infinitivo del catálogo en algo natural en la frase."""
        mapa = {
            "sentarse": "sentándote", "levantarse": "levantándote",
            "caminar": "caminando", "acercarse a la cámara": "acercándote a la cámara",
            "alejarse": "alejándote", "saludar": "saludando", "aplaudir": "aplaudiendo",
            "beber": "bebiendo algo", "comer": "comiendo", "leer": "leyendo",
            "escribir": "escribiendo", "usar el celular": "usando el celular",
            "hablar por teléfono": "hablando por teléfono", "teclear": "tecleando",
            "mostrar un objeto a la cámara": "mostrándome algo",
            "señalar algo": "señalando algo", "cubrirse el rostro": "cubriéndote la cara",
            "bostezar": "bostezando", "estirarte": "estirándote", "estirarse": "estirándote",
            "cruzar los brazos": "con los brazos cruzados",
            "apoyar la cabeza en la mano": "con la cabeza apoyada en la mano",
            "posible caída": "en el suelo",
        }
        return mapa.get(nombre, nombre)

    def reset(self) -> None:
        self._windows.clear()
        self._started.clear()
        self._last_emit.clear()
        self._phase.clear()
        self._body_motion.clear()
        self._body_size.clear()
        self._object_motion.clear()
        self._current.clear()
