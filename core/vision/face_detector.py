"""Detección de rostro y presencia (punto 2 del pedido).

Usa MediaPipe FaceDetection (ligero) para saber si hay una persona y dónde está
su cara (posición normalizada 0..1). Si MediaPipe no está, degrada a un detector
Haar de OpenCV. Si tampoco hay OpenCV, informa "sin backend" sin romper nada.

Devuelve, además del hecho de presencia, el RECORTE de la cara (ROI) para que el
motor de emociones (DeepFace) trabaje solo sobre el rostro y no sobre todo el
fotograma: más rápido y más preciso.

No analiza emociones aquí: si no hay rostro, no hay nada que pasar al siguiente
paso. Privacidad: el ROI vive en memoria y nunca se guarda.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class FaceResult:
    user_present: bool
    faces: int = 0
    # Posición normalizada del centro del rostro principal (0..1). None si no hay.
    face_position: Optional[dict] = None
    # Caja del rostro principal en píxeles (x, y, w, h). None si no hay.
    box: Optional[tuple] = None
    # Recorte BGR del rostro principal (ndarray) para DeepFace. None si no hay.
    roi: object = None

    def as_dict(self) -> dict:
        """Forma JSON que pide el documento."""
        if not self.user_present:
            return {"user_present": False}
        out = {"user_present": True}
        if self.face_position:
            out["face_position"] = self.face_position
        return out


# Un "proveedor de cajas" recibe el frame y devuelve una lista de
# (x, y, w, h, score) en píxeles. Se inyecta en pruebas; en producción lo
# resuelve MediaPipe o Haar automáticamente.
BoxProvider = Callable[[object], list]


class FaceDetector:
    def __init__(self, box_provider: BoxProvider | None = None, min_confidence: float = 0.5) -> None:
        self.min_confidence = float(min_confidence)
        self._provider = box_provider
        self._backend = "inyectado" if box_provider else None
        self._mp_face = None
        self._haar = None
        if box_provider is None:
            self._init_backend()

    @property
    def backend(self) -> str:
        return self._backend or "ninguno"

    def _init_backend(self) -> None:
        # 1) MediaPipe FaceDetection (rápido, con posición normalizada).
        try:
            import mediapipe as mp

            self._mp_face = mp.solutions.face_detection.FaceDetection(
                model_selection=0, min_detection_confidence=self.min_confidence
            )
            self._backend = "mediapipe"
            return
        except Exception as exc:
            print("[vision.face] MediaPipe FaceDetection no disponible; pruebo Haar:", exc)

        # 2) Respaldo: Haar de OpenCV.
        try:
            import cv2
            from pathlib import Path

            path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
            haar = cv2.CascadeClassifier(str(path))
            if not haar.empty():
                self._haar = haar
                self._backend = "haar"
                return
        except Exception as exc:
            print("[vision.face] Haar no disponible:", exc)

        self._backend = None

    # -------------------------------------------------------------------
    def detect(self, frame_bgr) -> FaceResult:
        """Analiza un fotograma BGR y devuelve la presencia + ROI principal."""
        if frame_bgr is None:
            return FaceResult(user_present=False)

        boxes = self._boxes(frame_bgr)
        if not boxes:
            return FaceResult(user_present=False, faces=0)

        h, w = self._shape(frame_bgr)
        # Rostro principal = el de mayor área (el más cercano/relevante).
        x, y, bw, bh, _score = max(boxes, key=lambda b: b[2] * b[3])
        cx = (x + bw / 2.0) / max(1, w)
        cy = (y + bh / 2.0) / max(1, h)
        roi = self._crop(frame_bgr, x, y, bw, bh, w, h)
        return FaceResult(
            user_present=True,
            faces=len(boxes),
            face_position={"x": round(float(cx), 3), "y": round(float(cy), 3)},
            box=(int(x), int(y), int(bw), int(bh)),
            roi=roi,
        )

    # -------------------------------------------------------------------
    def _boxes(self, frame_bgr) -> list:
        if self._provider is not None:
            try:
                return list(self._provider(frame_bgr) or [])
            except Exception:
                return []
        if self._mp_face is not None:
            return self._boxes_mediapipe(frame_bgr)
        if self._haar is not None:
            return self._boxes_haar(frame_bgr)
        return []

    def _boxes_mediapipe(self, frame_bgr) -> list:
        try:
            import cv2

            h, w = self._shape(frame_bgr)
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            result = self._mp_face.process(rgb)
            out = []
            for det in getattr(result, "detections", None) or []:
                score = float((det.score or [0.0])[0]) if getattr(det, "score", None) else 0.0
                rbb = det.location_data.relative_bounding_box
                x = max(0, int(rbb.xmin * w))
                y = max(0, int(rbb.ymin * h))
                bw = max(1, int(rbb.width * w))
                bh = max(1, int(rbb.height * h))
                out.append((x, y, bw, bh, score))
            return out
        except Exception as exc:
            print("[vision.face] fallo MediaPipe:", exc)
            return []

    def _boxes_haar(self, frame_bgr) -> list:
        try:
            import cv2

            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            found = self._haar.detectMultiScale(
                gray, scaleFactor=1.12, minNeighbors=5, minSize=(70, 70)
            )
            return [(int(x), int(y), int(w), int(h), 1.0) for (x, y, w, h) in found]
        except Exception as exc:
            print("[vision.face] fallo Haar:", exc)
            return []

    @staticmethod
    def _shape(frame_bgr) -> tuple[int, int]:
        try:
            h, w = frame_bgr.shape[:2]
            return int(h), int(w)
        except Exception:
            return 480, 640

    @staticmethod
    def _crop(frame_bgr, x, y, bw, bh, w, h):
        try:
            # Un pequeño margen ayuda a DeepFace a encuadrar mejor.
            mx, my = int(bw * 0.12), int(bh * 0.12)
            x0 = max(0, x - mx)
            y0 = max(0, y - my)
            x1 = min(w, x + bw + mx)
            y1 = min(h, y + bh + my)
            roi = frame_bgr[y0:y1, x0:x1]
            return roi if getattr(roi, "size", 0) else None
        except Exception:
            return None

    def close(self) -> None:
        try:
            if self._mp_face is not None:
                self._mp_face.close()
        except Exception:
            pass
