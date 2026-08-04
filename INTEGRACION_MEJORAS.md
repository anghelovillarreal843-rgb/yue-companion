# Cómo conectar las mejoras (sin romper nada)

Todo lo nuevo es **aditivo**: puedes adoptarlo poco a poco. Nada de lo que ya
funciona deja de funcionar si no tocas los módulos actuales. Aquí van los
enganches recomendados, del más seguro al más profundo.

## 1. Seguridad (hazlo ya, no requiere tocar código)

```bash
python -m tools.secret_scanner      # busca claves reales (masca los valores)
python -m tools.clean_project       # SIMULA la limpieza (no borra)
python -m tools.build_release       # ZIP limpio y verificado en dist/
```

Lee `SEGURIDAD_CLAVES.md` para saber qué claves revocar.

## 2. Gestor de estado del avatar (arregla las órdenes contradictorias)

Hoy `personality`, `music_emotion`, `camera_observer`, `media_companion` y el
modo profesora pueden mandarle animaciones al avatar a la vez. Para que dejen de
pelearse, crea **un** `YueStateManager` en el `Controller` y haz que las
animaciones pasen por él.

En `main.py`, dentro de `Controller.__init__` (al final, junto al resto de
componentes):

```python
from core.state import YueStateManager, ResourceManager, Priority

self.state = YueStateManager()
self.resources = ResourceManager(bus=self.state.bus)

# Cuando cambie el estado, refleja el avatar en la UI existente.
self.state.subscribe(lambda st: self.pet.set_pose(st.avatar_state)
                     if hasattr(self.pet, "set_pose") else None)

# Relaja animaciones caducadas (usa un QTimer que ya tienes para el idle).
# self._state_timer = QTimer(); self._state_timer.timeout.connect(self.state.tick)
# self._state_timer.start(500)
```

Y en los sitios que hoy animan el avatar, en vez de forzar la animación, pídela
con su prioridad:

```python
# Música (baja prioridad): solo baila si nadie más manda.
self.state.request_avatar("bailar", Priority.MEDIA, source="musica", ttl=4)

# Conversación (más prioridad): al hablar, gana sobre la música.
self.state.request_avatar("hablar", Priority.CONVERSATION, source="chat")

# Modo profesora (aún más): al explicar, ignora música y reacciones.
self.state.request_avatar("explicar", Priority.TEACHER, source="profesora")
```

La emoción se fija igual de fácil y, si nadie retiene el avatar, se sincroniza
con ella:

```python
self.state.set_emotion("feliz", intensity=0.8, source="conversacion")
```

> No hace falta migrar todos los módulos de golpe. Empieza por música y
> conversación (son los que más chocan) y el resto puede seguir como está.

## 3. Un solo dueño para la cámara (fin del choque clásico vs Vision V3)

Antes de abrir la webcam, pide el recurso. Si otro módulo con más prioridad la
necesita, te avisan por evento para soltarla:

```python
lease = self.resources.acquire(Resource.CAMERA, "vision_v3", priority=60)
if lease:
    try:
        ... # abrir y usar cv2.VideoCapture
    finally:
        self.resources.release(lease)
else:
    # la cámara ya la tiene otro módulo; no la abras en paralelo
    pass
```

Mismo patrón para `Resource.MICROPHONE` (voz vs. escucha), `Resource.SPEAKERS`
(voz vs. música) y `Resource.SCREEN`.

## 4. Memoria 2.0 (conversaciones, búsqueda por significado, backups)

`core/memory_ext.py` usa **la misma** `data/yue.db`; solo añade tablas nuevas.
Convive con `core/memory.py` sin conflicto.

```python
from core.memory_ext import MemoryExtension

mem2 = MemoryExtension(config.APP_DIR / "data" / "yue.db")

# Al empezar una charla:
conv = mem2.start_conversation(mode="companera", device="pc")

# Cada mensaje (guarda TODO el diálogo, también en modo profesora):
mem2.add_message(conv, "user", texto_usuario, source="voz", emotion="feliz")
mem2.add_message(conv, "assistant", respuesta_yue)

# Al cerrar:
mem2.end_conversation(conv, summary=resumen, emotion="feliz")

# Que YUE recuerde algo por significado (sin internet):
r = mem2.remember("cuando te hablé de mi gato")
if r:
    print(r["frase"], "->", r["texto"])   # «Me hablaste de esto el 2026-07-20 -> ...»

# Copia de seguridad diaria + verificación de integridad:
info = mem2.backup(config.APP_DIR / "data" / "backups")
```

### Backup automático diario (opcional, 4 líneas)

```python
from datetime import date
if mem2.get_state if False else True:  # engánchalo a tu arranque
    ultimo = (config.APP_DIR / "data" / "backups" / f"day_{date.today()}.flag")
    if not ultimo.exists():
        mem2.backup(config.APP_DIR / "data" / "backups")
        ultimo.write_text("ok")
```

## 5. Pruebas

```bash
python -m pytest tests/              # todo junto
python tests/test_state_manager.py   # solo el gestor de estado
python tests/test_memory_ext.py      # solo la memoria 2.0
```

## 6. Seguridad emocional híbrida (FASE 5)

`core/safety_ext.py` envuelve y mejora `core/safety.py`: en vez de un sí/no,
clasifica el riesgo en NINGUNO/LEVE/MODERADO/ALTO/CRÍTICO, baja falsos positivos
(negación, contexto de ficción) y sube con señal sostenida de cámara/ánimo.

En `Controller.on_user_message`, antes de armar el prompt:

```python
from core import safety_ext

info = safety_ext.assess(texto_usuario,
                         external_signal=self._malestar_sostenido())  # opcional
if info["level"] >= safety_ext.RiskLevel.MODERADO:
    directiva = safety_ext.directive_for(info["level"], resources=config.CRISIS_RESOURCES)
    # pásala a personality.build_system_prompt(..., safety_directive=directiva)
if safety_ext.should_escalate_ui(info["level"]):
    ...  # muestra los recursos de ayuda de forma visible
```

La directiva NUNCA incluye métodos; solo enruta a contención cálida y ayuda.

## 7. Rasgos de personalidad ajustables (FASE 5)

`core/personality_traits.py` da perillas 0..1 (sarcasmo, cariño, humor…) que se
convierten en un fragmento para el prompt. Se anexa a lo que ya construye
`personality.build_system_prompt`:

```python
from core.personality_traits import TraitProfile

self.traits = TraitProfile(config.APP_DIR / "data" / "traits.json")
base = personality.build_system_prompt(bond, facts, goals, safety_directive=dir)
system = base + "\n\n" + self.traits.to_prompt_fragment()

# Ajuste por voz: "sé más cariñosa" -> self.traits.nudge("carino", +0.15)
```

Los rasgos afectan el tono, nunca la seguridad: en crisis, YUE deja el sarcasmo
aunque esté al máximo (el propio fragmento lo recuerda).

## 8. Lip-sync por visemas (FASE 4)

`core/voice_ext/` da FORMA a la boca (vocales/consonantes), no solo apertura por
volumen. Al reproducir una frase con TTS:

```python
from core.voice_ext import merge_shape_with_envelope

# envelope = tu envolvente RMS del audio (la que ya calculas para el lip-sync)
frames = merge_shape_with_envelope(texto, envelope, fps=30)
# cada frame: {t, viseme, openness} -> manda 'viseme' y 'openness' al avatar VRM
```

Si no tienes el audio a mano, `text_to_visemes(texto)` da la secuencia de formas
con duración estimada.

## 9. Panel de privacidad (FASE 4)

`core/privacy.py` centraliza el consentimiento y el registro de sensores. Antes
de abrir cámara/micro/pantalla:

```python
from core.privacy import PrivacyManager, Sensor

self.privacy = PrivacyManager(config.APP_DIR / "data" / "privacy.db")
if self.privacy.mark_active(Sensor.CAMERA, reason="orden /mira"):
    try:
        ...  # abrir cámara
    finally:
        self.privacy.mark_inactive(Sensor.CAMERA)
# self.privacy.set_privacy_mode(True) -> "cortina": apaga y bloquea todo
```

Solo guarda metadatos (cuándo se usó cada sensor), nunca el contenido.

## 10. Modo Profesora con RAG (FASE 6/7)

`core/teacher_ext/` recupera del documento del alumno para responder con base real:

```python
from core.teacher_ext import DocumentRAG

self.rag = DocumentRAG(config.APP_DIR / "data" / "teacher" / "rag.db")
self.rag.index_document("clase_hoy", texto_del_pdf, title="Biología")

ctx = self.rag.answer_context(pregunta_alumno, top_k=3)
if ctx["found"]:
    system += "\n\nMATERIAL:\n" + ctx["context"] + "\n" + ctx["instruction"]
else:
    system += "\n" + ctx["instruction"]  # avisa que no está en el documento
```

## 11. Autonomía supervisada (FASE 8)

`core/autonomy_ext/` revisa cada acción antes de tocar el PC:

```python
from core.autonomy_ext import AutonomySupervisor, Action

self.sup = AutonomySupervisor(protected=["fotos_familia"])
plan = [Action("abrir", "Word"), Action("borrar", "borrador.docx")]
r = self.sup.dry_run(plan)
if r["puede_automatico"]:
    ...  # ejecutar
else:
    for d in r["decisiones"]:
        if d["requires_confirmation"]:
            tok = self.sup.queue_for_confirmation(Action(**d["action"]))
            # pregunta al usuario; si dice sí: act = self.sup.approve(tok)
        elif d["blocked"]:
            ...  # nunca se ejecuta
```

Las 21 baterías (12 previas + 9 nuevas de FASES 1–8) pasan en verde.

## 12. Varias IAs con relevo automático (sesión actual)

`core/ai_router.py` prueba varios proveedores gratis en fila y salta al siguiente
sin pausa si uno falla. Para enchufarlo a `core/ai_engine.py`, en `AIEngine.chat`
prueba primero el router y deja el camino actual de Groq como último respaldo:

```python
from core import ai_router

# En AIEngine.__init__:
self._router = ai_router.build_default_router()   # lee las claves del .env

# En AIEngine.chat, al principio:
disp = self._router.available()
if disp:
    try:
        out = self._router.chat(messages, timeout=timeout, temperature=self.temperature)
        return out["text"]
    except ai_router.AllProvidersFailed:
        pass  # ninguno respondió; sigue con el camino Groq de siempre (abajo)
```

Solo pega en el `.env` las claves que tengas (`GEMINI_API_KEY`, `CEREBRAS_API_KEY`,
`OPENROUTER_API_KEY`…). Para ver el estado de la fila: `self._router.report()`.

## Arreglos de esta sesión (ya aplicados en tu código)

- **Palabra «Yue» desactivada**: `config.WAKE_WORD_ENABLED=false`. YUE responde sin
  el gatillo. Vuelve a `true` en el `.env` si lo quieres.
- **Menú del avatar → «👁 Ver mi pantalla ahora»**: nueva señal `look_screen` en
  `ui/desktop_pet.py`, conectada en `main.py` a `self._glance` (lo mismo que `/mira`).
- **Consola sin spam**: los avisos repetidos de estado están tras
  `config.DEBUG_STATUS=false`. Ponlo en `true` solo para depurar.

Las 23 baterías (14 previas + 9 nuevas de esta sesión) pasan en verde.

## 13. Generador de trabajos con diseño (Excel y PowerPoint)

`core/doc_builder/` hace tablas y presentaciones **con diseño y rápido**, sin abrir
Office por COM. La IA solo devuelve la estructura en JSON; el módulo construye el
archivo. Se enchufa cuando el usuario pide "hazme una tabla…" o "una presentación…":

```python
from core import doc_builder

# 1) Le pides a la IA SOLO la estructura (usa el router multi-IA que ya tienes):
brief = "una tabla de registro de notas para 3er grado, 4 cursos, 7 alumnos"
messages = [{"role": "system", "content": doc_builder.TABLE_JSON_INSTRUCTIONS},
            {"role": "user", "content": brief}]
json_texto = self.engine.chat(messages)          # el motor con relevo automático

# 2) El módulo construye el .xlsx con diseño (rápido, sin COM):
plan = doc_builder.parse_table_plan(json_texto)
ruta = doc_builder.build_table(plan, "salida/notas.xlsx")

# Para PowerPoint es igual con DECK_JSON_INSTRUCTIONS + parse_deck_plan + build_deck:
messages = [{"role": "system", "content": doc_builder.DECK_JSON_INSTRUCTIONS},
            {"role": "user", "content": "una presentación sobre el ciclo del agua"}]
deck = doc_builder.parse_deck_plan(self.engine.chat(messages))
ruta = doc_builder.build_deck(deck, "salida/ciclo_agua.pptx")
```

Paletas disponibles: `colegio`, `verde`, `morado_yue`, `corporativo`, `coral`
(elige con `"palette"` en el JSON). Convive con `office_control.py`: úsalo para
generar archivos con diseño de una vez; deja COM solo si necesitas manipular un
Office ya abierto.

Las 24 baterías (14 previas + 10 nuevas de esta sesión) pasan en verde.
