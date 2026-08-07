"""Seguimiento temporal de objetos (punto 10 del pedido).

Sobre el detector de objetos añade lo que de verdad hace falta para conversar:

  - `object_id` estable, para no contar dos veces la misma botella,
  - estados `visible` / `perdido` y eventos de aparición y desaparición,
  - umbral de confianza DISTINTO por clase (un "celular" pequeño necesita menos
    exigencia que declarar que hay un "perro" en la habitación),
  - traducción de la etiqueta al español para el diálogo,
  - clases personalizadas añadibles en caliente.

Todo en memoria y sin dependencias externas.
"""
from __future__ import annotations

import time

from vision.tracking.base_tracker import BaseTracker, Track

# Traducción de las clases COCO/MediaPipe más frecuentes en una casa u oficina.
LABELS_ES: dict[str, str] = {
    "person": "persona", "laptop": "laptop", "cell phone": "celular",
    "cell_phone": "celular", "mobile phone": "celular", "book": "libro",
    "bottle": "botella", "cup": "taza", "wine glass": "copa", "bowl": "tazón",
    "chair": "silla", "couch": "sofá", "bed": "cama", "dining table": "mesa",
    "table": "mesa", "desk": "escritorio", "tv": "televisor",
    "tvmonitor": "televisor", "monitor": "monitor", "keyboard": "teclado",
    "mouse": "mouse", "remote": "control remoto", "backpack": "mochila",
    "handbag": "bolso", "clock": "reloj", "vase": "florero",
    "potted plant": "planta", "scissors": "tijeras", "teddy bear": "peluche",
    "dog": "perro", "cat": "gato", "bird": "ave", "banana": "plátano",
    "apple": "manzana", "sandwich": "sándwich", "pizza": "pizza",
    "fork": "tenedor", "knife": "cuchillo", "spoon": "cuchara",
    "refrigerator": "refrigeradora", "microwave": "microondas", "oven": "horno",
    "sink": "lavadero", "toilet": "inodoro", "door": "puerta",
    "window": "ventana", "toothbrush": "cepillo de dientes",
    "umbrella": "paraguas", "tie": "corbata", "suitcase": "maleta",
    "sports ball": "pelota", "bicycle": "bicicleta", "car": "auto",
}

# Umbrales por clase: más exigente cuanto más "afirmativa" sea la frase.
CLASS_THRESHOLDS: dict[str, float] = {
    "person": 0.45,
    "laptop": 0.45,
    "cell phone": 0.40,
    "book": 0.42,
    "bottle": 0.42,
    "cup": 0.42,
    "chair": 0.45,
    "dog": 0.60,
    "cat": 0.60,
    "bird": 0.65,
    "tv": 0.50,
    "bed": 0.55,
    "couch": 0.55,
    "dining table": 0.50,
}

DEFAULT_THRESHOLD = 0.50


def label_es(label: str) -> str:
    """Etiqueta en español; si no está en el diccionario, se devuelve tal cual."""
    key = (label or "").strip().lower()
    return LABELS_ES.get(key, LABELS_ES.get(key.replace("_", " "), key.replace("_", " ")))


class ObjectTracker(BaseTracker):
    def __init__(self, thresholds: dict[str, float] | None = None, **kwargs) -> None:
        kwargs.setdefault("iou_threshold", 0.3)
        kwargs.setdefault("max_distance", 0.15)
        kwargs.setdefault("max_missing", 10)
        kwargs.setdefault("min_hits", 2)
        kwargs.setdefault("match_label", True)
        super().__init__(**kwargs)
        self.thresholds = dict(CLASS_THRESHOLDS)
        if thresholds:
            self.thresholds.update({k.lower(): float(v) for k, v in thresholds.items()})
        self._custom_classes: set[str] = set()

    # ------------------------------------------------------------------
    def register_class(self, label: str, threshold: float | None = None,
                       spanish: str | None = None) -> None:
        """Registra una clase personalizada (para modelos entrenados aparte)."""
        key = label.strip().lower()
        self._custom_classes.add(key)
        if threshold is not None:
            self.thresholds[key] = float(threshold)
        if spanish:
            LABELS_ES[key] = spanish

    def threshold_for(self, label: str) -> float:
        return self.thresholds.get((label or "").strip().lower(), DEFAULT_THRESHOLD)

    # ------------------------------------------------------------------
    def update_from_detections(self, detections, now: float | None = None) -> list[Track]:
        """Acepta `ObjectObservation` o dicts; filtra por umbral de clase."""
        now = time.time() if now is None else float(now)
        dets: list[dict] = []
        for d in detections or []:
            label = getattr(d, "label", None) or (d.get("label") if isinstance(d, dict) else None)
            conf = getattr(d, "confidence", None)
            if conf is None and isinstance(d, dict):
                conf = d.get("confidence", 0.0)
            box = getattr(d, "bounding_box", None)
            if box is None and isinstance(d, dict):
                box = d.get("bounding_box") or d.get("box")
            if not label or not isinstance(box, dict):
                continue
            conf = float(conf or 0.0)
            if conf < self.threshold_for(label):
                continue
            dets.append({"label": str(label), "confidence": conf, "box": box})
        return self.update(dets, now=now)

    # ------------------------------------------------------------------
    def as_state(self) -> list[dict]:
        out = []
        for t in self.tracks():
            d = t.as_dict()
            d["object_id"] = d.pop("id")
            d["translated_label"] = label_es(t.label)
            out.append(d)
        return out

    def counts(self) -> dict[str, int]:
        """Conteo por clase SIN duplicar (usa los IDs, no las detecciones)."""
        counts: dict[str, int] = {}
        for t in self.tracks():
            counts[t.label] = counts.get(t.label, 0) + 1
        return counts

    def labels_es(self) -> list[str]:
        return sorted({label_es(t.label) for t in self.tracks()})

    def has(self, *labels: str) -> bool:
        """True si alguna de esas clases está visible ahora."""
        wanted = {l.strip().lower() for l in labels}
        return any(t.label.strip().lower() in wanted for t in self.tracks())

    def nearest_to(self, x: float, y: float, labels: tuple[str, ...] | None = None):
        """Pista más cercana a un punto normalizado (para 'sostiene un objeto')."""
        best, best_d = None, 1e9
        wanted = {l.lower() for l in labels} if labels else None
        for t in self.tracks():
            if wanted and t.label.lower() not in wanted:
                continue
            cx = t.box["x"] + t.box["w"] / 2.0
            cy = t.box["y"] + t.box["h"] / 2.0
            d = ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
            if d < best_d:
                best, best_d = t, d
        return best, best_d

    def changes_es(self) -> list[str]:
        """Frases cortas de lo que apareció o desapareció en la última pasada."""
        msgs = []
        for t in self.appeared():
            msgs.append(f"apareció {label_es(t.label)}")
        for t in self.disappeared():
            if t.hits >= self.min_hits:
                msgs.append(f"ya no veo {label_es(t.label)}")
        return msgs
