# Memoria episódica emocional

Capa **aditiva**. No sustituye ni modifica destructivamente nada de lo que ya
existía: `facts`, `goals`, `mood_log`, `affect_log`, `risk_events`,
`memoria_larga` y la consolidación siguen funcionando exactamente igual.

## Qué añade

| Sistema | Pregunta que responde |
|---|---|
| `affect_log` | ¿Qué **siente** ahora mismo? |
| `mood_log` | ¿Cómo ha **estado** estos días? |
| `facts` / `goals` | ¿Qué **sé** de él? |
| **`emotional_episodes`** | ¿Qué está **viviendo**, cuándo, por qué le importa, qué hicimos y cómo terminó? |

Los dos primeros **conviven** con el nuevo: un mismo mensaje escribe en
`affect_log` (la lectura) y, si procede, en `emotional_episodes` (el hecho).

## Archivos

| Archivo | Cambio |
|---|---|
| `core/episodic_memory.py` | **Nuevo.** Toda la lógica. |
| `core/memory.py` | Tabla + 4 índices + migración + ~16 métodos CRUD + `last_message_id()`. Aditivo. |
| `config.py` | Bloque `EPISODIC_*`. Aditivo. |
| `main.py` | 4 enganches + 4 métodos nuevos. |
| `tests/test_episodic_memory.py` | **Nuevo.** 49 pruebas. |

## Flujo

```
on_user_message()
  ├─ brain.process()            → afecto + necesidad + seguridad   (YA EXISTÍA)
  ├─ memory.add_affect()                                           (YA EXISTÍA)
  ├─ memory.add_message("user") 
  └─ _episodic_observe()        → detectar / actualizar / resolver  ← NUEVO
                                   (hilo aparte: no congela la UI)

_system_prompt()
  └─ episodic.context_block()   → máx. 3 recuerdos relevantes       ← NUEVO

_on_ai_done()
  └─ _episodic_note_response()  → rellena yue_action                ← NUEVO

_maybe_checkin()
  ├─ _episodic_followup()       → ¿algo concreto que retomar?       ← NUEVO
  └─ autonomy.checkin_question()→ «¿cómo va tu día?» (respaldo)     (YA EXISTÍA)
```

La observación va **después** de `add_message` a propósito: en ese punto ya
quedaron fuera los comandos, los modos y las órdenes de PC, y el mensaje ya
tiene `id`.

## Cómo se evita el coste

1. **Filtro local** (regex): descarta casi todo sin red ni coste.
2. **Extractor local**: si no hay motor o falla, sigue habiendo memoria. YUE
   offline recuerda igual.
3. **Extractor LLM**: solo si hay candidato, con presupuesto (`EPISODIC_MAX_LLM_CALLS`)
   y castigo tras 3 fallos seguidos.
4. **Importancia local**: el umbral (`0.55`) lo decide el código, no el modelo.

La emoción, intensidad, disparador y confianza **se reutilizan** de
`core.affect`. Aquí no se vuelve a analizar el estado emocional.

## Decisiones que conviene conocer

- **`status` ≠ `follow_up_state`.** Un episodio puede estar `unresolved` con la
  pregunta ya `asked`: YUE preguntó una vez y no insiste.
- **`event_at` ≠ `follow_up_at`.** Con hora exacta, +2 h. Solo con día, a las
  19:00 (y nunca antes de que el evento termine).
- **Seguridad primero.** Con riesgo ≥ MODERADO no se crea episodio ni se
  programa seguimiento. Un evento de riesgo en los últimos 2 días suspende
  todos los seguimientos episódicos.
- **Los límites mandan.** «Déjame solo» abre una ventana de silencio de 3 h
  durante la cual ningún episodio pendiente puede hablar.
- **`yue_action` es una etiqueta corta**, no la respuesta de YUE (esa ya está
  en `messages`).
- **Se guarda texto corto** (etiqueta, causa, desenlace) porque sin él no hay
  recuerdo. Nunca la conversación entera.

## Fase 2 · memory_ext

`core/memory_ext.py` **no está integrado en el runtime** (solo en su test). Su
`search()` es TF-IDF sobre una tabla de conversaciones que nadie escribe, así
que hoy no aportaría nada.

Punto de integración marcado en `find_related_open_episode()`: desempatar por
similitud semántica cuando el parecido textual cae en la franja dudosa
(0.30–0.45). Requiere antes que el runtime escriba en las tablas de
`memory_ext`, lo cual es un cambio independiente.

## Configuración principal

```
EPISODIC_MEMORY_ENABLED=true     # apagarlo devuelve YUE al comportamiento previo
EPISODIC_MIN_IMPORTANCE=0.55     # subir = más selectiva
EPISODIC_MAX_CONTEXT=3           # recuerdos por prompt
EPISODIC_FOLLOWUP_MAX=1          # preguntas por episodio
EPISODIC_RETENTION_DAYS=90
EPISODIC_DEBUG=false             # logs [EPISODE]
```

## Pruebas

```
python -m pytest tests/test_episodic_memory.py -v     # 49 pruebas
```

Cubren los 12 casos del diseño más la separación con `affect_log`, la migración
de bases antiguas y la robustez ante motor caído y memoria rota. Corren
**offline**, sin clave de IA.
