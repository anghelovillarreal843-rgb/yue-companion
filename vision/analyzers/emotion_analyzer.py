"""Estimación afectiva multimodal y PROBABILÍSTICA (punto 9 del pedido).

Premisa de diseño, escrita a propósito en el código: **no existe una lectura
real ni infalible de emociones por cámara**. Este módulo produce una ESTIMACIÓN
con confianza y grado de certeza, y una frase segura para decir en voz alta.

Nunca produce, ni puede producir:
  - diagnósticos médicos o psicológicos ("estás deprimido", "tienes ansiedad"),
  - afirmaciones sobre la verdad o la intención ("estás mintiendo"),
  - afirmaciones sobre sentimientos privados ("estás enamorado"),
  - certeza absoluta ("sé exactamente cómo te sientes").

Señales que combina (las que haya disponibles; nada es obligatorio):
  expresión facial, cejas, apertura de ojos, tensión de labios, sonrisa,
  dirección de la mirada, postura, movimiento corporal, inclinación de cabeza,
  ritmo de voz (SOLO si hay permiso explícito) y contexto de la conversación.

Acumula en el tiempo con un voto por ventana, de modo que la estimación no salte
de "alegre" a "molesto" por un fotograma suelto.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from vision.tracking.temporal_smoother import EMA, MajorityVote

# Categorías del pedido (clave interna -> nombre en español).
CATEGORIES: dict[str, str] = {
    "happy": "alegre",
    "sad": "triste o decaído",
    "surprised": "sorprendido",
    "tense": "tenso",
    "angry": "molesto",
    "tired": "cansado",
    "focused": "concentrado",
    "confused": "confundido",
    "neutral": "neutral",
    "undetermined": "indeterminado",
}

# Frases seguras: SIEMPRE marcan que es una impresión, nunca un hecho.
SAFE_PHRASES: dict[str, tuple[str, ...]] = {
    "happy": ("Te noto de buen ánimo, aunque puedo equivocarme.",
              "Me da la impresión de que estás contento."),
    "sad": ("Te veo un poco decaído, aunque no puedo saberlo con certeza.",
            "Me da la impresión de que estás algo bajoneado."),
    "surprised": ("Detecté señales compatibles con sorpresa.",
                  "Parece que algo te sorprendió."),
    "tense": ("Te noto algo tenso, aunque es solo una impresión.",
              "Detecté señales que podrían ser de tensión."),
    "angry": ("Percibo señales que podrían ser de molestia; corrígeme si me equivoco.",
              "Me da la impresión de que algo te incomodó."),
    "tired": ("Parece que podrías estar algo cansado.",
              "Te noto cansado, aunque no puedo asegurarlo."),
    "focused": ("Me da la impresión de que estás concentrado.",
                "Te veo bastante enfocado en algo."),
    "confused": ("Me parece ver cara de duda, aunque puedo estar leyéndolo mal.",
                 "Detecté señales compatibles con confusión."),
    "neutral": ("Te veo tranquilo, con expresión neutral.",),
    "undetermined": ("No consigo hacerme una idea clara de cómo te sientes.",),
}

# Frases PROHIBIDAS: si alguna aparece en un texto generado, se bloquea.
FORBIDDEN_PATTERNS: tuple[str, ...] = (
    "estás deprimido", "estas deprimido", "tienes depresión", "tienes depresion",
    "tienes ansiedad", "estás mintiendo", "estas mintiendo", "me estás mintiendo",
    "estás enamorado", "estas enamorado", "sé exactamente cómo te sientes",
    "se exactamente como te sientes", "sé cómo te sientes", "estás enfermo",
    "tienes un trastorno", "necesitas medicación", "necesitas medicacion",
    "eres bipolar", "tienes tdah", "estás loco", "estas loco",
)


def is_safe_phrase(text: str) -> bool:
    """False si el texto contiene una afirmación prohibida sobre la persona."""
    low = (text or "").strip().lower()
    return not any(p in low for p in FORBIDDEN_PATTERNS)


@dataclass
class AffectiveEstimate:
    """Estructura EXACTA del documento + campos útiles extra."""

    affective_state: str = "undetermined"
    confidence: float = 0.0
    certainty: str = "low"                      # low | medium | high
    signals: list = field(default_factory=list)
    safe_description: str = ""
    label_es: str = "indeterminado"
    timestamp: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        if self.affective_state == "undetermined":
            return {"affective_state": "undetermined", "confidence": 0.0}
        return {
            "affective_state": self.affective_state,
            "confidence": round(float(self.confidence), 3),
            "certainty": self.certainty,
            "signals": list(self.signals),
            "safe_description": self.safe_description,
        }


class EmotionAnalyzer:
    def __init__(
        self,
        *,
        min_confidence: float = 0.45,
        window: float = 4.0,
        allow_voice_signals: bool = False,
    ) -> None:
        self.min_confidence = float(min_confidence)
        self.allow_voice_signals = bool(allow_voice_signals)
        self._vote = MajorityVote(window=window)
        self._conf = EMA(0.25)
        self._last: AffectiveEstimate | None = None
        self._phrase_index = 0

    # ------------------------------------------------------------------
    def update(
        self,
        *,
        blendshapes: dict | None = None,
        expression: str = "",
        looking_at_camera: bool | None = None,
        head_pose: dict | None = None,
        pose_state: str = "",
        movement: str = "",
        hand_supporting_head: bool = False,
        eye_closure_rate: float = 0.0,
        voice_rate: float | None = None,
        conversation_hint: str = "",
        now: float | None = None,
    ) -> AffectiveEstimate:
        """Añade una observación y devuelve la estimación acumulada."""
        now = time.time() if now is None else float(now)
        scores: dict[str, float] = {k: 0.0 for k in CATEGORIES if k != "undetermined"}
        signals: list[str] = []
        bs = {k.lower(): float(v) for k, v in (blendshapes or {}).items()}

        def g(*names: str) -> float:
            vals = [bs[n] for n in names if n in bs]
            return sum(vals) / len(vals) if vals else 0.0

        # --- rostro (blendshapes de MediaPipe Face Landmarker) ----------
        smile = g("mouthsmileleft", "mouthsmileright")
        frown = g("mouthfrownleft", "mouthfrownright")
        brow_up = g("browouterupleft", "browouterupright", "browinnerup")
        brow_down = g("browdownleft", "browdownright")
        eye_blink = g("eyeblinkleft", "eyeblinkright")
        eye_wide = g("eyewideleft", "eyewideright")
        jaw_open = g("jawopen")
        lip_press = g("mouthpressleft", "mouthpressright")
        squint = g("eyesquintleft", "eyesquintright")

        if smile > 0.35:
            scores["happy"] += smile * 1.4
            signals.append("smile_detected")
        if frown > 0.30:
            scores["sad"] += frown * 1.2
            signals.append("mouth_frown")
        if brow_up > 0.35 and (eye_wide > 0.25 or jaw_open > 0.25):
            scores["surprised"] += (brow_up + eye_wide) * 0.8
            signals.append("raised_eyebrows")
        if brow_down > 0.35:
            scores["angry"] += brow_down * 0.9
            scores["focused"] += brow_down * 0.4
            signals.append("lowered_eyebrows")
        if lip_press > 0.30:
            scores["tense"] += lip_press * 1.0
            signals.append("lip_tension")
        if squint > 0.35 and brow_down > 0.2:
            scores["confused"] += squint * 0.7
            signals.append("squinting")
        if eye_blink > 0.55 or eye_closure_rate > 0.35:
            scores["tired"] += max(eye_blink, eye_closure_rate) * 1.2
            signals.append("frequent_eye_closure")
        if jaw_open > 0.6 and eye_blink > 0.4:
            scores["tired"] += 0.6
            signals.append("possible_yawn")

        # --- expresión ya resumida por otro módulo ---------------------
        expr_map = {
            "smiling": "happy", "surprised": "surprised", "sad_expression": "sad",
            "angry_expression": "angry", "tense": "tense", "sleepy": "tired",
            "confused": "confused", "thinking": "focused",
        }
        if expression in expr_map:
            scores[expr_map[expression]] = scores.get(expr_map[expression], 0.0) + 0.6
            signals.append(f"expression_{expression}")

        # --- postura y cuerpo -------------------------------------------
        if hand_supporting_head:
            scores["tired"] += 0.7
            scores["focused"] += 0.2
            signals.append("head_supported_by_hand")
        if movement == "still":
            scores["focused"] += 0.3
            scores["tired"] += 0.2
            signals.append("low_body_movement")
        elif movement == "moving":
            scores["tense"] += 0.15
        if pose_state == "leaning_forward":
            scores["focused"] += 0.35
            signals.append("leaning_forward")
        if head_pose:
            pitch = abs(float(head_pose.get("pitch", 0.0)))
            roll = abs(float(head_pose.get("roll", 0.0)))
            if pitch > 0.35:
                scores["tired"] += 0.25
                signals.append("head_tilted_down")
            if roll > 0.30:
                scores["confused"] += 0.2
                signals.append("head_tilted_side")
        if looking_at_camera is False:
            scores["focused"] += 0.15
        elif looking_at_camera is True:
            scores["happy"] += 0.05

        # --- voz (SOLO con permiso explícito) ---------------------------
        if voice_rate is not None and self.allow_voice_signals:
            if voice_rate > 1.25:
                scores["tense"] += 0.3
                signals.append("fast_speech_rate")
            elif voice_rate < 0.8:
                scores["tired"] += 0.3
                signals.append("slow_speech_rate")

        # --- contexto de la conversación --------------------------------
        hint_map = {"positivo": "happy", "negativo": "sad", "duda": "confused",
                    "trabajo": "focused"}
        if conversation_hint in hint_map:
            scores[hint_map[conversation_hint]] += 0.25
            signals.append(f"context_{conversation_hint}")

        # --- decisión ----------------------------------------------------
        total = sum(scores.values())
        if total <= 0:
            best, raw_conf = "neutral", 0.4
        else:
            best = max(scores, key=lambda k: scores[k])
            raw_conf = min(0.95, scores[best] / max(1.0, total) + min(0.3, scores[best] * 0.25))

        self._vote.add(best, raw_conf, now=now)
        self._conf.update(raw_conf)
        label, vote_conf = self._vote.winner(now=now)
        if not label:
            label, vote_conf = best, raw_conf

        # La confianza final mezcla la del fotograma con el acuerdo temporal.
        confidence = round(min(0.95, 0.5 * self._conf.get(raw_conf) + 0.5 * vote_conf), 3)

        if confidence < self.min_confidence or label in {"", "neutral"} and confidence < 0.5:
            estimate = AffectiveEstimate(
                affective_state="undetermined", confidence=0.0, certainty="low",
                signals=sorted(set(signals))[:6],
                safe_description=SAFE_PHRASES["undetermined"][0],
                label_es=CATEGORIES["undetermined"], timestamp=now,
            )
        else:
            estimate = AffectiveEstimate(
                affective_state=label,
                confidence=confidence,
                certainty=self._certainty(confidence),
                signals=sorted(set(signals))[:6],
                safe_description=self._phrase(label),
                label_es=CATEGORIES.get(label, label),
                timestamp=now,
            )
        self._last = estimate
        return estimate

    # ------------------------------------------------------------------
    @staticmethod
    def _certainty(confidence: float) -> str:
        if confidence >= 0.75:
            return "high"
        if confidence >= 0.55:
            return "medium"
        return "low"

    def _phrase(self, label: str) -> str:
        options = SAFE_PHRASES.get(label) or SAFE_PHRASES["undetermined"]
        self._phrase_index += 1
        phrase = options[self._phrase_index % len(options)]
        # Cinturón y tirantes: si alguna vez se colara algo prohibido, no sale.
        return phrase if is_safe_phrase(phrase) else SAFE_PHRASES["undetermined"][0]

    def last(self) -> AffectiveEstimate | None:
        return self._last

    def reset(self) -> None:
        self._vote.clear()
        self._conf.reset()
        self._last = None
