# Yue Companion · Control de PC nivel Jarvis

Esta versión conserva los bloqueos absolutos, las confirmaciones, el deshacer de texto y la verificación visual antes/después. Encima de esa base añade rutas más rápidas y auditables.

## Cambios aplicados

### 1. Controles reales antes que coordenadas

**Plan aplicado:** resolver primero por UI Automation (`click_element`), después por OCR (`click_text`) y dejar los porcentajes de pantalla como último recurso.

- Las órdenes simples como `haz clic en Guardar` ya generan un plan directo, sin llamar al LLM.
- Si el modelo entrega coordenadas junto con `name` o `text`, el validador convierte el paso a `click_element` o `click_text`.
- Los resultados indican `método=elemento`, `método=texto` o `método=coordenada`.

### 2. Office mediante COM

**Plan aplicado:** usar el modelo de objetos nativo de Microsoft Office y conservar teclado como respaldo.

Nuevo módulo: `core/office_control.py`.

Soporta Word, Excel y PowerPoint para:

- crear archivos nuevos;
- insertar texto, título y negrita básica;
- guardar en `.docx`, `.xlsx` o `.pptx`;
- leer el contenido del archivo abierto.

Requiere `pywin32` en Windows. Si COM u Office no están disponibles, Yue usa el fallback existente cuando `PC_OFFICE_FALLBACK_KEYBOARD=true`.

### 3. Autocorrección por ciclos

**Plan aplicado:** conservar hasta 6 ciclos, pero ejecutar bloques más pequeños para observar y corregir con mayor precisión.

- `PC_MAX_CYCLES=6`.
- `PC_MAX_ACTIONS_PER_CYCLE=4`.
- Cada fallo agrega al siguiente prompt: acción intentada, motivo, si es recuperable y alternativa recomendada.

### 4. Deshacer ampliado

**Plan aplicado:** guardar snapshots ligeros antes de mover/cerrar ventanas o arrastrar, y revertir solo cuando el contexto siga siendo seguro.

- `type_text`: `Ctrl+Z`.
- `move_window`: restaura posición y tamaño.
- `close_window`: reabre únicamente cuando existe información suficiente para hacerlo de forma conservadora.
- `drag`: invierte el arrastre solo si no cambió la ventana activa ni la lista de ventanas.

### 5. Catálogo dinámico de aplicaciones

**Plan aplicado:** escanear el Registro de Windows y accesos directos del menú Inicio sin bloquear el arranque.

Nuevo módulo: `core/app_catalog.py`.

- Caché: `data/apps_catalogo.json`.
- `_open_app` consulta primero el catálogo y después los alias fijos.
- Comando manual: `/reescanear_apps` o `/apps`.

### 6. Permisos granulares

**Plan aplicado:** mantener `_BLOCKED_TERMS` como bloqueo absoluto y añadir interruptores independientes.

Variables disponibles:

```env
PERM_ENVIAR_CORREOS=true
PERM_BORRAR_ARCHIVOS=true
PERM_USAR_TERMINAL=true
PERM_INSTALAR_SOFTWARE=true
PERM_CERRAR_VENTANAS=true
PERM_SOBRESCRIBIR_ARCHIVOS=true
```

Cambiar una a `false` causa rechazo directo; no se sustituye por una simple confirmación.

### 7. Bitácora visible

**Plan aplicado:** reutilizar los resultados existentes y publicarlos de forma asíncrona en el chat flotante.

Se muestran las últimas 5-10 acciones con éxito/fallo y detalle breve. La actualización no detiene al hilo que controla el PC.

### 8. Tareas en segundo plano

**Plan aplicado:** mantener el control largo en `PCWorker` (`QThread`) mientras el chat continúa atendiendo mensajes normales.

- Una segunda orden de PC no compite con la primera.
- `cancel()` sigue interrumpiendo esperas y ciclos mediante `_check_cancelled`.
- Al finalizar, Yue notifica `Ya terminé...`.

### 9. DPI y escalado

**Plan aplicado:** declarar DPI awareness antes de importar Qt o tomar capturas.

`main.py` intenta, en orden:

1. `PER_MONITOR_AWARE_V2`;
2. `SetProcessDpiAwareness(2)`;
3. `SetProcessDPIAware()`.

Esto alinea capturas y clics con escalados de Windows como 125 % o 150 %.

## Instalación/actualización

Desde PowerShell, dentro del proyecto:

```powershell
python -m pip install -r requirements.txt
python main.py
```

Para reconstruir el catálogo de aplicaciones desde el chat:

```text
/reescanear_apps
```

## Pruebas

Pruebas solicitadas:

```powershell
python tests/test_seguridad_control.py
python tests/test_ordenes_dificiles.py
python tests/test_mejoras.py
```

Pruebas adicionales de esta versión:

```powershell
python tests/test_documento_office.py
python tests/test_jarvis_control.py
```

También se validó toda la carpeta `tests/` y la compilación de todos los módulos Python.
