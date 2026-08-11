# Seguridad del repositorio · YUE Companion

Auditoría hecha durante la integración del sistema de comprensión emocional v2.
**Este documento no contiene ni una sola clave, contraseña ni contenido de las
bases de datos.** Solo describe el riesgo y cómo repararlo.

---

## 1. Riesgo detectado (ALTO) · archivos sensibles versionados

`.gitignore` está bien escrito y cubre correctamente los patrones peligrosos:

```
.env
.env.*
data/
*.db
*.sqlite
```

El problema es que **`.gitignore` no afecta a los archivos que Git ya está
siguiendo**. Estos tres se añadieron al repositorio ANTES de que existiera la
regla, así que siguen versionándose pese a estar listados:

| Archivo | Qué contiene | Riesgo |
|---|---|---|
| `.env` | claves de API de los proveedores | **ALTO** |
| `data/yue.db` | conversaciones, hechos, metas, ánimo | **ALTO** (datos personales) |
| `data/teacher/classes.db` | historial de clases | MEDIO |

`.env` aparece además en **4 commits del historial**, lo que significa que las
claves que hubiera en ese momento están en el historial de Git aunque hoy se
borre el archivo.

> No se ha inspeccionado ni volcado el contenido de ninguno de estos archivos.

---

## 2. Reparación · paso 1 (obligatorio y urgente)

**Rotar las claves.** Cualquier clave que haya estado en un `.env` versionado
debe considerarse comprometida, aunque el repositorio sea privado. Se revocan y
se generan nuevas en el panel de cada proveedor. Todo lo demás es secundario:
sin este paso, limpiar el historial no sirve de nada.

---

## 3. Reparación · paso 2: dejar de seguir los archivos

`--cached` los quita del control de versiones **sin borrarlos del disco**:

```bash
git rm --cached .env
git rm --cached data/yue.db
git rm --cached data/teacher/classes.db
git commit -m "seguridad: dejar de versionar .env y las bases de datos locales"
```

A partir de aquí `.gitignore` ya hace su trabajo y no vuelven a colarse.

Comprobación:

```bash
git ls-files | grep -iE "^\.env$|\.db$|\.sqlite"   # no debe devolver nada
```

---

## 4. Reparación · paso 3: limpiar el historial (opcional pero recomendable)

Solo si el repositorio se va a publicar o compartir. **Reescribe el historial**,
así que conviene hacer una copia antes y avisar a quien tenga clones.

Con `git-filter-repo` (la herramienta recomendada hoy):

```bash
pip install git-filter-repo
git filter-repo --invalidate-checkpoint \
    --path .env --path data/yue.db --path data/teacher/classes.db \
    --invert-paths
```

Después hay que volver a añadir el remoto y forzar el envío:

```bash
git remote add origin <URL>
git push --force --all
```

Si el repositorio nunca ha salido de la máquina, el paso 3 puede saltarse: con
rotar las claves (paso 1) y dejar de seguir los archivos (paso 2) es suficiente.

---

## 5. Verificado como CORRECTO

- `.gitignore` cubre `.env`, `data/`, `*.db`, `*.sqlite`, logs, `__pycache__`,
  entornos virtuales, multimedia generada y modelos descargables.
- `.env.example` / `env_EJEMPLO.txt` se versionan a propósito y solo llevan
  nombres de variable, nunca valores.
- `tools/secret_scanner.py` es una **herramienta de detección**, no un secreto:
  su presencia en el repositorio es correcta.
- Ningún módulo del proyecto imprime claves por consola. El sistema afectivo
  nuevo (`core/affect`, `core/support`) no lee ni registra credenciales.

---

## 6. Mejora aplicada · privacidad de la memoria emocional

Al margen de Git, la auditoría encontró una duplicación de datos personales
**dentro de la propia aplicación**:

`mood_log.texto_origen` guardaba una copia del mensaje completo del usuario que
ya estaba en la tabla `messages`. Era información privada duplicada sin ninguna
ganancia funcional.

Cambios hechos:

1. **`Memory.purge_mood_texts()`** vacía esa columna en las bases existentes.
   Se ejecuta **una sola vez** al arrancar (marca en `state`), es idempotente y
   conserva hora, emoción e intensidad, así que `get_mood_summary()` y
   `mood_risk_signal()` siguen funcionando exactamente igual.
2. **`main.py` ya no envía el texto** a `add_mood()`, ni por la ruta nueva ni
   por la clásica.
3. **Nueva tabla `affect_log`** con la lectura estructurada (emoción, valencia,
   activación, malestar, necesidad, confianza, sarcasmo, nivel de riesgo). El
   disparador se guarda **categorizado** —`estudios`, `relaciones`, `trabajo`,
   `salud`, `dinero`, `tecnico`, `otro`— y **nunca la frase literal**.

Resultado: YUE recuerda *cómo* ha estado el usuario y *por qué tipo* de cosas,
sin conservar una segunda copia de lo que dijo.
