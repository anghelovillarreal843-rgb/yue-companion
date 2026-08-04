# YUE · Arquitectura de Modos Inteligentes (Mode Manager + Modo Profesora)

Todo lo de este paquete es **ADITIVO**: no se borró ni se cambió ninguna función
existente. La personalidad e identidad de YUE y su memoria emocional quedan
**intactas**. Solo se añadió una capa que decide *qué motor conversa*.

---

## 1. Qué se añadió

### Nuevo paquete `mode_manager/` (genérico y desacoplado, sin Qt ni IA)
- `mode_state.py` — custodia el modo actual (arranca SIEMPRE en `companion`).
- `mode_commands.py` — detección de intención (activar/desactivar) por **voz o
  texto**, robusta a variantes: normaliza acentos, busca frases/paralabras clave
  y usa parecido difuso. No depende de frases exactas.
- `mode_router.py` — registro de modos y despacho central (`ModeRouter`).
- `mode_events.py` — bus de eventos (observador) para avisar de cambios de modo a
  la interfaz, la voz o el avatar sin acoplarlos.
- `mode_manager.py` — fachada `ModeManager` + `ModeSpec` (describe un modo) +
  `RouteResult`. Une estado, comandos, router y eventos.

### Nuevo paquete `teacher/` (Modo Profesora)
- `teacher_engine.py` — `TeacherEngine`. Mantiene su **propia** conversación de
  clase (separada de la memoria emocional). Arma el prompt de profesora
  reutilizando la identidad de YUE pero con libertad para explicar y preguntar.
  Extrae una evaluación oculta del modelo, la registra y limpia el texto visible.
- `progress.py` — `ProgressStore`: guarda preguntas, aciertos, puntajes y temas en
  JSON propio; exporta reportes a **Markdown, CSV y Excel** (Excel si hay
  `openpyxl`; si no, cae a CSV, que Excel abre igual).
- `documents.py` — lectores **degradables** de PDF, Word, PowerPoint, EPUB, OCR de
  imágenes y páginas web. Si falta una librería no rompe: avisa qué instalar.

### Integración mínima en el proyecto (aditiva)
- `main.py`:
  - Se registra el sistema de modos en el arranque (`_setup_modes` / `_register_modes`).
  - En `on_user_message` (el **punto único** de entrada) se insertan dos bloques:
    detección de cambio de modo y enrutado al motor del modo activo. El flujo de
    la compañera queda **igual** debajo.
  - Métodos nuevos: `_apply_mode_switch`, `_on_mode_event`, `_update_mode_indicator`,
    `_teacher_process`, `_on_teacher_done`.
  - Comandos nuevos: `/modo` (consulta o cambia de modo) y `/reporte` (exporta el
    progreso).
- `ui/foot_chat.py`: indicador visual de modo (`🤍 Compañera` / `📚 Profesora`) y
  método `set_mode()`.

---

## 2. Cómo se usa

**Activar el Modo Profesora** (voz o texto), por ejemplo:
> «Yue, activa el modo profesora» · «quiero una clase» · «enséñame» ·
> «actúa como profesora» · «modo enseñanza»

YUE responde con el mensaje de bienvenida y el indicador cambia a `📚 Profesora`.
A partir de ahí, **todo** lo que digas se interpreta como clase: explica, pregunta,
evalúa tus respuestas y registra tu progreso. Puedes pasarle un documento o una URL
en el mensaje y lo leerá como material.

**Salir** (voz o texto):
> «Yue, salir del modo profesora» · «terminar clase» · «modo normal» ·
> «regresa al modo compañera»

YUE guarda el progreso, avisa y vuelve a ser tu compañera (`🤍 Compañera`).

Comandos útiles: `/modo` (ver o cambiar modo), `/reporte md|csv|xlsx` (exportar
el progreso de la clase).

---

## 3. Garantías de seguridad (lo que NO se toca)
- La **memoria emocional** (`core/memory.py`) no se usa ni se modifica en la clase:
  el Teacher Mode lleva su propia transcripción y su propio almacén de progreso.
- La **personalidad principal** y el flujo de la compañera quedan intactos: YUE solo
  cambia su comportamiento conversacional mientras el modo está activo.
- El modo **persiste** durante toda la sesión y solo cambia por orden del usuario;
  nunca sale solo del Modo Profesora.
- YUE **siempre arranca** en modo compañera.

---

## 4. Cómo agregar un modo nuevo (escalabilidad)
No hay que tocar la lógica de detección ni de despacho. Basta con registrar un
`ModeSpec` con su `id`, sus frases de activación/desactivación, sus mensajes y su
`handler`:

```python
self.modes.register_mode(ModeSpec(
    id="programador", label="Programador", emoji="👩‍💻",
    activation_phrases=("modo programador", "ayúdame a programar", "modo código"),
    activation_keywords=(("modo", "programador"), ("modo", "codigo")),
    deactivation_phrases=("salir del modo programador", "modo normal"),
    activation_message="👩‍💻 Modo Programador activado. …",
    deactivation_message="Volviendo a compañera. …",
    handler=self._programador_process,   # tu motor
))
```

Así quedan preparados futuros modos (Investigador, Traductor, Médico, Tutor, etc.).

---

## 5. Librerías opcionales para lectura de material
La clase funciona sin ellas; si quieres leer cada formato:
- PDF: `pip install pdfplumber` (o `pypdf`)
- Word: `pip install python-docx`
- PowerPoint: `pip install python-pptx`
- EPUB: `pip install EbookLib` (hay respaldo por ZIP si falta)
- OCR de imágenes: `pip install pytesseract` + Tesseract (o el OCR propio de YUE)
- Excel en reportes: `pip install openpyxl` (si falta, se exporta CSV)
- Web: no requiere nada extra.
