# Mejoras de escucha de audio (aditivas, sin borrar nada)

Copia estos archivos sobre tu proyecto respetando las rutas. Todo es opcional y
degradable: si algo falla, Yue sigue funcionando como antes.

## Archivo NUEVO
- `core/media_profiler.py` — Perfilador que distingue **vídeo normal** de **vídeo
  musical** a partir de los últimos ~12 s de audio, con histéresis para no dar tumbos.

## Archivos MODIFICADOS (solo añadidos, marcados con `# NUEVO`)
- `core/system_audio.py`
- `core/listener.py`
- `config.py`
- `main.py`
- `.env.example`

---

## Petición 1 — Diferenciar vídeo normal de vídeo musical
- `system_audio.py` ya clasificaba cada segundo en silencio/música/voz/ruido. Ahora,
  además, alimenta el nuevo `MediaProfiler`, que mira la tendencia de varios segundos
  y decide el **tipo de contenido**:
  - `video_musical`: música dominante y ritmo estable (canción / videoclip).
  - `video_normal`: hay diálogo o mezcla variada (peli, vlog, tutorial…).
- Yue usa comentarios distintos según el tipo (banco `_COMENTARIOS_MEDIA`) y el motor
  de IA recibe ese dato en el contexto (`context_for_ai` / `describe`).

## Petición 2 — El sonido del vídeo/música se escribía en el chat como si le hablaras
- El `SystemAudioReactor` ahora avisa cuándo **hay media sonando** (con antirrebote).
- `main.py` conecta ese aviso al micrófono (`listener.set_media_playing`).
- Mientras suena un vídeo/música, el micro **exige la palabra clave «Yue…»** para
  tomarte en cuenta. Así el diálogo o la letra que salen por los altavoces no se
  cuelan en el chat. (Ajustable: `MIC_MEDIA_GUARD_*`.)

## Petición 3 — Por ratos se escuchaba a sí misma
- Al terminar de hablar, además del cooldown, se abre una **cola de vigilancia de eco**
  (`MIC_SELF_LISTEN_TAIL`, 2.5 s) que sigue filtrando su propia voz: descarta
  fragmentos muy cortos y lo que solape demasiado con su TTS reciente.
- El umbral de solape con su propia voz pasa a ser configurable y algo más estricto
  (`MIC_SPEAKING_ECHO_OVERLAP`, 0.34; antes estaba fijo en 0.40).

---

## Ajustes nuevos en `.env` (todos con valores por defecto sensatos)
```
MIC_MEDIA_GUARD_ENABLED=true
MIC_MEDIA_GUARD_REQUIRE_WAKE_WORD=true
MIC_MEDIA_GUARD_MIN_CHARS=8
MIC_SELF_LISTEN_TAIL=2.5
MIC_SPEAKING_ECHO_OVERLAP=0.34
MEDIA_PROFILE_WINDOW=12.0
MEDIA_PROFILE_MIN_SAMPLES=6
MEDIA_PROFILE_HYSTERESIS=3
MEDIA_PROFILE_SILENCE_RATIO=0.75
MEDIA_PROFILE_MUSIC_RATIO=0.62
MEDIA_PROFILE_VOICE_RATIO=0.30
MEDIA_PROFILE_TEMPO_CV=0.18
AUDIO_MEDIA_ON_BLOCKS=2
AUDIO_MEDIA_OFF_BLOCKS=6
```

> Nota: si prefieres que puedas hablarle sin decir «Yue» aunque haya un vídeo,
> pon `MIC_MEDIA_GUARD_REQUIRE_WAKE_WORD=false` (subirá algo el riesgo de que el
> audio del vídeo se cuele).
