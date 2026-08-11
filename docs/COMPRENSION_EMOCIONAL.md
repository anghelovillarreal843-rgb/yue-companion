# Comprensión emocional v2 · YUE Companion

Documentación de la arquitectura que sustituye al modelo
`EMOCIÓN → RESPUESTA PREDEFINIDA`.

---

## 1. El problema

`core/emotion.py` clasificaba emociones buscando palabras en listas y sumando
1.0 por cada coincidencia. Funcionaba con «Estoy triste» y fallaba con todo lo
demás:

| Mensaje | Antes | Por qué fallaba |
|---|---|---|
| «Al final no vino. Da igual.» | `neutral` | ninguna palabra emocional |
| «Súper feliz de perder mi trabajo 🙄» | `happy` | leía las palabras, no el sentido |
| «No estoy triste» | `sad` | «triste» está en el texto |
| «No quiero consejos» | *(invisible)* | no existía el concepto de límite |

Además, la MISMA etiqueta servía para interpretar al usuario y para animar el
avatar, así que YUE **imitaba** en lugar de acompañar.

---

## 2. La arquitectura nueva

```
        MENSAJE DEL USUARIO
                │
                ▼
   ┌───────────────────────────┐
   │  core.affect              │   ¿qué siente?
   │  AffectiveInterpreter     │ → AffectiveState
   └───────────────────────────┘
                │
                ▼
   ┌───────────────────────────┐
   │  core.support.needs       │   ¿qué quiere de mí?
   │  NeedDetector             │ → SupportIntent
   └───────────────────────────┘
                │
                ▼
   ┌───────────────────────────┐
   │  core.safety_ext          │   ¿hay riesgo?
   │  assess()  (graduado)     │ → SafetyAssessment
   └───────────────────────────┘
                │
                ▼
   ┌───────────────────────────┐
   │  core.support.policy      │   ¿cómo la acompaño?
   │  SupportPolicy            │ → SupportDecision
   └───────────────────────────┘
                │
        ┌───────┴────────┐
        ▼                ▼
  prompting.py     expression.py
  bloque LLM       YueExpression
        │                │
        ▼                ▼
   respuesta        YueStateManager
   de YUE           (arbitraje por prioridad)
                         │
                         ▼
                   pet.set_emotion()
```

Todo lo orquesta **`core/companion_brain.py`**, la única pieza que `main.py`
necesita conocer.

---

## 3. `core/affect` · ¿qué siente el usuario?

| Archivo | Responsabilidad |
|---|---|
| `models.py` | `AffectiveState`, taxonomía `Emotion` (17), `Boundary`, mapa VAD |
| `negation.py` | alcance real de la negación |
| `sarcasm.py` | sarcasmo por contradicción |
| `lexicon.py` | léxico ponderado: declaración vs indicio |
| `rules.py` | `FastAffectiveRules` — motor local |
| `context.py` | `AffectiveContext` — tendencia, duración, estabilidad |
| `semantic.py` | `SemanticInterpreter` (LLM) + `merge()` |
| `interpreter.py` | `AffectiveInterpreter` — orquestador híbrido |

### 3.1 Negación con alcance

No se busca la palabra «no»: se calcula hasta dónde LLEGA la negación. El
alcance se corta en puntuación fuerte, en adversativas (`pero`, `sino`) y en
marcadores de contraste (`solo`, `simplemente`), que en español introducen lo
que sí es cierto.

```
«No estoy enojado, solo cansado»
  └─ negado ─┘  └─ afirmado ─┘
  → anger a negated_emotions · tiredness como emoción principal
```

Un patrón que ya lleva la negación dentro (`no vino`, `no me aceptaron`) es
**inmune** al alcance: ahí la negación es parte del hecho, no lo anula.

### 3.2 Sarcasmo por contradicción

La señal principal no son los emojis, es el choque **dentro de una misma frase**
entre vocabulario positivo y un hecho negativo:

```
«Qué maravilla, se borraron seis horas de trabajo»
 └─ positivo ─┘  └──── evento malo ────┘        → 0.60 sin un solo emoji
```

Se suman después pistas menores: concesión irónica («sí sí», «claro claro»),
superlativos impostados, puntuación exagerada, emojis y contradicción con el
estado anterior. Con probabilidad ≥ 0.5 la lectura se **invierte**: se descartan
las emociones positivas y se instala la de fondo (frustración o decepción).

### 3.3 Confianza honesta

Una **declaración** («estoy triste») pesa 1.0; un **indicio** («no vino») pesa
0.45–0.55. Eso hace que la confianza signifique algo, y por tanto que la política
sepa cuándo debe preguntar en vez de afirmar.

### 3.4 Estrategia híbrida

```
mensaje → FastAffectiveRules → ¿confianza alta? → resultado
                              ↓ no
                        SemanticInterpreter (LLM)
                              ↓
                        merge(local, semántico)
```

Escala al modelo solo si la confianza es baja, el sarcasmo cae en zona dudosa
(0.25–0.72), hay negación sin afirmación, o el mensaje es largo y se leyó neutro.
**Nunca escala** si hay un límite explícito (ya está todo dicho), si el mensaje
es cortísimo, o si se agotó el presupuesto.

En `merge()`, los **límites y las emociones negadas los manda siempre el análisis
local**: un LLM que se salte un «no quiero consejos» no puede borrarlo. Si ambos
discrepan en la emoción, la confianza se topa en 0.72 — el desacuerdo es
información.

---

## 4. `core/support` · ¿qué hago con eso?

### 4.1 `wants_advice` es ternario

```python
True   # pidió consejo        → se puede aconsejar
False  # dijo que NO lo quiere → prohibido
None   # no lo sabemos        → por defecto, NO se aconseja
```

Que alguien no diga «no me aconsejes» no significa que quiera consejo. Ese salto
lógico es lo que convierte a un acompañante en alguien insoportable.

### 4.2 Prioridades de `SupportPolicy`

1. **Seguridad** — con riesgo ≥ MODERADO, todo lo demás pasa a segundo plano.
   Ni siquiera un «déjame solo» concede espacio aquí.
2. **Límites explícitos** — `wants_space`, `no_advice`, `no_questions`…
3. **Peticiones explícitas** — «¿qué hago?» → ADVISE · «ayúdame» → SOLVE
4. **Celebración** — una buena noticia no se convierte en terapia.
5. **Sarcasmo alto** — se responde al malestar de debajo, nunca al tono.
6. **Incertidumbre alta** — ASK, y se marca `acknowledge_uncertainty`.
7. **Malestar sin petición** — COMFORT + LISTEN, jamás soluciones.
8. **Conversación normal** — YUE puede ser ella misma sin protocolo encima.

Regla transversal: si la confianza es baja, YUE **no** dice «sé exactamente cómo
te sientes». Y si ya preguntó el turno anterior y el malestar sigue, deja de
preguntar: dos preguntas seguidas son un interrogatorio.

### 4.3 YUE responde, no imita

```
usuario: anger, arousal 0.9   →  YUE: worried, intensidad 0.62
usuario: sadness profunda     →  YUE: worried, presente y calmada
usuario: pride por un logro   →  YUE: proud/excited  ← aquí SÍ se contagia
```

Las emociones **positivas se comparten** (alegrarse con alguien es acompañarlo);
las negativas se responden con presencia. La intensidad empática tiene techo
(0.75): desbordarse con quien sufre no ayuda.

---

## 5. Arbitraje del avatar

`YueStateManager` gana un método nuevo, `request_emotion()`, con la misma lógica
de prioridad que ya tenía `request_avatar()`:

```
EMERGENCY (100) > USER (90) > CONVERSATION (70) > MEDIA (40) > IDLE (10)
 seguridad        apoyo       reacción directa    música
```

En `main.py`, las **once** llamadas sueltas a `pet.set_emotion(...)` pasan ahora
por `_set_avatar_emotion(...)`, que arbitra antes de aplicar. Consecuencia
práctica: mientras el usuario cuenta algo doloroso, una canción alegre **ya no
puede** poner al avatar eufórico.

---

## 6. Uso

```python
from core.companion_brain import CompanionBrain

brain = CompanionBrain(engine=ai_engine)          # sin engine → 100% offline
r = brain.process(texto, recent_messages=historial)

r.affect.primary_emotion      # Emotion.DISAPPOINTMENT
r.affect.confidence           # 0.64
r.intent.wants_advice         # None
r.decision.mode               # SupportNeed.LISTEN
r.decision.offer_advice       # False
r.expression.as_tuple()       # ("worried", 0.48, 6500)
r.prompt_block                # bloque para el system prompt
r.safety.level                # SafetyLevel.NONE
```

## 7. Configuración

| Variable | Por defecto | Qué hace |
|---|---|---|
| `AFFECT_SEMANTIC_ENABLED` | `true` | permite la segunda opinión del LLM |
| `AFFECT_CONFIDENCE_THRESHOLD` | `0.62` | umbral para escalar al modelo |
| `AFFECT_SEMANTIC_MAX_CALLS` | `40` | techo de llamadas por sesión |
| `AFFECT_SEMANTIC_TIMEOUT` | `12` | segundos máximos |
| `AFFECT_CONTEXT_TURNS` | `3` | intercambios de contexto |
| `AFFECT_DEBUG` | `false` | imprime la lectura de cada mensaje |

Con `AFFECT_SEMANTIC_ENABLED=false`, YUE funciona **sin red, sin coste y sin
latencia**, algo menos fina pero igual de segura.

## 8. Pruebas

```bash
python -m pytest tests/test_affect.py                    # 38
python -m pytest tests/test_support.py                   # 30
python -m pytest tests/test_companion_integration.py     # 39
python tests/test_affect_eval.py                         # informe del banco
```
