# Modelos de visión (MediaPipe Tasks)

Coloca aquí los archivos de modelo del sistema de visión por cámara. **No se
descargan automáticamente** (privacidad + tamaño del ejecutable). Si un archivo
falta, ese módulo simplemente queda inactivo y el resto de YUE sigue funcionando.

Nombres exactos que espera el sistema (ver `vision/model_manager.py`):

| Módulo | Archivo esperado |
|---|---|
| Detección de rostros | `face_detector.task` |
| Malla facial + blendshapes | `face_landmarker.task` |
| Postura corporal | `pose_landmarker.task` |
| Reconocimiento de gestos | `gesture_recognizer.task` |
| Detección de objetos | `object_detector.tflite` |
| Clasificación de imagen | `image_classifier.tflite` |
| Segmentación de imagen | `image_segmenter.tflite` |
| Segmentación interactiva | `interactive_segmenter.tflite` |

## De dónde bajarlos

Todos son modelos oficiales del **MediaPipe Models Zoo**
(https://ai.google.dev/edge/mediapipe/solutions/vision). Descarga la variante
`.task` / `.tflite` de cada solución (Face Detector, Face Landmarker, Pose
Landmarker, Gesture Recognizer, Object Detector, Image Classifier, Image
Segmenter, Interactive Segmenter) y renómbrala como en la tabla.

## Sobrescribir una ruta puntual

Puedes apuntar a un modelo en otra carpeta con una variable de entorno:

```
VISION_MODEL_FACE_LANDMARKER_PATH=D:\modelos\face_landmarker.task
```

## Privacidad

Los modelos corren **100% en local**. El sistema no guarda ni envía imágenes.
