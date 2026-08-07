"""Analizador de la habitación (punto 6 del pedido).

NO describe cada fotograma desde cero: mantiene un ESTADO temporal de la
habitación y solo señala lo que cambia. Así YUE puede decir "apareció una
persona" en vez de repetir la lista completa de muebles cada segundo.

Reglas de redacción (obligatorias):
  - lenguaje prudente: "parece", "probablemente", "diría que",
  - nunca se afirma algo por debajo del umbral de confianza,
  - se dicen pocas cosas y las más relevantes, no un inventario.

Produce exactamente la estructura pedida:

    {"scene_type", "scene_confidence", "people_count", "objects",
     "lighting", "changes", "description"}
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from vision.tracking.object_tracker import label_es

_LIGHT_ES = {
    "muy_baja": "muy baja",
    "baja": "baja",
    "media": "media",
    "alta": "alta",
}
_CLUTTER_ES = {
    "ordenado": "se ve ordenado",
    "medio": "está normal de orden",
    "desordenado": "se ve algo desordenado",
}


def _hedge(confidence: float) -> str:
    """Palabra de prudencia según cuánta confianza haya."""
    if confidence >= 0.75:
        return "Parece"
    if confidence >= 0.5:
        return "Diría que parece"
    return "No estoy segura, pero podría ser"


@dataclass
class RoomState:
    """Estado persistente de la habitación entre fotogramas."""

    scene_type: str = "desconocido"
    scene_confidence: float = 0.0
    lighting: str = "media"
    clutter: str = "medio"
    people_count: int = 0
    objects: dict[str, float] = field(default_factory=dict)   # etiqueta -> confianza
    updated_at: float = 0.0


class RoomAnalyzer:
    def __init__(
        self,
        *,
        min_object_confidence: float = 0.45,
        min_scene_confidence: float = 0.4,
        change_cooldown: float = 6.0,
        max_objects: int = 8,
    ) -> None:
        self.min_object_confidence = float(min_object_confidence)
        self.min_scene_confidence = float(min_scene_confidence)
        self.change_cooldown = float(change_cooldown)
        self.max_objects = int(max_objects)
        self.state = RoomState()
        self._last_change_at = 0.0
        self._pending_changes: list[str] = []

    # ------------------------------------------------------------------
    def update(
        self,
        *,
        scene_reading=None,
        object_tracks=None,
        people_count: int = 0,
        now: float | None = None,
    ) -> dict:
        """Fusiona la lectura del fotograma con el estado y devuelve el resumen."""
        now = time.time() if now is None else float(now)
        changes: list[str] = []

        # --- personas ---------------------------------------------------
        if people_count != self.state.people_count:
            if people_count > self.state.people_count:
                changes.append("apareció una persona" if people_count - self.state.people_count == 1
                               else f"aparecieron {people_count - self.state.people_count} personas")
            else:
                changes.append("se fue una persona" if self.state.people_count - people_count == 1
                               else f"se fueron {self.state.people_count - people_count} personas")
            self.state.people_count = int(people_count)

        # --- objetos ----------------------------------------------------
        fresh: dict[str, float] = {}
        for t in object_tracks or []:
            label = getattr(t, "label", None) or (t.get("label") if isinstance(t, dict) else None)
            conf = getattr(t, "confidence", None)
            if conf is None and isinstance(t, dict):
                conf = t.get("confidence", 0.0)
            if not label:
                continue
            conf = float(conf or 0.0)
            if conf < self.min_object_confidence:
                continue
            key = str(label).strip().lower()
            fresh[key] = max(fresh.get(key, 0.0), conf)

        for key in fresh:
            if key not in self.state.objects:
                changes.append(f"apareció {label_es(key)}")
        for key in list(self.state.objects):
            if key not in fresh:
                changes.append(f"ya no veo {label_es(key)}")
        self.state.objects = fresh

        # --- escena e iluminación --------------------------------------
        if scene_reading is not None:
            scene_type = getattr(scene_reading, "scene_type", "desconocido")
            scene_conf = float(getattr(scene_reading, "scene_confidence", 0.0))
            lighting = getattr(scene_reading, "lighting", self.state.lighting)
            clutter = getattr(scene_reading, "clutter", self.state.clutter)
            if scene_conf >= self.min_scene_confidence and scene_type != self.state.scene_type:
                if self.state.scene_type != "desconocido":
                    changes.append(f"el lugar ahora parece {scene_type}")
                self.state.scene_type = scene_type
            self.state.scene_confidence = scene_conf
            if lighting != self.state.lighting:
                if self.state.updated_at:
                    changes.append(f"la iluminación cambió a {_LIGHT_ES.get(lighting, lighting)}")
                self.state.lighting = lighting
            self.state.clutter = clutter

        self.state.updated_at = now

        # Los cambios se acumulan y solo salen cada `change_cooldown` segundos,
        # para no narrar cada parpadeo del detector.
        self._pending_changes.extend(changes)
        emitted: list[str] = []
        if self._pending_changes and (now - self._last_change_at) >= self.change_cooldown:
            # Deduplica conservando el orden.
            seen: set[str] = set()
            for c in self._pending_changes:
                if c not in seen:
                    seen.add(c)
                    emitted.append(c)
            self._pending_changes.clear()
            self._last_change_at = now

        return self.snapshot(changes=emitted)

    # ------------------------------------------------------------------
    def snapshot(self, changes: list[str] | None = None) -> dict:
        objetos = sorted(self.state.objects.items(), key=lambda kv: kv[1], reverse=True)
        objetos = objetos[: self.max_objects]
        return {
            "scene_type": self.state.scene_type,
            "scene_confidence": round(self.state.scene_confidence, 3),
            "people_count": self.state.people_count,
            "objects": [{"label": label_es(k), "raw_label": k, "confidence": round(v, 3)}
                        for k, v in objetos],
            "lighting": self.state.lighting,
            "clutter": self.state.clutter,
            "changes": list(changes or []),
            "description": self.describe(),
        }

    # ------------------------------------------------------------------
    def describe(self) -> str:
        """Descripción prudente en español, de una o dos frases."""
        s = self.state
        if not s.updated_at:
            return "Todavía no he mirado la habitación."

        partes: list[str] = []
        if s.scene_type != "desconocido" and s.scene_confidence >= self.min_scene_confidence:
            partes.append(f"{_hedge(s.scene_confidence)} {s.scene_type}")
        else:
            partes.append("No consigo identificar bien qué tipo de lugar es")

        if s.people_count == 1:
            partes.append("con una persona delante")
        elif s.people_count > 1:
            partes.append(f"con {s.people_count} personas")

        objetos = sorted(s.objects.items(), key=lambda kv: kv[1], reverse=True)[:4]
        if objetos:
            nombres = []
            for k, v in objetos:
                nombre = label_es(k)
                nombres.append(nombre if v >= 0.7 else f"probablemente {nombre}")
            partes.append("y veo " + ", ".join(nombres))

        frase = " ".join(partes).strip() + "."
        extras = []
        if s.lighting in {"baja", "muy_baja"}:
            extras.append(f"La iluminación se ve {_LIGHT_ES.get(s.lighting, s.lighting)}")
        elif s.lighting == "alta":
            extras.append("Hay bastante luz")
        if s.clutter in _CLUTTER_ES and s.clutter != "medio":
            extras.append(_CLUTTER_ES[s.clutter].capitalize())
        if extras:
            frase += " " + ". ".join(extras) + "."
        return frase

    def reset(self) -> None:
        self.state = RoomState()
        self._pending_changes.clear()
        self._last_change_at = 0.0
