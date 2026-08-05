# Integración del sistema de visión por cámara (MediaPipe Tasks)

Todo es **aditivo**: no se borró ni se cambió ninguna función existente de YUE.
El sistema está **apagado por defecto** (`VISION_MP_ENABLED=false`).

## Cómo aplicar

Descomprime este paquete **sobre la raíz de tu proyecto** (reemplaza los
archivos con el mismo nombre). O aplica a mano los pequeños bloques de más abajo.

## Archivos NUEVOS

```
vision/                      ← paquete completo del sistema de visión
  __init__.py, settings.py, camera_service.py, frame_hub.py,
  model_manager.py, observations.py, expression_engine.py,
  vision_state.py, vision_state_fusion.py, vision_scheduler.py,
  dialogue_context.py, avatar_bridge.py, controller.py,
  commands.py, integration.py, image_classifier.py
  detectors/  (base, face_detector, face_landmarker, pose_analyzer,
               gesture_recognizer, object_detector)
  segmentation/ (image_segmenter, interactive_segmenter)
models/vision/README.md      ← dónde poner los modelos .task/.tflite
docs/VISION_SYSTEM.md        ← documentación completa
tests/test_vision_system.py  ← 22 pruebas (sin cámara ni modelos)
```

## Archivos MODIFICADOS (solo se AGREGARON líneas)

- **main.py** — se engancha el sistema (junto al V3) y se agregan los comandos
  `/camara on|off` y `/vision …`, más un `_on_vision_status()`.
- **config.py** — bloque de claves `VISION_MP_*` / `VISION_*` (no toca
  `VISION_ENABLED`, que es la visión de pantalla).
- **.env.example** — mismas claves de ejemplo.
- **.gitignore** — excepción `!.env.example` (la regla `.env.*` lo ocultaba).
- **yue.spec** — `models/vision` en `datas` + `collect_data_files("mediapipe")`.

## Bloques mínimos (si prefieres editar a mano main.py)

Enganche (junto al `attach` de `vision_v3`, en el arranque):

```python
try:
    from vision import integration as vision_mp
    self.vision_mp = vision_mp.attach(self)
except Exception as exc:
    print("[vision-mp] no se pudo enganchar:", exc)
    self.vision_mp = None
```

Comandos (en `_handle_command`, junto al `/camara` existente):

```python
elif command in {"/vision", "/visión"}:
    from vision import commands as _vc
    self._yue_say(_vc.handle(getattr(self, "vision_mp", None), command, arg)
                  or "El sistema de visión no está disponible.")
```

## Encender el sistema

1. Pon los modelos en `models/vision/` (ver su README).
2. En `.env`: `VISION_MP_ENABLED=true` y `CAMERA_ENABLED=false`
   (o una `CAMERA_INDEX` distinta) para no pelear por la webcam.
3. Prueba en el chat: `/camara on`, luego `/vision estado`.

## Probar sin cámara

```bash
pytest tests/test_vision_system.py -q      # 22 passed
```

## Privacidad

Procesamiento 100% local. No se guardan ni se envían imágenes. La cámara se
libera al cerrar YUE.
