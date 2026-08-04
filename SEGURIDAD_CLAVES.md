# Seguridad de claves — acción requerida

> **Prioridad crítica.** El ZIP que compartiste traía claves API **reales**, y no
> solo en `.env`: también estaban dentro de `.env.example` (el archivo que se
> supone es la plantilla pública). Cualquiera con ese ZIP pudo usarlas.

## 1. Claves que debes revocar y regenerar YA

Estas claves ya se consideran **comprometidas** por haber viajado en el ZIP.
No basta con borrarlas del archivo: hay que **revocarlas en el panel del
proveedor** y generar unas nuevas.

| Proveedor | Variable | Dónde revocar |
|-----------|----------|----------------|
| **Groq**  | `GROQ_API_KEY` (aparecía en `.env` **y** en `.env.example`) | https://console.groq.com/keys → borra la clave y crea otra |
| **Together AI** | `TOGETHER_API_KEY` (aparecía en `.env`) | https://api.together.xyz/settings/api-keys → revoca y regenera |

Si en algún momento configuraste `BFL_API_KEY` u `OPENAI` con valor real,
revócalas también por precaución.

## 2. Cómo quedó la plantilla

- `.env.example` ya **no** contiene la clave real: ahora dice
  `GROQ_API_KEY=TU_CLAVE_GROQ_AQUI`. Ese es el archivo que **sí** puedes
  compartir.
- Tu `.env` real **no** debe compartirse nunca. El `.gitignore` y el
  empaquetador ya lo excluyen.

## 3. Flujo seguro a partir de ahora

1. Pon tus claves nuevas **solo** en `.env` (local, privado).
2. Antes de compartir o crear el instalador, ejecuta el detector:

   ```bash
   python -m tools.secret_scanner
   ```

   Si encuentra algo de confianza **ALTA**, para y arréglalo (sale con código
   distinto de cero, así puedes usarlo en CI).

3. Genera el paquete limpio con el constructor (hace todo el proceso seguro):

   ```bash
   python -m tools.build_release
   ```

   Copia el proyecto a una carpeta temporal, borra datos personales y cachés,
   comprueba que no haya `.env` ni secretos, y solo entonces crea el ZIP en
   `dist/`. Si detecta una clave real, **aborta** en vez de empaquetarla.

## 4. Nota sobre el historial

Si este proyecto está en un repositorio Git, borrar la clave en el último commit
**no** la quita del historial. Como ya la vas a revocar, lo importante es
regenerarla; opcionalmente puedes reescribir el historial con
`git filter-repo` o el BFG Repo-Cleaner.
