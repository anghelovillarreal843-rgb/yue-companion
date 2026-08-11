"""SensorFusion — combinar texto, voz, cámara e histórico sin que se pisen.

El problema que resuelve, en un ejemplo real:

    El usuario escribe:  «estoy muy triste»       (texto, confianza 0.90)
    La cámara ve:        cara neutra              (cámara, confianza 0.80)

Sin fusión, gana la última señal que llegue, y si llega la cámara YUE responde
«te veo bien» a alguien que acaba de decir que está fatal. Eso no es un fallo
menor: es exactamente lo contrario de acompañar.

La regla que aplica este módulo:

    LA CÁMARA ES EVIDENCIA, NO VERDAD.

Se implementa en tres capas:

1. **Ponderación por fiabilidad.** Cada fuente tiene un peso fijo
   (`SOURCE_RELIABILITY`) y compite con `confidence * peso`. Una cámara al 0.80
   pesa 0.44; un texto al 0.90 pesa 0.90.

2. **Autoridad del texto explícito.** Si la persona lo dijo con palabras
   (`explicit=True`), ninguna otra fuente puede cambiar la emoción. Como mucho
   aportan matiz secundario. Decir lo que uno siente gana siempre a parecerlo.

3. **Caducidad.** Una cara triste de hace un minuto ya no cuenta; una frase
   explícita aguanta mucho más. Cada fuente tiene su `SOURCE_TTL`.

Solo librería estándar, determinista y seguro entre hilos.
"""
from __future__ import annotations

import threading
import time

from .models import UserObservation, UserState

#: Confianza mínima para que una observación cuente algo. Por debajo, es ruido.
MIN_USABLE_CONFIDENCE = 0.15

#: Cuánto sube la confianza cuando otra fuente independiente dice lo mismo.
#: Coincidir importa, pero no convierte una corazonada en certeza.
AGREEMENT_BONUS = 0.12

#: Emociones que se consideran «sin información». Una cámara que ve neutro NO
#: está aportando evidencia de que la persona esté bien: está aportando nada.
_VACIAS = frozenset({"neutral", "none", "unknown", ""})


class SensorFusion:
    """Guarda la última observación de cada fuente y las combina bajo demanda.

    Una fuente solo tiene UNA observación viva: la nueva sustituye a la vieja.
    Así el sistema nunca acumula historial infinito ni necesita limpieza.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._observations: dict[str, UserObservation] = {}

    # ------------------------------------------------------------------ API
    def observe(self, observation: UserObservation) -> None:
        """Registra lo que dice una fuente. No decide nada: solo anota."""
        if observation is None:
            return
        with self._lock:
            self._observations[observation.source] = observation

    def clear(self, source: str | None = None) -> None:
        with self._lock:
            if source is None:
                self._observations.clear()
            else:
                self._observations.pop((source or "").strip().lower(), None)

    def active(self, now: float | None = None) -> list[UserObservation]:
        """Observaciones aún vigentes, de más a menos peso efectivo."""
        now = time.time() if now is None else now
        with self._lock:
            vivas = [o for o in self._observations.values() if not o.is_expired(now)]
        return sorted(vivas, key=lambda o: o.effective_confidence, reverse=True)

    def snapshot(self) -> list[dict]:
        """Para el panel de depuración."""
        return [o.to_dict() for o in self.active()]

    # ------------------------------------------------------------- fusión
    def fuse(self, base: UserState | None = None,
             now: float | None = None) -> UserState:
        """Combina todas las observaciones vivas en un solo `UserState`.

        `base` permite conservar campos que la fusión no calcula (necesidad,
        tendencia, tema...), que vienen del cerebro afectivo y no de sensores.
        """
        now = time.time() if now is None else now
        base = base or UserState()
        vivas = self.active(now)

        # Confianza por fuente: se rellena SIEMPRE, aunque esa fuente no gane.
        # Es lo que permite ver luego, en los logs, quién dijo qué.
        por_fuente = {"text": 0.0, "voice": 0.0, "camera": 0.0}
        for obs in vivas:
            if obs.source in por_fuente:
                por_fuente[obs.source] = obs.effective_confidence

        utiles = [o for o in vivas
                  if o.effective_confidence >= MIN_USABLE_CONFIDENCE
                  and o.emotion not in _VACIAS]

        if not utiles:
            # Nadie dice nada con fundamento: se conserva lo que hubiera, pero
            # con la confianza al día. Inventar «está bien» sería mentir.
            return UserState(
                emotion=base.emotion, secondary_emotion=base.secondary_emotion,
                confidence=0.0, valence=base.valence, arousal=base.arousal,
                distress=base.distress, uncertainty=1.0,
                need=base.need, secondary_need=base.secondary_need,
                topic=base.topic, trigger=base.trigger,
                trend=base.trend, sustained=base.sustained,
                duration_s=base.duration_s, stability=base.stability,
                text_confidence=por_fuente["text"],
                voice_confidence=por_fuente["voice"],
                camera_confidence=por_fuente["camera"],
                dominant_source="none", explicit=False,
                safety_level=base.safety_level, updated_at=now,
            )

        ganadora = self._elegir_ganadora(utiles)
        acuerdo = self._nivel_de_acuerdo(ganadora, utiles)

        confianza = min(1.0, ganadora.effective_confidence + acuerdo * AGREEMENT_BONUS)
        secundaria = self._emocion_secundaria(ganadora, utiles)

        # La incertidumbre baja con la confianza, pero NO desaparece cuando las
        # fuentes se contradicen: si el texto dice una cosa y la cara otra, hay
        # algo que no cuadra y conviene que YUE lo sepa.
        contradicciones = sum(
            1 for o in utiles
            if o is not ganadora and not self._compatibles(o, ganadora))
        incertidumbre = max(0.0, 1.0 - confianza) + 0.15 * contradicciones

        return UserState(
            emotion=ganadora.emotion,
            secondary_emotion=secundaria,
            confidence=confianza,
            valence=ganadora.valence,
            arousal=ganadora.arousal,
            distress=base.distress if ganadora.source != "text" else base.distress,
            uncertainty=min(1.0, incertidumbre),
            need=base.need,
            secondary_need=base.secondary_need,
            topic=base.topic,
            trigger=ganadora.detail or base.trigger,
            trend=base.trend,
            sustained=base.sustained,
            duration_s=base.duration_s,
            stability=base.stability,
            text_confidence=por_fuente["text"],
            voice_confidence=por_fuente["voice"],
            camera_confidence=por_fuente["camera"],
            dominant_source=ganadora.source,
            explicit=ganadora.explicit,
            safety_level=base.safety_level,
            updated_at=now,
        )

    # -------------------------------------------------------------- interno
    def _elegir_ganadora(self, utiles: list[UserObservation]) -> UserObservation:
        """Quién manda en la lectura del usuario.

        Primero manda SIEMPRE la palabra explícita. Solo cuando nadie ha dicho
        nada con palabras se compara por confianza efectiva.
        """
        explicitas = [o for o in utiles if o.explicit and o.source == "text"]
        if explicitas:
            # Si hubiera varias (no debería), la más reciente.
            return max(explicitas, key=lambda o: o.timestamp)

        # Sin frase explícita, el texto sigue teniendo ventaja por su peso 1.00,
        # pero ya compite de tú a tú con las demás fuentes.
        return max(utiles, key=lambda o: (o.effective_confidence, o.timestamp))

    def _nivel_de_acuerdo(self, ganadora: UserObservation,
                          utiles: list[UserObservation]) -> int:
        return sum(1 for o in utiles
                   if o is not ganadora and self._compatibles(o, ganadora))

    @staticmethod
    def _compatibles(a: UserObservation, b: UserObservation) -> bool:
        """¿Dos observaciones apuntan a lo mismo?

        No hace falta la misma etiqueta exacta: «tristeza» y «cansancio» van en
        la misma dirección. Se compara el signo de la valencia, que es lo que
        de verdad importa para decidir cómo acompañar.
        """
        if a.emotion == b.emotion:
            return True
        if abs(a.valence) < 0.2 or abs(b.valence) < 0.2:
            return False
        return (a.valence > 0) == (b.valence > 0)

    def _emocion_secundaria(self, ganadora: UserObservation,
                            utiles: list[UserObservation]) -> str:
        """La cámara SÍ puede aportar matiz, aunque no pueda mandar.

        Es la forma de no tirar información útil: si la persona dice que está
        bien pero se la ve agotada, eso queda registrado como emoción
        secundaria. YUE puede tenerlo en cuenta con suavidad, sin contradecirla.
        """
        candidatas = [o for o in utiles
                      if o is not ganadora and o.emotion != ganadora.emotion]
        if not candidatas:
            return ""
        mejor = max(candidatas, key=lambda o: o.effective_confidence)
        if mejor.effective_confidence < MIN_USABLE_CONFIDENCE + 0.05:
            return ""
        return mejor.emotion


def effective_confidence(confidence: float, source: str) -> float:
    """Atajo funcional: confianza ya ponderada por la fiabilidad de la fuente."""
    from .models import SOURCE_RELIABILITY
    peso = SOURCE_RELIABILITY.get((source or "").strip().lower(), 0.5)
    return max(0.0, min(1.0, float(confidence) * peso))
