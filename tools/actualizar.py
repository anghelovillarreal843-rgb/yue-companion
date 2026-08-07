"""Actualiza YUE desde un zip SIN dejar carpetas anidadas ni perder el .env.

Problema que evita: al descomprimir el zip dentro de la carpeta que ya existe,
Windows crea `yue_companion\\yue_companion\\…` y se acaba ejecutando la copia
vieja de fuera. Los arreglos "no funcionan" aunque estén en el disco.

Uso (desde la carpeta del proyecto):

    python tools/actualizar.py C:\\Users\\villa\\Downloads\\yue_companion_vision_avanzada.zip

Qué hace:

  1. comprueba que el zip es de YUE y ve qué versión trae,
  2. guarda una copia de seguridad de tu `.env`, `data/` y `models/vision/`,
  3. detecta si ya hay una carpeta anidada y avisa,
  4. copia los archivos nuevos ENCIMA, sin tocar `.env` ni `data/`,
  5. comprueba la integridad al terminar.

Nunca borra tu `.env`, tu base de datos ni tus modelos descargados.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Nunca se sobrescriben: son tuyos.
PROTEGIDOS = (".env", "data", "models")


def titulo(texto: str) -> None:
    print()
    print("=" * 70)
    print(texto)
    print("=" * 70)


def version_del_zip(carpeta: Path) -> str:
    archivo = carpeta / "vision" / "version.py"
    if not archivo.is_file():
        return "anterior a 2.3.0 (sin sello de versión)"
    try:
        for linea in archivo.read_text(encoding="utf-8").splitlines():
            if linea.startswith("VERSION"):
                return linea.split("=", 1)[1].strip().strip('"\'')
    except Exception:
        pass
    return "desconocida"


def version_actual() -> str:
    try:
        sys.path.insert(0, str(RAIZ))
        from vision import version as v
        return v.VERSION
    except Exception:
        return "anterior a 2.3.0 (sin sello de versión)"


def main() -> int:
    titulo("YUE — Actualizador")

    if len(sys.argv) < 2:
        print("  Falta la ruta del zip.")
        print()
        print("  Uso:")
        print("    python tools/actualizar.py RUTA_DEL_ZIP")
        print()
        print("  Ejemplo:")
        print(r"    python tools/actualizar.py C:\Users\villa\Downloads\yue.zip")
        return 1

    zip_path = Path(sys.argv[1]).expanduser()
    if not zip_path.is_file():
        print(f"  No encuentro el archivo: {zip_path}")
        return 1

    print(f"  Proyecto:  {RAIZ}")
    print(f"  Zip:       {zip_path}")
    print(f"  Versión instalada: {version_actual()}")

    # --- aviso de carpeta anidada -----------------------------------
    anidada = RAIZ / "yue_companion"
    if anidada.is_dir() and (anidada / "main.py").is_file():
        print()
        print("  AVISO: ya hay una carpeta anidada:")
        print(f"    {anidada}")
        print("  Es de una descompresión anterior mal hecha. Cuando termine")
        print("  esta actualización podrás borrarla sin miedo.")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # --- extraer -------------------------------------------------
        titulo("1. Leyendo el zip")
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmp_path)
        except Exception as exc:
            print(f"  No pude abrir el zip: {exc}")
            return 1

        # El zip trae un único directorio raíz: lo localizamos.
        origen = tmp_path
        if not (origen / "main.py").is_file():
            candidatos = [d for d in tmp_path.iterdir()
                          if d.is_dir() and (d / "main.py").is_file()]
            if not candidatos:
                print("  Este zip no parece ser de YUE (no encuentro main.py).")
                return 1
            origen = candidatos[0]

        print(f"  Contenido en: {origen.name}/")
        print(f"  Versión del zip: {version_del_zip(origen)}")

        # --- copia de seguridad -------------------------------------
        titulo("2. Copia de seguridad de lo tuyo")
        respaldo = RAIZ / "_respaldo_antes_de_actualizar"
        if respaldo.exists():
            shutil.rmtree(respaldo, ignore_errors=True)
        respaldo.mkdir(parents=True, exist_ok=True)
        guardados = []
        for nombre in PROTEGIDOS:
            fuente = RAIZ / nombre
            if not fuente.exists():
                continue
            destino = respaldo / nombre
            try:
                if fuente.is_dir():
                    shutil.copytree(fuente, destino, dirs_exist_ok=True)
                else:
                    shutil.copy2(fuente, destino)
                guardados.append(nombre)
            except Exception as exc:
                print(f"  No pude respaldar {nombre}: {exc}")
        print(f"  Guardado en: {respaldo}")
        for nombre in guardados:
            print(f"    · {nombre}")
        if not guardados:
            print("    (no había nada que respaldar)")

        # --- copiar archivos nuevos ---------------------------------
        titulo("3. Copiando la versión nueva")
        copiados = saltados = 0
        for ruta in origen.rglob("*"):
            if not ruta.is_file():
                continue
            relativa = ruta.relative_to(origen)
            primera = relativa.parts[0]
            # Nunca se pisa lo tuyo. Los modelos tampoco: pesan y ya están.
            if primera in PROTEGIDOS:
                saltados += 1
                continue
            if "__pycache__" in relativa.parts or relativa.suffix == ".pyc":
                continue
            destino = RAIZ / relativa
            try:
                destino.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ruta, destino)
                copiados += 1
            except Exception as exc:
                print(f"  No pude copiar {relativa}: {exc}")
        print(f"  Archivos actualizados: {copiados}")
        print(f"  Respetados (tuyos):    {saltados}")

    # --- limpiar cachés para que no se cargue código viejo ----------
    titulo("4. Limpiando cachés de Python")
    borradas = 0
    for cache in RAIZ.rglob("__pycache__"):
        try:
            shutil.rmtree(cache, ignore_errors=True)
            borradas += 1
        except Exception:
            pass
    print(f"  Carpetas __pycache__ borradas: {borradas}")

    # --- comprobar integridad ---------------------------------------
    titulo("5. Comprobación")
    for modulo in [m for m in list(sys.modules) if m.startswith("vision")]:
        sys.modules.pop(modulo, None)
    try:
        sys.path.insert(0, str(RAIZ))
        from vision import version as version_mod
        print(version_mod.informe())
        ok = version_mod.al_dia()
    except Exception as exc:
        print(f"  No pude comprobar la versión: {exc}")
        ok = False

    titulo("Listo" if ok else "Terminado con avisos")
    if ok:
        print("  Actualización correcta. Tu .env, tu base de datos y tus")
        print("  modelos siguen intactos.")
        if anidada.is_dir() and (anidada / "main.py").is_file():
            print()
            print("  Puedes borrar la carpeta anidada sobrante:")
            print(f"    rmdir /s /q \"{anidada}\"")
    print()
    print("  Siguiente paso:")
    print("    python diag_vision_avanzada.py --camara")
    print()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
