# YUE · Control de PC por elementos reales + aprendizaje continuo

> **Estado actual:** esta guía conserva el historial de la integración visual y
> del aprendizaje. El flujo de ejecución vigente es el agente modular descrito
> en `ARQUITECTURA_AGENTE_PC.md`; ya no existe una ruta separada de “ciclos
> visuales” para ejecutar acciones.

Todo es **aditivo**: no se borró ninguna acción, método ni comportamiento existente.
Las acciones viejas (`click{x_pct,y_pct}`, `drag`, `hotkey`…) siguen funcionando igual.

Verificado: 40/40 pruebas en verde (`test_mejoras.py`) y `py_compile` sobre los 9 archivos.

---

## 1. Archivos incorporados históricamente

| Origen en el ZIP entregado | Destino en tu proyecto |
|---|---|
| `desktop_ui.py` | `core/desktop_ui.py` |
| `screen_text.py` | `core/screen_text.py` |
| `screen_diff.py` | `core/screen_diff.py` |
| `learning/__init__.py` | `core/learning/__init__.py` |
| `learning/skills.py` | `core/learning/skills.py` |
| `learning/lessons.py` | `core/learning/lessons.py` |
| `learning/integration.py` | `core/learning/integration.py` |

`data/learning/` se crea sola desde `config.py`.

**Qué hace cada uno**

- `core/desktop_ui.py` — pywinauto backend `uia`: `list_windows()`, `focus_window()`,
  `close_window()` (solo exacto + lista negra del sistema), `active_window_elements()`
  con caché de 1.2 s, `find_element_center()` con coincidencia difusa. Respaldo con
  `pygetwindow`. Fuera de Windows devuelve vacío y el flujo visual sigue igual.
- `core/screen_text.py` — OCR con motor enchufable. `pytesseract` por defecto;
  `register_engine("mi_motor", MiClase)` + `OCR_ENGINE=mi_motor` en el `.env` para
  cambiarlo. `find_text()` agrupa palabras por línea, así que encuentra frases, no
  solo palabras sueltas.
- `core/screen_diff.py` — firma perceptual: aHash 8×8 **+** diff de píxeles sobre
  miniatura gris. Solo usa Pillow (ya lo tienes). Ante la duda dice "sí cambió":
  preferimos no castigar una acción que sí funcionó.
- `core/learning/` — recetas, lecciones y puente.

---

## 2. `config.py` — 2 inserciones

**a)** Línea 96: `PC_MAX_CYCLES` pasa de `"3"` a `"6"`.

**b)** Justo después de `PC_MAX_TYPE_CHARS` (línea 101), añade los bloques
`PC_VERIFY_*`, `PC_UI_*`, `OCR_*` y `LEARNING_*` (ver `modificados/config.py`).

**c)** Después de `PC_SCREENSHOT_DIR` (línea ~188), añade:

```python
LEARNING_DIR = DATA_DIR / "learning"       # skills.json y lessons.json
LEARNING_DIR.mkdir(parents=True, exist_ok=True)
```

---

## 3. `core/pc_control.py` — 9 inserciones

| Dónde | Qué |
|---|---|
| tras `import config` (línea 21) | `from core import desktop_ui, screen_diff, screen_text` |
| `_ALLOWED_ACTIONS` (43) | + las 5 acciones nuevas; y debajo `_VERIFY_ACTIONS`, `_SOFT_FAIL_ACTIONS`, `_CONTROL_TYPES` |
| `__init__` | `self.verify_actions`, `self.ui_context`, `self._last_history`, `self.last_discarded` + propiedad `last_history` |
| `execute()`, bucle de ciclos | `_screen_info()` se extrae a `info`, detección de no-progreso, `discarded`, `last_cycle_progressed`, semilla de lecciones |
| `_screen_info()` | ahora devuelve `hash`, `windows`, `ui_elements`, `active_window` + métodos `_windows_context` / `_elements_context` |
| `_validate_plan()` | tolerante; la validación de un paso se movió a `_validate_step()` |
| `_execute_action()` | firma previa, 5 ramas nuevas con fallo blando, `_verify_result()` |
| métodos nuevos | `_act_click_element`, `_act_click_text`, `_act_focus_window`, `_act_list_windows`, `_act_close_window`, `_click_xy`, `_before_signature`, `_verify_result` |
| `_describe()` | + 5 descripciones |

Lo más fácil: sustituir el archivo por `modificados/pc_control.py` o aplicar
`cambios.patch`.

---

## 4. `core/ai_engine.py` — 2 inserciones

- `plan_pc_task`: schema documenta las acciones nuevas y ordena **preferir
  `click_element` / `click_text` sobre `click` por coordenadas**; el historial ahora
  conserva las 3 lecciones aprendidas + los 12 pasos recientes (antes las lecciones
  se caían de la ventana); `user_text` incluye el contexto de UI.
- Método nuevo `_ui_context_text(screen_info)` (estático) justo antes de
  `plan_pc_actions`.

---

## 5. `main.py` — 4 inserciones

- `PCWorker.run`: prueba `learning.try_replay()` **antes** de `execute()`; si hay
  receta, emite el resultado y no gasta ni un ciclo de visión.
- `Controller.__init__`: `self._pc_instruction = ""` + `learning.bind_engine(self.engine)`.
- `_run_pc_order`: guarda `self._pc_instruction`.
- `_on_pc_done` / `_on_pc_failed`: llaman a `after_result` / `after_error` y avisan
  cuando la orden salió "de memoria".

---

## 6. `requirements.txt`

```
pywinauto>=0.6.8; sys_platform == "win32"
pygetwindow>=0.0.9
comtypes>=1.2
pytesseract>=0.3.10
```

**Tesseract no es un paquete de Python.** Instálalo aparte:
`winget install UB-Mannheim.TesseractOCR` marcando el idioma **Spanish**. Si no queda
en el PATH, pon en el `.env`:
`OCR_TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe`

---

## 7. Cómo queda cada problema del log

| Problema | Solución |
|---|---|
| `"Acción no permitida: vacía"` abortaba todo | el paso se descarta y entra al historial como `paso descartado: …`; solo revienta si **todos** son inválidos |
| clics por coordenadas fallan | `click_element` (centro real vía UIA) y `click_text` (centro real vía OCR); el prompt exige preferirlos |
| no había verificación | mini-captura antes/después de `click`, `double_click`, `type_text`, `press`, `click_element`, `click_text` → `ok=False` + `"sin efecto visible"` |
| no manejaba ventanas | `focus_window`, `list_windows`, `close_window` + `windows`/`active_window` en el contexto |
| ciclos infinitos | 2 ciclos con el mismo hash y ninguna acción `ok=True` → aborta con mensaje claro |

---

## 8. Decisiones que conviene que revises

1. **Fallo blando en las acciones nuevas.** Si `click_element` no encuentra el
   control, **no** lanza excepción: devuelve `ok=False` con el motivo y el ciclo
   siguiente lo lee y prueba otra vía. Era lo coherente con tu punto 5; si prefieres
   que aborte, quita `_SOFT_FAIL_ACTIONS` del `elif` de `_execute_action`.
2. **Guardia de literales en las recetas** (`_literales_compatibles`). Sin esto,
   *"escribe hola mundo como estás amigo"* y *"escribe adiós mundo como estás amigo"*
   dan 0.92 de similitud y Yue escribiría el texto equivocado. Ahora, si el
   `type_text`/`open_url`/`search_web` guardado no aparece literalmente en la orden
   nueva, se replanifica.
3. **La cortesía se ignora al normalizar** (`yue`, `por favor`, `porfa`, `gracias`).
   Con umbral 0.85 estricto, *"abre el bloc de notas por favor"* daba 0.83 y no
   reusaba la receta. Con el filtro da 1.00.
4. **Los fallos solo castigan a la receta que se iba a usar de verdad**
   (`_receta_aplicable`). Si `find()` ya la había descartado y el fallo vino del
   planificador visual, la receta no tiene la culpa.
5. **La reflexión del LLM corre en un hilo daemon.** `_on_pc_failed` está en el hilo
   de Qt: una llamada síncrona a Groq te congelaría la UI 2-3 s. El fallo se guarda al
   instante y la lección se rellena después.
6. **Ratio histórico.** Una receta con 5 éxitos y 3 fallos (0.62) queda retirada hasta
   que el planificador visual la haga subir de 0.7. Se auto-recupera, pero es lento;
   si te molesta, resetea `failures` en `record_success`.
7. **DPI.** `pywinauto` da coordenadas físicas y `pyautogui` lógicas. Con escalado de
   Windows al 125-150 % en varios monitores pueden desalinearse. Si ves clics
   desviados, marca *"Anular el comportamiento de PPP alto"* en el `.exe` de Python o
   añade `ctypes.windll.shcore.SetProcessDpiAwareness(2)` al arranque de `main.py`.
8. **Coste de `_screen_info()`.** Enumerar UIA cuesta 200-600 ms por ciclo. Está
   cacheado 1.2 s y se apaga con `PC_UI_CONTEXT=false`.

---

## 8-bis. Arreglos tras el diagnóstico en Windows 10 real

1. **`similarity()` daba 0.92 por contención simple** → «I» estaba contenido en
   «Siete» y `click_element` clicaba cualquier cosa. Ahora la contención exige
   palabra completa y proporción de longitud, los nombres de 1-2 caracteres solo
   valen si son idénticos, se ignoran los sufijos de atajo (`(Ctrl+Shift+E)`) y los
   nombres que ni empiezan igual llevan penalización. Umbral 0.72 -> **0.75**.
   14 casos reales cubiertos en `test_mejoras.py`.
2. **El avatar VRM falseaba la verificación.** Parpadea y respira, así que la
   pantalla "cambiaba" siempre y `sin efecto visible` no saltaba nunca. Ahora
   `screen_diff.signature(exclude_rects)` enmascara las ventanas de la propia Yue
   (`PC_VERIFY_IGNORE_TITLES`), tanto en `_verify_result` como en `try_replay`.
3. **`looks_like_pc_command` no conocía los verbos nuevos**: «enfoca la ventana…» o
   «cierra la ventana…» se iban al chat. Añadidos, cuidando los falsos positivos
   («cierra los ojos y imagina» sigue siendo conversación).

## 8-ter. Segunda ronda de arreglos (tras el 2º diagnóstico)

4. **Ninguna ventana de Yue llamaba a `setWindowTitle`.** Sus títulos estaban vacíos,
   `list_windows()` descarta las ventanas sin título y la máscara del avatar no habría
   encontrado nada: la verificación se habría roto al arrancar `main.py`. Ahora
   `own_window_rects()` identifica las ventanas propias **por PID** (`win32gui.EnumWindows`
   + `GetWindowThreadProcessId`), con el título como respaldo. Además se añadió
   `setWindowTitle` a las 4 clases de `ui/` y todas están protegidas contra `close_window`.
5. **Las ventanas propias se omiten del contexto del planificador**: no son objetivos
   válidos y solo gastarían atención del modelo.
6. **Tesseract se autodetecta** en las rutas típicas de UB-Mannheim aunque winget no
   haya refrescado el PATH de la sesión. `OCR_TESSERACT_CMD` sigue mandando si se define.
7. **El schema del planificador es condicional**: si no hay OCR, no se ofrece `click_text`
   y se le dice explícitamente que no lo use, para no gastar ciclos en una acción muerta.

## 8-quater. Tercera ronda (tras ejecutar main.py)

8. **Causa raíz de «Acción no permitida: vacía»**: el modelo emite pasos con otra
   forma (`{"type":"open_app"}`, `{"open_app":"notepad"}`, `"list_windows"`,
   `{"action":"clickElement"}`, `{"action":{"name":"focus_window"}}`). Antes se
   perdían todos. Ahora `_coerce_step()` + `_canon_action()` los reparan: 10 de 12
   formas reales se recuperan. Los sinónimos (`press_key`, `write`, `launch`,
   `esperar`…) se mapean a la acción real.
9. **El log ahora dice QUÉ llegó**: `Acción no permitida: vacía · recibí {...}`.
   Sin eso no había forma de saber qué inventaba el modelo.
10. **Prueba B del diagnóstico** ya no depende de que el usuario mueva una ventana:
    abre y cierra el menú Inicio para provocar un cambio real y medible.

## 9. Prueba rápida

```bash
python test_mejoras.py     # 40 comprobaciones, sin Windows ni LLM
```

Luego, en tu PC:

```
Yue, abre el bloc de notas y escribe hola          # 1ª vez: agente autónomo
Yue, abre el bloc de notas y escribe hola          # 2ª vez: agente autónomo (necesita 2 éxitos)
Yue, abre el bloc de notas y escribe hola          # 3ª vez: "Esto ya lo sabía hacer"
```

Mira `data/learning/skills.json` y `data/learning/lessons.json` para ver qué aprendió.
