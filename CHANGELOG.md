# Cambios realizados

## Generador de trabajos con diseño: Excel y PowerPoint (sesión actual)

Para que YUE haga tablas y presentaciones **con diseño y rápido**, sin depender de abrir Office por COM (que era lo lento).

### `core/doc_builder/` — tablas y presentaciones con diseño
- Separa "pensar el contenido" de "construir el archivo": la IA devuelve solo la **estructura en JSON** (rápido y barato) y el módulo la convierte en `.xlsx`/`.pptx` de una sola pasada con una **paleta de diseño** aplicada. Nada de ir celda por celda por COM.
- **Excel** (`build_table`, con openpyxl): título, encabezados con color, filas alternas, bordes, anchos automáticos, panel fijo, autofiltro y fila de **TOTAL con fórmula SUMA**. Probado generando un registro de notas real (7 alumnos, 4 cursos) con las sumas calculadas y 0 errores de fórmula.
- **PowerPoint** (`build_deck`, con python-pptx): portada y secciones con fondo de color; diapositivas de contenido sobre blanco con títulos grandes; viñetas legibles; tablas con encabezado de color. Sigue las buenas prácticas de diseño (contraste fuerte de tamaños, márgenes amplios, **sin las rayitas bajo los títulos que delatan a la IA**). Probado generando una clase de 7 diapositivas, validada y revisada visualmente.
- 5 paletas listas (colegio, verde, morado_yue, corporativo, coral) y un lector de JSON tolerante a ```fences``` y texto extra. 10 pruebas nuevas que generan archivos `.xlsx` y `.pptx` de verdad y verifican su contenido.

## Varias IAs con relevo + arreglos de uso (sesión anterior)

### Sistema de varias IAs con relevo automático (`core/ai_router.py`)
- Router multi-proveedor: pone en fila varias APIs **gratis** (Groq, Gemini, Cerebras, OpenRouter, y hueco para OpenAI) y, si una se cae, se queda sin cupo o retira el modelo, pasa a la siguiente **al instante, sin esperas**. Justo el fallo que se veía en pantalla ("Ninguno de los modelos de tu proveedor funcionó").
- Clasifica cada fallo (sin cupo / clave mala / modelo retirado / red) y pone un enfriamiento a ese proveedor para no reintentarlo en vano; cuando pasa, vuelve solo a la fila. Todas son APIs compatibles con OpenAI, así que se manejan igual.
- Los proveedores se configuran por variables de entorno: solo pegas las claves que tengas (`GEMINI_API_KEY`, `CEREBRAS_API_KEY`, `OPENROUTER_API_KEY`…); los sin clave no entran. Probado con 11 casos (relevo, sin cupo, recuperación, sin pausas).

### Arreglos que pediste
- **Ya no hace falta empezar con «Yue».** `WAKE_WORD_ENABLED` pasa a `false` por defecto: YUE responde a todo lo que le digas o escribas sin el gatillo. (Reversible en el `.env`.)
- **Menú del avatar con visión.** Nueva opción **«👁 Ver mi pantalla ahora»** en el clic derecho del avatar, conectada a la misma acción que `/mira`. Así ya puedes pedirle que mire la pantalla desde el menú.
- **Consola sin spam.** Los avisos repetidos `[media-companion]` y `[oido]` de la terminal ahora están tras `DEBUG_STATUS` (apagado por defecto); los avisos de "no te oigo bien" se mantienen. Reversible poniendo `DEBUG_STATUS=true`.

## Voz, visión, personalidad, profesora y autonomía (FASES 4–8)

Todo aditivo y probado (9 baterías nuevas, 21 en total en verde), en español y sin dependencias nuevas (solo librería estándar).

### FASE 4 · Voz y visión
- **Lip-sync por visemas** (`core/voice_ext/`): convierte el texto en formas de boca reales (AA/E/I/O/U, bilabiales, labiodentales…), no solo apertura por volumen. Maneja dígrafos del español (ch, ll, rr, qu, gu) y la `h` muda. `merge_shape_with_envelope()` une la forma (texto) con la apertura (envolvente RMS del audio) para sincronizar con la voz real.
- **Panel de privacidad** (`core/privacy.py`): consentimiento explícito por sensor (cámara, micro, pantalla, audio del sistema), registro de cuándo se usó cada uno (solo metadatos, nunca contenido) y un **modo privacidad maestro** que apaga y bloquea todo. Los sensores no se activan sin permiso.

### FASE 5 · Personalidad y seguridad emocional
- **Seguridad emocional híbrida** (`core/safety_ext.py`): envuelve al detector regex y clasifica el riesgo en NINGUNO/LEVE/MODERADO/ALTO/CRÍTICO. Baja falsos positivos (negación, contexto de ficción, factores protectores) y sube con señal sostenida de cámara/ánimo. Cada nivel enruta a una directiva de contención cálida; **nunca** incluye métodos de daño.
- **Rasgos de personalidad ajustables** (`core/personality_traits.py`): perillas 0..1 (sarcasmo, cariño, energía, formalidad, curiosidad, paciencia, humor, iniciativa) que se guardan en JSON y generan un fragmento para el prompt. Regla de oro escrita en el propio fragmento: en crisis, YUE deja el sarcasmo aunque esté al máximo.

### FASE 6/7 · Modo Profesora
- **RAG local** (`core/teacher_ext/`): trocea el documento del alumno, recupera los fragmentos relevantes por significado (TF-IDF + coseno, offline) y arma un contexto **citable** para que la profesora responda apoyándose en el material real y avise cuando algo no está en el documento en vez de inventarlo. Es la base para explicar y evaluar pegado al texto.

### FASE 8 · Autonomía supervisada
- **Supervisor de autonomía** (`core/autonomy_ext/`): revisa cada acción antes de tocar el PC y la clasifica en SEGURO / CONFIRMAR / BLOQUEADO. Bloquea lo peligroso e irreversible (formatear, borrar el sistema), exige confirmación para lo que tiene consecuencias (borrar, enviar, pagar, tocar `.env`/claves) y deja pasar solo lo inocuo. Incluye listas de permitidos/protegidos, cola de aprobación por token y un diario de decisiones.

## Mejoras de seguridad, arquitectura y memoria (FASES 1–3)

### FASE 1 · Seguridad y limpieza
- Detector automático de secretos `tools/secret_scanner.py`: enmascara los valores, distingue confianza ALTA/MEDIA/BAJA y sale con código de error para usarlo como puerta antes de empaquetar. Detectó claves reales de Groq (en `.env` **y** en `.env.example`) y de Together.
- **Corregido:** `.env.example` tenía una clave Groq real incrustada; ahora lleva un marcador (`TU_CLAVE_GROQ_AQUI`). Ver `SEGURIDAD_CLAVES.md` con las claves a revocar.
- Limpiador `tools/clean_project.py` (simulación por defecto, `--apply` para borrar) que quita cachés, bases con datos personales, logs, audios y documentos generados, protegiendo código y assets.
- Constructor `tools/build_release.py`: copia a staging, limpia, verifica que no haya `.env` ni secretos y solo entonces crea `dist/…_clean.zip`; **aborta** si encuentra una clave real.
- `.gitignore` ampliado (secretos, `data/`, bases, cachés, multimedia, materiales generados).

### FASE 2 · Núcleo de estado (`core/state/`)
- `EventBus`: comunicación entre módulos sin acoplarlos, segura entre hilos y con aislamiento de errores.
- `ResourceManager`: propiedad exclusiva de recursos físicos (cámara, micrófono, altavoces, pantalla, avatar…). Un solo dueño a la vez; expropiación por prioridad con aviso al dueño anterior. Resuelve el choque cámara clásica vs Vision V3.
- `YueStateManager`: única fuente de verdad del estado de YUE y **arbitraje del avatar por prioridad** (EMERGENCIA > USUARIO > PROFESORA > CONVERSACIÓN > EMOCIÓN > MULTIMEDIA > AMBIENTAL > IDLE). El avatar deja de recibir órdenes contradictorias; animaciones con caducidad que se relajan a la emoción base.

### FASE 3 · Memoria 2.0 (`core/memory_ext.py`, aditiva)
- Añade a la misma `data/yue.db` (sin tocar tablas existentes): `conversations`, `conv_messages` (con fuente, emoción, importancia, confianza y borrado lógico) y `mem_backups`.
- **Búsqueda por significado sin internet** (TF-IDF + coseno en español): devuelve el mensaje original, su fecha y un nivel de confianza (exacto/aproximado/inferido). YUE puede decir «me hablaste de esto el día X».
- Copias de seguridad con verificación de integridad (`PRAGMA integrity_check`) y restauración que respalda la base actual antes de sobrescribir.

### Pruebas
- Nuevas baterías `tests/test_state_manager.py` (13 pruebas) y `tests/test_memory_ext.py` (8 pruebas).
- Las 12 baterías previas siguen en verde; total 14 archivos de prueba OK. Verificado además que la memoria 2.0 es aditiva sobre una copia de la `yue.db` real (cero pérdida de datos, integridad `ok`).
- Guía de integración paso a paso en `INTEGRACION_MEJORAS.md`.


## Motor autónomo modular de Control de PC

- Reemplazo del flujo central por `core/agent/`, con comprensión previa, plan interno completo, tareas atómicas y grafo de dependencias.
- Cola estricta: una tarea solo inicia cuando sus dependencias terminaron; un error no recuperable detiene la ejecución y nunca libera descendientes.
- Locks reales y ordenados para teclado, mouse, portapapeles, ventana activa y recursos nombrados; concurrencia únicamente explícita y sin conflictos.
- `ApplicationManager` reutiliza ventanas abiertas y espera su aparición mediante condiciones observables.
- `VerificationEngine` comprueba aplicaciones, foco, ventanas, archivos, contenido y efectos de UI después de cada acción.
- `RecoveryEngine` aplica reintentos, backoff y alternativas registrables antes de pedir intervención.
- Contexto temporal y deduplicación por ámbito para impedir aperturas, guardados o escritura repetidos.
- Bitácora JSONL con tiempos, resultados, errores y reintentos, redactando el contenido de texto.
- Registro de plugins con handler propio y catálogo dinámico para la IA; agregar una capacidad ya no requiere modificar el switch legado.
- Eliminación de pausas fijas de carga en el flujo activo de aplicaciones, ventanas y Office.
- Nueva documentación `ARQUITECTURA_AGENTE_PC.md` y batería `tests/test_agent_architecture.py` (27 pruebas).
- Acciones compuestas históricas se atomizan mediante expanders registrados; Office crear/escribir/guardar ya forma pasos dependientes independientes.
- Los grupos paralelos heredan una barrera común y `parallel=true` sin grupo permanece secuencial.
- `wait_for` soporta estabilidad de UI, procesos, elementos y disponibilidad de locks de mouse, teclado, portapapeles y foco.

## Control de PC nivel Jarvis · Office COM, permisos y autocorrección

- `click_element` y `click_text` tienen prioridad sobre coordenadas; cada resultado
  registra el método real usado para auditoría.
- Nuevo `core/office_control.py`: Word, Excel y PowerPoint mediante COM para crear,
  escribir con formato básico, guardar y leer; teclado solo como fallback. Esta
  ruta reemplaza la secuencia histórica de `Ctrl+N` descrita más abajo.
- Planificador configurado en 6 ciclos de hasta 4 pasos, con diagnóstico explícito
  y alternativa para cada fallo del ciclo anterior.
- Deshacer conservador para texto, mover/cerrar ventanas y arrastres mediante
  snapshots ligeros.
- Nuevo catálogo dinámico `data/apps_catalogo.json` y comando `/reescanear_apps`.
- Permisos por capacidad en `.env`, conservando `_BLOCKED_TERMS` como bloqueo total.
- Bitácora visible de acciones, actualizada sin bloquear el hilo de control.
- Control largo en `PCWorker` mientras el chat sigue disponible; cancelación
  interrumpible y aviso al terminar.
- DPI awareness activado antes de Qt para escalado 125 %, 150 %, etc.
- Nueva batería `tests/test_jarvis_control.py`; toda la carpeta `tests/` pasa.

## Documento en blanco de Office + lectura de intención (no literal)

- `core/pc_control.py`: al abrir Word, Excel o PowerPoint (que arrancan en la
  pantalla de inicio con plantillas, NO en un lienzo vacío) se crea el documento
  en blanco con `Ctrl+N` antes de escribir. Antes el texto se perdía contra el
  buscador de plantillas. Aditivo: helpers `_es_app_office` y
  `_pasos_documento_en_blanco`, gatillados solo para ofimática (el Bloc de notas
  ya abre en blanco y en Chrome/Edge `Ctrl+N` abriría otra ventana).
  - `abre word` (a secas) ahora deja un documento en blanco listo para dictar.
  - `abre word y escribe "…"` (literal) inserta el lienzo entre el foco y el
    tecleo: `open_app → wait → focus_window → Ctrl+N → wait → type_text`.
- `_pide_contenido` ahora entiende la INTENCIÓN y no se lo toma literal: además
  de «una carta…», capta verbos de redacción al frente («hazme…», «genera…»,
  «resume…», «explica…», «describe…») y marcadores de tema («sobre X»,
  «acerca de X»). Así «escribe sobre la contaminación» se REDACTA en vez de
  teclearse tal cual. Sigue siendo literal si va entre comillas, con dos puntos
  o es texto corto sin señal de redacción.
- La orden suelta «escribe X» también pasa por ese filtro: literal se teclea,
  contenido se deja al planificador con IA.
- `core/ai_engine.py`: el planificador con IA recibe dos instrucciones nuevas —
  crear el lienzo de Office con `Ctrl+N` antes de escribir, e interpretar la
  intención (redactar el contenido real) en vez de teclear la frase de la orden.
- `tests/test_documento_office.py` (nuevo): 30+ comprobaciones del lienzo en
  blanco y del literal-vs-contenido, todas en verde.
- `tests/test_seguridad_control.py` (nuevo): batería de seguridad que faltaba
  (lista negra completa, `alt+f4`/`ctrl+alt+supr` bloqueados, recorte de
  coordenadas y longitudes, guardia de títulos en `close_window`).
- `tests/test_core.py`: ahora corre también suelto (`python tests/test_core.py`),
  no solo con pytest.

## Modelos de Groq con respaldo y verificación por celdas

- `core/ai_fallback.py` (nuevo): muestra el mensaje real de error de Groq en
  lugar de un `404 Client Error` sin contexto, consulta `/v1/models` para saber
  qué modelos existen de verdad en la cuenta y elige uno vivo si el del `.env`
  fue retirado.
- `plan_pc_task` ya no muere cuando el modelo de visión desaparece: prueba otro
  modelo con visión y, si no hay ninguno, planifica solo con texto apoyándose en
  `ui_elements` y `windows`.
- Un fallo de modelo (404) dejó de contarse como «plan ilegible», así que ya no
  quema los 3 ciclos repitiendo una llamada imposible.
- `chat()` y `look()` reintentan una vez con un modelo vivo si el suyo murió.
- `screen_diff.changed()` suma una tercera señal: la celda de la miniatura que
  más cambió (rejilla 8x8). Un dígito de la calculadora movía el 0.069% de la
  pantalla, por debajo del umbral global de 0.15%, y se marcaba «sin efecto
  visible» aunque el clic hubiera funcionado.
- `.env`: `GROQ_MODEL_FALLBACKS`, `GROQ_VISION_FALLBACKS`, `PC_VERIFY_TILES`,
  `PC_VERIFY_TILE_RATIO`.
- `tests/diagnostico_groq.py` (nuevo): lista los modelos de la cuenta y prueba
  chat y visión de verdad.

## Visión silenciosa, cámara local y control de PC

- Se retiró del avatar la opción para activar o desactivar la visión de pantalla.
- La visión queda disponible al iniciar, sin comentarios automáticos.
- Se añadió conexión automática a webcam con indicador visible.
- Se añadió análisis local de blendshapes faciales y puntos de postura, sin identidad ni almacenamiento de imágenes.
- Se añadieron `/camara` y preguntas naturales sobre expresión o postura.
- Las órdenes “abre la calculadora y haz clic en el 3” y “cierra la ventana de Calculadora” usan planes directos.
- Abrir una aplicación que ya está abierta la enfoca en vez de crear otra instancia.
- El cierre de ventanas acepta una coincidencia difusa fuerte y única.
- Se ajustó el alto mínimo del chat para evitar avisos repetidos de `setGeometry` en Windows.


## 1. Emociones del avatar

- Las emociones dejaron de mostrarse como etiquetas textuales.
- El encabezado ahora muestra solo `Vínculo · nivel X/10`.
- Limpieza automática de marcadores como `[Feliz]`, `Emoción: triste` o `Estoy curiosa` al inicio de una respuesta.
- Reacción visual inmediata al mensaje del usuario.
- Inferencia combinada entre el mensaje del usuario y la respuesta de YUE.
- Nuevos movimientos de hombros, brazos, torso, inclinación, rebote, temblor y frecuencia de parpadeo.

## 2. Chat

- Entrada estable mediante un `QLineEdit` especializado.
- Botón de envío adicional.
- La caja nunca se deshabilita durante trabajos en segundo plano.
- Animación de desvanecimiento más ligera.
- Redimensionamiento diferido para reducir tirones.
- Los estados y respuestas no recuperan el foco mientras YUE controla otra aplicación.
- Las respuestas antiguas de IA se descartan al recibir una instrucción nueva.

## 3. Voz e interrupciones

- Reproducción TTS cancelable inmediatamente.
- El micrófono permanece activo mientras YUE habla.
- Interrupción automática cuando se reconoce una frase distinta del eco.
- Comparación antieco global, por fragmentos y por palabras.
- Palabra de activación opcional durante la interrupción.
- Orden natural `Yue, detente` para detener voz y control del PC.

## 4. Iniciativa automática

- Eliminado `Crear una idea nueva ahora` del menú, señales y comandos.
- La creación se inicia solo durante periodos de inactividad.
- Genera planes, checklists, borradores, plantillas y mejoras prácticas.
- Guarda las creaciones en Markdown.
- Puede abrir automáticamente el archivo generado mediante una operación local segura.
- Mantiene límites por sesión y evita actuar mientras el usuario escribe o controla el PC.

## 5. Control visual del PC

- Aumento del límite configurable a 24 acciones.
- Ejecución iterativa de hasta 3 ciclos visuales.
- Revisión de pantalla entre bloques de acciones.
- Historial de pasos para evitar repetir acciones.
- Nuevas acciones: abrir rutas, clic derecho, arrastrar, desplazamiento horizontal y captura.
- Nuevos atajos directos: copiar, pegar, seleccionar todo, guardar, deshacer, rehacer, pestañas, recargar y navegación.
- Cancelación cooperativa entre pasos y durante esperas.
- Validación reforzada de coordenadas, botones, texto, teclas y rutas.

## Control del PC por elementos reales

- Acciones nuevas basadas en la UI real con pywinauto (backend `uia`): `click_element{name, control_type?}`,
  `focus_window{title}`, `list_windows`, `close_window{title}`.
- `close_window` exige titulo exacto y nunca toca ventanas del sistema.
- Accion `click_text{text}`: localiza texto visible con OCR (pytesseract por defecto, motor enchufable
  via `register_engine`) y clica en su centro real.
- El planificador recibe contexto rico: ventanas abiertas, cual tiene el foco y hasta 40 controles
  visibles de la ventana activa. El prompt ordena preferir `click_element`/`click_text` sobre los
  clics por coordenadas.
- Verificacion por accion: mini-captura antes/despues de `click`, `double_click`, `type_text`, `press`,
  `click_element` y `click_text`. Si la pantalla no cambia, `ActionResult.ok=False` con
  "sin efecto visible" y el ciclo siguiente lo lee.
- Tolerancia a pasos malformados: un paso invalido se descarta y se anota como "paso descartado"
  en vez de abortar el plan entero. Solo falla si todos los pasos del ciclo son invalidos.
- Deteccion de no-progreso: dos ciclos con la misma pantalla y ninguna accion con `ok=True` cortan
  la ejecucion con un mensaje claro. `PC_MAX_CYCLES` sube de 3 a 6.
- Se mantienen intactas `_BLOCKED_TERMS`, las teclas seguras y la cancelacion con `_cancel_event`.

## Aprendizaje continuo (`core/learning/`)

- `skills.py`: biblioteca de recetas en `data/learning/skills.json`. `record_success`, `find`
  (similitud por tokens normalizados, umbral 0.85, minimo 2 exitos y ratio 0.7) y `record_failure`
  (2 fallos seguidos degradan la receta y devuelven el control al planificador visual).
- `lessons.py`: memoria de errores en `data/learning/lessons.json`. Pide al LLM una reflexion de
  2-3 frases sobre por que fallo y que hacer distinto; `relevant()` recupera las de ordenes parecidas.
  La llamada al LLM corre en un hilo daemon para no congelar la UI de Qt.
- `integration.py`: `try_replay` (repite una receta conocida sin gastar ciclos de vision),
  `enrich_history` (inyecta lecciones en el planificador), `after_result` y `after_error`.
- Cambios en `main.py` limitados a cuatro enganches; el flujo visual sigue igual si el paquete falla.
