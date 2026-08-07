"""Descarga los modelos de visión de YUE (con autorización explícita).

El `ModelRegistry` NUNCA descarga nada por su cuenta, a propósito. Esta
herramienta es la vía consciente para traerlos: te dice qué falta, cuánto pesa,
de dónde sale y te PIDE confirmación antes de bajar nada.

Uso:

    python tools/download_vision_models.py              # interactivo
    python tools/download_vision_models.py --si         # sin preguntar
    python tools/download_vision_models.py --lista      # solo mostrar qué falta
    python tools/download_vision_models.py --minimo     # solo lo imprescindible
    python tools/download_vision_models.py hand_landwarker pose_landmarker

Los archivos van a `models/vision/`. Vienen de los servidores oficiales de
Google MediaPipe. Si una descarga falla, se borra el archivo a medias para no
dejar un modelo corrupto.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vision.models.model_registry import SPECS, ModelRegistry  # noqa: E402

# Lo mínimo para que YUE vea de verdad: rostro, manos, postura y objetos.
MINIMOS = ("face_detector", "face_landmarker", "hand_landmarker",
           "pose_landmarker", "object_detector")

# Qué habilita cada modelo, para que la decisión sea informada.
HABILITA = {
    "face_detector": "presencia de personas y conteo",
    "face_landmarker": "expresiones y estimación de ánimo",
    "pose_landmarker": "postura y la mayoría de las acciones",
    "hand_landmarker": "manos, dedos y gestos",
    "gesture_recognizer": "catálogo de gestos (respaldo de manos)",
    "object_detector": "objetos, y con ellos beber/comer/usar el celular",
    "image_classifier": "pista extra del tipo de escena (opcional)",
    "image_segmenter": "segmentación (opcional, no usada por defecto)",
    "interactive_segmenter": "segmentación interactiva (opcional)",
}

MB = 1024 * 1024


def humano(n: int) -> str:
    if n <= 0:
        return "?"
    if n < MB:
        return f"{n / 1024:.0f} KB"
    return f"{n / MB:.1f} MB"


def barra(descargado: int, total: int, ancho: int = 30) -> str:
    if total <= 0:
        return f"  {humano(descargado)}"
    proporcion = min(1.0, descargado / total)
    llenos = int(proporcion * ancho)
    return (f"  [{'█' * llenos}{'·' * (ancho - llenos)}] "
            f"{proporcion * 100:5.1f}%  {humano(descargado)}/{humano(total)}")


def descargar(url: str, destino: Path, nombre: str) -> bool:
    """Descarga con barra de progreso. Limpia el archivo si falla."""
    import urllib.error
    import urllib.request

    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_suffix(destino.suffix + ".parcial")
    inicio = time.time()
    try:
        peticion = urllib.request.Request(url, headers={"User-Agent": "YUE/1.0"})
        with urllib.request.urlopen(peticion, timeout=60) as respuesta:
            total = int(respuesta.headers.get("Content-Length") or 0)
            descargado = 0
            with open(temporal, "wb") as fh:
                while True:
                    bloque = respuesta.read(64 * 1024)
                    if not bloque:
                        break
                    fh.write(bloque)
                    descargado += len(bloque)
                    print(barra(descargado, total), end="\r", flush=True)
        print(" " * 70, end="\r")
        temporal.replace(destino)
        segundos = time.time() - inicio
        print(f"  OK  {nombre}: {humano(destino.stat().st_size)} en {segundos:.1f} s")
        return True
    except urllib.error.HTTPError as exc:
        print(f"  ERROR {nombre}: el servidor respondió {exc.code}")
    except urllib.error.URLError as exc:
        print(f"  ERROR {nombre}: sin conexión ({exc.reason})")
    except KeyboardInterrupt:
        print(f"\n  Cancelado por el usuario.")
        raise
    except Exception as exc:
        print(f"  ERROR {nombre}: {exc}")
    finally:
        if temporal.exists():
            try:
                temporal.unlink()
            except Exception:
                pass
    return False


def main() -> int:
    argumentos = [a for a in sys.argv[1:]]
    sin_preguntar = "--si" in argumentos or "-y" in argumentos
    solo_lista = "--lista" in argumentos or "-l" in argumentos
    solo_minimo = "--minimo" in argumentos or "-m" in argumentos
    pedidos = [a for a in argumentos if not a.startswith("-")]

    registro = ModelRegistry()
    print()
    print("=" * 70)
    print("YUE — Modelos de visión")
    print("=" * 70)
    print(f"Carpeta: {registro.base}")
    print()

    # ---------------- estado actual ----------------
    faltan: list[str] = []
    for clave, spec in SPECS.items():
        estado = registro.verify(clave)
        marca = "[ OK ]" if estado.valid else "[FALTA]"
        detalle = humano(estado.size) if estado.valid else (estado.reason or "")
        print(f"  {marca} {clave:22s} {HABILITA.get(clave, ''):45s} {detalle}")
        if not estado.valid and spec.url:
            faltan.append(clave)

    sin_url = [k for k, s in SPECS.items()
               if not registro.verify(k).valid and not s.url]
    if sin_url:
        print()
        print("  Sin URL oficial (opcionales, no se descargan): "
              + ", ".join(sin_url))

    if not faltan:
        print()
        print("  Todos los modelos descargables ya están instalados.")
        return 0

    # ---------------- selección ----------------
    if pedidos:
        desconocidos = [p for p in pedidos if p not in SPECS]
        if desconocidos:
            print(f"\n  No conozco: {', '.join(desconocidos)}")
            print(f"  Nombres válidos: {', '.join(SPECS)}")
            return 1
        objetivo = [p for p in pedidos if p in faltan]
        ya = [p for p in pedidos if p not in faltan]
        if ya:
            print(f"\n  Ya instalados, los salto: {', '.join(ya)}")
    elif solo_minimo:
        objetivo = [k for k in MINIMOS if k in faltan]
    else:
        objetivo = list(faltan)

    if not objetivo:
        print("\n  No hay nada que descargar con esos criterios.")
        return 0

    print()
    print("-" * 70)
    print("Voy a descargar:")
    for clave in objetivo:
        print(f"  · {clave:22s} {SPECS[clave].description}")
        print(f"    {SPECS[clave].url}")
    print("-" * 70)
    print("Origen: servidores oficiales de Google MediaPipe.")
    print("Tamaño total aproximado: 20-40 MB.")

    if solo_lista:
        print()
        print("Modo lista: no descargo nada. Quita --lista para hacerlo.")
        return 0

    if not sin_preguntar:
        print()
        try:
            respuesta = input("¿Descargo estos modelos? [s/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelado.")
            return 1
        if respuesta not in {"s", "si", "sí", "y", "yes"}:
            print("Cancelado. No he descargado nada.")
            return 0

    # ---------------- descarga ----------------
    print()
    correctos, fallidos = [], []
    for clave in objetivo:
        spec = SPECS[clave]
        destino = registro.base / spec.filename
        print(f"Descargando {clave}…")
        try:
            if descargar(spec.url, destino, clave):
                estado = registro.verify(clave)
                if estado.valid:
                    correctos.append(clave)
                else:
                    print(f"  AVISO {clave}: descargado pero no pasa la validación "
                          f"({estado.reason})")
                    fallidos.append(clave)
            else:
                fallidos.append(clave)
        except KeyboardInterrupt:
            print("\nInterrumpido. Los modelos ya descargados se conservan.")
            break

    # ---------------- resumen ----------------
    print()
    print("=" * 70)
    print(f"Descargados: {len(correctos)}   Fallidos: {len(fallidos)}")
    if fallidos:
        print("Fallaron: " + ", ".join(fallidos))
        print("Puedes bajarlos a mano y ponerlos en:")
        print(f"  {registro.base}")
    print()
    print("Comprueba el resultado con:")
    print("  python diag_vision_avanzada.py")
    print("o dentro de YUE:  /vision modelos")
    print()
    return 0 if not fallidos else 1


if __name__ == "__main__":
    raise SystemExit(main())
