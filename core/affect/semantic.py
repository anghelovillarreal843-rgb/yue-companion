"""SemanticInterpreter — la segunda opinión, SOLO para lo ambiguo.

Las reglas locales resuelven la gran mayoría de mensajes. Este módulo existe
para el resto: frases donde el significado depende del mundo, no de las
palabras («da igual, ya me lo esperaba»), o donde hay una contradicción que un
regex no puede juzgar.

Principios de diseño, pensados para que esto NO se convierta en un coste:

  - Se llama poco y tarde: solo lo invoca `interpreter.py` cuando la lectura
    local queda floja o contradictoria.
  - Prompt minúsculo y salida JSON estricta: pocos tokens de ida y de vuelta.
  - Presupuesto por sesión: si se agota, se sigue con reglas y ya está.
  - Degradación total: sin motor, sin red o con un JSON roto, se devuelve None
    y el sistema continúa con la lectura local. Nunca revienta, nunca bloquea.

El modelo NO decide el comportamiento de YUE. Solo aporta una lectura afectiva
que luego se FUSIONA con la local (ver `merge`), donde las reglas conservan la
última palabra en negaciones y límites explícitos —cosas que un LLM se salta
más a menudo de lo que uno quisiera—.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from .models import (
    AffectiveState, Boundary, Emotion, boundary_from_name, clamp, emotion_from_name,
)

_EMOCIONES = ", ".join(e.value for e in Emotion)
_LIMITES = ", ".join(b.value for b in Boundary)

_SYSTEM = (
    "Eres un analizador afectivo. Lees un mensaje en español y devuelves SOLO un "
    "objeto JSON, sin texto alrededor, sin markdown y sin explicaciones.\n"
    "Campos exactos:\n"
    '{"primary_emotion": str, "secondary_emotion": str|null, "valence": float, '
    '"arousal": float, "confidence": float, "sarcasm_probability": float, '
    '"negated_emotions": [str], "explicit_boundary": [str], "possible_trigger": str}\n'
    f"primary_emotion y secondary_emotion deben ser una de: {_EMOCIONES}.\n"
    f"explicit_boundary solo puede contener: {_LIMITES}.\n"
    "valence va de -1 (muy negativo) a 1 (muy positivo). arousal de 0 (calmado) "
    "a 1 (muy activado). confidence y sarcasm_probability de 0 a 1.\n"
    "REGLAS:\n"
    "- Si la persona NIEGA una emoción ('no estoy triste'), ponla en "
    "negated_emotions y NO la uses como primary_emotion.\n"
    "- Si usa palabras positivas para describir algo malo, es sarcasmo: sube "
    "sarcasm_probability y elige la emoción REAL de fondo (frustration o "
    "disappointment), nunca joy.\n"
    "- possible_trigger solo si el propio mensaje lo dice; si no, cadena vacía. "
    "No inventes causas.\n"
    "- Si no hay evidencia suficiente, usa confidence baja. Es preferible "
    "reconocer la duda a acertar por casualidad."
)


@dataclass
class SemanticInterpreter:
    """Envoltorio del motor conversacional para lectura afectiva.

    Parámetros
    ----------
    engine
        Cualquier objeto con `.chat(messages, timeout=...)` que devuelva texto
        (el `AIEngine` del proyecto encaja tal cual).
    timeout
        Segundos máximos. Corto a propósito: si tarda, mejor reglas.
    max_calls
        Presupuesto de llamadas por sesión. Evita sorpresas en la factura.
    """

    engine: object | None = None
    timeout: int = 12
    max_calls: int = 40
    _calls: int = field(default=0, init=False)
    _fallos: int = field(default=0, init=False)
    _bloqueado_hasta: float = field(default=0.0, init=False)

    # ---- disponibilidad ---------------------------------------------------
    @property
    def available(self) -> bool:
        """¿Se puede usar ahora mismo? Comprueba motor, presupuesto y castigo."""
        if self.engine is None or not hasattr(self.engine, "chat"):
            return False
        if self._calls >= self.max_calls:
            return False
        return time.time() >= self._bloqueado_hasta

    @property
    def calls_used(self) -> int:
        return self._calls

    def reset_budget(self) -> None:
        self._calls = 0
        self._fallos = 0
        self._bloqueado_hasta = 0.0

    # ---- interpretación ---------------------------------------------------
    def interpret(self, message: str, recent_context: str = "") -> AffectiveState | None:
        """Lectura afectiva del modelo. None si no se pudo (y eso está bien)."""
        if not self.available or not (message or "").strip():
            return None

        contenido = (message or "").strip()[:600]
        if recent_context:
            contenido = (
                "Contexto reciente (solo para entender la situación, NO lo analices):\n"
                + recent_context.strip()[:600]
                + "\n\nMensaje a analizar:\n" + contenido
            )

        mensajes = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": contenido},
        ]

        self._calls += 1
        try:
            crudo = self.engine.chat(mensajes, timeout=self.timeout)  # type: ignore[union-attr]
        except Exception:
            self._penalizar()
            return None

        estado = parse_json_state(crudo)
        if estado is None:
            self._penalizar()
        else:
            self._fallos = 0
        return estado

    def _penalizar(self) -> None:
        """Tras varios fallos seguidos se descansa un rato.

        Si el proveedor está caído o el modelo no sabe devolver JSON, insistir
        solo añade latencia a cada mensaje del usuario.
        """
        self._fallos += 1
        if self._fallos >= 3:
            self._bloqueado_hasta = time.time() + 300.0
            self._fallos = 0


_RE_JSON = re.compile(r"\{.*\}", re.S)


def parse_json_state(raw: str) -> AffectiveState | None:
    """Convierte la respuesta del modelo en AffectiveState. Tolerante a ruido."""
    texto = (raw or "").strip()
    if not texto:
        return None
    texto = texto.replace("```json", " ").replace("```", " ")
    m = _RE_JSON.search(texto)
    if not m:
        return None
    try:
        datos = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(datos, dict):
        return None

    primaria = emotion_from_name(datos.get("primary_emotion")) or Emotion.NEUTRAL
    secundaria = emotion_from_name(datos.get("secondary_emotion"))
    if secundaria == primaria:
        secundaria = None

    negadas = tuple(
        e for e in (emotion_from_name(x) for x in _lista(datos.get("negated_emotions")))
        if e is not None
    )
    limites = tuple(
        b for b in (boundary_from_name(x) for x in _lista(datos.get("explicit_boundary")))
        if b is not None
    )

    confianza = clamp(datos.get("confidence", 0.5), 0.0, 0.9)  # el LLM nunca llega a 1
    return AffectiveState(
        primary_emotion=primaria,
        secondary_emotion=secundaria,
        valence=clamp(datos.get("valence", 0.0), -1.0, 1.0),
        arousal=clamp(datos.get("arousal", 0.4), 0.0, 1.0),
        confidence=confianza,
        uncertainty=clamp(1.0 - confianza, 0.0, 1.0),
        possible_trigger=str(datos.get("possible_trigger") or "")[:80],
        sarcasm_probability=clamp(datos.get("sarcasm_probability", 0.0), 0.0, 1.0),
        negated_emotions=negadas,
        explicit_boundary=limites,
        evidence=("lectura semántica",),
        source="semantic",
    )


def _lista(valor) -> list:
    if isinstance(valor, (list, tuple)):
        return list(valor)
    if isinstance(valor, str) and valor.strip():
        return [valor]
    return []


def merge(local: AffectiveState, remoto: AffectiveState | None) -> AffectiveState:
    """Fusiona la lectura local con la semántica.

    Quién gana en qué, y por qué:

    - **Límites explícitos y emociones negadas**: manda SIEMPRE lo local. Son
      hechos textuales que las reglas leen con precisión quirúrgica y que los
      modelos se saltan con demasiada frecuencia. Ignorar un «no quiero
      consejos» porque el LLM no lo vio sería el peor fallo posible.
    - **Emoción y valencia**: manda quien tenga más confianza. Para eso se
      llamó al modelo.
    - **Sarcasmo**: se toma el máximo. Ambos detectores tienen falsos
      negativos, ninguno inventa sarcasmo de la nada.
    - **Causa**: la local si existe (viene literal del texto); si no, la del
      modelo.
    """
    if remoto is None:
        return local

    gana_remoto = remoto.confidence > local.confidence + 0.05

    primaria = remoto.primary_emotion if gana_remoto else local.primary_emotion
    secundaria = remoto.secondary_emotion if gana_remoto else local.secondary_emotion

    # Una emoción negada localmente NUNCA puede ser la principal.
    negadas = tuple(dict.fromkeys(local.negated_emotions + remoto.negated_emotions))
    if primaria in local.negated_emotions:
        primaria = (local.primary_emotion
                    if local.primary_emotion not in local.negated_emotions
                    else Emotion.NEUTRAL)
    if secundaria in negadas:
        secundaria = None
    if secundaria == primaria:
        secundaria = None

    peso = 0.65 if gana_remoto else 0.35
    valencia = local.valence * (1 - peso) + remoto.valence * peso
    activacion = local.arousal * (1 - peso) + remoto.arousal * peso

    confianza = max(local.confidence, remoto.confidence)
    # Si los dos no coinciden en la emoción, no puede quedar una confianza alta:
    # el desacuerdo ES información.
    if local.primary_emotion != remoto.primary_emotion:
        confianza = min(confianza, 0.72)

    return AffectiveState(
        primary_emotion=primaria,
        secondary_emotion=secundaria,
        valence=valencia,
        arousal=activacion,
        confidence=confianza,
        uncertainty=max(0.0, min(local.uncertainty, remoto.uncertainty) if
                        local.primary_emotion == remoto.primary_emotion
                        else max(local.uncertainty, remoto.uncertainty) * 0.9),
        possible_trigger=local.possible_trigger or remoto.possible_trigger,
        sarcasm_probability=max(local.sarcasm_probability, remoto.sarcasm_probability),
        negated_emotions=negadas,
        # Los límites SIEMPRE los manda el análisis local. Innegociable.
        explicit_boundary=local.explicit_boundary or remoto.explicit_boundary,
        evidence=tuple(dict.fromkeys(local.evidence + remoto.evidence))[:8],
        source="hybrid",
    )
