# Pruebas del control de PC · YUE

Orden recomendado: primero las capas automáticas, luego las órdenes reales. Si el
diagnóstico falla en las capas 1-3, no pruebes órdenes: el LLM no tiene con qué
trabajar y vas a culpar al modelo de un problema de dependencias.

---

## Fase 0 · Automático (2 minutos, no toca tu PC)

```bash
pytest -q tests/test_agent_architecture.py  # arquitectura del agente (27 pruebas)
python tests/test_mejoras.py                 # regresiones de lógica anterior
python tests/diagnostico_pc.py               # qué funciona de verdad en TU máquina
```

El diagnóstico imprime 7 capas. Lo que importa:

| Capa | Si falla | Consecuencia |
|---|---|---|
| 1. Ventanas | pywinauto no instalado | `focus_window`/`close_window`/`click_element` muertos |
| 2. Controles | la ventana no expone UIA | esa app concreta solo admite clics por coordenadas |
| 3. OCR | Tesseract sin instalar | `click_text` muerto (el resto sigue) |
| 4. Diff | `changed=True` con la pantalla quieta | todo se dará por bueno; sube `PC_VERIFY_PIXEL_RATIO` |
| 5. Contexto | 0 ui_elements | el modelo está ciego y volverá a inventar coordenadas |

**La capa 2 es la más importante.** Es la lista literal de nombres que verá el
planificador. Pon la Calculadora en primer plano y ejecuta el diagnóstico: si ves
`Button «Siete»`, `click_element{name:"Siete"}` va a funcionar.

Prueba dirigida de un clic real, sin LLM de por medio:

```bash
python tests/diagnostico_pc.py --clic "Siete"     # con la Calculadora al frente
python tests/diagnostico_pc.py --texto "Archivo"  # con el Bloc de notas al frente
```

Si el clic cae desviado → es escalado DPI, no el modelo (ver §8 de `INTEGRACION.md`).

---

## Fase 1 · Una prueba por mejora

### A) `click_element` — el problema de los clics por coordenadas

1. Cierra la Calculadora si está abierta.
2. `abre la calculadora y haz clic en el 7`
3. **Esperado:** aparece el 7 en el visor.
4. **Cómo saber que usó el camino nuevo:** en la consola, el estado del paso dice
   `clic en el control «Siete»`, no `hacer clic`.

Antes esto fallaba ~la mitad de las veces porque el modelo adivinaba `x_pct/y_pct`.
Repítelo 5 veces: deberían salir 5 de 5.

### B) `click_text` — texto sin control UIA

1. Abre una web cualquiera en Chrome.
2. `haz clic en el texto Iniciar sesión` (o cualquier texto visible de la página).
3. **Esperado:** el estado dice `clic en el texto «...» en (x,y)`.
4. Si dice `El OCR no encontró...`, el texto está en un tipo de letra que Tesseract
   no lee bien; prueba con otro más grande.

### C) Ventanas

```text
que ventanas tengo abiertas
enfoca la ventana del bloc de notas
cierra la ventana del bloc de notas
```

**Prueba de seguridad — esta tiene que FALLAR:**

```text
cierra la ventana de Administrador de tareas
cierra la ventana del bloc          <- título incompleto
```

La primera debe responder `No cierro ventanas del sistema`; la segunda,
`Para cerrar necesito el título exacto`. Si alguna cierra algo, avísame.

### D) Verificación «sin efecto visible»

Sin ventana enfocada (clic en el escritorio primero):

```bash
python tests/diagnostico_pc.py --orden "presiona f7"
```

**Esperado:** `[--] press: presionar f7 · sin efecto visible` y `ok=False`.
Antes esto se reportaba como éxito. Ese `ok=False` es lo que ahora lee el modelo en
el ciclo siguiente para cambiar de estrategia en vez de repetir.

### E) Tolerancia a pasos malformados

No se puede forzar a voluntad (depende de que el modelo se equivoque), pero ya no
mata la orden. Deja la consola abierta y busca esta línea:

```
[control-pc] paso descartado: Acción no permitida: vacía
```

Si aparece **y la orden siguió adelante**, el bug de tu log está muerto. En el
historial del siguiente ciclo el modelo verá `paso descartado: …`.

### F) No-progreso (antes: bucle infinito)

Pide algo imposible con una ventana quieta al frente:

```text
haz clic en el botón Enviar cohete a Marte
```

**Esperado:** tras 2 ciclos sin cambios corta con
*«Me detengo: la pantalla no cambió en dos ciclos seguidos…»*.
No debe llegar a los 6 ciclos ni quedarse dando vueltas.

### G) Aprendizaje

Importante: **usa una orden que NO resuelva el regex directo**, o nunca se guardará
receta. `abre X y escribe Y` va por regex; `abre la calculadora y haz clic en el 7`
va por el planificador visual. Esa sirve.

1. Cierra la Calculadora. `abre la calculadora y haz clic en el 7` → agente autónomo.
2. Cierra la Calculadora. Repite → agente autónomo otra vez (van 2 éxitos).
3. Cierra la Calculadora. Repite → **«Listo. Esto ya lo sabía hacer: N pasos de memoria.»**
   y en un par de segundos, sin analizar la pantalla.

Comprueba `data/learning/skills.json`:

```json
{ "instruction": "abre la calculadora y haz clic en el 7",
  "successes": 3, "failures": 0, "avg_cycles": 2.0, "degraded": false }
```

**Degradación:** con la receta ya guardada, cambia el escenario para que falle (por
ejemplo, deja la Calculadora abierta con otra ventana encima). Dos fallos seguidos →
`"degraded": true` y vuelve sola al planificador visual.

**Lecciones:** provoca cualquier fallo y mira `data/learning/lessons.json` a los ~5 s.
El campo `lesson` debe traer 2-3 frases del LLM. Si sale vacío, revisa `LEARNING_REFLECT`
y la consola.

**Guardia de literales:** con la receta de `...haz clic en el 7` guardada, pide
`abre la calculadora y haz clic en el 3`. **No** debe repetir de memoria (te habría
pulsado el 7). Debe replanificar.

---

## Fase 2 · Comparación honesta

Para saber si de verdad está «más mejor» y no solo distinto, mide. Diez órdenes,
las mismas dos veces, apuntando éxitos y ciclos:

| Orden | ¿Salió bien? | Ciclos |
|---|---|---|
| abre la calculadora y haz clic en el 7 | | |
| abre el bloc de notas y escribe una lista de 3 compras | | |
| abre chrome y entra a wikipedia | | |
| enfoca la ventana del bloc de notas | | |
| … | | |

Con `PC_UI_CONTEXT=false` en el `.env` vuelves al comportamiento viejo (solo captura
+ coordenadas). Pasa la lista con `false`, luego con `true`. La diferencia en la
columna «¿Salió bien?» es la respuesta real a tu pregunta.

---

## Regla de oro

Prueba siempre con cosas **reversibles** (Calculadora, Bloc de notas sin guardar) y
ten `Yue, detente` a mano. `pyautogui.FAILSAFE` sigue activo: **mueve el ratón a la
esquina superior izquierda para abortar en seco**.
