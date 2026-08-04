# Arquitectura del agente autónomo de Control de PC

## Propósito

El Control de PC de YUE ya no interpreta una petición completa como un único clic, una única búsqueda ni una secuencia improvisada. Toda ejecución atraviesa el mismo ciclo:

```text
Comprender → planificar → atomizar → validar → ordenar → ejecutar
→ verificar → actualizar contexto → recuperar o continuar
```

El plan es interno. La interfaz solo recibe progreso general y resultados ya ejecutados.

## Flujo completo

1. **Comprensión del objetivo**
   - `ActionParser` analiza la instrucción completa.
   - Detecta conectores de secuencia, precedencia y paralelismo en español e inglés.
   - Produce `GoalAnalysis`, que acompaña al plan y se envía al modelo junto con el contexto temporal.

2. **Planificación**
   - `TaskPlanner` solicita un plan completo restante, no el siguiente clic aislado.
   - Cada paso debe ser una acción atómica con `id`, parámetros y `depends_on`.
   - `parallel_group` solo habilita concurrencia explícita; no la supone.
   - Los planes directos existentes son optimizaciones deterministas, no una ruta de ejecución distinta.

3. **Normalización y validación**
   - Se aplanan grupos anidados.
   - Se descartan pasos ilegibles sin perder los pasos independientes válidos.
   - Se remapean dependencias y se eliminan descendientes huérfanos.
   - Se valida que el grafo no tenga IDs repetidos, dependencias inexistentes ni ciclos.
   - Las esperas fijas generadas después de abrir una aplicación se transforman en `wait_for` por condición.

4. **Cola DAG**
   - `TaskQueue` solo libera tareas cuyas dependencias terminaron correctamente.
   - Una falla no recuperable detiene inmediatamente la cola.
   - No se ejecutan descendientes de una tarea fallida.
   - La deduplicación evita repetir acciones idempotentes dentro del mismo contexto.

5. **Ejecución atómica**
   - `TaskExecutor` adquiere todos los locks declarados por la acción.
   - Comprueba permisos y confirmaciones.
   - Ejecuta un único handler.
   - Verifica el resultado antes de marcar la tarea como completada.
   - Actualiza contexto y bitácora en un bloque `finally`, incluso ante excepciones.

6. **Verificación y recuperación**
   - `VerificationEngine` observa ventana, foco, archivo, controles, pantalla o condición específica.
   - `RecoveryEngine` decide entre reintentar, usar una alternativa registrada o detenerse para solicitar ayuda.
   - Los intentos fallidos se conservan en la bitácora, pero una recuperación exitosa completa la tarea.

7. **Replanificación**
   - Cuando corresponde, YUE vuelve a observar después de ejecutar el plan.
   - El siguiente plan recibe el historial y el contexto actualizado.
   - La IA devuelve `done=true` cuando el objetivo ya está cumplido.

## Módulos

| Módulo | Responsabilidad |
|---|---|
| `core/agent/models.py` | Modelos de objetivo, tarea, plan, resultado y estado |
| `core/agent/parser.py` | Análisis estructural de lenguaje natural |
| `core/agent/planner.py` | Plan completo, atomización, dependencias y validación DAG |
| `core/agent/registry.py` | Registro extensible de capacidades y políticas |
| `core/agent/queue.py` | Cola estricta, dependencias, deduplicación y concurrencia segura |
| `core/agent/executor.py` | Ejecución de una sola tarea con locks y verificación |
| `core/agent/locks.py` | Locks ordenados de teclado, mouse, portapapeles, foco y recursos nombrados |
| `core/agent/context.py` | Memoria temporal de aplicación, ventana, archivo, documento, texto y resultados |
| `core/agent/application_manager.py` | Reutilización y detección de aplicaciones abiertas |
| `core/agent/window_manager.py` | Lectura, búsqueda, foco y espera de ventanas |
| `core/agent/focus_manager.py` | Garantía explícita de foco |
| `core/agent/verification.py` | Verificadores observables por tipo de acción |
| `core/agent/waits.py` | Sondeo adaptativo cancelable con timeout |
| `core/agent/recovery.py` | Reintentos y alternativas registrables |
| `core/agent/logger.py` | Bitácora JSONL estructurada y redactada |
| `core/agent/runtime.py` | Orquestación completa y API compatible |
| `core/pc_control.py` | Fachada pública y adaptadores físicos existentes |

## Locks y concurrencia

Los recursos estándar son:

- `keyboard`
- `mouse`
- `clipboard`
- `active_window`

También existen locks nombrados, por ejemplo `app:word`. Los locks se adquieren siempre en orden estable para evitar interbloqueos.

Una tarea solo puede ejecutarse en paralelo cuando:

1. el plan lo declara con el mismo `parallel_group`;
2. todas las tareas del grupo comparten la misma barrera de dependencias;
3. su definición tiene `parallel_safe=True`;
4. no comparte ningún recurso con las demás tareas del lote.

`parallel=true` sin `parallel_group` no desprende una tarea del flujo: se conserva
el orden secuencial seguro.

Por tanto, dos tareas nunca usan simultáneamente el teclado, el mouse, el portapapeles o el foco.

## Reutilización de aplicaciones

`ApplicationManager.ensure_open()`:

1. normaliza alias;
2. busca una ventana existente por similitud y pistas de título;
3. la enfoca si existe;
4. solo si no existe, invoca el launcher;
5. espera dinámicamente hasta detectar una ventana real.

Esto evita abrir Word, Chrome, Edge, Excel, PowerPoint, Paint, Calculadora, Explorador u otras aplicaciones repetidamente.

## Contexto temporal

`ContextManager` mantiene durante una ejecución:

- ID de ejecución;
- instrucción y objetivo;
- aplicación y ventana activas;
- archivo, ruta y documento activos;
- texto producido;
- proceso actual;
- resultados por tarea;
- firmas de acciones ya ejecutadas.

Cada acción puede producir contexto nuevo. Por ejemplo, un archivo guardado pasa a ser el archivo activo y se usa automáticamente en pasos posteriores.

## Esperas inteligentes

Las cargas de interfaz no usan `sleep(5)` ni números asumidos. `SmartWaiter` consulta una condición con intervalo adaptativo hasta que:

- aparece o desaparece una ventana;
- la aplicación está lista;
- cambia el estado visual;
- el foco llega al destino;
- existe el archivo;
- aparece un control o diálogo;
- la interfaz mantiene un estado estable durante varias observaciones;
- queda disponible un lock de cursor, teclado, portapapeles o ventana activa;
- vence el timeout;
- el usuario cancela.

Los únicos temporizadores no observables permitidos son pausas solicitadas explícitamente por el usuario y backoff corto entre reintentos; nunca se usan para asumir que una aplicación terminó de cargar.

## Antirrepetición

Hay dos niveles:

1. **En el plan:** tareas idempotentes idénticas se fusionan y sus dependencias se remapean.
2. **En ejecución:** la firma de acción se combina con su ámbito actual (ventana/documento). Si ya terminó correctamente, la cola la omite y registra la deduplicación.

`allow_repeat=true` permite una repetición deliberada.

## Recuperación

La política predeterminada es:

1. reintentar hasta `max_attempts`;
2. aplicar backoff cancelable;
3. intentar una alternativa registrada cuando exista, por ejemplo `click_element ↔ click_text`;
4. detener la cola y devolver un motivo útil si no hay una salida segura.

Se pueden registrar estrategias nuevas en `RecoveryEngine` sin modificar la cola.

## Registro interno

La ruta predeterminada es:

```text
data/logs/pc_agent.jsonl
```

Cada evento incluye, según corresponda:

- timestamp;
- `run_id` y `plan_id`;
- tarea y acción;
- resultado;
- duración;
- error;
- número de intento;
- verificación;
- recuperación o deduplicación.

El logger no guarda texto completo introducido: registra longitud y hash para reducir exposición de contenido sensible.

## Agregar una capacidad sin modificar el núcleo

Una capacidad nueva se registra con handler, recursos, verificador y metadatos para el planificador:

```python
controller.register_agent_action(
    "read_sensor",
    lambda step: {"ok": True, "detail": read_sensor(step["name"])},
    resources=("sensor_bus",),
    parallel_safe=True,
    verifier="none",
    description="Lee un sensor local",
    parameters="name",
)
```

A partir de ese registro:

- `TaskPlanner` acepta `read_sensor`;
- el catálogo dinámico la presenta a la IA;
- `TaskQueue` conoce sus recursos y paralelismo;
- `TaskExecutor` llama directamente a su handler;
- no se edita `_execute_action`, el planificador, la cola ni el runtime.

El handler puede devolver `ActionResult`, `dict`, `bool` o una cadena descriptiva.
Los handlers registrados son código local de confianza: deben respetar las mismas
reglas de seguridad y permisos que cualquier integración física.

Una capacidad histórica que todavía agrupe varias operaciones puede aportar un
`expander`. El planificador la normaliza antes de construir el DAG:

```python
controller.register_agent_action(
    "crear_reporte",
    handler_compatibilidad,
    expander=lambda raw: [
        {"id": "crear", "action": "office_create", "app": raw["app"]},
        {"id": "escribir", "action": "office_write", "app": raw["app"],
         "text": raw["text"], "depends_on": ["crear"]},
    ],
)
```

## Configuración

```text
PC_AGENT_MAX_WORKERS=3
PC_AGENT_PARALLEL=true
PC_AGENT_RETRY_ATTEMPTS=3
PC_AGENT_WAIT_TIMEOUT=10
PC_AGENT_APP_TIMEOUT=15
PC_AGENT_MAX_REPLANS=6
PC_AGENT_LOG_PATH=data/logs/pc_agent.jsonl
```

La seguridad existente sigue activa: lista negra, permisos granulares, confirmaciones, teclas y atajos permitidos, límites de texto/coordenadas, PyAutoGUI FAILSAFE y cancelación cooperativa.

## Pruebas

`tests/test_agent_architecture.py` cubre:

- conectores secuenciales y paralelos;
- construcción y validación del DAG;
- conversión a esperas dinámicas;
- detención estricta ante errores;
- reintentos y alternativas;
- antirrepetición contextual;
- exclusión mutua de teclado;
- reutilización de aplicaciones abiertas;
- registro y ejecución de plugins;
- catálogo dinámico enviado al planificador;
- resultado final correcto después de una recuperación;
- atomización de acciones compuestas mediante expanders;
- barreras comunes para grupos paralelos;
- espera por estabilidad y locks de recursos;
- protección frente a conectores absorbidos como parámetros.

La batería específica contiene **27 pruebas automáticas**.
