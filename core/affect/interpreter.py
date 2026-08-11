"""AffectiveInterpreter — la puerta de entrada del sistema afectivo.

Es la ÚNICA clase que el resto del proyecto necesita conocer para saber cómo
está el usuario. Por dentro orquesta la estrategia híbrida:

    mensaje
      ↓
    FastAffectiveRules            (local, microsegundos, siempre)
      ↓
    ¿confianza alta y sin contradicciones?  → resultado
      ↓ no
    SemanticInterpreter           (LLM, solo si hace falta y hay presupuesto)
      ↓
    merge(local, semántico)       → resultado
      ↓
    AffectiveContext.push(...)    (memoria corta: tendencia, duración)

Cuándo escala al modelo (y solo entonces):
  - la confianza local queda por debajo del umbral;
  - el sarcasmo cae en la zona dudosa (ni claro ni descartado);
  - hay emociones negadas pero ninguna afirmada (sabemos qué NO siente);
  - el mensaje es largo y contradictorio.

Cuándo NO escala nunca:
  - hay un límite explícito detectado (ya está todo lo importante dicho);
  - la lectura local es clara;
  - el mensaje es trivial o cortísimo;
  - se agotó el presupuesto o el modelo está caído.

Además cachea por texto normalizado: en una conversación real la gente repite
frases, y no tiene sentido pagar dos veces por la misma.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from . import negation
from .context import AffectiveContext
from .models import AffectiveState, Emotion
from .rules import FastAffectiveRules
from .semantic import SemanticInterpreter, merge

#: Por debajo de esta confianza, la lectura local se considera floja.
UMBRAL_CONFIANZA = 0.62
#: Zona en la que el sarcasmo es más dudoso que útil.
ZONA_SARCASMO = (0.25, 0.72)
#: Mensajes por debajo de esto no merecen una llamada de red.
MIN_CHARS_SEMANTICO = 12


@dataclass
class AffectiveInterpreter:
    """Lectura afectiva híbrida con memoria corta de contexto."""

    engine: object | None = None
    use_semantic: bool = True
    confidence_threshold: float = UMBRAL_CONFIANZA
    context_turns: int = 3          # cuántos intercambios recientes se pasan
    cache_size: int = 64

    rules: FastAffectiveRules = field(default_factory=FastAffectiveRules)
    semantic: SemanticInterpreter | None = field(default=None)
    context: AffectiveContext = field(default_factory=AffectiveContext)
    _cache: OrderedDict = field(default_factory=OrderedDict, repr=False)
    _semantic_calls: int = field(default=0, init=False)

    def __post_init__(self):
        if self.semantic is None:
            self.semantic = SemanticInterpreter(engine=self.engine)
        elif self.engine is not None and getattr(self.semantic, "engine", None) is None:
            self.semantic.engine = self.engine

    # ------------------------------------------------------------------ API
    def interpret(self, message: str, recent_context=None,
                  previous_affect: AffectiveState | None = None,
                  *, allow_semantic: bool | None = None,
                  remember: bool = True) -> AffectiveState:
        """Interpreta un mensaje y devuelve el estado afectivo estimado.

        Parámetros
        ----------
        message
            Lo que acaba de escribir o decir el usuario.
        recent_context
            Intercambios recientes. Acepta una cadena ya formateada o una lista
            de dicts tipo `{"role": "user", "content": "..."}` (el mismo formato
            que usa la memoria del proyecto). Se recorta a `context_turns`.
        previous_affect
            Estado anterior. Si no se pasa, se toma del contexto interno.
        allow_semantic
            Fuerza o prohíbe la consulta al modelo. Por defecto decide solo.
        remember
            Si False, no se guarda en el contexto (útil para tests y análisis
            de mensajes que no son del usuario).
        """
        texto = (message or "").strip()
        if not texto:
            vacio = AffectiveState(confidence=0.0, uncertainty=1.0)
            return vacio

        anterior = previous_affect if previous_affect is not None else self.context.current
        valencia_previa = anterior.valence if anterior is not None else None

        clave = negation.normalize(texto)
        en_cache = self._cache.get(clave)
        if en_cache is not None:
            self._cache.move_to_end(clave)
            if remember:
                self.context.push(en_cache)
            return en_cache

        # ---- 1) siempre reglas locales -------------------------------------
        estado = self.rules.analyze(texto, previous_valence=valencia_previa)

        # ---- 2) ¿hace falta una segunda opinión? ---------------------------
        permitido = self.use_semantic if allow_semantic is None else bool(allow_semantic)
        if permitido and self._needs_semantic(estado, texto):
            contexto_txt = self._format_context(recent_context)
            remoto = self.semantic.interpret(texto, contexto_txt) if self.semantic else None
            if remoto is not None:
                self._semantic_calls += 1
                estado = merge(estado, remoto)

        # ---- 3) el contexto reciente puede matizar la lectura --------------
        estado = self._apply_context(estado, anterior)

        self._guardar_cache(clave, estado)
        if remember:
            self.context.push(estado)
        return estado

    # ------------------------------------------------------------- decisiones
    def _needs_semantic(self, estado: AffectiveState, texto: str) -> bool:
        """¿Merece la pena preguntarle al modelo? Conservador a propósito."""
        if self.semantic is None or not self.semantic.available:
            return False
        if len(texto.strip()) < MIN_CHARS_SEMANTICO:
            return False
        # Con un límite explícito ya sabemos lo importante: no gastes red.
        if estado.explicit_boundary:
            return False

        if estado.confidence < self.confidence_threshold:
            return True
        if ZONA_SARCASMO[0] <= estado.sarcasm_probability < ZONA_SARCASMO[1]:
            return True
        # Sabemos qué NO siente pero no qué sí: justo el hueco que el modelo llena.
        if estado.negated_emotions and estado.primary_emotion == Emotion.NEUTRAL:
            return True
        # Mensaje largo leído como neutro: casi siempre se nos escapó algo.
        if estado.primary_emotion == Emotion.NEUTRAL and len(texto) > 140:
            return True
        return False

    def _apply_context(self, estado: AffectiveState,
                       anterior: AffectiveState | None) -> AffectiveState:
        """Matiza la lectura con lo que venía pasando.

        Dos ajustes, ambos conservadores:

        1. **Continuidad**: si el mensaje actual se leyó como neutro con poca
           confianza y el anterior era claramente negativo, es más probable que
           la persona siga en ello que que se le haya pasado de golpe. Se
           arrastra la emoción anterior, pero con confianza REBAJADA y dejando
           constancia en la evidencia — nunca se finge saber más de lo que se sabe.
        2. **Malestar sostenido**: si el contexto muestra varios turnos
           negativos seguidos, se sube un poco el malestar percibido para que la
           política de apoyo lo trate como algo que viene de largo.
        """
        if anterior is None:
            return estado

        arrastrado = estado
        if (estado.primary_emotion == Emotion.NEUTRAL
                and estado.confidence < 0.5
                and anterior.is_negative
                and anterior.confidence >= 0.55
                and not estado.explicit_boundary):
            arrastrado = estado.with_(
                primary_emotion=anterior.primary_emotion,
                valence=anterior.valence * 0.6,
                arousal=anterior.arousal * 0.8,
                confidence=min(estado.confidence + 0.10, 0.55),
                uncertainty=min(1.0, estado.uncertainty + 0.10),
                evidence=estado.evidence + ("continúa el estado anterior",),
            )

        if self.context.sustained and arrastrado.is_negative:
            arrastrado = arrastrado.with_(
                valence=max(-1.0, arrastrado.valence - 0.08),
                evidence=arrastrado.evidence + ("malestar sostenido varios turnos",),
            )
        return arrastrado

    # ------------------------------------------------------------- utilidades
    def _format_context(self, recent_context) -> str:
        """Compacta el contexto reciente a unas pocas líneas.

        Se limita a `context_turns` intercambios porque el objetivo es entender
        de qué se está hablando, no volver a mandar la conversación entera.
        """
        if not recent_context:
            return ""
        if isinstance(recent_context, str):
            return recent_context.strip()[:800]

        lineas: list[str] = []
        try:
            items = list(recent_context)[-(self.context_turns * 2):]
        except TypeError:
            return ""
        for item in items:
            if isinstance(item, dict):
                rol = str(item.get("role", "")).strip()
                cont = str(item.get("content", "")).strip()
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                rol, cont = str(item[0]), str(item[1])
            else:
                rol, cont = "", str(item)
            if not cont:
                continue
            quien = "Usuario" if rol == "user" else ("YUE" if rol == "assistant" else rol or "-")
            lineas.append(f"{quien}: {cont[:180]}")
        return "\n".join(lineas[-(self.context_turns * 2):])

    def _guardar_cache(self, clave: str, estado: AffectiveState) -> None:
        self._cache[clave] = estado
        self._cache.move_to_end(clave)
        while len(self._cache) > max(4, int(self.cache_size)):
            self._cache.popitem(last=False)

    # ------------------------------------------------------------ diagnóstico
    def stats(self) -> dict:
        """Métricas para diagnóstico: cuánto se está usando la red, de verdad."""
        return {
            "semantic_calls": self._semantic_calls,
            "semantic_available": bool(self.semantic and self.semantic.available),
            "semantic_budget_used": getattr(self.semantic, "calls_used", 0),
            "cache_size": len(self._cache),
            "context": self.context.summary(),
        }

    def reset(self) -> None:
        """Olvida el contexto y la caché (p. ej. al empezar una sesión nueva)."""
        self.context.clear()
        self._cache.clear()
