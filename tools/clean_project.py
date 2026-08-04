"""Limpieza del proyecto (FASE 1 · Seguridad y privacidad).

Quita del árbol todo lo que NO debe viajar en un ZIP o instalador: cachés de
Python, bases de datos con datos reales, logs personales, memoria del usuario,
capturas, audios y documentos generados durante las pruebas.

Por seguridad:
  - Por defecto es SIMULACIÓN (dry-run): solo enseña qué borraría, no borra nada.
  - Con --apply borra de verdad.
  - NUNCA toca el código fuente ni los assets del avatar (final.vrm, yue.png...).
  - build_release.py lo usa sobre una COPIA, así tu carpeta de trabajo y tus
    recuerdos reales quedan intactos.

Uso:
    python -m tools.clean_project                 # simulación sobre el proyecto
    python -m tools.clean_project --apply         # borra de verdad (¡cuidado!)
    python -m tools.clean_project --path carpeta --apply
"""
from __future__ import annotations

import argparse
import fnmatch
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Carpetas completas a eliminar (relativas a la raíz).
_DIRS_TO_REMOVE = [
    "__pycache__",           # se regenera solo
    "data/logs",             # logs personales del agente
    "data/memories",         # recuerdos del usuario
    "data/autonomy",         # creaciones/knowledge personales
    "data/learning",         # recetas aprendidas (pueden traer rutas privadas)
    "data/teacher",          # notas, asistencia, alumnos reales
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
]

# Patrones de archivos a eliminar en TODO el árbol.
_GLOBS_TO_REMOVE = [
    "*.pyc", "*.pyo",
    "*.log", "*.jsonl",
    "*.mp3", "*.wav", "*.ogg",
    "*.tmp", "*.temp", "*.bak",
    "*~",
]

# Archivos concretos en la raíz que son datos generados / personales.
_ROOT_FILES_TO_REMOVE = [
    ".env",                                  # secretos reales
    "data/yue.db",                           # memoria real del usuario
    "data/apps_catalogo.json",               # catálogo de apps del equipo real
    "informe_anemia.docx",                   # documentos generados en pruebas
    "trabajo_anemia.pptx",
    "Tabla de multiplicar del 1 al 20.xlsx",
]

# Nunca borrar estos patrones aunque encajen con lo de arriba.
_NEVER = ["*.py", "*.md", "*.txt", "*.example", "*.spec", ".gitignore",
          "requirements.txt", "*.vrm", "*.png", "*.xml", "*.task", "*.onnx", "*.bin"]


def _protected(path: Path) -> bool:
    name = path.name
    return any(fnmatch.fnmatch(name, pat) for pat in _NEVER)


def plan(root: Path):
    """Calcula (dirs, files) que se eliminarían, sin tocar nada."""
    dirs, files = [], []

    for rel in _DIRS_TO_REMOVE:
        p = root / rel
        if p.exists() and p.is_dir():
            dirs.append(p)

    removed_dirs = {d.resolve() for d in dirs}

    for pat in _GLOBS_TO_REMOVE:
        for p in root.rglob(pat):
            if not p.is_file() or _protected(p):
                continue
            if any(parent.resolve() in removed_dirs for parent in p.parents):
                continue  # ya cae dentro de una carpeta que se elimina entera
            files.append(p)

    for rel in _ROOT_FILES_TO_REMOVE:
        p = root / rel
        if p.exists() and p.is_file():
            if any(parent.resolve() in removed_dirs for parent in p.parents):
                continue
            files.append(p)

    # Únicos y ordenados
    files = sorted(set(files))
    return dirs, files


def clean(root: Path, apply: bool = False):
    dirs, files = plan(root)

    print(f"[clean] Proyecto: {root}")
    print(f"[clean] Modo: {'BORRADO REAL' if apply else 'SIMULACIÓN (usa --apply para borrar)'}\n")

    if not dirs and not files:
        print("[clean] Nada que limpiar. El proyecto ya está limpio.")
        return

    if dirs:
        print("Carpetas a eliminar:")
        for d in dirs:
            print(f"  - {d.relative_to(root)}/")
    if files:
        print("\nArchivos a eliminar:")
        for f in files:
            print(f"  - {f.relative_to(root)}")

    if not apply:
        print("\n[clean] Simulación. No se borró nada.")
        return

    for f in files:
        try:
            f.unlink()
        except OSError as e:
            print(f"  ! no se pudo borrar {f}: {e}")
    for d in dirs:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except OSError as e:
            print(f"  ! no se pudo borrar {d}: {e}")

    print(f"\n[clean] Listo. {len(files)} archivo(s) y {len(dirs)} carpeta(s) eliminadas.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Limpieza del proyecto YUE")
    ap.add_argument("--path", default=str(ROOT))
    ap.add_argument("--apply", action="store_true", help="Borra de verdad")
    args = ap.parse_args(argv)
    clean(Path(args.path).resolve(), apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
