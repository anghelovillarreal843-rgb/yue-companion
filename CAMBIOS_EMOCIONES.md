# Cambios: reacción emocional (audio) y lectura de emociones (cámara)

Dos peticiones, ambas **aditivas** (no se borró nada; todo puede desactivarse
por `.env`). Si un módulo nuevo faltara, YUE sigue funcionando como antes.

---

## 1) YUE siente y expresa lo que escucha (música y vídeos)

Antes, al oír el audio del PC, YUE ponía una cara según un mapa grueso
(`música movida → excited`, `tranquila → happy`). Ahora **afina esa cara con lo
que realmente siente**, usando los rasgos que ya se extraían del audio
(sonoridad, tempo, brillo, graves, golpes) y el tipo de contenido del perfilador.

Ejemplos de lo que expresa el avatar:

- Música **lenta y oscura** → melancólica (`sad`), le llega.
- Música **muy suave y cálida** → ternura (`love`).
- Música **movida y brillante** → con chispa (`playful` / `excited`).
- Música **intensa pero apagada** → absorta (`focused`).
- **Vídeo normal / película**: se engancha (`curious`), se sobresalta con un
  golpe fuerte (`surprised`), se relaja en escenas suaves (`relaxed`).

Cuando toca comentar (con el mismo cuentagotas de siempre), **lo que dice cuadra
con la cara** ("esta me toca la fibra…", "¡uy, esta me pone las pilas!").

Es **determinista**: la misma música produce siempre la misma cara (sin parpadeo).

**Archivos**
- `core/music_emotion.py` — NUEVO. Traduce el audio a una emoción de la paleta
  del avatar y ofrece frases acordes.
- `core/system_audio.py` — hooks guardados dentro de `decide_reaction` (import
  guardado + refinamiento de la cara + preferencia de frase acorde).

**Desactivar**: `AUDIO_FEEL_EMOTIONS=false` (vuelve a la reacción anterior).

---

## 2) La cámara lee qué siente la persona

La cámara ya se abría sola al iniciar (si el equipo tiene webcam) y leía los
*blendshapes* de MediaPipe, pero se quedaba en señales ("sonrisa visible"). Ahora
**estima la emoción** de la persona y YUE la conoce:

- Sonrisa → **contenta**; comisuras abajo + cejas internas arriba → **triste**;
  boca abierta + ojos muy abiertos → **sorprendida**; ceño fruncido + nariz →
  **molesta**; mirada concentrada → **pensativa**; sin señales → **neutro**.

Con esa lectura:

- Al preguntar "¿qué expresión tengo?", "¿cómo me ves?", "¿qué ves por la
  cámara?"… YUE responde con su impresión ("por su expresión, diría que se le ve
  contenta"), siempre aclarando que es una impresión, no una certeza.
- La IA recibe ese ánimo aparente como contexto (sin fotos, sin identidad).
- **Espejo empático**: YUE **acompaña** con la cara del avatar (si te ve triste,
  se preocupa; **no** te devuelve el enfado). Solo pone gesto, no habla sola, y
  cede la cara cuando ya la gobierna la música/un vídeo, una orden de PC o
  mientras habla.

Sigue siendo **local y en memoria**: no guarda fotos ni vídeo, no reconoce
identidades y no envía imágenes de cámara a la IA.

**Activación automática**: sin cambios de uso. Al iniciar, YUE busca una webcam y,
si la hay, se activa sola (insignia verde **CÁMARA**). Configurable con
`CAMERA_ENABLED`.

**Archivos**
- `core/face_emotion.py` — NUEVO. Estima la emoción del rostro desde los
  blendshapes, da la lectura en español y el mapeo del espejo empático.
- `core/camera_observer.py` — campo opcional `person_emotions` en
  `CameraObservation`, métodos `emotion_reading_es()` / `rich_summary_es()`,
  inferencia por rostro en el analizador y uso de la lectura en `describe()` y
  `context_for_ai()`. `summary_es()` clásico queda intacto.
- `core/commands.py` — regla natural nueva → `camera_status`.
- `main.py` — espejo empático en `_on_camera_observation` (antes no hacía nada).

**Desactivar el espejo**: `CAMERA_EMPATHY_ENABLED=false`.
Ajustes: `CAMERA_EMPATHY_MIN_CONFIDENCE` (def. 0.55), `CAMERA_EMPATHY_MIN_GAP`
(def. 8.0 s).

---

## Pruebas

`python tests/test_emociones_av.py` cubre ambos módulos y su integración
(sin cámara, sin audio, sin GUI). El test de cámara existente
(`tests/test_mejoras.py`, bloque [18]) sigue pasando: `summary_es()` no cambió.
