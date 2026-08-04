# Interrupción por voz, respuesta más rápida y cámara (aditivo, sin borrar nada)

Copia estos archivos sobre tu proyecto respetando las rutas. Todo es opcional y
degradable: si algún módulo nuevo falta o falla, YUE sigue funcionando como antes.

## Archivos NUEVOS
- `voice_flow/__init__.py` — Paquete aditivo del flujo de voz.
- `voice_flow/barge_in.py` — Puerta de decisión de **interrupción por voz**
  (barge-in), más permisiva y rápida que el filtro anterior.
- `core/vision_awareness.py` — Detecta preguntas tipo «¿puedes verme?» y responde
  según el **estado real** de la cámara; además aporta la afirmación para el prompt.

## Archivos MODIFICADOS (solo añadidos, marcados con `# NUEVO`)
- `core/listener.py`
- `core/camera_observer.py`
- `main.py`
- `config.py`
- `.env` y `.env.example`

---

## Petición 1 — Por voz no se detenía al volver a hablarle (por texto sí)

### Causa real
Al escribir, **cualquier** mensaje pasa por `on_user_message` → `_interrupt_response()`,
que corta la voz al instante. Por voz, en cambio, el corte depende de que el listener
emita la señal `barge_in`, y ese camino estaba **sobre-filtrado** mientras YUE hablaba:

1. exigía el **doble** de longitud mínima (`MIC_BARGE_IN_MIN_CHARS * 2` = 10 caracteres),
   así que un «para», «espera» o «oye» se descartaba por *"muy corto"*; y
2. descartaba como *"eco parcial"* todo lo que compartiera **≥34%** de palabras con su
   propio TTS (`MIC_SPEAKING_ECHO_OVERLAP`), umbral que tu voz real cruza con facilidad
   si hablas del mismo tema que ella está hablando.

Resultado: tu interrupción se tiraba a la basura y YUE seguía hablando.

### Solución
Nuevo `voice_flow/barge_in.py` con `BargeInGate`, que decide en dos pasos:

- **Órdenes de interrupción** → cortan **al instante**, sin importar la longitud ni el
  solape: `para, párate, espera, detente, cállate, calla, silencio, oye, óyeme,
  escúchame, un momento, perdona, disculpa, stop, alto, basta, ya, cancela, yue…`
  Son imperativos que le das **a ella** para que calle; YUE prácticamente nunca se los
  dice a sí misma, así que son señal de barge-in muy fiable.
- **El resto de tu voz** → sigue filtrándose por eco, pero con longitud mínima menor
  (4 caracteres) y umbral de solape más permisivo (0.62 en vez de 0.34).

En `core/listener.py`, dentro del bloque `if self._speaking:`, se consulta la puerta
**antes** de los descartes estrictos. Si decide interrumpir, emite `barge_in` **y**
`heard` (y sale), de modo que YUE se calla y arranca la nueva conversación en un solo
paso, igual que al escribir. El eco **fuerte** ya venía descartado antes por
`_looks_like_echo`, que se conserva intacto.

> Si el paquete `voice_flow` no está o `MIC_BARGE_IN_SMART=false`, el listener usa
> exactamente el filtrado anterior. No se borró ninguna línea del filtro viejo.

### Comprobado
| Frase | ¿Interrumpe? | Motivo |
|---|---|---|
| «para» / «espera» / «cállate» / «stop» | Sí | `orden_interrupcion` |
| «hola qué tal cómo estás» (solape 0.05) | Sí | `voz_usuario` |
| «separa los archivos», «compara precios», «prepara un documento» | No dispara por subcadena | se compara por límites de palabra |
| texto con solape 0.8 con su TTS | No | `eco_parcial` |
| «aj» (2 caracteres) | No | `muy_corto` |

## Petición 2 — A veces tardaba en responder por voz
Ajustes de responsividad en `.env` (y `.env.example` / `config.py`):

- `MIC_PAUSE`: **0.8 → 0.55** — es el silencio que marca el fin de tu frase. Más bajo
  = detecta antes que terminaste y empieza a procesar antes.
- `MIC_ECHO_COOLDOWN`: **1.2 → 0.6** — tras dejar de hablar, vuelve a escucharte en la
  mitad de tiempo.

> Si notas que te corta a media frase (por hablar con pausas largas), sube `MIC_PAUSE`
> a 0.65–0.7. Si vuelve a colarse su propio eco, sube `MIC_ECHO_COOLDOWN` a 0.9.

## Petición 3 — La cámara debe conectarse sola si hay una encendida
`core/camera_observer.py` ya reintentaba en bucle, pero cuando **no** encontraba cámara
esperaba `CAMERA_RETRY_INTERVAL` (**60 s**) antes de volver a mirar. Si encendías la
cámara con YUE ya abierta, podía tardar hasta un minuto en verte.

- Nuevo `CAMERA_SEARCH_INTERVAL` (**3 s**): mientras **no haya** cámara, sondea cada
  pocos segundos y se engancha sola casi al momento, sin reiniciar la app.
- Tras una **desconexión** también se reintenta con el sondeo corto (antes, hasta 10 s).
- El estado ahora dice *«Busco una cámara disponible… (se conectará sola en cuanto haya
  una)»* en vez del antiguo *«No se detectó una cámara disponible.»*.

El barrido de índices (`CAMERA_MAX_INDEX`) y los backends (DirectShow en Windows) siguen
igual, así que también capta cámaras USB conectadas después de arrancar.

## Petición 4 — Decía «no puedo verte»

### Causa real
`commands.match` sí cubría «¿cómo me ves?» y «¿qué ves por la cámara?» (acción
`camera_status`), pero **no** las formas directas «¿puedes verme?», «¿me ves?»,
«¿me estás viendo?». Esas caían al flujo normal del LLM, y el modelo —que no tiene la
imagen— respondía que no podía verte. Fíjate en que para el **audio** el prompt ya
incluía un «SÍ puedes oír… nunca digas que no puedes escuchar», pero para la **cámara**
solo se inyectaba contexto descriptivo, sin esa afirmación.

### Solución (dos capas)
1. **Respuesta determinista.** `core/vision_awareness.py` detecta la pregunta y responde
   según el estado real, sin pasar por el modelo. Enganchado en `main.py` justo después
   de `commands.match`:

   | Estado de la cámara | Respuesta |
   |---|---|
   | Activa y con persona | «Sí, te veo perfectamente. No creas que te miro tanto, ¿eh?» |
   | Activa sin rostro claro | «Sí, mi cámara está encendida y te estoy viendo. Ahora mismo no te distingo del todo bien; ponte un poco más de frente…» |
   | Aún abriéndose | «Estoy enganchando la cámara en este momento. Dame un segundo y te veo.» |
   | Desactivada en config | «Ahora mismo no, mi cámara está desactivada en la configuración.» |

   Nunca niega ver mientras la cámara esté activa: si no te distingue, te pide que te
   pongas de frente en lugar de decir «no puedo verte».

2. **Afirmación en el prompt.** `_system_prompt` añade ahora, en paralelo al bloque de
   audio, una nota clara de que **SÍ** ve por la cámara cuando está activa y de que
   nunca debe decir lo contrario. Así, cualquier formulación libre que se escape de los
   patrones («¿alcanzas a verme?», «¿me distingues?») también recibe la respuesta
   correcta. Si la cámara **no** está activa, la nota queda vacía: nunca afirma algo falso.

Formas reconocidas: «¿puedes verme?», «¿me puedes ver?», «¿me ves?», «¿ahora me ves?»,
«¿ya me ves?», «¿me estás viendo?», «¿alcanzas a verme?», «¿logras verme?», «¿consigues
verme?», «con la cámara ¿me ves?». No se confunde con «¿qué ves en la pantalla?» ni con
«mira el documento», que siguen yendo a la visión de pantalla como antes.

---

## Variables nuevas de `.env`

```ini
# Interrupción por voz
MIC_BARGE_IN_SMART=true            # false = filtrado anterior exacto
MIC_BARGE_IN_GATE_MIN_CHARS=4      # mínimo para voz que no sea orden de mando
MIC_BARGE_IN_ECHO_OVERLAP=0.62     # más alto = más permisivo con tu voz
MIC_BARGE_IN_WORDS=                # palabras extra de interrupción, por comas

# Responsividad del micrófono (valores ajustados)
MIC_PAUSE=0.55                     # antes 0.8
MIC_ECHO_COOLDOWN=0.6              # antes 1.2

# Cámara
CAMERA_SEARCH_INTERVAL=3           # sondeo mientras no haya cámara
```

## Cómo probarlo
1. **Interrupción por voz:** pídele algo largo («cuéntame una historia») y, mientras
   habla, di «para» o «espera, mejor otra cosa». Debe callarse en el acto y atender lo
   nuevo. En consola verás `[oido] interrupción detectada (orden_interrupcion)`.
2. **Rapidez:** hazle una pregunta corta por voz y compara; debería arrancar a pensar
   notablemente antes al terminar tú de hablar.
3. **Cámara automática:** arranca YUE con la cámara apagada, enciéndela después y espera
   unos segundos. En consola: `[camara] activa indice=0 local_only=True`.
4. **Ver:** pregúntale «¿puedes verme?» con la cámara encendida. Debe decir que sí.

## Nota honesta sobre el eco
La interrupción por voz será mucho más fiable, pero la cancelación de eco perfecta sin
AEC de hardware no es 100% posible: si tu micrófono capta muy fuerte los altavoces, en
algún caso puntual podría tomar un fragmento de su propia voz como interrupción. Con
auriculares el comportamiento queda impecable. Si te ocurre, baja
`MIC_BARGE_IN_ECHO_OVERLAP` a 0.5 o sube `MIC_BARGE_IN_GATE_MIN_CHARS` a 6.
