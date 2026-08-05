# Sistema de visión por cámara de YUE (MediaPipe Tasks)

Sistema modular de visión artificial con **una sola cámara**, construido sobre
**MediaPipe Tasks**. Es **aditivo**, **opt-in** y **degradable**: apagado por
defecto, no toca nada del YUE existente, y si falta la cámara, un modelo o la
propia librería MediaPipe, simplemente se desactiva la parte afectada sin romper
el resto.

> No confundir con `core/vision/` (percepción V3 basada en DeepFace) ni con la
> "visión de pantalla" (`VISION_ENABLED`). Este sistema vive en el paquete
> `vision/` y se enciende con `VISION_MP_ENABLED`.

---

## 1. Arquitectura

```
                 ┌────────────────────────────────────────────────────────┐
   Webcam ──────►│ CameraService  (UNA sola cv2.VideoCapture, hilo propio) │
                 └───────────────┬────────────────────────────────────────┘
                                 │ publica el último fotograma
                          ┌──────▼──────┐
                          │  FrameHub   │  (solo el frame más reciente, BGR/RGB)
                          └──────┬──────┘
        cada módulo lee a SU frecuencia el último frame disponible
   ┌───────────────┬───────────────┬───────────────┬───────────────┬─────────────┐
   ▼               ▼               ▼               ▼               ▼             ▼
FaceDetector  FaceLandmarker  PoseAnalyzer  GestureRecognizer  ObjectDetector  (Classifier /
(presencia)   (+blendshapes)  (postura)     (+gestos dinám.)   (objetos)        Segmenters:
   │               │               │               │               │            bajo demanda)
   └───────────────┴───────────────┴───────┬───────┴───────────────┘
                                           ▼
                                 ┌───────────────────┐
                                 │ VisionStateFusion │  (combina + TTL + eventos)
                                 └─────────┬─────────┘
                                           ▼
                                 ┌───────────────────┐
                                 │   VisionState     │  (fuente única de verdad)
                                 └───┬───────────┬───┘
                                     ▼           ▼
                         build_visual_context   AvatarBridge
                         (contexto al LLM)       (gestos del avatar, con cooldowns)
```

Un **planificador** (`VisionScheduler`) da a cada detector su propio hilo a su
propia frecuencia. Nunca se acumulan colas: cada worker toma **el último**
fotograma (cola conceptual de tamaño 1). Un fallo de un módulo se aísla y no
afecta a los demás. Nada corre en el hilo de la interfaz.

---

## 2. Módulos y modelos

| Módulo | Archivo (`models/vision/`) | Frecuencia típica | Rol |
|---|---|---|---|
| Face Detector | `face_detector.task` | 5–10 FPS | Presencia/conteo/posición rápidos |
| Face Landmarker | `face_landmarker.task` | 6–15 FPS | Malla + **blendshapes** → expresión/emoción |
| Pose Landmarker | `pose_landmarker.task` | 5–10 FPS | Postura, brazos, movimiento |
| Gesture Recognizer | `gesture_recognizer.task` | 10–20 FPS | Gestos estáticos + dinámicos (Wave/Swipe) |
| Object Detector | `object_detector.tflite` | 1–3 FPS | Objetos comunes del entorno |
| Image Classifier | `image_classifier.tflite` | bajo demanda | Escena/imagen |
| Image Segmenter | `image_segmenter.tflite` | bajo demanda | Fondo: blur/quitar/reemplazar |
| Interactive Segmenter | `interactive_segmenter.tflite` | bajo demanda | Recorte por punto |

Los modelos **no se descargan solos**. Ver `models/vision/README.md`.

---

## 3. Instalación

```bash
pip install -r requirements.txt   # ya incluye mediapipe, opencv-python, numpy
```

Coloca los `.task`/`.tflite` en `models/vision/` (ver su README).

---

## 4. Configuración (.env)

Todo es opcional; hay valores por defecto seguros. Claves principales:

```dotenv
VISION_MP_ENABLED=false          # interruptor MAESTRO (opt-in)
VISION_PERFORMANCE_MODE=balanced # low | balanced | high
CAMERA_INDEX=0                   # (compartida con el resto de YUE)
CAMERA_TARGET_FPS=0              # 0 = usar el del perfil

VISION_FACE_DETECTOR_ENABLED=true
VISION_FACE_LANDMARKER_ENABLED=true
VISION_POSE_ENABLED=true
VISION_GESTURE_ENABLED=true
VISION_OBJECTS_ENABLED=true
VISION_IMAGE_CLASSIFIER_ENABLED=false
VISION_HOLISTIC_ENABLED=false    # si =true, apaga landmarks/pose/gestos redundantes

VISION_MAX_FACES=3
VISION_MAX_HANDS=2
VISION_SAVE_FRAMES=false         # privacidad: por diseño NO se guardan imágenes
VISION_EXTERNAL_UPLOAD=false     # privacidad: NUNCA se suben imágenes
```

> Si enciendes `VISION_MP_ENABLED=true`, pon `CAMERA_ENABLED=false` (observador
> clásico) o usa una `CAMERA_INDEX` distinta para no pelear por la misma webcam.

Ver la lista completa en `.env.example`.

---

## 5. Comandos en el chat

| Comando | Acción |
|---|---|
| `/camara` | Describe lo que ve la cámara |
| `/camara on` / `/camara off` | Enciende / apaga el sistema |
| `/vision` o `/vision estado` | Estado de módulos, FPS y privacidad |
| `/vision objetos` | Objetos detectados |
| `/vision gestos` | Último gesto detectado |
| `/vision escena` | Clasifica la escena (bajo demanda) |
| `/vision metricas` | FPS reales y tiempos de inferencia por módulo |
| `/vision privacidad` | Explica la política de privacidad |

---

## 6. Uso desde código

```python
from vision import integration as vision_mp
self.vision_mp = vision_mp.attach(self)     # None si está apagado

ctx = self.vision_mp.context_for_ai()       # texto breve para el prompt del LLM
snap = self.vision_mp.snapshot()            # dict con todo el estado visual
print(self.vision_mp.describe())            # resumen en español
```

`snapshot()` devuelve: `camera`, `presence`, `faces`, `pose`, `hands`,
`objects`, `scene`, `events`.

---

## 7. Privacidad

- Procesamiento **100% local**; no hay ninguna ruta de guardado de fotogramas.
- No se sube ninguna imagen a servicios externos.
- La cámara se libera correctamente al cerrar YUE.
- El sistema arranca **apagado**; el usuario decide activarlo.

---

## 8. Extender el sistema

**Agregar un modelo nuevo:** añade su nombre a `MODEL_FILES` en
`vision/model_manager.py`, crea un detector que herede de
`vision/detectors/base.py::BaseDetector` y regístralo en
`VisionSystem._build_detectors` / `_register_jobs`.

**Agregar un gesto dinámico:** amplía `GestureRecognizerModule._dynamic()` en
`vision/detectors/gesture_recognizer.py` (analiza la trayectoria de la muñeca) y
su reacción de avatar en `vision/avatar_bridge.py::_GESTURE_REACTION`.

**Nueva expresión/emoción:** ajusta `classify_expression()` y
`_EXPRESSION_TO_EMOTION` en `vision/expression_engine.py`.

---

## 9. Probar sin cámara ni modelos

```bash
pytest tests/test_vision_system.py -q
```

Las pruebas usan fotogramas simulados (numpy) y una `FakeCapture`, y verifican
la degradación cuando faltan modelos. No requieren webcam.

---

## 10. Empaquetado (PyInstaller)

`yue.spec` ya incluye:
- `("models/vision", "models/vision")` en `datas` (los modelos que coloques),
- `collect_data_files("mediapipe")` (grafos internos de MediaPipe),
- `collect_submodules("mediapipe")` en `hiddenimports`.

```bash
pyinstaller yue.spec --noconfirm
```

---

## 11. Sobre la estimación de emociones (importante)

Los **blendshapes** miden **movimientos faciales**, no sentimientos. La emoción
es siempre una **estimación aproximada**; el sistema la reporta con confianza y
YUE habla con prudencia ("parece que…", "tal vez…"), nunca como un hecho.
