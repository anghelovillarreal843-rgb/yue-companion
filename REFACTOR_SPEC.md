# REFACTOR_SPEC — YUE Companion

> **Documento vivo** derivado de `AUDITORIA_ARQUITECTURA.md`. Este spec **cierra** las 6 decisiones
> de diseño pedidas; no deja ningún punto "a decidir". Es la guía accionable para el refactor.
> Estado: **aprobado para implementación por pasos** (ver §5). Sin código tocado todavía.

---

## Regla de placeholders (política permanente)

Cualquier valor sin confirmar pendiente se omite o se muestra en estado neutro al entregar;
**nunca se publica un placeholder visible.** En este spec no queda ningún `TODO` abierto:
todas las decisiones de las 6 secciones están cerradas. Lo único explícitamente *no implementado*
es la implementación Linux de `PlatformController`, por decisión deliberada (§3) — pero el punto
de extensión está definido y verificado con un `NotImplementedError` controlado.

---

## Decisiones tomadas (registro)

| # | Decisión | Razonamiento |
|---|----------|--------------|
| D1 | `vision` NO importa `core.*`. Se crea paquete neutro `contracts/` con `VisionHost` y `OCRPort`. | La auditoría detecta ciclo funcional `core ↔ vision`. Romperlo exige que el contrato viva fuera de `core` (si viviera en `core`, `vision` seguiría importando `core`). |
| D2 | La capa de abstracción de plataforma vive en `platform/` (interfaz) + `platform/windows/` (implementación actual) + `platform/linux/` (stub). | Linux es objetivo declarado de compatibilidad; mover el código Win32 hoy a un único sitio con fábrica `sys.platform` es el cambio más aislado y reversible del refactor. |
| D3 | `PCController` conserva PyAutoGUI (cross-platform) pero recibe `PlatformController` por inyección para toda operación Win32 (elementos, ventanas, office). | `pc_control` usa `desktop_ui` a nivel de módulo (línea 26); inyectar el controlador elimina esa dependencia estática y permite testear sin hardware. |
| D4 | `Controller` (main.py) se desglosa en 9 directores nuevos bajo `engine/` + la utilidad base `WorkerRegistry`, en orden de extracción con verificación tras cada paso. | La auditoría marca `Controller` (~3800 líneas) como mayor riesgo; extraer por responsabilidad única permite smoke-test cada extracción. |
| D5 | Orden de PRs: tests base → plataforma → ciclo core↔vision → desglose de Controller (de lo más aislado a lo más riesgoso). | Piramide de riesgo de la auditoría §5: tocar plataforma/ventanas sin tests es lo más peligroso; se cubre primero. |
| D6 | Tests mínimos (smoke) obligatorios antes de tocar `PCController`, `CameraObserver`, `SystemAudioReactor`, `VoiceListener`, `desktop_ui`, `office_control`. | Todos manejan hardware/sistema real; un smoke determinista con dobles evita regresiones silenciosas imposibles de reproducir en CI. |

---

## 1. ESTRUCTURA OBJETIVO

Se conservan **todas** las carpetas existentes (`core/`, `ui/`, `voice_flow/`, `mode_manager/`,
`teacher/`, `tools/`, `vision/`) con su contenido y sus responsabilidades actuales.
Solo se crean 3 paquetes nuevos, exigidos por el acoplamiento detectado:

```
yue-companion/
├── main.py                  # ADELGAZA a wiring puro + main() (ver §4)
├── config.py                # sin cambio de contrato; se mantiene como está
├── contracts/               # NUEVO — interfaces neutras (sin imports de core/vision entre sí)
│   ├── __init__.py
│   ├── vision_host.py       # D1: VisionHost (contrato que la app expone a vision)
│   └── ocr_port.py          # D1: OCRPort (contrato que core necesita para OCR)
├── platform/                # NUEVO — abstracción de sistema operativo (§3)
│   ├── __init__.py          # fábrica get_platform_controller() -> PlatformController
│   ├── contracts.py         # PlatformController (ABC) + dataclasses de resultados
│   ├── windows/             # implementación REAL (código actual movido):
│   │   ├── __init__.py
│   │   ├── desktop_ui.py    #  ← desde core/desktop_ui.py (SIN cambios de lógica)
│   │   ├── office.py        #  ← desde core/office_control.py
│   │   ├── media_source.py  #  ← detect_media_source()+proceso desde core/media_companion.py
│   │   ├── windows_utils.py #  ← rutinas win32 sueltas (ctypes/window win32 en screen_observation)
│   │   └── dpi.py           #  ← enable_dpi_awareness() desde main.py
│   └── linux/               # punto de extensión — SOLO stub (NO implementar ahora):
│       └── __init__.py      # LinuxController(PlatformController): NotImplementedError con mensaje claro
├── engine/                  # NUEVO — directores extraídos de Controller (§4)
│   ├── __init__.py
│   ├── worker_registry.py   # WorkerRegistry: utilidad base compartida (ex _track_worker) — paso 1 de §4.2
│   ├── controller_ctx.py    # context compartido: señales, speaker, chat, pet, state_manager, workers, camera, last_visual_risk_ask (service-locator)
│   ├── dialogue.py          # DiálogoDirector (conversación, system prompt, greet, AI callbacks)
│   ├── voice_director.py    # VoiceDirector (mic, wake word, barge-in, diagnostics voice)
│   ├── memory_proactive.py  # MemoryProactive (observación episodio/historia, follow-ups, check-ins)
│   ├── teacher_director.py  # TeacherDirector (modos teacher, PDF, autoadvance)
│   ├── vision_director.py   # VisionDirector (glance, screen obs, vision status, camera emotion)
│   ├── media_director.py    # MediaDirector (audio/media, letras, reacciones)
│   ├── pc_director.py       # PCDirector (órdenes PC, undo/repeat, rutinas, confirmación voz)
│   ├── autonomy_director.py # AutonomyDirector (autonomía, internal actions, head control)
│   └── emotion_orchestrator.py # EmotionOrchestrator (avatar, estado, wellbeing nudges)
├── core/                    # se mantiene, PERO:
│   ├── screen_ocr.py        # pierde import de vision*; usa OCRPort inyectado (D1)
│   ├── screen_observation.py# pierde parte win32 → usa PlatformController.foreground_app()
│   ├── pc_control.py        # recibe PlatformController por inyección (D3); sin imports platform
│   ├── desktop_ui.py        # re-export temporal de platform/windows/desktop_ui.py (PR2→PR6)
│   └── listener.py          # pierde import de voice_flow → BargeInGate inyectado (PR5)
└── vision/                  # se mantiene; pierde imports de core.* (D1)
    └── integration.py       # usa VisionHost inyectado en attach(host)
```

**Reglas de dirección de dependencias (invariantes objetivo del refactor):**

1. `core/*` nunca importa `vision`, `teacher`, `voice_flow` ni `ui` (solo `contracts`, `config` y `core/*`). → hoy se viola en `core/screen_ocr.py` (vision), `core/listener.py` (voice_flow), `core/screen_vision.py` (teacher+desktop_ui); el spec los cierra en PR2/PR3/PR5.
2. `vision/*` nunca importa `core` ni `main` (solo `contracts`). → hoy se viola en `vision/integration.py`; se cierra en PR3/PR4.
3. Todo código que toque sistema operativo vive bajo `platform/` y solo se consume **inyectado**.
4. `main.py` y `engine/` son los únicos lugares que conectan módulos entre sí (composición).
5. `config.py` mantiene su contrato público actual; no se mueven claves ni constantes.

---

## 2. RUPTURA DEL CICLO core ↔ vision

**Decisión cerrada: `vision` NO importa de `core`; `core` consume visión a través de interfaces.**

### 2.1 Los 3 puntos exactos que hoy crean el ciclo

| Hoy | Problema | Solución |
|-----|----------|----------|
| `vision/integration.py:106,149` → `from core.state import Priority` | `vision` depende de `core.state` concreto | Sustituir por método del `VisionHost` inyectado; el host resuelve prioridad internamente. |
| `vision/integration.py::attach(app)` usa duck-typing sobre `app={()}` (atributos `state_manager`, `audio`, `speaker`) | Contrato implícito y frágil | `attach(host: VisionHost)`: hosts tipado explícito con la firma abajo. |
| `core/screen_ocr.py:60` → `from vision.ocr.ocr_engine import OCREngineChain` | `core` depende de `vision` (lado opuesto del ciclo) | `screen_ocr` recibe un `OCRPort`; la implementación real la registra la composición desde `main.py`. |

### 2.2 Interfaz `VisionHost` (en `contracts/vision_host.py`, nuevo)

Contrato que **la app** implementa y **vision** consume. Contiene exactamente lo que
`vision/integration.py` usa hoy, con firma cerrada:

```python
class VisionHost(ABC):
    @abstractmethod
    def request_emotion(self, name: str, intensity: float, duration_ms: int,
                        priority: int, source: str) -> None: ...
    @abstractmethod
    def emotion_would_win(self, priority: int) -> bool: ...
    @property
    @abstractmethod
    def media_playing(self) -> bool: ...          # TTS/audio ocupado
    @property
    @abstractmethod
    def is_speaking(self) -> bool: ...            # el avatar/locutor habla
    # EMOTION_PRIORITY: int  constante exportada (equivalente a Priority.EMOTION)
```

- `core/state/state_manager.py::YueStateManager` será el **proveedor** de `request_emotion`
  y `emotion_would_win` (ya implementa esa semántica).
- `main.py` construye un `VisionHostAdapter` que delega en `self` (el `Controller`) y lo pasa a
  `vision.integration.attach(host)`.
- `Priority.EMOTION` (valor actual) se expone como constante `VisionHost.EMOTION_PRIORITY`
  para que `vision` no necesite importar `core.state`.

### 2.3 Interfaz `OCRPort` (en `contracts/ocr_port.py`, nuevo)

Contrato que **core** consume y **vision** implementa:

```python
class OCRPort(ABC):
    @abstractmethod
    def recognize(self, image, *, lang: str = "spa") -> str: ...
```

- `core/screen_ocr.py::OCREngineChain` → se convierte en el adaptador que implementa `OCRPort`
  (la clase que hoy es `vision.ocr.ocr_engine.OCREngineChain` pasa a instanciarse vía el port).
  A la inversa, `vision.ocr.ocr_engine` deja de importar `core.screen_text` (usa el port o mueve
  el texto a un helper interno puro).
- `main.py` registra la implementación real en la composición; `screen_ocr` la recibe por inyección.

### 2.4 Cómo se verifica el rompimiento (test de arquitectura)

`tests/test_arch_dependencies.py` (nuevo, PR 3) ejecuta:

```python
## Test verificable
# - importar `vision.integration`, `vision.controller`, `vision.ocr.ocr_engine`: assert de que
#   `sys.modules` NO contiene ninguna clave que empiece por "core." ni "main.".
# - importar `core.screen_ocr`, `core.pc_control`, `core.listener`, `core.screen_vision`:
#   assert de que `sys.modules` NO contiene "vision.", "teacher.", "voice_flow.".
# Validador: la suite de pytest (comando: `python -m pytest tests/test_arch_dependencies.py -v`).
# Momento: corre en cada PR de refactor (gate de CI desde el PR 3 en adelante).
```

---

## 3. CAPA DE ABSTRACCIÓN DE PLATAFORMA

**Decisión cerrada:** interfaz `PlatformController` en `platform/contracts.py`; implementación
Windows real en `platform/windows/` (código actual movido sin cambios de lógica); punto de
extensión documentado para Linux con `platform/linux/` (stub `NotImplementedError`).

### 3.1 Interfaz `PlatformController` (ABC)

Métodos extraídos de las funciones que la auditoría marca como Windows. Firmas cerradas:

```python
class PlatformController(ABC):
    # — Ventanas / UI real (origen: core/desktop_ui.py) —
    def is_available(self) -> bool: ...                    # pywinauto presente
    def list_windows(self, limit: int = 25) -> list[dict]: ...
    def active_window_title(self) -> str: ...
    def focus_window(self, title: str) -> str: ...
    def close_window(self, title: str) -> str: ...
    def active_window_elements(self, limit: int = 40, use_cache: bool = True) -> list[dict]: ...
    def find_element_center(self, desc, threshold: float = 0.75) -> tuple | None: ...
    def element_has_focus(self, name: str, threshold: float = 0.75) -> bool: ...
    def set_click_through(self, activar: bool) -> int: ...
    def own_hwnds(self) -> list[int]: ...
    def target_hwnd(self) -> int: ...
    def window_snapshot(self, title: str = "") -> dict | None: ...
    def windows_snapshot(self, limit: int = 40) -> list[dict]: ...
    def move_window(self, title, x, y, width, height) -> str: ...
    def restore_window_snapshot(self, snapshot) -> str: ...
    def reopen_window_snapshot(self, snapshot, timeout: float = 6.0) -> str: ...

    # — App en primer plano / proceso (origen: media_companion + screen_observation) —
    def foreground_app(self) -> tuple[str, str]: ...      # (título, nombre_proceso)
    def media_pick_process(self) -> str: ...              # proceso activo para fuente de media

    # — Office/COM (origen: core/office_control.py) —
    def office_available(self) -> bool: ...
    def office_open(self, app: str, path) -> object: ...   # delega en OfficeController
    def office_read(self, app: str) -> object: ...
    def office_save(self, app: str, path) -> object: ...
    def office_write(self, app: str, text) -> object: ...

    # — Proceso/app (origen: core/app_catalog.py y main.py) —
    def launch_app(self, caminos, args: Sequence[str]) -> str: ...
    def set_dpi_awareness(self) -> bool: ...              # desde main.py:7
```

Se respeta el **degradado elegante**: una implementación que no soporte una operación devuelve
`None`/`[]`/bool-`False` (igual que hoy), nunca lanza. `LinuxController` (stub) hereda el default
degradado y **solo levanta `NotImplementedError` si alguien intenta usarlo en producción**
(movido a `platform/linux/__init__.py` con mensaje: `"YUE: control de plataforma Linux no implementado aún."`).

### 3.2 Asignación concreta de dónde va cada código actual

| Código actual | Va a | Extra |
|---------------|------|-------|
| `core/desktop_ui.py` (todo, ~796 líneas) | `platform/windows/desktop_ui.py` | En PR2 se crea la copia nueva y `core/desktop_ui.py` queda como **re-export fino temporal** (`from platform.windows.desktop_ui import *`) para no romper `core/screen_vision.py:188`, `tests/test_mejoras.py:153`, `tests/diagnostico_pc.py`, `tests/test_jarvis_control.py:19`, `tests/test_ordenes_dificiles.py:175`; los consumidores se migran a `PlatformController` y el re-export se borra en PR6. |
| `core/office_control.py` (clase `OfficeController`, errores) | `platform/windows/office.py` (misma clase, mismo API público) | `core` deja de importarlo; `main.py`/`engine` inyectan. |
| `detect_media_source()` + helpers win32 de `core/media_companion.py` | `platform/windows/media_source.py` | La parte psutil/now_playing cross-platform se queda en `core/media_companion.py`; solo ventana/proceso va a `platform`. |
| Parte win32 de `core/screen_observation.py` (líneas 208-212) | `platform/windows/windows_utils.py` | `screen_observation` usa `PlatformController.foreground_app()`. |
| `main.py::enable_dpi_awareness` (líneas 7-25) | `platform/windows/dpi.py` | `main()` llama `get_platform_controller().set_dpi_awareness()`. |
| `core/app_catalog.py` (lanzar/registrar, `os.startfile`) | implementado sobre `PlatformController.launch_app` | `AppCatalog` recibe el controlador inyectado. |

### 3.3 Fábrica y selección

`platform/__init__.py`:

```python
def get_platform_controller() -> PlatformController:
    if platform.system().lower() == "windows":
        return WindowsPlatformController()          # platform/windows/__init__.py
    if platform.system().lower() == "linux":
        return LinuxController()                    # stub, degradado (sin lanzar)
    return NullPlatformController()                 # degradado genérico para el resto
```

Con esto, **todas** las ramas `if sys.platform == ...` que la auditoría listó en §3 se centralizan:
`main.py:9`, `media_companion.py:527`, `screen_observation.py:208`, `app_catalog.py:60,81,149`,
`office_control.py:57,75`, `system_audio.py:621`, `pc_control.py:541,2119`, `desktop_ui.py:80`.
`core/system_audio.py` y `core/screen_observation.py` consultan `platform_controller.is_available()`
en vez de comparar SO a mano.

**Nota para ocultar el placeholder:** `platform/linux/` queda como stub *deliberado*, documentado
en la docstring del módulo como "extensión futura recomendada" — no como dependencia faltante.

---

## 4. DESGLOSE DE `Controller` (main.py, ~3800 líneas)

**Decisión cerrada:** `Controller` se reduce a *composición de 9 directores* (en `engine/`) +
handlers de eventos Qt breves. `main()` queda solo como bootstrap.

### 4.1 Responsabilidades extraídas y destino

| # | Responsabilidad (métodos actuales) | Módulo nuevo | Razones |
|---|------------------------------------|--------------|---------|
| 1 | **Conversación** `on_user_message`, `_on_ai_done/_on_ai_failed`, `_greet`, `_system_prompt`, `_handle_command` (routing) | `engine/dialogue.py` · `DiálogoDirector` | Núcleo del bucle conversacional, bien delimitado |
| 2 | **Voz** `_toggle_voice`, `_toggle_mic`, `_diagnose_voice`, `_build_wake_re`, `_strip_wake_word`, `_wake_ignored`, `_on_heard`, `_on_text_message`, `_on_barge_in`, `_on_mic_status` | `engine/voice_director.py` · `VoiceDirector` | Todo micrófono/wake-word en una sola pieza |
| 3 | **Memoria proactiva** `_episodic_observe`, `_story_observe`, `_episodic_followup`, `_maybe_checkin`, `_episodic_/_story_note_response`, `_schedule_memory_consolidation`, `_external_mood_signal`, `_should_check_visual_risk`, `_wellbeing_nudge_due`, `_start/_resolve_mood_checkin`, `_yue_may_take_initiative`, `_diagnose_memory` | `engine/memory_proactive.py` · `MemoryProactive` | Iniciativas/follow-ups/check-ins = una responsabilidad |
| 4 | **Teacher / modos** `_setup_modes`, `_register_modes`, `_on_mode_event`, `_propose_teacher_mode`, `_apply_mode_switch`, `_teacher_process`, `_on_files_dropped`, `_teacher_*` (guided pdf, explain, next/youtube page, autoadvance, assistant, specialize) | `engine/teacher_director.py` · `TeacherDirector` | Todo el subdominio profesor completo |
| 5 | **Visión/pantalla** `_glance`, `_on_screen_observed`, `_on_vision_done/_failed`, `_diagnose_vision`, `_on_vision_diag`, `_vision_preflight`, `_on_vision_preflight`, `_on_vision_status`, `_on_camera_status`, `_on_camera_observation`, `_log_camera_mood`, `_maybe_camera_emotion_checkin`, `_deliver_emotion_checkin`, `_on_emotion_talk_done`, `vision_state`, `vision_resumen` | `engine/vision_director.py` · `VisionDirector` | Vision + cámara + emoción facial |
| 6 | **Media/audio** `_on_media_heard`, `_recent_lyrics`, `_describe_audio`, `_on_audio_status`, `_on_audio_reaction`, `_media_companion_context`, `_on_media_state`, `_on_media_companion_status`, `_on_media_companion_avatar`, `_on_media_companion_reaction` | `engine/media_director.py` · `MediaDirector` | Comportamiento ante música/vídeo |
| 7 | **Control de PC** `_run_pc_order`, `_on_pc_done/_failed`, `_undo_last_pc`, `_repeat_last_pc`, `_on_undo/_repeat_done`, `_on_recovery_failed`, `_save_routine`, `_run_routine`, `_list_routines`, `_show_activity`, `_pc_confirm_by_voice`, `_do_confirm_ask`, `_resolve_pc_confirmation`, `_parse_yes_no`, `_on_pc_action_log` | `engine/pc_director.py` · `PCDirector` | Línea completa de acciones de escritorio + confirmación por voz |
| 8 | **Autonomía** `_toggle_autonomy`, `_autonomous_create`, `_on_autonomy_done`, `_on_autonomy_failed`, `_run_internal_action`, `_set_head_control` | `engine/autonomy_director.py` · `AutonomyDirector` | Creación autónoma + acciones internas |
| 9 | **Emoción/avatar/estado** `_set_avatar_emotion`, `_publish_companion_state`, `_state_tick`, `_sync_system_state`, `_refresh_bond`, `_update_mode_indicator`, `debug_state` | `engine/emotion_orchestrator.py` · `EmotionOrchestrator` | Estado del companion |

**Infraestructura compartida — `_track_worker` (decisión cerrada):** `_track_worker`
(main.py:4277) es infraestructura pura y compartida: registra el worker, lo lanza y lo
desregistra al terminar (`worker.finished → remove`). La usan ~10 call sites repartidos entre
varios de los futuros directores (voz, memoria, teacher, visión, media, PC, autonomía) **antes**
de que exista `EmotionOrchestrator`, por lo que **NO puede extraerse en el paso 8**.

→ **Decisión: se extrae ANTES de cualquier director, como utilidad base compartida**
`engine/worker_registry.py::WorkerRegistry` (nuevo paso 1 de §4.2), y se distribuye a todos
los directores vía `controller_ctx.workers.track(worker)`. Se descarta la alternativa de que
cada director reciba una referencia al `Controller`/host para llamarlo mientras sigue ahí:
eso violaría el invariante "los directores no conocen al `Controller`" y dejaría una deuda
técnica que habría que volver a tocar en el paso final. Con `WorkerRegistry` por delante, cada
paso de extracción es verificable de forma independiente (los workers funcionan igual antes y
después) y `EmotionOrchestrator` queda sin carga de workers.

**Acceso cross-director — `_yue_may_take_initiative` y `_should_check_visual_risk` (decisión cerrada):**
estos dos métodos de la fila 3 se extraen **completos con `MemoryProactive`** (paso 3 de §4.2),
no se delegan. Su dependencia real (verificado en código, main.py:1385 y main.py:1501) NO es
lógica de `AutonomyDirector`/`VisionDirector` — es **estado que hoy cuelga de `Controller`** y
servicios de `core`:

| Dependencia de lectura | Estado actual | Vía de acceso en `MemoryProactive` |
|------------------------|---------------|-----------------------------------|
| `self.state_manager` (`yue_state().initiative`, `INITIATIVE_LEVELS`) | atributo de `Controller` | `controller_ctx.state_manager` (ya existe en `ctx`) |
| `self.camera` (en `safety.detect_risk_from_camera(self.camera, self.memory)`) | atributo de `Controller` creado en `__init__` | `controller_ctx.camera` — registro mutable: lo publica `Controller` al arrancar y, desde el paso 7, lo publica `VisionDirector` (quien pasa a poseer el ciclo de vida de la cámara). `MemoryProactive` nunca cambia |
| `self._last_visual_risk_ask` (cooldown, mutable) | atributo de `Controller` | `controller_ctx.last_visual_risk_ask` (mutable, con su `get`/`set`) |
| `self.memory`, `core/safety.py`, `config.CAMERA_RISK_ASK_COOLDOWN` | atributo/servicios de `core` | inyectados igual que hoy (ctx.memory + imports de core permitidos) |

→ **Mecanismo único: `controller_ctx` como service-locator compartido (mismo patrón que
`WorkerRegistry`, NO delegación directa al `Controller`).** Regla: ningún director recibe
referencia al `Controller`; todo dato que otra extracción posee se publica/lee en `ctx`.
Esto elimina la alternativa "delegación temporal directa" por la misma razón que en
`_track_worker`: evita deuda que habría que revertir en el paso final. Desde el paso 2,
`MemoryProactive` es autocontenido: funciona igual aunque `VisionDirector` (paso 7) y
`EmotionOrchestrator` (paso 9) aún no existan.

**Lo que NO se mueve de `Controller` (quedan como cables/adhesivos):** `__init__` (ensambla
directores), `toggle_chat`, `_user_float`, `_interrupt_response`, `_yue_say`, `shutdown`, y todos
los handlers `_on_*` de señales Qt que solo reenvían a un director. **Regla:** todo método que
quede en `Controller` debe caber en ≤5 líneas (delegación pura) o se considera no extraído.

### 4.2 Orden de extracción (para verificar tras CADA paso, no al final)

Se sigue el orden de **menor a mayor riesgo de regresión**, y cada paso termina con
`python -m pytest -q` + arranque manual (`python main.py` → chat de texto → comando de voz):
verde obligatorio antes de pasar al siguiente.

| Paso | Extracción | Verificación específica (además de suite + arranque) |
|------|-----------|------------------------------------------------------|
| 1 | `WorkerRegistry` (utilidad base, ex `_track_worker`) + `controller_ctx.workers` | Suite + arranque; enviar 1 mensaje de chat y esperar 2 s: el worker de IA se registra y se desregistra al terminar (assert debug de `registry.size() == 0`); cerrar la app sin hilos huérfanos ("killed threads" ausente). |
| 2 | `VoiceDirector` (voz/wake-word/mic) | Probar: "oye Yue, qué hora es" por micrófono; des/toggle voz; `_diagnose_voice`. Sin cambio en conversacion |
| 3 | `MemoryProactive` (observar episodio/historia, follow-ups, check-in) | Probar: mencionar un evento con fecha → confirmar follow-up agendado; `_diagnose_memory`. |
| 4 | `PCDirector` (órdenes/rutinas/confirmación) | Probar: comando de PC real (abrir app), confirmación por voz, undo/repeat, `_list_routines`. |
| 5 | `MediaDirector` (audio/música/letras) | Probar: reproducir audio → detectar fuente, reaccionar, letras. |
| 6 | `TeacherDirector` (modos/PDF/autoadvance) | Probar: arrastrar PDF, página siguiente, modo profesor, autoadvance. |
| 7 | `VisionDirector` (glance/cámara/emoción) | Probar con cámara apagada (degradado) y con cámara encendida: `_glance`, estado, emoción facial. |
| 8 | `AutonomyDirector` (autonomía) | Probar: toggle autonomía, creación autónoma con confirmación. |
| 9 | `EmotionOrchestrator` (avatar/estado) | Probar: estados del companion, `debug_state`, mover/animar avatar. |
| 10 | `DiálogoDirector` (conversación) | Probar: flujo completo de chat (texto, IA, error de IA → fallback). |
| 11 | Adelgazar `__init__`/`main()` a wiring puro. | Suite completa + arranque con y sin venv del sistema. |

**Nota de consistencia:** los pasos 2-8 usan `ctx.workers.track(worker)` (del paso 1) para todo
lanzamiento de workers; ningún director llama a `_track_worker` del `Controller` ni conoce al host.

*Justificación del orden:* la conversación (`DiálogoDirector`) es el camino más ejercitado y con más
side-effects (AI, memoria, voz); se deja al final porque tocar su wiring mientras otros directores
siguen dentro de `Controller` duplicaría el riesgo. Voz/memoria/PC son aditivos y fáciles de aislar
primero.

---

## 5. ORDEN DE EJECUCIÓN (PRs/commits)

Secuencia de PRs, **de lo más aislado a lo más riesgoso**, cada uno verificable de forma
independiente. Los PRs 1-4 preparan el terreno; el 5 es el desglose de `Controller`.

### PR 1 — Base de tests (sin tocar producción)
Contenido: smoke tests de §6 + `tests/test_arch_dependencies.py` (aunque aún falla, se deja
apagado o con `pytest.mark.skip`) + correr la suite actual para fijar estado verde/baseline.
- **Verificar:** `python -m pytest -q` → mismas fallas/passes que antes del PR; los smoke tests nuevos verdes.
- **Manual:** nada (no toca producción).

**ESTADO EJECUTADO (baseline fijado, branch `refactor/arquitectura`):**
- La suite NO podía correr bajo pytest antes de este PR: 9 archivos script-style con
  `sys.exit`/`raise SystemExit` a nivel de módulo rompían la colección (INTERNALERROR).
  Se excluyeron con `collect_ignore` en `tests/conftest.py` (documentado ahí mismo); siguen
  corriendo directos: `python tests/<archivo>.py` → todos "TODO OK":
  `test_documento_office`, `test_emociones_av`, `test_jarvis_control`, `test_mejoras`,
  `test_memory_consolidation`, `test_now_playing`, `test_ordenes_dificiles`,
  `test_seguridad_control`, `test_story_memory`.
- **Baseline `python -m pytest -q`: 596 passed · 7 failed · 2 skipped · 39 warnings (11 s).**
  Los 7 fallos son PRE-EXISTENTES (sin relación con el refactor): `test_affect_eval` × 5
  (falta el corpus `tests/eval/afecto_eval.jsonl`), `test_screen_vision::test_extra_...`
  (config de proveedor ausente) y `test_vision_avanzada::TestPlanificador::test_worker_...`
  (comportamiento de atributo `_stop` en QThread).
- Gate de arquitectura `test_arch_dependencies.py` creado con `pytest.mark.skip` hasta el PR 3.
- Smoke tests de §6 creados y VERDES (22 passed · 2 skipped): `test_desktop_ui_smoke`,
  `test_office_smoke`, `test_pc_control_smoke`, `test_camera_observer_smoke`,
  `test_system_audio_smoke`, `test_voice_listener_smoke`.
- Para ejecutar el gate del PR en adelante: `python -m pytest -q`.

### PR 2 — Abstracción de plataforma (mover a `platform/`, D2/D3)
Contenido: crear `platform/` (fábrica + `win32` + stub linux), mover `desktop_ui`, `office`,
`media_source`, `dpi`; re-escribir los `import desktop_ui`/`office_control` como inyección en
`pc_control`/`app_catalog`/`main` con `get_platform_controller()`. **Todo degradado intacto.**
- **Verificar:** `python -m pytest -q` (verde, sin tests nuevos) + `python main.py` en Windows:
  listar/enfocar/cerrar ventana, office open/save/read, detectar fuente media, click-through.
  En Linux: arranca, `is_available()` False, no lanza.

### PR 3 — Romper ciclo `core ↔ vision` (D1)
Contenido: crear `contracts/` (`VisionHost`, `OCRPort`), adaptar `vision/integration.py` a
`attach(host)`, `screen_ocr` a `OCRPort`, subir `tests/test_arch_dependencies.py` (ya pasa).
- **Verificar:** `python -m pytest tests/test_arch_dependencies.py -v` (verde) +
  `python -m pytest -q` + arranque con visión activa y con visión apagada.

### PR 4 — Extender la arquitectura-emoción a host (pegado con PR 3)
Contenido: `VisionHostAdapter` en `main.py`; `YueStateManager` provee `request_emotion/would_win`;
`main.py` deja de importar `core.state.Priority` en `vision` (se usa `VisionHost.EMOTION_PRIORITY`).
- **Verificar:** mismo set que PR 3 + prueba manual de emoción por rostro con cámara.

### PR 5 — Desglose de `Controller` (D4) — 11 commits, uno por paso de §4.2
Contenido: extraer en el orden de §4.2 (commits 5.1…5.11; cada commit = 1 paso).
- **Verificar por commit:** `python -m pytest -q` verde + prueba manual listada en §4.2 para ese paso.
- **Gate final del PR:** suite completa verde + `main.py` arranca con y sin cámara/mic/audio +
  cada director se puede instanciar aislado (test de humo `assert director is not None`).

### PR 6 — Limpieza post-refactor (fuera de alcance de decisión, citado)
Contenido: borrar `core/desktop_ui.py` ya vacío de referencias, docstrings nuevos en `contracts/`,
actualizar `AUDITORIA_ARQUITECTURA.md` (sección "acoplamientos") y este spec a "implementado".
- **Verificar:** suite + ejecución manual de arranque + `git grep desktop_ui` devuelve solo `platform/`.

> **Rollback:** si un PR rompe la suite, revertir ese PR únicamente (los anteriores son ortogonales).

---

## 6. TESTS FALTANTES (smoke ANTES de tocar cada módulo)

Smoke tests deterministas (sin hardware real, con dobles) para los 6 módulos de la auditoría §5.
Todos **verdes desde el PR 1**, antes de cualquier refactor de esos módulos.

| Módulo | Smoke mínimo (archivo propuesto) | Qué prueba (sin tocar hardware) |
|--------|----------------------------------|--------------------------------|
| `platform/windows/desktop_ui.py` (antes `core/desktop_ui.py`) | `tests/test_desktop_ui_smoke.py` | `norm()`/`similarity()` (funciones puras) con casos borde (tildes, espacios); `is_available()` → False cuando `pywinauto` ausente (monkeypatch de `sys.modules`); `list_windows()` devuelve `[]`/fallback sin lanzar; con `Desktop` trueado inyectado, `active_window_title()` devuelve el título fake. |
| `platform/windows/office.py` (antes `core/office_control.py`) | `tests/test_office_smoke.py` | `available()` → False en no-Windows sin importar COM (assert `win32com` NO está en `sys.modules`); `create_document("word")` lanza `OfficeUnavailable` con mensaje legible; ninguno de los métodos importa `win32com.client` en la llamada. |
| `core/pc_control.py::PCController` | `tests/test_pc_control_smoke.py` | Instanciar con `PlatformController` fake + `pyautogui` monkeypatcheado; `looks_like_pc_command("abre chrome")` == True y `looks_like_pc_command("hola")` == False; ejecutar un paso **sin hardware** (p. ej. `type` con clipboard fake) devuelve `ActionResult(success=True)`; si `platform_controller.office_available()` es False, `office_open` degrada sin lanzar. |
| `core/camera_observer.py::CameraObserver` | `tests/test_camera_observer_smoke.py` | Construir con `cv2.VideoCapture` falso (`read()` → `(True, zeros)`); 1 tick del lazo produce un `CameraObservation` neutro y no excepción; si `mediapipe` ausente (`sys.modules` monkeypatch), el lazo no se rompe (degradado). |
| `core/system_audio.py` | `tests/test_system_audio_smoke.py` | Funciones puras sin hardware: `analyze_block()` sobre un array sintético devuelve dict de características con las claves esperadas; `classify(feats)` devuelve `(etiqueta, bool)` con ruido blanco → silencio/voz; `decide_reaction()` con `ReactorState` vacío no lanza y devuelve `None`/neutral. |
| `core/listener.py::VoiceListener` | `tests/test_voice_listener_smoke.py` | Inicializar sin micrófono (fake `Microphone` que lanza); `list_microphones()` devuelve `[]` sin lanzar; `_norm()` y wake-word regular code puro (**no probar captura real**): `_strip_wake_word("oye yue abre chrome")` → `"abre chrome"`. |

**Regla de viabilidad:** cada smoke usa `monkeypatch`/dobles de `sys.modules`; **no abre cámara,
micrófono, PyAutoGUI ni COM** en CI. Si una prueba intenta tocar hardware, se marca
`@pytest.mark.hw` y se excluye del runner por defecto.

---

## Cierre

Las 6 decisiones están cerradas (tabla de "Decisiones tomadas", §0). Los PRs 1-6 son la única
forma de realizar este spec: cada uno tiene verificación independiente explícita. Si durante la
implementación surge algo que contradiga una decisión ya tomada, se **señala el conflicto en este
documento** (al no sobrescribirlo en silencio) antes de cambiarlo.