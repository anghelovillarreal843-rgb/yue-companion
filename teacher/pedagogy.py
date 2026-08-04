"""Sistema de Aprendizaje del Profesor (§4) — una de las funciones principales.

Cuando un profesor humano imparte una clase, YUE puede OBSERVAR cómo enseña y
construir un MODELO PEDAGÓGICO PROPIO: no copia literalmente al docente, sino que
mide patrones (forma de explicar, vocabulario, estructura, uso de ejemplos, ritmo,
motivación) y los agrega como estadísticas que evolucionan clase a clase (§14).

Cómo funciona:
    - `observe(text)` recibe una intervención del profesor (transcrita por voz o
      leída por OCR/pantalla) y extrae RASGOS pedagógicos numéricos.
    - `PedagogyModel` promedia esos rasgos con memoria (media móvil), de modo que
      el estilo aprendido mejora y se estabiliza con más observaciones.
    - `style_directive_es()` traduce el modelo a una instrucción en español que se
      inyecta en el prompt de la profesora, para que YUE enseñe con un estilo
      informado por el docente, SIN dejar de ser YUE (§7).
    - Persistencia JSON propia (aditiva, degradable). No toca la memoria emocional.

Puro: sin Qt, sin IA. Solo análisis de texto y estadística ligera.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata


# Marcadores de estructura y estilo que buscamos en la explicación del profesor.
_CONECTORES = (
    "primero", "segundo", "tercero", "luego", "despues", "despues", "entonces",
    "por lo tanto", "en conclusion", "en resumen", "es decir", "por ejemplo",
    "recuerden", "presten atencion", "observen", "fijense", "noten",
)
_MOTIVACION = (
    "muy bien", "excelente", "perfecto", "buen trabajo", "van bien", "animo",
    "no se preocupen", "tranquilos", "ustedes pueden", "sigan asi", "felicidades",
)
_PREGUNTA_RETO = ("que pasaria", "por que", "como", "que creen", "alguien sabe",
                  "quien me dice", "que opinan")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def observe(text: str) -> dict:
    """Extrae rasgos pedagógicos de UNA intervención del profesor.

    Devuelve un dict de métricas normalizadas (aprox. 0..1 salvo conteos), que
    luego el modelo agrega. No juzga «bien/mal»: mide estilo.
    """
    raw = text or ""
    n = _norm(raw)
    palabras = re.findall(r"[a-zñáéíóú]+", n)
    n_pal = len(palabras) or 1
    frases = [f for f in re.split(r"[.!?]+", raw) if f.strip()]
    n_frases = len(frases) or 1

    largas = sum(1 for w in palabras if len(w) >= 9)   # proxy de vocabulario técnico
    return {
        "muestras": 1,
        "palabras": n_pal,
        "long_frase": round(n_pal / n_frases, 2),          # ritmo/estructura
        "vocab_tecnico": round(largas / n_pal, 3),         # nivel de vocabulario
        "conectores": _cuenta(n, _CONECTORES),             # estructura discursiva
        "ejemplos": n.count("por ejemplo") + n.count("p ej"),
        "preguntas": raw.count("?"),
        "retos": _cuenta(n, _PREGUNTA_RETO),               # preguntas socráticas
        "motivacion": _cuenta(n, _MOTIVACION),             # forma de motivar
    }


def _cuenta(n: str, claves) -> int:
    return sum(n.count(k) for k in claves)


class PedagogyModel:
    """Modelo pedagógico evolutivo, con media móvil y persistencia JSON."""

    # Peso de cada nueva observación en la media móvil (0..1). Más bajo = más
    # estable (aprende despacio y no lo desvía una sola intervención rara).
    ALPHA = 0.15

    def __init__(self, path: str | None = None):
        self._lock = threading.RLock()
        self._path = path
        self._model = {
            "muestras": 0,
            "long_frase": 0.0,
            "vocab_tecnico": 0.0,
            "conectores_por_100": 0.0,
            "ejemplos_por_100": 0.0,
            "preguntas_por_100": 0.0,
            "retos_por_100": 0.0,
            "motivacion_por_100": 0.0,
            "actualizado": 0.0,
        }
        self._observando = False
        self._load()

    # -------------------------------------------------- observación en vivo
    def set_observing(self, on: bool) -> None:
        with self._lock:
            self._observando = bool(on)

    def is_observing(self) -> bool:
        with self._lock:
            return self._observando

    def ingest(self, text: str) -> bool:
        """Observa una intervención SOLO si la observación está activa.

        Devuelve True si la incorporó al modelo.
        """
        if not self.is_observing():
            return False
        rasgos = observe(text)
        if rasgos["palabras"] < 4:      # ignora ruidos/frases mínimas
            return False
        self._update(rasgos)
        return True

    # -------------------------------------------------- actualización
    def _update(self, r: dict) -> None:
        por100 = 100.0 / (r["palabras"] or 1)
        nuevos = {
            "long_frase": r["long_frase"],
            "vocab_tecnico": r["vocab_tecnico"],
            "conectores_por_100": r["conectores"] * por100,
            "ejemplos_por_100": r["ejemplos"] * por100,
            "preguntas_por_100": r["preguntas"] * por100,
            "retos_por_100": r["retos"] * por100,
            "motivacion_por_100": r["motivacion"] * por100,
        }
        with self._lock:
            m = self._model
            primera = m["muestras"] == 0
            a = 1.0 if primera else self.ALPHA
            for k, v in nuevos.items():
                m[k] = round((1 - a) * m[k] + a * v, 3)
            m["muestras"] += 1
            m["actualizado"] = time.time()
            self._save()

    # -------------------------------------------------- lectura del estilo
    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._model)

    def has_learned(self) -> bool:
        with self._lock:
            return self._model["muestras"] >= 3

    def style_directive_es(self) -> str:
        """Traduce el modelo aprendido a una instrucción de estilo para el prompt.

        Se inyecta en el Modo Profesora para que YUE enseñe con el estilo que
        aprendió del docente, sin copiarlo ni perder su identidad (§7).
        """
        if not self.has_learned():
            return ""
        m = self.snapshot()
        rasgos = []
        # Vocabulario / nivel.
        if m["vocab_tecnico"] >= 0.12:
            rasgos.append("usa vocabulario técnico preciso y define los términos")
        elif m["vocab_tecnico"] <= 0.06:
            rasgos.append("explica con lenguaje sencillo y cercano")
        # Ritmo / estructura de frase.
        if m["long_frase"] >= 22:
            rasgos.append("desarrolla las ideas con detalle antes de concluir")
        elif m["long_frase"] <= 12:
            rasgos.append("ve al grano con frases cortas y directas")
        # Ejemplos.
        if m["ejemplos_por_100"] >= 0.4:
            rasgos.append("apóyate mucho en ejemplos concretos")
        # Estructura discursiva.
        if m["conectores_por_100"] >= 1.5:
            rasgos.append("estructura la explicación con conectores (primero, luego, en resumen)")
        # Preguntas / método socrático.
        if m["preguntas_por_100"] >= 1.0 or m["retos_por_100"] >= 0.5:
            rasgos.append("intercala preguntas para que el alumno piense")
        # Motivación.
        if m["motivacion_por_100"] >= 0.3:
            rasgos.append("motiva y da ánimo con frecuencia")
        if not rasgos:
            return ""
        return (
            f"Estilo pedagógico aprendido de tu profesor (basado en {m['muestras']} "
            "observaciones; inspírate en él SIN dejar de ser tú): " + "; ".join(rasgos) + "."
        )

    def report_text_es(self) -> str:
        m = self.snapshot()
        if m["muestras"] == 0:
            return "Aún no he observado a ningún profesor, así que no tengo un modelo pedagógico."
        return (
            f"Modelo pedagógico ({m['muestras']} observaciones): "
            f"vocabulario técnico {m['vocab_tecnico']}, "
            f"frases de ~{m['long_frase']} palabras, "
            f"ejemplos/100pal {m['ejemplos_por_100']}, "
            f"preguntas/100pal {m['preguntas_por_100']}, "
            f"motivación/100pal {m['motivacion_por_100']}."
        )

    # -------------------------------------------------- persistencia
    def _load(self):
        if self._path and os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._model.update(json.load(f))
            except Exception as exc:
                print("[profesora/pedagogia] no pude cargar el modelo:", exc)

    def _save(self):
        if not self._path:
            return
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._model, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            print("[profesora/pedagogia] no pude guardar el modelo:", exc)
