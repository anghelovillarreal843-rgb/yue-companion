# Visión de pantalla de YUE

Documento de referencia del sistema que permite a YUE MIRAR tu pantalla.
Todo lo de aquí es **aditivo**: no se borró ninguna función que ya funcionaba.

## Flujo

```
usuario: "mira mi pantalla"
   |
core/screen_capture.py      captura validada (monitor, timestamp, frame_age)
   |
core/screen_analyzer.py     clasificación LOCAL del contenido (sin gastar IA)
   |
   +-- OCR  (core/screen_ocr.py)      -> core/ai_router.py   (modelos de TEXTO)
   +-- VISIÓN (core/vision_router.py) -> APIs multimodales
   |
core/screen_observation.py  ScreenObservation  ->  YUE responde
```

## Los dos routers son independientes

| | `AIRouter` (`core/ai_router.py`) | `VisionRouter` (`core/vision_router.py`) |
|---|---|---|
| Para qué | chat y planificación | solo imágenes |
| Modelos | de texto | **solo multimodales** |
| Cerebras | sí | **no** (no acepta imágenes) |

Comparten *concepto* (clases de error, enfriamientos, logs), no lista de modelos.
Ningún proveedor entra a la fila visual sin declarar `supports_vision = True`.

## Fallos que activan el siguiente proveedor

`quota` (429, rate limit) · `auth` (401/403) · `model_gone` (404, retirado) ·
`no_vision` (el modelo no acepta imágenes) · `network` · `timeout` ·
`server` (5xx) · `empty` (respuesta vacía) · `bad_response` (incompatible).

`no_vision` marca el modelo de forma **permanente** en la sesión.
Hay tope duro de intentos (`max_attempts`): nunca hay bucles infinitos.

## Estrategia por tipo de contenido

| Tipo detectado | Estrategia |
|---|---|
| texto, código | OCR |
| fotografía, gráfico, vídeo, juego | visión multimodal |
| PDF, interfaz, navegador, error, mixto | ambos |

Si **toda** la visión multimodal falla y hay texto → *fallback* OCR.
YUE nunca dice "no puedo ver" mientras pueda leer algo.

## Configuración (.env)

```ini
VISION_PROVIDER=auto        # auto | gemini | openrouter | groq | together | openai | custom | none
```

* `auto` → usa toda la fila con relevo automático (**recomendado**).
* `<nombre>` → ese proveedor va primero, los demás quedan de respaldo.
* `none` → visión multimodal apagada a propósito; sigue funcionando por OCR.

Ajustes finos: `SCREEN_VISION_MONITOR`, `SCREEN_VISION_MAX_FRAME_AGE`,
`SCREEN_VISION_CACHE_SECONDS`, `SCREEN_VISION_STRATEGY`,
`SCREEN_VISION_TEMPORAL_FRAMES`, `SCREEN_VIDEO_MOTION_THRESHOLD`.

## Caché

Se reutiliza una observación reciente solo si: la pantalla no cambió (hash
perceptual), la pregunta es la misma y no pasó la ventana de caché.
**"mira ahora", "qué ves ahora", "mira mi pantalla"** siempre fuerzan captura nueva.

## Vídeo

Memoria temporal de 5 frames enviada como **texto resumido**, no como cinco
imágenes. El modelo rellena `cambio_respecto_antes`; si responde
"sin evidencia suficiente", el parser lo descarta para que YUE no lo repita
como un hecho. El vídeo se detecta por **movimiento**, no por audio: un vídeo
silenciado también se ve.

## PDF

* **PDF como archivo** → `teacher/documents.py` (`read_pdf_page_deep`), máxima calidad.
* **PDF en pantalla** → `core/screen_analyzer.py` (OCR + visión de la página visible).
* `documents.estrategia_para_pdf()` decide cuál de los dos aplica.

## Diagnóstico

```bash
python diag_vision_pantalla.py     # informe completo, sin exponer claves
```

También `/diagvision` dentro de YUE.

## Pruebas

```bash
python -m pytest tests/test_screen_vision.py -v
```
