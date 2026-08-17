# Auditoría de Arquitectura — YUE Companion

> Diagnóstico SOLO de lectura. No se ha modificado ningún archivo.
> Fecha: 14-08-2026 · Excluidos: `.venv`, `__pycache__`, `_respaldo_antes_de_actualizar`, `.git`.

---

## 1. Inventario de tamaño

Conteo de líneas de todos los `*.py` (257 archivos), ordenado de mayor a menor.

| Líneas | Archivo |
|-------:|---------|
| 4335 | main.py |
| 2331 | core/pc_control.py |
| 2038 | core/memory.py |
| 1775 | core/episodic_memory.py |
| 1725 | core/story_memory.py |
| 1389 | core/media_companion.py |
| 1343 | config.py |
| 1338 | tests/test_vision_avanzada.py |
| 1153 | core/memory_ext.py |
| 861 | core/ai_engine.py |
| 796 | core/desktop_ui.py |
| 778 | core/system_audio.py |
| 744 | core/camera_observer.py |
| 732 | vision/perception_engine.py |
| 683 | tests/test_episodic_memory.py |
| 678 | tests/test_screen_vision.py |
| 653 | core/state/state_manager.py |
| 646 | core/listener.py |
| 628 | teacher/documents.py |
| 604 | core/gemini_vision.py |
| 596 | tests/test_agent_architecture.py |
| 584 | vision/controller.py |
| 564 | tests/test_vision_reactiva.py |
| 552 | tests/test_memory_relevance.py |
| 551 | core/screen_analyzer.py |
| 546 | tests/test_state_global.py |
| 528 | core/state/models.py |
| 517 | vision/reactive_layer.py |
| 510 | teacher/teacher_engine.py |
| 490 | vision/live_state.py |
| 436 | tests/test_story_memory.py |
| 432 | vision/settings.py |
| 430 | core/support/policy.py |
| 416 | core/screen_observation.py |
| 410 | core/vision_router.py |
| 396 | vision/voice_intents.py |
| 383 | tests/test_companion_integration.py |
| 381 | vision/ocr/ocr_engine.py |
| 378 | ui/desktop_pet.py |
| 374 | ui/foot_chat.py |
| 374 | core/voice.py |
| 373 | vision/analyzers/action_analyzer.py |
| 373 | core/agent/planner.py |
| 370 | diag_vision_avanzada.py |
| 354 | tests/test_vision_system.py |
| 353 | vision/tracking/hand_tracker.py |
| 350 | tests/test_affect.py |
| 341 | core/screen_vision.py |
| 338 | core/companion_brain.py |
| 326 | vision/camera_manager.py |
| 318 | vision/camera_backend.py |
| 312 | core/music_emotion.py |
| 309 | tests/test_mejoras.py |
| 309 | core/emotion.py |
| 309 | core/agent/verification.py |
| 306 | core/affect/models.py |
| 303 | core/head_control.py |
| 302 | vision/detectors/action_detector.py |
| 302 | core/office_control.py |
| 300 | core/affect/rules.py |
| 296 | vision/analyzers/emotion_analyzer.py |
| 293 | core/privacy.py |
| 292 | tests/test_support.py |
| 291 | vision/models/model_registry.py |
| 287 | tests/diagnostico_pc.py |
| 285 | teacher/classlog.py |
| 282 | core/ai_fallback.py |
| 281 | core/safety_ext.py |
| 278 | vision/privacy_manager.py |
| 270 | core/app_catalog.py |
| 268 | core/affect/lexicon.py |
| 265 | core/affect/semantic.py |
| 263 | core/support/needs.py |
| 261 | core/screen_capture.py |
| 261 | core/learning/skills.py |
| 257 | core/ai_router.py |
| 254 | vision/capabilities.py |
| 250 | core/teacher_ext/rag.py |
| 250 | core/screen_text.py |
| 247 | core/affect/interpreter.py |
| 243 | vision/integration.py |
| 241 | core/agent/registry.py |
| 239 | core/vision/vision_controller.py |
| 238 | core/state/mapping.py |
| 234 | vision/context_builder.py |
| 234 | tests/test_affect_eval.py |
| 233 | tools/download_vision_models.py |
| 232 | core/commands.py |
| 229 | tests/test_ordenes_dificiles.py |
| 228 | teacher/structure.py |
| 228 | core/face_emotion.py |
| 226 | teacher/pedagogy.py |
| 225 | core/memory_consolidation.py |
| 224 | core/state/fusion.py |
| 222 | teacher/assistant.py |
| 220 | vision/event_manager.py |
| 220 | core/support/expression.py |
| 220 | core/doc_builder/pptx_builder.py |
| 218 | core/doc_builder/plan.py |
| 215 | tests/test_jarvis_control.py |
| 214 | vision/analyzers/room_analyzer.py |
| 214 | core/support/prompting.py |
| 212 | core/autonomy_ext/supervisor.py |
| 210 | vision/legacy_adapter.py |
| 210 | tools/actualizar.py |
| 209 | vision/tracking/base_tracker.py |
| 209 | vision/camera_service.py |
| 208 | core/media_profiler.py |
| 207 | vision/ocr/document_scanner.py |
| 204 | tests/test_gemini_vision.py |
| 204 | core/vision/camera.py |
| 202 | core/learning/integration.py |
| 201 | core/vision_awareness.py |
| 199 | core/screen_ocr.py |
| 198 | core/affect/sarcasm.py |
| 195 | core/voice_ext/visemes.py |
| 194 | vision/detectors/scene_detector.py |
| 193 | vision/tracking/temporal_smoother.py |
| 192 | tests/test_seguridad_control.py |
| 191 | teacher/progress.py |
| 189 | core/autonomy.py |
| 188 | core/vision/face_detector.py |
| 187 | tools/secret_scanner.py |
| 187 | core/agent/models.py |
| 186 | vision/detectors/pose_analyzer.py |
| 184 | core/affect/negation.py |
| 182 | mode_manager/mode_manager.py |
| 181 | vision/commands.py |
| 180 | core/video_gen.py |
| 180 | core/agent/queue.py |
| 176 | tests/test_state_manager.py |
| 176 | core/screen_diff.py |
| 175 | core/affect/context.py |
| 171 | core/personality_traits.py |
| 170 | teacher/participation.py |
| 169 | vision/ocr/text_stabilizer.py |
| 168 | diag_vision_pantalla.py |
| 166 | core/state/renderer.py |
| 165 | core/learning/lessons.py |
| 164 | core/vision/emotion_manager.py |
| 163 | vision/tracking/object_tracker.py |
| 162 | core/music_gen.py |
| 161 | core/image_gen.py |
| 159 | vision/detectors/text_detector.py |
| 157 | vision/analyzers/gesture_analyzer.py |
| 156 | vision/dialogue_context.py |
| 155 | diag_gemini.py |
| 154 | core/state/resources.py |
| 153 | tests/diagnostico_groq.py |
| 150 | voice_flow/barge_in.py |
| 149 | vision/expression_engine.py |
| 149 | tools/clean_project.py |
| 149 | core/agent/runtime.py |
| 148 | core/vision/emotion_ai.py |
| 147 | tests/test_memory_ext.py |
| 147 | tests/test_doc_builder.py |
| 147 | core/personality.py |
| 145 | vision/vision_scheduler.py |
| 144 | mode_manager/mode_commands.py |
| 143 | tests/test_now_playing.py |
| 140 | core/doc_builder/excel_builder.py |
| 139 | vision/detectors/gesture_recognizer.py |
| 139 | core/agent/parser.py |
| 138 | tests/test_autonomy_supervisor.py |
| 136 | vision/version.py |
| 135 | core/agent/context.py |
| 134 | vision/analyzers/attention_analyzer.py |
| 133 | vision/tracking/person_tracker.py |
| 132 | core/state/arbiter.py |
| 128 | core/text_sanitizer.py |
| 127 | core/vision/integration.py |
| 125 | core/ui_bridge.py |
| 124 | tests/test_documento_office.py |
| 122 | tests/test_commands.py |
| 121 | vision/detectors/object_detector.py |
| 121 | vision/detectors/face_landmarker.py |
| 120 | tests/test_privacy.py |
| 120 | tests/test_emociones_av.py |
| 120 | core/vision/perf_profile.py |
| 120 | core/vision/attention_detector.py |
| 119 | tests/test_memory_consolidation.py |
| 117 | diag_perception.py |
| 116 | vision/vision_state_fusion.py |
| 116 | tools/build_release.py |
| 115 | tests/test_respuesta_limpia.py |
| 113 | vision/observations.py |
| 113 | diag_vision.py |
| 112 | tests/test_config_env.py |
| 111 | tests/test_media_companion.py |
| 110 | tests/test_teacher_rag.py |
| 110 | teacher/path_repair.py |
| 109 | vision/segmentation/image_segmenter.py |
| 108 | core/safety.py |
| 107 | vision/avatar_bridge.py |
| 106 | core/groq_tuning.py |
| 105 | tests/test_personality_traits.py |
| 103 | tests/test_visemes.py |
| 101 | core/vision/events.py |
| 99 | vision/model_manager.py |
| 99 | tests/test_core.py |
| 98 | tests/test_safety_ext.py |
| 95 | core/youtube.py |
| 90 | ui/speech_bubble.py |
| 89 | core/bonding.py |
| 86 | vision/detectors/base.py |
| 84 | core/agent/executor.py |
| 83 | vision/vision_log.py |
| 82 | vision/detectors/hand_landmarker.py |
| 75 | ui/floating_text.py |
| 74 | vision/frame_hub.py |
| 74 | core/agent/recovery.py |
| 72 | core/agent/waits.py |
| 71 | vision/segmentation/interactive_segmenter.py |
| 71 | vision/detectors/face_detector.py |
| 70 | tests/test_ai_router.py |
| 69 | vision/vision_state.py |
| 69 | ui/theme.py |
| 69 | core/activity.py |
| 68 | core/now_playing.py |
| 67 | core/agent/logger.py |
| 65 | mode_manager/mode_router.py |
| 60 | core/vision/avatar_emotion_controller.py |
| 58 | core/state/events.py |
| 57 | core/voice_affect.py |
| 57 | core/state/__init__.py |
| 57 | core/agent/window_manager.py |
| 56 | vision/image_classifier.py |
| 55 | core/agent/application_manager.py |
| 53 | mode_manager/mode_state.py |
| 52 | mode_manager/mode_events.py |
| 51 | core/vision/__init__.py |
| 48 | vision/__init__.py |
| 47 | core/agent/locks.py |
| 46 | tests/conftest.py |
| 42 | core/affect/__init__.py |
| 30 | core/localserver.py |
| 29 | core/support/__init__.py |
| 26 | teacher/__init__.py |
| 25 | core/agent/__init__.py |
| 20 | core/doc_builder/__init__.py |
| 19 | vision/analyzers/__init__.py |
| 19 | core/learning/__init__.py |
| 18 | core/voice_ext/__init__.py |
| 18 | core/agent/focus_manager.py |
| 15 | voice_flow/__init__.py |
| 15 | tests/diagnostico_groq_keys.py |
| 14 | vision/tracking/__init__.py |
| 14 | mode_manager/__init__.py |
| 10 | vision/ocr/__init__.py |
| 8 | vision/detectors/__init__.py |
| 8 | core/teacher_ext/__init__.py |
| 8 | core/autonomy_ext/__init__.py |
| 4 | vision/models/__init__.py |
| 1 | vision/segmentation/__init__.py |
| 1 | tools/__init__.py |
| 0 | ui/__init__.py |
| 0 | core/__init__.py |

**Totales: 257 archivos `.py` · 69.399 líneas.**

---

## 2. Archivo(s) monolito

Archivos de **código fuente** con más de 500 líneas. (Los tests>500 se listan aparte al final.)

### 2.0 `main.py` (4335 líneas) — el monolito principal ⚠️

Composición raíz de la app (QThreads, bridges y el controlador). Concentra UI de escritorio, workers, lógica de integración y orquestación.

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 7 | `enable_dpi_awareness()` | Ajusta DPI del proceso vía Win32 | — |
| 88 | `_valencia_activacion_camara(clave)` | Decide si la cámara debe activarse según claves | — |
| 120 | `class CameraBridge(QObject)` | Puente Qt para cámara | — |
| 125 | `class AudioBridge(QObject)` | Puente Qt para audio | — |
| 134 | `class MediaCompanionBridge(QObject)` | Puente Qt para compañero de medios | — |
| 141 | `class AiWorker(QThread)` | Hilo de IA | — |
| 157 | `class PdfPageVisionWorker(QThread)` | Hilo probando página PDF | — |
| 184 | `class VisionWorker(QThread)` | Hilo de visión | — |
| 237 | `class VisionLegacyWorker(QThread)` | Hilo de visión legacy | — |
| 259 | `class VisionDiagWorker(QThread)` | Hilo de diagnóstico de visión | — |
| 346 | `class OcrVisionWorker(QThread)` | Hilo de visión OCR | — |
| 365 | `class VisionPreflightWorker(QThread)` | Hilo de preflight de visión | — |
| 384 | `class AppScanWorker(QThread)` | Hilo que escanea apps | — |
| 399 | `class PCWorker(QThread)` | Hilo de control de PC | — |
| 436 | `class PCRecoveryWorker(QThread)` | Hilo de recuperación de control de PC | — |
| 468 | `class AutonomyWorker(QThread)` | Hilo de autonomía | — |
| 484 | `class Controller(QObject)` | **Orquestador maestro** de toda la app | [MEZCLA] |
| 4311 | `def main()` | Bootstrap de arranque, tray, hotkeys | [MEZCLA] |

`Controller` (≈3800 líneas internas) y `main()` concentran responsabilidades de **UI + red + memoria + visión + audio + control de PC + teacher**, acoplando todo el dominio al adaptador Qt (PyQt5).

### 2.1 `core/pc_control.py` (2331 líneas)

Control de escritorio por comandos (clic, teclas, arrastre, OCR de UI).

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 48 | `PCControlError(RuntimeError)` | Error específico del controlador | — |
| 53 | `ActionResult` | Dataclass de resultado de accion | — |
| 143 | `_norm_kw(text)` | Normaliza keywords | — |
| 221 | `_canon_action(value)` | Normaliza nombre de acción | — |
| 241 | `_coerce_step(item)` | Coerciona paso en dict | — |
| 286 | `_texto_accion(valor)` | Texto legible de acción | — |
| 293 | `_resumen_paso(item)` | Resumen de un paso | — |
| 302 | `_norm(text)` | Normaliza texto | — |
| 306 | `looks_like_pc_command(text)` | Detecta si texto es comando de PC | — |
| 333 | `class PCController` | **Ejecuta acciones: clic, teclado, clipboard, OCR, múltiples backends** | [MEZCLA] |

`PCController` mezcla: **lógica de negocio de comandos + PyAutoGUI (hardware) + pywinauto/UI real + OCR + ofimática**. Backends dispares bajo una clase gigante.

### 2.2 `core/memory.py` (2038 líneas)

Memoria/carisma del asistente (recuerdos, perfiles, intereses).

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 58 | `class Memory` | **Persistencia + scoring + generación de lenguaje + búsqueda emocional** | [MEZCLA] |
| 2030 | `_clamp01(valor, defecto)` | Acota valor 0-1 | — |

### 2.3 `core/episodic_memory.py` (1775 líneas)

Memoria episódica y de señales.

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 55 | `_cfg` | Lee config | — |
| 61 | `_debug()` | Flag de debug | — |
| 65 | `_log(msg)` | Log | — |
| 74 | `_norm(texto)` | Normaliza texto | — |
| 90 | `_tokens(texto)` | Tokeniza | — |
| 96 | `_jaccard(a,b)` | Similitud de conjuntos | — |
| 251 | `EpisodeSignals` | Struct de señales extraídas | — |
| 279 | `detect_signals(text, ...)` | Extrae fechas/intensidad/emociones de texto | — |
| 340 | `_inicio_dia(dt)` | Inicio del día | — |
| 344 | `resolve_event_time(text, now)` | Resuelve tiempos relativos | — |
| 450 | `parse_iso_event_time(valor, now)` | Parsea ISO | — |
| 468 | `schedule_follow_up(event_at, ...)` | Agenda seguimiento | — |
| 510 | `score_importance(...)` | Puntúa importancia | — |
| 553 | `EpisodeDraft` | Borrador episódico | — |
| 617 | `_clip(texto, maximo)` | Recorta texto | — |
| 622 | `extract_local(text, signals, ...)` | Extracción local | — |
| 681 | `parse_extraction(raw, ...)` | Parse de extracción LLM | — |
| 742 | `class EpisodicMemory` | **Persistencia + clarificación de fechas + dedupe + búsqueda LLM** | [MEZCLA] |
| 1716 | `_con_articulo(etiqueta)` | Artículo gramatical | — |
| 1736 | `_outcome_emotion(text)` | Emoción de resultado | — |

### 2.4 `core/story_memory.py` (1725 líneas)

Memoria narrativa (personas, conflictos, historias).

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 60 | `_cfg` | Lee config | — |
| 85 | `slugify(texto)` | Slugifica | — |
| 107 | `story_key_for(...)` | Clave de historia | — |
| 295 | `class Mention` | Dato de mención de persona | — |
| 325 | `detect_people(text, ...)` | Detecta mención de personas | — |
| 422 | `detect_closure(text)` | Detecta cierre narrativo | — |
| 428 | `detect_conflict(text)` | Detecta conflicto | — |
| 437 | `compute_significance(...)` | Significancia | — |
| 477 | `reinforce_confidence(...)` | Refuerza confianza | — |
| 545 | `class StoryMemory` | **Persistencia + clasificación + narrativa generativa + búsqueda** | [MEZCLA] |
| 1649 | `parse_stories(raw)` | Parsea historias LLM | — |

### 2.5 `core/media_companion.py` (1389 líneas)

Compa del medio (música/vídeo): analiza y reacciona.

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 63 | `MusicAnalysis` | Resultado musical | — |
| 81 | `VisualAnalysis` | Resultado visual | — |
| 103 | `MediaSource` | Fuente de media | — |
| 112 | `CompanionReaction` | Reacción | — |
| 122 | `AvatarMediaState` | Estado media del avatar | — |
| 135 | `analyze_music(obs, ...)` | Clasifica música | — |
| 298 | `parse_visual_response(...)` | Parsea respuesta visual LLM | — |
| 371 | `fuse_emotions(...)` | Fusiona audio+visual | — |
| 406 | `ContinuousEmotionState` | Estado emocional continuo | — |
| 458 | `detect_media_source()` | Detecta qué app suena | [MEZCLA] (Win32 + audio) |
| 561 | `class MediaCompanion` | **Ciclo de vida: captura Windows + predict + reacción + TTS** | [MEZCLA] |

### 2.6 `config.py` (1343 líneas)

Configuración central (lector de `.env` + helpers).

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 33 | `_env_raw` | Lee variable cruda | — |
| 50 | `_env_float` | Lee float | — |
| 62 | `_env_int` | Lee int | — |
| 78 | `_env_bool` | Lee bool | — |
| 97 | `_env_conf01` | Lee confianza | — |
| 123 | `_groq_keys()` | Rota claves Groq | — |
| 243 | `vision_endpoint()` | Endpoint de visión | — |

Archivo plano con +900 líneas de constantes. No es clase monolito pero es un **bloque de texto gigante** (acoplamiento por import a todo el proyecto).

### 2.7 `core/memory_ext.py` (1153 líneas)

Extensión de memoria (retrievers TF-IDF / embeddings).

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 460 | `BaseRetriever` | Interfaz de recuperación | — |
| 478 | `TfidfRetriever` | Recuperador TF-IDF | — |
| 547 | `EmbeddingRetriever` | Recuperador por embeddings | — |
| 594 | `register_retriever` | Registro de fábricas | — |
| 599 | `build_retriever` | Construye retriever | — |
| 610 | `class MemoryExtension` | **Orquestación de escritura + consulta + RAG sobre memoria** | [MEZCLA] |

### 2.8 `core/ai_engine.py` (861 líneas)

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 9 | `class AIEngine` | **Capa de IA: routing de modelos + fallback + prompts + historial + perfil** | [MEZCLA] |

### 2.9 `core/desktop_ui.py` (796 líneas) — Windows

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 43 | `norm` | Normaliza texto | — |
| 79 | `is_windows()` | ¿Es Windows? | — |
| 83 | `available()` | ¿pywinauto disponible? | — |
| 100 | `list_windows` | Lista ventanas UIA | — |
| 133 | `_list_windows_fallback` | Respaldo pygetwindow | — |
| 168 | `_active_title_win32()` | Título activo via win32gui | — |
| 182 | `active_window_title()` | Título de ventana activa | — |
| 186 | `focus_window(title)` | Enfoca ventana | — |
| 252 | `close_window(title)` | Cierra ventana | — |
| 304 | `active_window_elements` | Elementos UIA activos | — |
| 366 | `find_element_center` | Centro de un elemento | — |
| 391 | `own_window_rects()` | Rectos de ventanas propias | — |
| 463 | `describe_windows` | Describe ventanas | — |
| 474 | `focused_element_name()` | Nombre del elemento enfocado | — |
| 498 | `element_has_focus(...)` | ¿Elemento tiene foco? | — |
| 521 | `own_hwnds()` | Handles propios | — |
| 552 | `_aplicar_click_through(...)` | Click-through (ventana "fantasma") | — |
| 577 | `set_click_through(...)` | Activa/desactiva click-through | — |
| 616 | `target_hwnd()` | Handle objetivo | — |
| 681 | `window_snapshot(title)` | Captura snapshot de ventana | — |
| 723 | `move_window(title, ...)` | Mueve ventana | — |
| 744 | `restore_window_snapshot(snapshot)` | Restaura snapshot | — |
| 758 | `reopen_window_snapshot(snapshot, ...)` | Reabre snapshot | — |

Módulo **100% Windows/Win32/pywinauto** con degradado elegante codificado a mano en ~20 funciones sueltas.

### 2.10 `core/system_audio.py` (778 líneas)

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 77 | `AudioObservation` | Observación de audio | — |
| 104 | `Reaction` | Reacción | — |
| 115 | `_rfft_mag` | FFT | — |
| 125 | `analyze_block` | Características del bloque audio | — |
| 151 | `classify(feats)` | Clasifica | — |
| 180 | `estimate_tempo` | Detecta tempo | — |
| 255 | `ReactorState` | Estado del reactor | — |
| 264 | `decide_reaction(...)` | Decide reacción | — |
| 394 | `class SystemAudioReactor` | **Captura audio (PyAudio/hardware) + lifestyle permanente + reacciones** | [MEZCLA] |

### 2.11 `core/camera_observer.py` (744 líneas)

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 47 | `CameraObservation` | Observación de cámara | — |
| 204 | `_MediaPipeAnalyzer` | Análisis MediaPipe (cara) | — |
| 376 | `class CameraObserver` | **Lazo captura cámara + MediaPipe + estado + hilos** | [MEZCLA] |

### 2.12 `vision/perception_engine.py` (732 líneas)

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 53 | `class PerceptionEngine` | **Pipeline completo de percepción (UI + escena + memoria reactiva)** | [MEZCLA] |

### 2.13 `core/state/state_manager.py` (653 líneas)

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 40 | `Priority` | Prioridad de estados | — |
| 52 | `YueState` | Struct de estado | — |
| 70 | `_AvatarHold` | Holder avatar | — |
| 78 | `_EmotionHold` | Holder emoción | — |
| 94 | `class YueStateManager` | **Arbiter de prioridad + transiciones + timeouts + persistencia** | [MEZCLA] |

### 2.14 `core/listener.py` (646 líneas)

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 23 | `list_microphones()` | Lista micrófonos | — |
| 46 | `class VoiceListener` | **Captura micrófono (SpeechRecognition) + wake-word + barge-in + hilos** | [MEZCLA] |

Usa `voice_flow.BargeInGate` (import de módulo hermano).

### 2.15 `teacher/documents.py` (628 líneas)

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 40 | `SourceText` | Fuente de texto | — |
| 48 | `find_source(text)` | Detecta fuente | — |
| 78 | `to_local_path` | Convierte a ruta local | — |
| 125 | `read_source` | Lee fuente (dispatch) | — |
| 317 | `read_pdf_pages` | Lee páginas PDF | — |
| 360 | `page_count` | Cuenta páginas | — |
| 413 | `render_pdf_page_png` | Rasteriza página | — |
| 449 | `ocr_image_text` | OCR de imagen | — |
| 472 | `read_pdf_page_deep` | Lectura profunda página | — |
| 499 | `read_pdf_full_deep` | Lectura profunda completa | — |
| 529 | `capabilities()` | Capacidades | — |
| 560 | `pdf_visible_en_pantalla()` | ¿PDF visible? | [MEZCLA] (visión + archivo) |
| 601 | `estrategia_para_pdf(...)` | Estrategia de lectura | — |

### 2.16 `core/gemini_vision.py` (604 líneas)

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 56 | `GeminiVisionError` | Error | — |
| 107-152 | getters de config | Lee endpoints | — |
| 191 | `_headers()` | Headers HTTP | — |
| 223 | `_post(...)` | HTTP POST | — |
| 306 | `_parte_imagen` | Parte imagen b64 | — |
| 321 | `analizar_pantalla` | Analiza pantalla | — |
| 333 | `capturar_pantalla_b64` | Captura pantalla | — |
| 362 | `analizar_imagen_archivo` | Analiza imagen archivo | — |
| 409 | `analizar_video_pantalla` | Analiza vídeo | — |
| 460 | `analizar_pdf` | Analiza PDF | — |
| 495 | `subir_archivo` | Sube archivo | — |
| 572 | `preflight` | Comprueba conectividad | — |

### 2.17 `vision/controller.py` (584 líneas)

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 44 | `class VisionSystem` | **Orquesta cámara + detección + OCR + reactivo** en un solo `VisionSystem` | [MEZCLA] |

### 2.18 `core/screen_analyzer.py` (551 líneas)

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 96 | `class ScreenAnalyzer` | **Analiza pantalla via LLM + captura + respuesta estructurada** | [MEZCLA] |

### 2.19 `core/state/models.py` (528 líneas)

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 46 | `UserState` | Estado de usuario | — |
| 154 | `YueBehaviorState` | Comportamiento | — |
| 237 | `SystemState` | Estado de sistema | — |
| 300 | `YueGlobalState` | Estado global | — |
| 370 | `UserObservation` | Observación | — |
| 434 | `YueProposal` | Propuesta | — |

### 2.20 `vision/reactive_layer.py` (517 líneas)

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 97 | `class ReactiveLayer` | **Loop reactivo de visión (poses, atención, memoria)** | [MEZCLA] |

### 2.21 `teacher/teacher_engine.py` (510 líneas)

| Línea | Símbolo | Qué hace | Mezcla |
|------:|---------|----------|:------:|
| 68 | `class TeacherEngine` | **Pedagogía + seguimiento + memoria de clases + rag** | [MEZCLA] |

---

### Archivos de test >500 líneas (no monolito de producto, pero voluminosos)

`tests/test_vision_avanzada.py` (1338) · `tests/test_episodic_memory.py` (683) ·
`tests/test_screen_vision.py` (678) · `tests/test_agent_architecture.py` (596) ·
`tests/test_vision_reactiva.py` (564) · `tests/test_memory_relevance.py` (552) ·
`tests/test_state_global.py` (546).

---

## 3. Dependencias específicas de Windows

### Imports de pywin32 / win32 / comtypes / pywinauto

Los imports están **dentro de funciones/`try`** (la mayoría con degradado), no a nivel de módulo. Por ello el regex de imports no los captura; se listan los usos reales:

| Archivo:línea | Hallazgo |
|---------------|----------|
| core/desktop_ui.py:95 | `from pywinauto import Desktop` (dentro de `_desktop()`) |
| core/desktop_ui.py:170 | `import win32gui` en `_active_title_win32()` |
| core/desktop_ui.py:231-232 | `import win32con` + `import win32gui` (enfoque) |
| core/desktop_ui.py:286-287 | `import win32con` + `import win32gui` (cerrar ven.) |
| core/desktop_ui.py:321 | `from pywinauto import Desktop` (elementos UIA) |
| core/desktop_ui.py:412-413 | `import win32gui` + `import win32process` |
| core/desktop_ui.py:485 | `from pywinauto.uia_defines import IUIA` |
| core/desktop_ui.py:527-528 | `import win32gui` + `import win32process` |
| core/desktop_ui.py:555-556 | `import win32con` + `import win32gui` |
| core/desktop_ui.py:88 | `import pywinauto` (detectar disponibilidad) |
| core/pc_control.py:1768 | Error: `click_element` requiere pywinauto en Windows |
| core/office_control.py:60 | `import win32com.client` (Dentro de método) |
| core/office_control.py:79 | `import win32com.client` + `pythoncom` |

### LLamadas directas a Win32 via `ctypes`

| Archivo:línea | Hallazgo |
|---------------|----------|
| main.py:9-25 | `ctypes.windll.user32/shcore/kernel32` (DPI) — en rama `if sys.platform != "win32": return` |
| core/desktop_ui.py:667 | `ctypes.windll.kernel32.OpenProcess` |
| core/desktop_ui.py:673 | `ctypes.windll.kernel32.QueryFullProcessImageNameW` |
| core/desktop_ui.py:676 | `ctypes.windll.kernel32.CloseHandle` |
| core/media_companion.py:471 | `ctypes.windll.user32.GetForegroundWindow` |
| core/media_companion.py:473 | `ctypes.windll.user32.GetWindowThreadProcessId` |
| core/screen_observation.py:210 | `ctypes.windll.user32.GetForegroundWindow` |
| core/screen_observation.py:212 | `ctypes.windll.user32.GetWindowThreadProcessId` |

### Rutas Windows hardcodeadas con backslash

| Archivo:línea | Hallazgo |
|---------------|----------|
| core/screen_text.py:96 | `r"C:\Program Files\Tesseract-OCR\tesseract.exe"` |
| core/screen_text.py:97 | `r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"` |
| config.py:658 | Comentario `C:\Program Files\Tesseract-OCR\tesseract.exe` |
| core/app_catalog.py:26 | Parseo de `"C:\Ruta\app.exe",0` (formato Windows) |
| core/autonomy_ext/supervisor.py:70 | Regex `windows|c:` |
| teacher/path_repair.py:11,17,29,57,67 | Repara `C:Users\...` / `C:\...` (rutas Windows de usuario) |
| teacher/documents.py:98 | Comentario reparación drive-letter |
| tools/actualizar.py:74 | Doc `C:\Users\villa\Downloads\...` |
| tests/test_jarvis_control.py:163 | Test de target Windows |
| diag_gemini.py:147 · diag_vision.py:60 | Docs de rutas Windows |

### Ramas según SO (`os.name` / `sys.platform` / `platform.system()`)

| Archivo:línea | SO condicionado | Rama |
|---------------|-----------------|------|
| main.py:9 | `sys.platform != "win32"` | Aborta arranque en no-Windows |
| core/desktop_ui.py:80 | `platform.system()=="windows"` | Habilita solo en Windows |
| core/media_companion.py:527 | `sys.platform=="win32"` | `_is_windows()` |
| core/screen_observation.py:208 | `sys.platform=="win32"` | Capturar ventana activa |
| core/camera_backend.py:56 | `platform.system()` | Selección de backend cámara |
| core/pc_control.py:541,683,2119,2199 | `platform.system()` | atajos cmd/ctrl, paste, modo |
| core/vision/camera.py:58 | `platform.system()=="windows"` | `CAP_DSHOW` |
| core/office_control.py:57,75 | `platform.system()!="windows"` | Corto-circuito sin Office |
| core/system_audio.py:621 | `platform.system()!="windows"` | Degrada reactor de audio |
| core/app_catalog.py:60,81,149 | `platform.system()` | Lanzar/registrar apps; `os.startfile` |
| core/camera_observer.py:723 | `platform.system()=="windows"` | `CAP_DSHOW` |
| tests/test_vision_avanzada.py:1082 | `platform.system()=="windows"` | Asume Windows para DSHOW |

### Bibliotecas en `requirements.txt` no multiplataforma

| Paquete | Estado |
|---------|--------|
| `pywinauto; sys_platform == "win32"` | Solo Windows (correctamente marcado con marker) |
| `pywin32; sys_platform == "win32"` | Solo Windows (marcado con marker) |
| `comtypes` | Resto del backend UIA; dependencia de pywinauto (usable solo Windows por propósito) |
| `PyQt5` / `PyQtWebEngine` | Multiplataforma pero orientado a escritorio |
| `PyAudio` | Multiplataforma, pero instalar en Windows exige wheel/portaudio |
| `pyautogui` | Multiplataforma (usa Win32, Quartz o X11 internamente) |
| `tesseract` binario | No es pip; rutas Windows hardcodeadas (aunque mira `OCR_TESSERACT_CMD`) |
| `edge-tts`, `gTTS`, `kokoro-onnx` | Multiplataforma |
| `opencv-python`, `mediapipe` | Multiplataforma |

Conclusión: el **arranque (`main.py`) es bloqueado en sistemas que no sean Windows**, y buena parte del control/desktop (`desktop_ui`, `pc_control`, `office_control`, `media_companion`, `screen_observation`) es **funcional solo en Windows**, con degradados que devuelven `None`/vacíos.

---

## 4. Acoplamientos y dependencias circulares

### Mapa de dependencias entre módulos top-level

```
                    ┌──────────────────────────────────────────────┐
                    │                     main.py                  │  ← composición raíz
                    │ importa: core, ui, vision, teacher, mode_…   │
                    └───────────────┬──────────────┬───────────────┘
                                    │              │
   core  ───────────────────────────┘              │
     │  importa internamente core/* (ai_engine, pc_control, state,
     │  memory*, screen_*, system_audio, camera_observer, etc.)
     │
     ├──►  vision  (core.listener? no; core/screen_ocr → vision.ocr.ocr_engine)
     ├──►  voice_flow (core/listener.py → voice_flow.BargeInGate)
     ├──►  teacher   (core/screen_vision.py → teacher.documents)
     └──►  ui        (core/ui_bridge, core/desktop_ui  ← PyQt)

   vision  ───────────►  core   (vision/integration.py → core.state)
   vision.controller → (vision/cámara, detectors, ocr, reactive, seg)
   ui      ───────────►  core   (ui/desktop_pet, foot_chat, floating_text → core)
   teacher ───────────►  core   (teacher/documentos → core/screen_observation)
   mode_manager ─────► (interno: mode_manager/mode_manager, mode_router, mode_state, mode_events)
   voice_flow ───────► (barge_in)
   tools    ─────────►  vision (actualizar, download_vision_models)
```

### Dependencias "hacia arriba" / acoplamiento entre hermanos

- **`core → vision`**: `core/screen_ocr.py:60` importa `vision.ocr.ocr_engine`. `core` dependiendo de `vision` rompe la idea de que `core` sea la capa base independiente.
- **`core → voice_flow`**: `core/listener.py:18` importa `voice_flow.BargeInGate`.
- **`core → teacher`**: `core/screen_vision.py:197` importa `teacher.documents`.
- **`vision → core`** (a la inversa): `vision/integration.py:34` y `vision/controller.py` importan `core.state`/`core.ai_engine`. → Con las anteriores forma **ida y vuelta entre `core` y `vision`**.

### Posible dependencia circular

- `core/screen_ocr` → `vision.ocr.ocr_engine` **y** `vision.ocr.ocr_engine` → `core.screen_text` (`vision/ocr/ocr_engine.py:60` importa `core.screen_text`).
  → **Ciclo funcional `core ↔ vision`** aunque mitigado por imports flojos/lazy dentro de funciones (no hay ciclo en import-time porque se hace dentro de métodos, pero existe acoplamiento circular de diseño).

### Acoplamiento excesivo

- `main.py` importa `core`, `ui`, `vision`, `teacher`, `mode_manager`, `voice_flow`: es el único lugar que debería unirlo todo, pero arrastra ~3800 líneas de lógica real en `Controller`, no solo wiring.
- `vision/controller.py` (VisionSystem) depende de **~20 submódulos** de `vision/*`: alto fan-in, centralizado en un solo punto.
- `core/pc_control.py` importa `core.agent`, `core.app_catalog`, `core.learning`, `core.office_control`: control de PC acoplado a aprendizaje y ofimática.

---

## 5. Riesgos para el refactor (sin tocar sin tests)

**Hardware / sistema real** (imposible de probar en CI, efectos laterales fuertes):
- `core/pc_control.py::PCController` — PyAutoGUI (movimiento real del ratón/teclado), OCR, pywinauto, múltiples backends. Alto riesgo.
- `core/camera_observer.py::CameraObserver` + `_MediaPipeAnalyzer` — cámara + MediaPipe en bucle.
- `core/system_audio.py::SystemAudioReactor` — PyAudio/micrófono en bucle continuo.
- `core/listener.py::VoiceListener` — micrófono + SpeechRecognition.
- `core/head_control.py`, `core/face_emotion.py`, `core/vision/camera.py` — hardware de rostro/cámara.
- `core/music_emotion.py`, `core/media_companion.py` — reproduce audio/música, detecta fuente de sistema.
- `core/desktop_ui.py` — manipula ventanas UIA reales (cierra/enfoca/mueve).
- `core/office_control.py` — COM/pywin32 sobre Office real.

**Monolitos gigantes** (alta probabilidad de regresión aun cambiando poco):
- `main.py::Controller` (4335 líneas) — tocar `Controller`/`main()` afecta a toda la app. Sin test directo dedicado.
- `core/memory.py::Memory`, `core/episodic_memory.py::EpisodicMemory`, `core/story_memory.py::StoryMemory` — los tests (`test_episodic_memory`, `test_memory_relevance`, `test_story_memory`, `test_memory_ext`) existen y cubren partes, pero las clases mezclan persistencia+scoring+NLP; refactor arriesgado fuera de esos tests parciales.
- `core/ai_engine.py::AIEngine` — capa de modelo con fallback y prompts; sin test dedicado (solo `test_ai_router.py` pequeño).
- `vision/controller.py::VisionSystem`, `vision/perception_engine.py::PerceptionEngine`, `vision/reactive_layer.py::ReactiveLayer` — pipelines largos; cobertura teórica en los tests `test_vision_*` pero muchos exigen hardware simulado.

**Sin test directo / no cubiertos claramente**:
- `core/desktop_ui.py` (todo Win32) — sin test propio; `tools/clean_project.py`, `tools/secret_scanner.py`, `tools/actualizar.py` sin tests.
- `core/system_audio.py` — sin test dedicado.
- `core/office_control.py` — requiere Office real; sin test.
- `core/ui_bridge.py`, `core/state/renderer.py`, `core/state/arbiter.py` — poca/lógica core mezclada con UI.
- `core/screen_ocr.py`, `core/screen_text.py`, `core/support/policy.py` — dependen de Tesseract/OCR externo.

**Side-effects globales / estado mundial**:
- `config.py` — leído por casi todo el proyecto; cualquier cambio de contrato revienta imports. Riesgo alto por acoplamiento estático.
- `core/state/state_manager.py::YueStateManager` — estado global con prioridad/transiciones; cualquier timeout/persistencia afecta a UI y a `Controller`.
- `config.py::_groq_keys()` y `core/groq_tuning.py` — rotación de claves y tuning; tocar sin cuidado puede dejar la IA sin credenciales/rotación rota.

**Sintético**: ~558 `def test_*` en `tests/`, pero concentrados en memoria/visión/estado; los ejes PC-control, desktop UI Win32, audio, ofimática y configuración global quedan con cobertura baja o nula.

---

*Fin de la auditoría (diagnóstico únicamente, sin cambios aplicados).*