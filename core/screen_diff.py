"""Firma perceptual de la pantalla: sirve para saber si una acción hizo algo.

Combina dos señales baratas:
  * aHash 8x8 (64 bits) -> detecta cambios estructurales (ventana nueva, menú).
  * diff de píxeles sobre una miniatura gris -> detecta cambios pequeños
    (una letra escrita, un cursor que se movió).

Se considera que la pantalla CAMBIÓ si cualquiera de las dos lo indica: preferimos
un falso "sí cambió" antes que marcar como fallida una acción que sí funcionó.

Solo usa Pillow + pyautogui/mss, que ya están en requirements.txt.
"""
from __future__ import annotations

from dataclasses import dataclass

import config

_THUMB = (320, 180)
_HASH_SIZE = 8


@dataclass
class Signature:
    """Firma de una pantalla: hash perceptual + miniatura en gris."""
    hash: str
    thumb: object = None      # PIL.Image en escala de grises (puede ser None)

    def __bool__(self) -> bool:
        return bool(self.hash)


def _grab():
    """Captura la pantalla completa como PIL.Image (o None si no se puede)."""
    try:
        import pyautogui
        return pyautogui.screenshot()
    except Exception as exc:
        print("[screen-diff] captura no disponible:", exc)
        return None


def _mask(image, exclude_rects):
    """Pinta de negro las zonas a ignorar (el avatar de Yue se mueve solo).

    Sin esto, el VRM parpadeando y respirando hace que la pantalla SIEMPRE
    parezca cambiada y la verificación no detecte nunca un "sin efecto visible".
    """
    if not exclude_rects or image is None:
        return image
    try:
        from PIL import ImageDraw
        copia = image.copy()
        lienzo = ImageDraw.Draw(copia)
        ancho, alto = copia.size
        for rect in exclude_rects:
            try:
                izq, arr, der, aba = (int(v) for v in rect)
            except Exception:
                continue
            izq = max(0, min(izq, ancho))
            arr = max(0, min(arr, alto))
            der = max(0, min(der, ancho))
            aba = max(0, min(aba, alto))
            if der > izq and aba > arr:
                lienzo.rectangle([izq, arr, der, aba], fill=0)
        return copia
    except Exception as exc:
        print("[screen-diff] no pude enmascarar:", exc)
        return image


def ahash(image, size: int = _HASH_SIZE) -> str:
    """Hash perceptual promedio en hexadecimal."""
    try:
        small = image.convert("L").resize((size, size))
        pixels = list(small.getdata())
        media = sum(pixels) / len(pixels)
        bits = "".join("1" if p >= media else "0" for p in pixels)
        return f"{int(bits, 2):0{size * size // 4}x}"
    except Exception as exc:
        print("[screen-diff] no pude calcular el hash:", exc)
        return ""


def hamming(a: str, b: str) -> int:
    """Distancia de Hamming entre dos hashes hexadecimales."""
    if not a or not b or len(a) != len(b):
        return 999
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def signature_from_image(image, exclude_rects=None) -> Signature:
    if image is None:
        return Signature("", None)
    try:
        gris = image.convert("L")
        gris = _mask(gris, exclude_rects)
        thumb = gris.resize(_THUMB)
    except Exception:
        gris, thumb = image, None
    return Signature(ahash(gris), thumb)


def signature(exclude_rects=None) -> Signature:
    """Firma de la pantalla actual, ignorando las zonas indicadas."""
    return signature_from_image(_grab(), exclude_rects)


def pixel_diff_ratio(a, b) -> float:
    """Proporción de píxeles de la miniatura que cambiaron de forma apreciable."""
    if a is None or b is None:
        return 0.0
    try:
        from PIL import ImageChops
        diff = ImageChops.difference(a, b)
        datos = diff.getdata()
        umbral = int(getattr(config, "PC_VERIFY_PIXEL_DELTA", 14))
        cambiados = sum(1 for value in datos if value >= umbral)
        return cambiados / max(1, len(datos))
    except Exception as exc:
        print("[screen-diff] no pude comparar píxeles:", exc)
        return 0.0


def tile_diff_max_ratio(a, b, tiles: int = 0) -> float:
    """Mayor proporción de cambio encontrada en UNA sola celda de la miniatura.

    El ratio global falla con cambios pequeños: pulsar el «3» en la calculadora
    solo repinta el visor, unos pocos píxeles de una miniatura de 320x180. Eso
    queda MUY por debajo de PC_VERIFY_PIXEL_RATIO y la acción se marcaba como
    «sin efecto visible» aunque hubiera funcionado. Partiendo la miniatura en
    celdas, ese mismo cambio pesa mucho dentro de su celda y sí se detecta.
    """
    if a is None or b is None:
        return 0.0
    try:
        from PIL import ImageChops
        tiles = int(tiles or getattr(config, "PC_VERIFY_TILES", 8))
        tiles = max(2, min(tiles, 16))
        diff = ImageChops.difference(a, b)
        ancho, alto = diff.size
        paso_x = max(1, ancho // tiles)
        paso_y = max(1, alto // tiles)
        umbral = int(getattr(config, "PC_VERIFY_PIXEL_DELTA", 14))
        mejor = 0.0
        for arr in range(0, alto, paso_y):
            for izq in range(0, ancho, paso_x):
                celda = diff.crop((izq, arr, min(izq + paso_x, ancho),
                                   min(arr + paso_y, alto)))
                datos = celda.getdata()
                total = len(datos)
                if not total:
                    continue
                cambiados = sum(1 for value in datos if value >= umbral)
                mejor = max(mejor, cambiados / total)
        return mejor
    except Exception as exc:
        print("[screen-diff] no pude comparar por celdas:", exc)
        return 0.0


def changed(before: Signature | None, after: Signature | None) -> bool:
    """True si la pantalla cambió entre las dos firmas."""
    if not before or not after:
        return True                      # ante la duda, no castigamos la acción
    distancia = hamming(before.hash, after.hash)
    if distancia >= int(getattr(config, "PC_VERIFY_HASH_DISTANCE", 1)):
        return True
    ratio = pixel_diff_ratio(before.thumb, after.thumb)
    if ratio >= float(getattr(config, "PC_VERIFY_PIXEL_RATIO", 0.0015)):
        return True
    # Tercera señal: un cambio pequeño pero concentrado (un dígito, un cursor,
    # una casilla marcada) también cuenta como efecto real.
    local = tile_diff_max_ratio(before.thumb, after.thumb)
    return local >= float(getattr(config, "PC_VERIFY_TILE_RATIO", 0.02))
