# YUE — Cerebro central: qué cambió y por qué

Refactorización **incremental y aditiva**. No se reescribió nada, no se borró
ninguna funcionalidad y no se renombró nada en masa.

**Tests: 506 pasando** (476 antes + 30 nuevos). El único fallo
(`test_screen_vision.py::test_extra_la_fila_visual...`) ya existía antes de tocar
nada y no tiene relación: es sobre los proveedores del `VisionRouter`.

---

## 1. Lo que el análisis encontró

Antes de programar recorrí los 244 archivos. Tu arquitectura estaba mejor de lo
que temías: **`CompanionExpressionPolicy` ya implementaba correctamente**
`USER EMOTION ≠ YUE EMOTION` (usuario triste → YUE `worried`, no `sad`). No la
dupliqué. Se reutiliza tal cual.

Los problemas reales eran cinco:

| # | Problema | Evidencia |
|---|---|---|
| 1 | `state_manager.tick()` **no se llamaba nunca** | Cero `QTimer` conectado. Los TTL solo caducaban de rebote |
| 2 | **3 fugas** al avatar (no once) | `vision/integration.py:153`, `core/vision/integration.py:60` y sus rutas sin-Qt |
| 3 | La cámara **decidía** la emoción de YUE | `main.py:2254`, con `_PRIO_MEDIA` |
| 4 | `SystemState` decorativo | `mic_state`/`camera_state`/`teacher_mode` casi nunca se escribían |
| 5 | Sin prosodia real | `core/voice.py` = TTS, `core/listener.py` = STT, `voice_flow/` = barge-in |

Un hallazgo extra: el modo profesora usaba **prioridad de conversación (70) con
TTL de 2.6 s**. Cualquier cosa —incluida la música— le quitaba la cara a los tres
segundos de empezar a explicar. Ahora usa `TEACHER` (80) sin TTL.

---

## 2. Archivos nuevos

| Archivo | Qué hace |
|---|---|
| `core/state/models.py` | `UserState`, `YueBehaviorState`, `SystemState`, `YueGlobalState`, `UserObservation`, `YueProposal`, `SOURCE_RELIABILITY`, `SOURCE_TTL` |
| `core/state/fusion.py` | Fusión de sensores con pesos y caducidad por fuente |
| `core/state/arbiter.py` | Propuestas por prioridad, TTL y **recálculo al expirar** |
| `core/state/mapping.py` | `CompanionResult` → `UserState` + `YueProposal` |
| `core/state/renderer.py` | **El único `pet.set_emotion()` del proyecto** |
| `core/voice_affect.py` | Hueco preparado. Devuelve `None` a propósito |
| `tests/test_state_global.py` | Los 8 escenarios pedidos + 16 extra |

## 3. Archivos modificados

- **`core/state/state_manager.py`** — Se añadió `observe()`, `propose()`,
  `update_user()`, `update_system()`, `global_state()`, `subscribe_global()`,
  `debug_snapshot()`. **Toda la API anterior sigue idéntica**: `set_emotion()`,
  `request_avatar()`, `update()`, `subscribe()`, `get_state()`. `request_emotion()`
  ahora crea propuestas por dentro, así que **todo `main.py` fluye al árbitro sin
  cambiar sus llamadas**. Los 13 tests originales pasan sin tocarlos.
- **`main.py`** — Renderer enganchado, `QTimer` del latido, `_state_tick()`,
  `_sync_system_state()`, `_publish_companion_state()`, `_propose_teacher_mode()`,
  `debug_state()`. La cámara pasó a reportar evidencia.
- **`vision/integration.py`** y **`core/vision/integration.py`** — Fugas cerradas.
- **`config.py`** — `STATE_TICK_MS` (250) y `STATE_LOG_DECISIONS` (true).

---

## 4. Las decisiones que importan

### La cámara es evidencia, no verdad

Tres capas, en `fusion.py`:

1. **Ponderación**: `confidence × SOURCE_RELIABILITY[source]`.
   Texto 1.00 · voz 0.80 · histórico 0.65 · cámara 0.55.
2. **Autoridad del texto explícito**: si la persona lo dijo con palabras,
   ninguna otra fuente cambia la emoción. Punto.
3. **Caducidad**: cara triste caduca a los 15 s; frase explícita, a los 90 s.

Verificado en ejecución real:

```
USER: sadness (dom=text, sec=happy)
YUE : worried  <- la cámara (conf 0.92) no movió nada
```

La lectura de la cámara **no se tira**: queda como `secondary_emotion`. Y como
las fuentes se contradicen, `uncertainty` no baja a cero. YUE puede tenerlo en
cuenta con suavidad sin llevarle la contraria a la persona.

### El TTL ya no cae a neutral

`tick()` cada 250 ms caduca propuestas y **recalcula**. Al terminar la clase no
se apaga: gana lo que siguiera vivo debajo.

```
teacher (80, ttl 0.25s) ─── expira ──→ conversation (70) recupera el mando
```

### Coherencia del estado ganador

Todos los campos vienen del **mismo** ganador. Ya no puede pasar
`cara=triste + voz=alegre + gesto=baile`.

### `initiative` — lo que faltaba

Derivado de `SupportNeed` (tu enum, sin duplicar):

| Necesidad | behavior | voice | initiative |
|---|---|---|---|
| `LISTEN` | listening | gentle | low |
| `COMFORT` | comforting | soft | low |
| `SOLVE` | solving | steady | high |
| `GIVE_SPACE` | waiting | quiet | **none** |
| `SAFETY` | grounding | steady | medium |

Si la persona pidió espacio, `initiative = "none"` y no se negocia con ninguna
tabla.

### `initiative` se USA, no solo se calcula

Era el riesgo real de este campo: quedarse decorativo como `mic_state`. Ahora
`_yue_may_take_initiative()` lo consulta en **tres** sitios:

- `_maybe_checkin()` — el check-in proactivo de ánimo
- `_autonomous_create()` — la iniciativa autónoma
- `can_speak()` de la visión — los comentarios sobre lo que ve la cámara

Verificado en ejecución:

```
usuario: "dejame en paz, no quiero hablar de esto ahora"
  necesidad detectada : GIVE_SPACE
  iniciativa de YUE   : none
  ¿puede hacer check-in? False
  ¿puede ser autónoma?   False
```

Antes, el temporizador de check-in habría preguntado «¿cómo estás del 1 al 5?»
a alguien que acababa de pedir que lo dejaran en paz.

### La voz no inventa datos

`voice_confidence = 0.0`. La tentación era derivarlo de la energía del micro, y
es justo lo que no se debe hacer: hablar fuerte no es estar enfadado. El
enganche está listo — cuando implementes el clasificador, solo hay que devolver
una `UserObservation` desde `analyze()`.

---

## 5. Excepciones conservadas (punto 38)

Quedan **tres** llamadas directas a `pet.set_emotion()` fuera del renderer, todas
de degradación:

| Archivo | Línea | Por qué se conserva |
|---|---|---|
| `main.py` | 997 | Sin gestor de estado no hay renderer, y sin renderer **nadie pintaría nunca** el avatar. Solo se alcanza si `core.state` no llegó a importarse |
| `vision/integration.py` | 140 | Igual |
| `core/vision/integration.py` | 80 | Igual |

Todas están documentadas en el código y **cubiertas por `test_6`**, que falla si
aparece una cuarta en cualquier módulo.

También conservé `bridge.gesture.connect(pet.play_gesture)`: un gesto puntual
(saludar) no compite por el estado emocional.

---

## 6. Cómo verificarlo en tu máquina

```bash
python -m pytest tests/test_state_global.py -v
python -m pytest tests/test_state_manager.py tests/test_companion_integration.py -q
```

Y en vivo, desde donde tengas acceso al `Controller`:

```python
app.debug_state()
```

Imprime USER / YUE / SYSTEM, la propuesta ganadora marcada con `►`, todas las
propuestas activas con su TTL restante, y las observaciones vivas con confianza
bruta **y** ponderada. Es la herramienta para cuando YUE ponga una cara rara y
quieras saber quién la pidió.

---

## 6.b Estado del sistema: de decorativo a real

`_sync_system_state()` en cada latido, **más** actualizaciones inmediatas donde
esperar 250 ms daba una respuesta falsa:

| Punto | Por qué es inmediato |
|---|---|
| `_toggle_mic` / `_toggle_voice` | El usuario acaba de pulsar el interruptor |
| `_on_media_state` | Al parar la música se **retira** su propuesta (no se deja caducar) |
| `_yue_say` | `voice="speaking"` antes de hablar: durante los primeros 250 ms de cada frase el estado decía que YUE estaba callada |

`activity` se resuelve por urgencia: `controlling` > `teaching` > `watching` >
`conversing` > `idle`.

## 7. Lo que dejé fuera a propósito

- **No creé eventos para todo.** Solo seis (`estado_usuario_cambiado`,
  `estado_yue_cambiado`, `estado_sistema_cambiado`, `propuesta_recibida`, más
  los dos que ya existían). Convertir cada línea en un evento habría sido la
  sobreingeniería que pedías evitar.
- **No toqué las prioridades existentes.** El apoyo al usuario sigue en `USER`
  (90), así que gana al modo profesora (80). Es tu diseño y tiene sentido: si
  alguien acaba de decir que está fatal, la clase puede esperar ocho segundos.
- **No implementé prosodia.** No hay datos reales que usar.
- **No mapeé `voice_style` a parámetros de TTS.** `Speaker.say()` solo acepta
  texto, y traducir "soft"/"steady" a rate/pitch de Edge-TTS sin poder probarlo
  en Windows sería inventar. El campo viaja en el estado y está listo para
  cuando quieras consumirlo en `core/tts/`.

## 7.b `tests/conftest.py` (nuevo)

Instala un doble de PyQt5 **solo si no está instalado**. En tu máquina no hace
nada y las pruebas corren contra el Qt real; fuera de ella permite probar
`Controller` sin arrancar la interfaz. Sin esto, `test_iniciativa_*` y
`test_sync_system_state_*` no podrían existir.

## 8. Riesgo conocido

El único punto que no pude probar aquí es el arranque real con PyQt5 (no está
instalado en este entorno; sí verifiqué que `main.py` importa entero con stubs y
que `_state_tick`/`_sync_system_state` funcionan con una app simulada). **Arranca
YUE una vez y comprueba que ves el latido**: si `debug_state()` muestra TTLs que
bajan, el `QTimer` está vivo.
