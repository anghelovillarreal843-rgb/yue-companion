# Memoria histórica relevante

> `core/memory_ext.py` · `search_history()` / `context_block()`

Esta capa resuelve un problema concreto: YUE solo veía los últimos 12 mensajes,
así que **no podía acordarse de nada de hace semanas o meses**. Si el usuario
decía «Andrea volvió a escribirme», aquella pelea con Andrea de hace tres meses
ya no existía para ella.

---

## 1. Dónde encaja

```
                       MENSAJE DEL USUARIO
                               │
              ┌────────────────┼─────────────────┐
              │                │                 │
              ▼                ▼                 ▼
       MEMORIA RECIENTE   MEMORY_EXT       MEMORIA EPISÓDICA
       recent_messages()  search_history()  episodic_memory.py
       últimos 12 turnos  pasado relevante  eventos importantes
              │                │                 │
              └────────────────┼─────────────────┘
                               │
                               ▼
                       CONTEXTO PARA EL LLM
```

Las tres son **complementarias** y ninguna sustituye a otra:

| Capa | Qué guarda | Cuándo aparece |
|---|---|---|
| `recent_messages(12)` | Los últimos turnos, tal cual | Siempre |
| `episodic_memory.py` | Acontecimientos **estructurados** (entrevista, ruptura, examen) con emoción, fecha y seguimiento | Cuando hay un evento vivo o próximo |
| `memory_ext` | **Texto crudo** del historial completo | Solo si algo antiguo encaja con el mensaje de ahora |

Ejemplo de la diferencia: «mañana tengo una entrevista» crea un **episodio**
(con fecha y un «¿cómo te fue?» programado). «Andrea es mi compañera de
universidad» no es un evento, no crea episodio — pero seis meses después
`memory_ext` puede recuperarlo si vuelve a salir Andrea.

---

## 2. Una sola fuente del historial

`search_history()` lee **`messages`**, la tabla de `core/memory.py`. No copia
nada.

Esto era importante por dos motivos:

1. **No duplicar.** `memory_ext` ya tenía sus propias tablas (`conversations`,
   `conv_messages`) de la fase 3. Siguen ahí y su API sigue funcionando por
   compatibilidad, pero **el runtime de YUE no escribe en ellas**. Si se hubieran
   usado, cada mensaje viviría en dos sitios.
2. **Privacidad.** Si el usuario borra su historial, se borra de verdad: aquí no
   queda una copia olvidada en otra tabla.

---

## 3. Los dos problemas que había que evitar

### El mensaje actual no es un recuerdo

`main.py` guarda primero y construye el prompt después:

```python
self.memory.add_message("user", text)   # main.py, línea ~1839
...
system = self._system_prompt(...)       # main.py, línea ~1888
```

Sin protección, el resultado más parecido a «Andrea volvió a escribirme» sería
**exactamente ese mismo mensaje**, con similitud ≈ 1.0. La exclusión se hace
**por ID**, no por parecido:

```python
corte = id del mensaje nº MEMORY_RELEVANCE_SKIP_RECENT contando desde el final
candidatos = mensajes con id < corte
```

Como `SKIP_RECENT` (12) coincide con el `n` de `recent_messages(12)`, queda
fuera el mensaje actual **y** todo lo que ya viaja al modelo por la vía
inmediata. Hay además una red de seguridad: se descarta cualquier candidato cuyo
texto sea idéntico a la consulta.

### Las respuestas de YUE no son hechos

Por defecto solo se buscan mensajes con `role="user"`. Si hace meses YUE dijo
«quizás Andrea vive en Lima», eso **no puede** reaparecer como
`RECUERDO: Andrea vive en Lima`. Con `MEMORY_RELEVANCE_ROLE=any` sí entran, pero
el bloque del prompt los marca explícitamente como *«TÚ (YUE) dijiste, NO es un
dato suyo»*.

---

## 4. Cómo se puntúa

El motor por defecto (`TfidfRetriever`) es TF-IDF **enriquecido**, todo con
librería estándar y sin internet:

- **Palabra literal** (peso 1.00)
- **Raíz aproximada** (0.85) — un stemmer ligero del español: «trabajar»,
  «trabajo» y «trabajamos» comparten `trabaj`.
- **Concepto** (0.70) — un diccionario de ~27 temas cotidianos (mascota, muerte,
  estudio, pareja, salud…). Es lo que permite que *«extraño a mi mascota»*
  encuentre *«mi perro murió»* sin compartir ni una palabra.
- **N-gramas de letras** (0.10) — red de seguridad para variantes y erratas.

La similitud mezcla dos medidas, porque cada una sola se equivoca:

- **Coseno**: parecido simétrico. Castiga a los mensajes largos y se diluye
  cuando la consulta trae relleno.
- **Cobertura**: qué proporción del peso de la consulta cubre el documento.
  Responde a «¿este recuerdo trata de lo que acaba de decir?».

Luego, y **solo para desempatar**, se aplican recencia e importancia:

```python
final = similitud * (1 + 0.15 * recencia + 0.10 * importancia)
```

Los pesos son pequeños a propósito: «Andrea me engañó» (hace 3 meses) debe ganar
a «hoy almorcé pollo» (ayer) cuando se pregunta por Andrea.

### Umbral y diversidad

Si **nada** supera `MEMORY_RELEVANCE_MIN_SCORE`, no se manda nada al prompt. Es
preferible no recordar a recordar cualquier cosa.

Y se deduplica: en vez de tres versiones del mismo momento («Andrea me llamó»,
«Andrea me llamó ayer», «te dije que Andrea me llamó») se intenta devolver
piezas complementarias de la historia.

---

## 5. Cambiar el algoritmo sin tocar `main.py`

`search_history()` es la interfaz pública y no cambia aunque cambie el motor:

```python
from core.memory_ext import BaseRetriever, register_retriever

class MiMotor(BaseRetriever):
    nombre = "mio"
    def puntuar(self, consulta, documentos) -> list[float]:
        ...   # 0..1 por documento, mismo orden

register_retriever("mio", MiMotor)
# y en .env:  MEMORY_RELEVANCE_BACKEND=mio
```

Hay un `EmbeddingRetriever` incluido como demostración. **No se activa solo y no
descarga nada**: solo entra si se pide expresamente *y* `sentence-transformers`
ya está instalado; si no, cae a TF-IDF en silencio. YUE nunca depende de
internet por esto.

---

## 6. Configuración

Todo en `config.py` / `.env`. Los que más se tocan:

| Opción | Por defecto | Para qué |
|---|---|---|
| `MEMORY_RELEVANCE_ENABLED` | `true` | Con `false`, YUE se comporta como antes |
| `MEMORY_RELEVANCE_TOP_K` | `3` | Cuántos recuerdos como mucho |
| `MEMORY_RELEVANCE_MIN_SCORE` | `0.20` | **Súbelo si saca temas viejos sin venir a cuento** |
| `MEMORY_RELEVANCE_SKIP_RECENT` | `12` | Debe coincidir con `recent_messages(n)` |
| `MEMORY_RELEVANCE_ROLE` | `user` | `any` para incluir a YUE (no recomendado) |
| `MEMORY_RELEVANCE_MAX_CHARS` | `320` | Recorte por frase o palabra, nunca a media palabra |
| `MEMORY_RELEVANCE_DEBUG` | `false` | Log `[memory-ext]` en consola |

---

## 7. Diagnóstico

```
/recuerdos Andrea volvió a escribirme
```

Muestra en el chat exactamente qué recuerdos entrarían al prompt y con qué
puntuación. Sin argumento usa el último mensaje. Si dice *«sin recuerdos por
encima del umbral»*, eso es el sistema funcionando bien, no un fallo.

Con `MEMORY_RELEVANCE_DEBUG=true`, la consola muestra:

```
[memory-ext] query: Andrea volvió a escribirme
[memory-ext] candidatos: 850
[memory-ext] resultado 1 score=0.319 sim=0.312 id=1
[memory-ext] resultado 2 score=0.313 sim=0.293 id=3
```

Estos logs **nunca** llegan al usuario.

---

## 8. Qué NO hace

- No interviene en `episodic_memory.py`. El «HUECO FASE 2» sigue abierto **a
  propósito**: son memorias con trabajos distintos, y atar la creación de
  episodios a los umbrales de esta búsqueda haría que un cambio aquí moviera de
  rebote la memoria episódica.
- No convierte cada conversación en memoria permanente: no guarda nada, solo
  lee.
- No mete todos los recuerdos encontrados al prompt: `top_k` y umbral mandan.
- No puede impedir que YUE hable. Todo es *best-effort*: si esta capa revienta,
  el prompt se genera igual con las demás memorias.

---

## 9. Pruebas

```
python -m pytest tests/test_memory_relevance.py
python tests/test_memory_relevance.py
```

25 pruebas, incluidos los seis casos del encargo (recuperación por nombre, no
devolver el mensaje actual, irrelevancia, mensajes del asistente, continuidad de
`recent_messages`, y fallo de `memory_ext`) más tres de integración que llaman
al `_system_prompt` **real** de `main.py`.
