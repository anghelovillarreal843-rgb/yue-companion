# YUE · Acompañamiento multimedia continuo

## Arquitectura respetada

La implementación amplía los módulos existentes en lugar de sustituirlos:

- `core/system_audio.py` conserva una sola captura loopback y publica sus observaciones ya analizadas.
- `core/media_companion.py` coordina audio, frames individuales, emoción continua, comentarios, avatar y memoria.
- `core/ai_engine.py` sigue siendo el único acceso al proveedor de visión.
- `core/memory.py` conserva las tablas existentes y agrega tablas multimedia mediante migración automática.
- `ui/avatar.html` y `ui/desktop_pet.py` reciben parámetros ambientales; mantienen gestos, parpadeo, respiración, lip-sync y expresiones previas.
- `main.py` cruza los hilos por señales Qt y mantiene prioridad para conversación, control de PC, visión explícita y modo profesora.

## Flujo

```text
Audio loopback existente ──> MusicAnalysis ─┐
                                            ├─> fusión emocional ─> estado gradual
Frame individual de pantalla ─> Vision ─────┘                         │
                                                                      ├─> avatar
                                                                      ├─> comentario opcional
                                                                      └─> memoria multimedia
```

No se graba audio ni vídeo. Los frames se comprimen en memoria, se analizan y se descartan. La cola visual tiene tamaño uno para no acumular imágenes ni RAM.

## Detección y rendimiento

El modo comienza cuando el reactor acústico existente confirma sonido sostenido. Funciona con Spotify, YouTube, navegadores, reproductores y cualquier aplicación que produzca audio. La aplicación/título se infiere desde la ventana activa, `now_playing` y procesos conocidos como respaldo.

La visión:

- captura entre 0.5 y 2 segundos;
- acelera ante cambios fuertes de escena;
- desacelera en escenas estables;
- omite análisis si la pantalla no cambió;
- enmascara la zona del avatar para que su movimiento no genere falsos cambios;
- usa una cola de último frame y un único hilo de visión;
- pausa capturas durante el periodo de reintento si el proveedor visual falla.

## Emoción y comentarios

El estado emocional es vectorial y persistente. Cada emoción sube y baja con constantes de tiempo configurables; al terminar el contenido, vuelve gradualmente a neutral.

La fusión incluye refuerzos semánticos, por ejemplo:

- llanto visual + música triste → mayor tristeza;
- batalla + música épica/acción → emoción, concentración, admiración y expectativa;
- escena tierna + música romántica → ternura/afecto;
- golpe sonoro + corte fuerte → sorpresa.

Los comentarios tienen doble barrera: el coordinador evalúa importancia, diálogo, intervalo, concentración y espontaneidad; `main.py` vuelve a comprobar que no haya clase, conversación, escritura, voz, visión u orden de PC en curso.

## Avatar

Durante multimedia puede:

- mirar suavemente hacia la pantalla;
- mantener respiración, parpadeo y microgestos existentes;
- cabecear y balancearse discretamente según ritmo y tempo;
- cerrar brevemente los ojos en fragmentos calmados;
- expresar alegría, tristeza, sorpresa, enojo, curiosidad, emoción, admiración, vergüenza, ternura y concentración mediante los perfiles existentes.

## Memoria multimedia

Se agregaron:

- `multimedia_sessions`: sesión, fuente, título, emoción dominante y resumen;
- `multimedia_events`: escenas importantes/favoritas y comentarios realmente entregados;
- `multimedia_items`: canciones/videos, veces consumidos, emoción y preferencia.

Esto permite resolver frases como `pon la canción anterior` o `pon la canción de ayer`, además de aprender expresiones explícitas como `me encantó` o `no me gusta`.

## Configuración

Todos los parámetros están en `.env.example`:

- activación de acompañamiento, visión, comentarios y avatar;
- intervalo visual mínimo, base y máximo;
- sensibilidad a cambios de escena;
- tiempos de visión y reintento;
- intervalo mínimo/máximo de comentarios;
- espontaneidad;
- intensidad y velocidad de subida/caída emocional;
- sensibilidad musical/visual;
- movimiento del avatar.

La comprensión semántica de frames requiere un proveedor de visión válido en las variables ya existentes (`VISION_PROVIDER`, `VISION_API_KEY`, `VISION_BASE_URL` y modelo). Sin él, el acompañamiento acústico, emocional, de avatar y memoria sigue funcionando; el análisis visual se pausa de forma degradable.

## Validación realizada

- compilación completa con `python -m compileall`;
- sintaxis JavaScript del avatar con `node --check`;
- 4 pruebas nuevas de análisis musical, fusión, emoción gradual y memoria;
- pruebas existentes de comandos, núcleo, memoria consolidada, `now_playing` y emociones audiovisuales.

La interfaz completa no se inició en el entorno de modificación porque no dispone de PyQt5 ni loopback WASAPI. Debe validarse el arranque final en Windows con las dependencias del `requirements.txt`.
