# Visión avanzada de YUE

Sistema modular de percepción por cámara: lectura de textos, descripción de la
habitación, seguimiento de manos y dedos, reconocimiento de acciones,
estimación afectiva y reconocimiento de objetos.

Todo es **aditivo**: nada de lo que ya funcionaba se ha borrado. El sistema
está **apagado por defecto** (`VISION_MP_ENABLED=false`).

---

## 1. Encender el sistema

En tu `.env`:

```ini
VISION_MP_ENABLED=true
VISION_PERCEPTION_ENABLED=true

# IMPORTANTE: solo un sistema puede abrir la webcam.
CAMERA_ENABLED=false        # apaga el observador clásico
VISION_V3_ENABLED=false     # y el sistema V3
```

> Si prefieres dejar el observador clásico encendido (lo necesita el **control
> por cabeza**), dale a cada sistema un índice distinto:
> `CAMERA_INDEX=0` para el clásico y otra webcam en el índice 1.

Arranca YUE y comprueba el estado:

```
/vision capacidades
/vision modelos
```

---

## 2. Arquitectura

```
                 ┌──────────────────┐
   webcam  ────► │  CameraManager   │  cerrojo global: UNA sola cv2.VideoCapture
                 └────────┬─────────┘
                          │
                 ┌────────▼─────────┐
                 │     FrameHub     │  solo el ÚLTIMO fotograma (nunca hay cola)
                 └────────┬─────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │  VisionScheduler: un hilo por     │
        │  módulo, cada uno a su FPS        │
        └─────────────────┬─────────────────┘
                          │
    ┌──────────┬──────────┼──────────┬──────────┐
    ▼          ▼          ▼          ▼          ▼
  rostro    manos      pose      objetos     escena
    │          │          │          │          │
    └──────────┴────┬─────┴──────────┴──────────┘
                    ▼
        ┌───────────────────────┐
        │  Trackers (IDs        │  PersonTracker, ObjectTracker, HandTracker
        │  estables + suavizado)│
        └───────────┬───────────┘
                    ▼
        ┌───────────────────────┐
        │  Analizadores         │  habitación, gestos, acciones, emociones,
        └───────────┬───────────┘  atención
                    ▼
        ┌───────────────────────┐
        │  EventManager         │  histéresis, duración mínima, enfriamiento
        └───────────┬───────────┘
                    ▼
        ┌───────────────────────┐
        │ VisionContextBuilder  │  4-6 frases en español para el LLM
        └───────────┬───────────┘
                    ▼
             avatar · voz · chat · memoria
```

**Todo pasa antes por `PrivacyManager`.** Si un módulo está apagado o el modo
privacidad está activo, ese trabajo ni siquiera se ejecuta.

### Archivos

```
vision/
├── camera_manager.py       cerrojo global + autobúsqueda + reconexión
├── frame_hub.py            último fotograma (ya existía)
├── perception_engine.py    orquestador
├── event_manager.py        antirrebote de eventos
├── context_builder.py      contexto breve para el chat
├── privacy_manager.py      interruptores de privacidad
├── capabilities.py         qué se puede hacer y por qué no
├── voice_intents.py        17 órdenes habladas/escritas
├── legacy_adapter.py       compatibilidad con CameraObserver
├── tracking/
│   ├── base_tracker.py         IoU + centroide, IDs estables
│   ├── person_tracker.py       personas, acercarse/alejarse
│   ├── object_tracker.py       objetos, umbral por clase, traducción
│   ├── hand_tracker.py         21 puntos, dedos, pinza, orientación
│   └── temporal_smoother.py    EMA, histéresis, voto por mayoría
├── detectors/
│   ├── face_detector.py        (ya existía)
│   ├── face_landmarker.py      (ya existía)
│   ├── pose_analyzer.py        (ya existía)
│   ├── gesture_recognizer.py   (ya existía)
│   ├── object_detector.py      (ya existía)
│   ├── hand_landmarker.py      NUEVO — 21 puntos por mano
│   ├── text_detector.py        NUEVO — ¿hay texto y dónde?
│   ├── scene_detector.py       NUEVO — luz, orden, tipo de lugar
│   └── action_detector.py      NUEVO — evidencias por fotograma
├── analyzers/
│   ├── room_analyzer.py        estado de la habitación + cambios
│   ├── gesture_analyzer.py     gestos con antirrebote
│   ├── action_analyzer.py      22 acciones por análisis temporal
│   ├── emotion_analyzer.py     estimación afectiva probabilística
│   └── attention_analyzer.py   ¿me está mirando?
├── ocr/
│   ├── ocr_engine.py           Paddle → EasyOCR → Tesseract → nulo
│   ├── text_stabilizer.py      acuerdo entre fotogramas
│   └── document_scanner.py     perspectiva, recorte, contraste, inclinación
└── models/
    └── model_registry.py       existencia, hash, carga única, sin descargas
```

---

## 3. Lectura de textos (OCR)

Instala **uno** de estos (todos locales y gratuitos):

```bash
pip install paddleocr      # mejor calidad en español
pip install easyocr        # buena alternativa
# Tesseract ya viene con YUE para el OCR de pantalla
```

Sin ninguno, el sistema sigue funcionando: `capabilities()["ocr"]` da `False` y
YUE te dice qué falta.

### Cadena de proceso

1. `TextDetector` busca regiones con aspecto de texto (gradiente morfológico).
2. `DocumentScanner` recorta, corrige la perspectiva, endereza y realza.
3. `OCREngineChain` lee con el mejor motor disponible.
4. `TextStabilizer` espera a que **3 fotogramas coincidan** antes de darlo por bueno.

### Cómo se usa

```
Tú:   Lee lo que estoy mostrando
YUE:  Leo esto con buena confianza: "Reunión viernes 7 de agosto"

Tú:   Lee solamente el título
Tú:   ¿Qué dice este documento?
Tú:   No leas la pantalla        ← desactiva el OCR
```

Por defecto `VISION_OCR_ONLY_ON_REQUEST=true`: **YUE no lee sola**. Solo detecta
que hay algo legible y espera a que se lo pidas.

Resultado:

```python
{"text": "Municipalidad Distrital de Nanchoc", "confidence": 0.91,
 "language": "es", "stable": True, "region": [x1, y1, x2, y2], "timestamp": 0.0}
```

---

## 4. Manos y dedos

`HandTracker` trabaja sobre los 21 puntos y deduce:

| Qué | Detalle |
|---|---|
| Lado | izquierda / derecha, con ID estable |
| Dedos | los cinco, uno a uno, extendido o flexionado |
| Palma | hacia la cámara / hacia afuera / de canto |
| Pinza | pulgar-índice, con distancia normalizada |
| Gestos | mano abierta, puño, señalar, pulgar arriba, pulgar abajo, victoria, OK, pinza, saludo, llamada |
| Conteo | 0-5 por mano, 0-10 con las dos |
| Movimiento | acercarse, alejarse, saludo, deslizamiento |
| Contexto | mano cerca del rostro, objeto sostenido |

Un gesto **sostenido genera un solo evento**, no uno por fotograma:

```python
{"event": "gesture_detected", "gesture": "thumbs_up",
 "hand": "right", "confidence": 0.93, "duration": 1.2}
```

---

## 5. Acciones

22 acciones, todas por **análisis temporal**. Ninguna se deduce de una sola
postura.

```
sentarse · levantarse · caminar · acercarse · alejarse · saludar · aplaudir
beber · comer · leer · escribir · usar el celular · hablar por teléfono
teclear · mostrar un objeto · señalar · cubrirse el rostro · bostezar
estirarse · cruzar los brazos · apoyar la cabeza en la mano · posible caída
```

Ejemplo real de cómo se decide **beber**:

```python
ActionRule("drinking",
    evidence=("hand_near_mouth", "bottle_detected"),   # ambas obligatorias
    any_of=("object_near_mouth",),
    min_ratio=0.5,          # en la mitad de la ventana temporal
    min_duration=1.0,       # sostenido al menos 1 segundo
    sequence=("upward_motion", "hold", "downward_motion"),
    cooldown=12.0)
```

Salida:

```python
{"action": "drinking", "confidence": 0.84, "person_id": 1,
 "start_time": 0.0, "duration": 2.8,
 "evidence": ["hand_near_mouth", "bottle_detected", "upward_motion"]}
```

**Caída:** la regla más estricta de todas — tronco horizontal + ausencia de
movimiento durante **3,5 segundos** con un 85 % de acuerdo. Y aun así se llama
`possible_fall` y se comunica como una pregunta preocupada, nunca como un
diagnóstico de accidente.

---

## 6. Emociones

> **No existe una lectura real ni infalible de emociones por cámara.**
> Esta frase está escrita en el propio código, en `analyzers/emotion_analyzer.py`.

Lo que hay es una **estimación probabilística** con confianza y certeza:

```python
{"affective_state": "tired", "confidence": 0.67, "certainty": "low",
 "signals": ["frequent_eye_closure", "head_supported_by_hand", "low_body_movement"],
 "safe_description": "Parece que podrías estar algo cansado."}
```

Por debajo del umbral:

```python
{"affective_state": "undetermined", "confidence": 0.0}
```

### Lo que YUE puede decir

- «Pareces algo cansado, aunque no puedo saberlo con certeza.»
- «Me da la impresión de que estás concentrado.»
- «Detecté señales compatibles con sorpresa.»

### Lo que NO puede decir nunca

«Estás deprimido» · «Estás mintiendo» · «Tienes ansiedad» · «Estás enamorado» ·
«Sé exactamente cómo te sientes» · cualquier diagnóstico médico o psicológico.

Hay una lista de patrones prohibidos (`FORBIDDEN_PATTERNS`) y una función
`is_safe_phrase()` que **bloquea** cualquier frase generada que los contenga.
Está cubierta por pruebas.

Para desactivarlo:

```
Tú:  No analices mis emociones
```

---

## 7. Órdenes de voz

Funcionan igual **habladas** y **escritas** en el chat:

| Frase | Efecto |
|---|---|
| «Activa / desactiva la cámara» | enciende o apaga y libera el dispositivo |
| «¿Qué ves?» | descripción prudente de lo que hay |
| «Describe mi habitación» | tipo de lugar, objetos, luz, orden |
| «Lee este texto» / «¿Qué dice este documento?» | OCR bajo petición |
| «Lee solamente el título» | primera línea |
| «¿Qué estoy sosteniendo?» | objeto en la mano |
| «¿Cuántos dedos muestro?» | conteo |
| «Sigue mi mano» | seguimiento durante 30 s |
| «¿Qué estoy haciendo?» | acción en curso |
| «No analices mis emociones» | apaga la estimación afectiva |
| «No leas la pantalla» | apaga el OCR |
| «No guardes observaciones» | deja de registrar eventos |
| «Olvida lo que viste» | borra el historial visual |
| «Modo privacidad» | apaga **todo** el análisis |
| «Solo analiza cuando te lo pida» | percepción dormida hasta petición |

### Comandos de barra

```
/camara on | off
/vision estado | capacidades | modelos | metricas
/vision leer | titulo | habitacion | accion | dedos | sostengo | animo
/vision privacidad | privado | publico | olvida
```

---

## 8. Privacidad

Los valores por defecto son **los más restrictivos**:

| Variable | Por defecto | Qué hace |
|---|---|---|
| `VISION_PROCESS_LOCAL` | `true` | todo se procesa en tu equipo |
| `VISION_SAVE_FRAMES` | `false` | **nunca** se guardan imágenes ni vídeo |
| `VISION_SAVE_EVENTS` | `false` | no se guardan observaciones |
| `VISION_ALLOW_CLOUD` | `false` | no se envía nada fuera |
| `VISION_ONLY_ON_REQUEST` | `false` | ponlo en `true` para máxima privacidad |
| `VISION_OCR_ONLY_ON_REQUEST` | `true` | YUE no lee sola |
| `VISION_EMOTION_USE_VOICE` | `false` | la voz solo se usa con permiso |

`PrivacyManager` es la **única puerta**: ningún módulo analiza nada sin
preguntar antes. El modo privacidad apaga todo con una sola llamada.

`.gitignore` ya excluye `.env`, `data/` y los logs.

---

## 9. Modelos

Van en `models/vision/`. **Nunca se descargan solos.**

| Archivo | Para qué |
|---|---|
| `face_detector.task` | detección rápida de rostros |
| `face_landmarker.task` | 478 puntos + blendshapes (emociones) |
| `pose_landmarker.task` | 33 puntos de postura (acciones) |
| `hand_landmarker.task` | 21 puntos por mano |
| `gesture_recognizer.task` | catálogo de gestos (respaldo de manos) |
| `object_detector.tflite` | objetos comunes |
| `image_classifier.tflite` | pista del tipo de escena (opcional) |

```
/vision modelos     ← te dice cuáles tienes y cuáles faltan, con la URL oficial
```

`ModelRegistry` comprueba existencia, tamaño mínimo y hash, y carga cada modelo
**una sola vez**. Si falta uno, **solo ese módulo** se desactiva.

---

## 10. Rendimiento

| Módulo | FPS por defecto |
|---|---|
| Rostro | 8-10 |
| Puntos faciales | 10-15 |
| Manos | 10-20 |
| Pose | 8-15 |
| Objetos | 3 |
| Escena | 0,3 (cada ~3 s) |
| Acciones | 6 |
| Vigilancia de texto | 0,5 |
| OCR | solo bajo petición |

Cada módulo corre en su **propio hilo** y toma siempre el último fotograma. Si
uno va lento, los demás no esperan y **nunca** se acumulan colas. Nada corre en
el hilo de Qt, así que ni la interfaz ni el audio se congelan.

```
/vision metricas    ← FPS reales y milisegundos por módulo
```

Perfiles: `VISION_PERFORMANCE_MODE=low | balanced | high`.

---

## 11. Migración desde CameraObserver

El sistema clásico **sigue intacto**. Para probar el nuevo sin borrar nada:

```ini
VISION_MP_ENABLED=true
VISION_REPLACE_LEGACY=true
```

`self.camera` pasa a ser `LegacyCameraObserverAdapter`, que expone exactamente
la misma interfaz (`.active`, `.start()`, `.stop()`, `.latest()`, `.describe()`,
`.context_for_ai()`, `.risk_signal()`, `.set_fast_mode()`) pero por dentro habla
con el motor nuevo.

> **Ojo:** el **control por cabeza** (`HeadCursorController`) necesita los
> landmarks crudos por fotograma, que el motor nuevo no entrega. Si lo usas,
> deja `VISION_REPLACE_LEGACY=false` y mantén el observador clásico.

Plan de retirada, cuando el nuevo esté validado:

1. `VISION_REPLACE_LEGACY=true` durante unos días de uso real.
2. Marcar `core/camera_observer.py` como obsoleto.
3. Migrar el control por cabeza al motor nuevo.
4. Borrar el módulo antiguo solo cuando nadie lo importe.

---

## 12. Pruebas

```bash
python -m unittest tests.test_vision_avanzada     # 56 pruebas, sin webcam
python tests/test_vision_avanzada.py -v
```

Cubren los 21 casos del pedido: cámara desconectada, cámara ocupada, fotograma
vacío, una y varias personas, ambas manos, conteo de dedos, gestos sostenidos,
OCR repetido, texto inestable, objeto que aparece y desaparece, sentarse y
levantarse, beber, emoción indeterminada, confianza insuficiente, módulo
faltante, modelo faltante, cierre limpio de hilos, privacidad activada, cámara
desactivada y ausencia de claves API.

Ninguna necesita webcam, MediaPipe ni modelos.

---

## 13. Si la cámara no entrega imagen

Síntoma típico en Windows:

```
Fotogramas recibidos: 1  ->  0.2 FPS reales
```

La cámara **abre** pero solo da un fotograma. Ejecuta:

```bash
python diag_vision_avanzada.py --camara
```

La sección 6 prueba **cada índice con cada backend** y te dice cuál funciona:

```
  [FALLA] índice 0 + MSMF: abre pero no entrega flujo sostenido
  [ OK ] índice 2 + DSHOW: 3/3 lecturas, 640x480, ~24 FPS

  RECOMENDADO: índice 2 con DSHOW
    CAMERA_INDEX=2
    VISION_CAMERA_BACKEND=dshow
```

Copia esas dos líneas al `.env`.

### Causas, en orden de frecuencia

1. **Otro programa retiene la cámara**: Teams, Zoom, OBS, Discord, el navegador
   o el propio YUE ya abierto. Ciérralos todos y reintenta.
2. **Backend equivocado.** `VISION_CAMERA_BACKEND=msmf|dshow|any`.
   MSMF es el nativo de Windows 10/11; DSHOW funciona mejor con webcams viejas.
3. **Índice equivocado.** Un portátil puede exponer varias entradas y solo una
   ser la webcam real.
4. **Permisos:** Configuración → Privacidad y seguridad → Cámara.

El aviso `VIDEOIO(DSHOW): backend is generally available but can't be used to
capture by index` es normal en los índices vacíos; solo importa si aparece en
el índice que quieres usar.

---

## 14. Correcciones incluidas

- **`self.speaker.is_speaking()`** en `main.py` (3 sitios). Es una `@property`
  en `core/voice.py:47`, así que la llamada con paréntesis lanzaba
  `TypeError: 'bool' object is not callable`. La de la línea 1805 estaba dentro
  de un `try/except` que se lo tragaba, o sea que esa barrera de empatía nunca
  llegó a funcionar; las otras dos reventaban de verdad.
- **`vision_mp` no se detenía en `shutdown()`**: sus hilos quedaban vivos y la
  webcam tomada hasta que moría el proceso.
- **Tres sistemas peleando por la webcam**: ahora hay un cerrojo global por
  índice y solo arranca uno de los dos caminos, nunca los dos.
- **Choque de nombres**: `VISION_ENABLED` ya significaba *visión de pantalla*
  en `config.py:107`. Por eso el interruptor maestro sigue siendo
  `VISION_MP_ENABLED`; usar el del documento habría roto el proveedor
  multimodal.
- **La cámara entregaba un solo fotograma en Windows.** Tres causas sumadas:
  la apertura se validaba con UNA lectura, no se probaba MSMF, y se aplicaba
  `CAP_PROP_BUFFERSIZE` sobre DSHOW (que no lo soporta). Ahora
  `vision/camera_backend.py` exige varias lecturas consecutivas, prueba los
  backends por orden y recuerda el que funciona.
- **Reconexión demasiado agresiva.** El contador de 5 fallos con esperas de
  0,2 s tumbaba la captura en un segundo ante cualquier microcorte, y entraba
  en un ciclo de reconexión de 3 s. Ahora la tolerancia es temporal (2,5 s) y,
  si la cámara muere sin dar apenas fotogramas, se prueba otro backend.
- **`scan()` daba falsos positivos**: bastaba un `read()` correcto para dar un
  índice por bueno. Ahora exige la misma validación de flujo sostenido.
