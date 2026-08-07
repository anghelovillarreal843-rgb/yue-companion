"""Preparación de la imagen antes del OCR (punto 5 del pedido).

Un fotograma de webcam con una hoja de papel en la mano es el peor caso posible
para un OCR: perspectiva, sombra, desenfoque e inclinación. Este módulo lo
arregla en cuatro pasos, todos con OpenCV clásico:

  1. `find_document`   -> busca el cuadrilátero de la hoja/cartel/pantalla,
  2. `warp`            -> corrige la perspectiva y recorta la zona de interés,
  3. `deskew`          -> endereza la inclinación residual del texto,
  4. `enhance`         -> mejora contraste (CLAHE), nitidez y binariza si toca.

`prepare()` encadena los cuatro y devuelve la imagen lista para el OCR. Si no
hay OpenCV, devuelve el fotograma tal cual: el OCR seguirá intentándolo.
"""
from __future__ import annotations

import logging

log = logging.getLogger("vision.ocr.scanner")


def _cv2():
    try:
        import cv2
        return cv2
    except Exception:  # pragma: no cover
        return None


def _np():
    try:
        import numpy as np
        return np
    except Exception:  # pragma: no cover
        return None


class DocumentScanner:
    """Convierte un fotograma con un documento en una imagen plana y legible."""

    def __init__(self, min_area_ratio: float = 0.08, target_width: int = 1000) -> None:
        self.min_area_ratio = float(min_area_ratio)
        self.target_width = int(target_width)

    # ------------------------------------------------------------------
    def find_document(self, frame_bgr):
        """Cuadrilátero del documento (4 puntos) o None si no se distingue."""
        cv2, np = _cv2(), _np()
        if cv2 is None or np is None or frame_bgr is None:
            return None
        try:
            h, w = frame_bgr.shape[:2]
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
            gray = cv2.GaussianBlur(gray, (5, 5), 0)
            edges = cv2.Canny(gray, 60, 180)
            edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            frame_area = float(w * h)
            best, best_area = None, 0.0
            for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
                area = cv2.contourArea(cnt)
                if area / frame_area < self.min_area_ratio:
                    continue
                peri = cv2.arcLength(cnt, True)
                approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
                if len(approx) == 4 and cv2.isContourConvex(approx) and area > best_area:
                    best, best_area = approx.reshape(4, 2).astype("float32"), area
            return best
        except Exception as exc:
            log.debug("no pude buscar el documento: %s", exc)
            return None

    # ------------------------------------------------------------------
    def warp(self, frame_bgr, quad=None):
        """Corrige la perspectiva del cuadrilátero. Sin quad, devuelve el original."""
        cv2, np = _cv2(), _np()
        if cv2 is None or np is None or frame_bgr is None:
            return frame_bgr
        if quad is None:
            quad = self.find_document(frame_bgr)
        if quad is None:
            return frame_bgr
        try:
            rect = self._order_points(quad, np)
            (tl, tr, br, bl) = rect
            width = int(max(self._norm(br - bl, np), self._norm(tr - tl, np)))
            height = int(max(self._norm(tr - br, np), self._norm(tl - bl, np)))
            if width < 40 or height < 40:
                return frame_bgr
            dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
                           dtype="float32")
            matrix = cv2.getPerspectiveTransform(rect, dst)
            return cv2.warpPerspective(frame_bgr, matrix, (width, height))
        except Exception as exc:
            log.debug("no pude corregir la perspectiva: %s", exc)
            return frame_bgr

    @staticmethod
    def _order_points(pts, np):
        """Ordena en [arriba-izq, arriba-der, abajo-der, abajo-izq]."""
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[int(s.argmin())]
        rect[2] = pts[int(s.argmax())]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[int(diff.argmin())]
        rect[3] = pts[int(diff.argmax())]
        return rect

    @staticmethod
    def _norm(vec, np) -> float:
        return float(((vec[0]) ** 2 + (vec[1]) ** 2) ** 0.5)

    # ------------------------------------------------------------------
    def crop(self, frame_bgr, box: dict, margin: float = 0.04):
        """Recorta una caja normalizada {x,y,w,h} con un pequeño margen."""
        if frame_bgr is None or not box:
            return frame_bgr
        try:
            h, w = frame_bgr.shape[:2]
            x1 = max(0, int((box["x"] - margin) * w))
            y1 = max(0, int((box["y"] - margin) * h))
            x2 = min(w, int((box["x"] + box["w"] + margin) * w))
            y2 = min(h, int((box["y"] + box["h"] + margin) * h))
            if x2 - x1 < 12 or y2 - y1 < 8:
                return frame_bgr
            return frame_bgr[y1:y2, x1:x2]
        except Exception:
            return frame_bgr

    # ------------------------------------------------------------------
    def deskew(self, image, max_angle: float = 20.0):
        """Endereza la inclinación del texto usando el rectángulo mínimo."""
        cv2, np = _cv2(), _np()
        if cv2 is None or np is None or image is None:
            return image
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
            thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
            coords = cv2.findNonZero(thresh)
            if coords is None or len(coords) < 40:
                return image
            angle = cv2.minAreaRect(coords)[-1]
            if angle < -45:
                angle = 90 + angle
            if abs(angle) < 0.4 or abs(angle) > max_angle:
                return image
            h, w = image.shape[:2]
            matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
            return cv2.warpAffine(image, matrix, (w, h),
                                  flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        except Exception as exc:
            log.debug("no pude enderezar: %s", exc)
            return image

    # ------------------------------------------------------------------
    def enhance(self, image, binarize: bool = False):
        """Mejora contraste y nitidez (CLAHE + realce). Opcionalmente binariza."""
        cv2, np = _cv2(), _np()
        if cv2 is None or np is None or image is None:
            return image
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
            # Escala a un ancho cómodo para el OCR (ni diminuto ni gigante).
            h, w = gray.shape[:2]
            if w > 0 and abs(w - self.target_width) / self.target_width > 0.3:
                scale = self.target_width / float(w)
                gray = cv2.resize(gray, (int(w * scale), int(h * scale)),
                                  interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
            clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
            gray = clahe.apply(gray)
            gray = cv2.bilateralFilter(gray, 5, 55, 55)
            # Realce de nitidez suave (unsharp mask).
            blur = cv2.GaussianBlur(gray, (0, 0), 2.0)
            gray = cv2.addWeighted(gray, 1.5, blur, -0.5, 0)
            if binarize:
                gray = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                             cv2.THRESH_BINARY, 31, 11)
            return gray
        except Exception as exc:
            log.debug("no pude mejorar la imagen: %s", exc)
            return image

    # ------------------------------------------------------------------
    def prepare(self, frame_bgr, box: dict | None = None, binarize: bool = False):
        """Encadena recorte -> perspectiva -> enderezado -> realce."""
        img = frame_bgr
        if img is None:
            return None
        if box:
            img = self.crop(img, box)
        quad = self.find_document(img)
        if quad is not None:
            img = self.warp(img, quad)
        img = self.deskew(img)
        return self.enhance(img, binarize=binarize)

    def sharpness(self, image) -> float:
        """Varianza del laplaciano: sirve para descartar fotogramas borrosos."""
        cv2 = _cv2()
        if cv2 is None or image is None:
            return 0.0
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
            return float(cv2.Laplacian(gray, cv2.CV_64F).var())
        except Exception:
            return 0.0
