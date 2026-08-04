# YUE V3 PERCEPTION SYSTEM

Sistema nuevo para que YUE **vea** a la persona: presencia, rostro, emoción y
atención. Usa modelos ya existentes (**OpenCV + MediaPipe + DeepFace**), sin
entrenar nada. Es **100% aditivo** y **opt-in**: apagado por defecto, no toca el
núcleo de YUE ni el observador de cámara clásico.

## Por qué es opt-in (importante)

`core/camera_observer.py` **ya usa la webcam** (MediaPipe face+pose, emociones
por blendshapes, espejo empático). Dos sistemas no pueden abrir la misma cámara a
la vez. Por eso el V3 nace **desactivado** (`VISION_V3_ENABLED=false`): así todo
sigue igual que hoy. Cuando quieras probarlo:

```env
VISION_V3_ENABLED=true
CAMERA_ENABLED=false        # apaga el observador clásico para no competir
# (o, si tienes 2 cámaras, deja el clásico y ponle a V3 otra: CAMERA_INDEX=1)
```

## Arquitectura (core/vision/)

```
core/vision/
├── camera.py                    # CameraEngine: captura, FPS por perfil, reconexión, liberación
├── face_detector.py             # MediaPipe FaceDetection (+ respaldo Haar): presencia y posición
├── emotion_ai.py                # DeepFace: happy/sad/angry/fear/surprise/neutral/disgust (throttle)
├── attention_detector.py        # presente / atento / ausente / rostro perdido / ausencia larga
├── emotion_manager.py           # emoción -> frase (YUE/KAI), con límites por hora y enfriamiento
├── avatar_emotion_controller.py # emoción -> gesto del avatar (espejo empático, no imita el enojo)
├── perf_profile.py              # perfiles LOW/MEDIUM/HIGH (auto por psutil o config)
├── events.py                    # VisionEvent + EventBus (el puente al cerebro de YUE)
├── vision_controller.py         # orquestador / fachada
└── integration.py               # enganche opt-in de 1 línea con main.py (marshalado a Qt)
```

## Cómo se conecta (sin tocar el núcleo)

Dos vías, ambas opcionales:

1. **Eventos** (`EventBus`): la visión solo EMITE `VisionEvent` y quien quiera se
   suscribe. Forma exacta del evento de emoción:
   ```json
   {"type": "emotion_detected", "emotion": "happy", "confidence": 90}
   ```
2. **Callbacks**: `on_avatar_emotion`, `on_speak`, `on_status`.

### Enganche recomendado (1 línea en main.py)

En `YueApp._setup(...)` (o donde armas la cámara), añade:

```python
from core.vision import integration as vision_v3
self.vision_v3 = vision_v3.attach(self)   # None e inerte si VISION_V3_ENABLED=false
```

`attach(self)` crea un pequeño QObject de señales (igual que tu `CameraBridge`)
para marshalar al hilo de Qt y conecta:
- gestos del avatar → `self.pet.set_emotion(...)`
- frases de YUE     → `self._yue_say(...)`
- estado de cámara  → `self.pet.set_camera_active(...)`

No hace falta nada más. Si el V3 está apagado, la línea no hace nada.

### Uso manual (si prefieres controlarlo tú)

```python
from core.vision import VisionController
vc = VisionController(on_avatar_emotion=self.pet.set_emotion, on_speak=self._yue_say)
vc.bus.subscribe(mi_handler)          # opcional: recibir eventos
vc.start(); ...; vc.stop()
```

## Perfiles por hardware (punto 8)

| Perfil | Equipo típico            | DeepFace | Captura |
|--------|--------------------------|----------|---------|
| LOW    | i3 antiguo, 8 GB, iGPU   | cada 8 s | ~12 FPS |
| MEDIUM | 16 GB, GPU media         | cada 5 s | ~20 FPS |
| HIGH   | GPU dedicada             | cada 2 s | ~30 FPS |

`VISION_PERF_PROFILE=auto` los elige con psutil (núcleos + RAM). Sin psutil → MEDIUM.

## Privacidad (punto 9)

Análisis 100% local. **Nunca** se guardan ni se envían imágenes: no hay ninguna
ruta de escritura de fotogramas a propósito. `CAMERA_PRIVACY_MODE=true` lo deja
constatado y lo registra al arrancar la cámara.

## Comportamiento (puntos 4–7)

- **Emociones → frases** con carácter (YUE tsundere / KAI seco), con tope
  `MAX_EMOTION_RESPONSES_PER_HOUR`, confianza mínima y enfriamiento por emoción.
  La personalidad **no cambia**: la emoción solo influye en tono/personaje/avatar.
- **Avatar reactivo**: espejo empático (te ve triste → se preocupa; feliz → se
  contagia; no te imita el enojo). Estados con nombre NORMAL/FELIZ/TRISTE/SORPRENDIDO.
- **Atención**: tras `VISION_ABSENCE_SECONDS` (10 min por defecto) sin verte,
  YUE/KAI suelta una frase de espera. Detecta también "rostro perdido".

## Dependencias

`opencv-python` y `mediapipe` ya estaban. **Opcionales** (solo si activas V3):

```bash
pip install deepface tf-keras psutil
```

Sin DeepFace → hay presencia/atención pero no emociones. Sin psutil → perfil MEDIUM.

## Diagnóstico y pruebas

```bash
python diag_perception.py                      # chequeo de solo lectura (no abre cámara)
python tests\test_vision_system.py    # 22 pruebas, sin webcam ni DeepFace (Windows)
# En Linux/Mac:  python tests/test_vision_system.py
# (No uses «python -m unittest tests.test_vision_system»: tests/ no es un paquete.)
```

Las pruebas inyectan cámara/rostro/emoción falsos, así que corren en cualquier
equipo. Cubren cámara, fallo de cámara, presencia, emoción, throttle, perfiles,
atención (incl. ausencia larga), límites del gestor, espejo empático, bus de
eventos e integración de punta a punta.
