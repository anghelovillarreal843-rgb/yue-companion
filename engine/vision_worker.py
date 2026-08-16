"""VisionWorker (PR 5, paso 7).

QThread del paquete de visión que viven en engine/ (mismo patrón que
engine/pc_worker.py): VisionWorker, VisionLegacyWorker, VisionDiagWorker,
OcrVisionWorker y VisionPreflightWorker. El VisionDirector los lanza y los
registra en ctx.workers.
"""
from PyQt5.QtCore import QThread, pyqtSignal

from core import screen_capture as vision


class VisionWorker(QThread):
    """MIRA la pantalla con el flujo completo: captura -> clasificación ->
    OCR y/o VisionRouter -> respuesta.

    ADITIVO: antes llamaba directo a `engine.look()` con un único proveedor y,
    si ese fallaba, YUE decía "no pude ver". Ahora la observación estructurada
    trae texto OCR aunque toda la visión multimodal se caiga, así que YUE puede
    seguir contando qué hay en pantalla.

    Emite `observed(object)` con la ScreenObservation para quien la quiera.
    """
    done = pyqtSignal(str)
    failed = pyqtSignal(str)
    observed = pyqtSignal(object)

    def __init__(self, engine, system, instruction, question="", force_fresh=True,
                 monitor=None):
        super().__init__()
        self.engine = engine
        self.system = system
        self.instruction = instruction
        self.question = question or instruction
        self.force_fresh = bool(force_fresh)
        self.monitor = monitor

    def run(self):
        try:
            obs = self.engine.look_screen(
                question=self.question, force_fresh=self.force_fresh,
                monitor=self.monitor,
            )
            try:
                self.observed.emit(obs)
            except Exception:
                pass
            if not obs:
                # Ni descripción visual ni texto: informamos del motivo real.
                motivo = " | ".join(str(e)[:160] for e in (obs.errors or []))
                self.failed.emit(motivo or "no obtuve nada de la pantalla")
                return
            respuesta = self.engine.answer_from_observation(
                obs, self.system, question=self.question)
            if not (respuesta or "").strip():
                # El router de texto tampoco respondió: al menos devolvemos lo
                # que se observó, en crudo, antes que un "no puedo ver".
                respuesta = (obs.visual_description
                             or ("Esto es lo que alcanzo a leer en tu pantalla:\n\n"
                                 + obs.ocr_text[:900]))
            self.done.emit(respuesta)
        except Exception as exc:
            self.failed.emit(str(exc))


class VisionLegacyWorker(QThread):
    """Camino antiguo (una sola llamada a `engine.look()` con la captura).

    Se conserva para el diagnóstico y para cualquier flujo que ya dependiera de
    él; el flujo normal usa VisionWorker.
    """
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, engine, system, instruction):
        super().__init__()
        self.engine = engine
        self.system = system
        self.instruction = instruction

    def run(self):
        try:
            self.done.emit(self.engine.look(self.system, vision.capture_b64(), self.instruction))
        except Exception as exc:
            self.failed.emit(str(exc))


class VisionDiagWorker(QThread):
    """Autodiagnóstico de visión: prueba CAPTURA y MODELO por separado."""
    done = pyqtSignal(object)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def run(self):
        report = []
        # 0) FILA de proveedores visuales (sin exponer ninguna clave).
        try:
            fila = self.engine.vision_router_report()
            if fila:
                disponibles = [p for p in fila if p.get("disponible")]
                detalle = " · ".join(
                    f"{p['nombre']}/{p['modelo'][:34]}"
                    f"{'' if p.get('disponible') else ' (' + (p.get('ultimo_error') or 'sin clave')[:28] + ')'}"
                    for p in fila[:5]
                )
                report.append((
                    f"Fila visual ({len(disponibles)}/{len(fila)} disponibles)",
                    bool(disponibles), detalle,
                ))
            else:
                report.append(("Fila visual", False,
                               "vacía (VISION_PROVIDER=none o sin claves multimodales)"))
        except Exception as exc:
            report.append(("Fila visual", False, str(exc)[:200]))
        # 0b) Endpoint único de compatibilidad.
        try:
            info = self.engine.vision_diag_info()
            report.append((
                "Endpoint de compatibilidad",
                bool(info.get("ok")),
                f"host={info.get('host','?')} · modelo={info.get('modelo','?')} · clave={info.get('clave','?')}",
            ))
        except Exception as exc:
            report.append(("Endpoint de compatibilidad", False, str(exc)[:200]))
        # 0c) Motor OCR (es el respaldo final: importa saber si existe).
        try:
            from core import screen_ocr
            hay_ocr = screen_ocr.available()
            report.append(("Motor OCR (respaldo)", bool(hay_ocr),
                           f"motor={screen_ocr.engine_name()}"))
        except Exception as exc:
            report.append(("Motor OCR (respaldo)", False, str(exc)[:200]))
        # 1) Captura de pantalla (con monitores y antigüedad del frame).
        info_cap = vision.capture_info()
        if info_cap["ok"]:
            report.append((
                "Captura de pantalla", True,
                f"{info_cap['width']}x{info_cap['height']}px · monitor "
                f"{info_cap.get('monitor', 1)}/{info_cap.get('monitors', 1)} · "
                f"{info_cap['bytes']} bytes · frame_age {info_cap.get('frame_age', 0)}s · "
                f"método {info_cap.get('method', '?')}",
            ))
        else:
            report.append(("Captura de pantalla", False, info_cap["note"]))
            self.done.emit(report)
            return
        # 2) Mirada REAL de principio a fin (clasificación + visión + OCR).
        try:
            obs = self.engine.look_screen(
                question="¿Qué se ve en la pantalla? Una sola frase.",
                force_fresh=True,
            )
            report.append((
                "Clasificación de contenido", True,
                f"tipo={obs.detected_content_type} · confianza={obs.confidence:.2f} · "
                f"estrategia={obs.strategy}",
            ))
            report.append((
                "Visión multimodal", obs.has_vision(),
                (f"{obs.provider_used}/{obs.model_used}: {obs.visual_description[:150]}"
                 if obs.has_vision()
                 else " | ".join(str(e)[:120] for e in obs.errors) or "sin respuesta"),
            ))
            report.append((
                "OCR de pantalla", obs.has_text(),
                f"{obs.ocr_chars} caracteres leídos" if obs.has_text() else "sin texto legible",
            ))
        except Exception as exc:
            report.append(("Mirada completa", False, str(exc)[:300]))
        self.done.emit(report)


class OcrVisionWorker(QThread):
    """Visión por OCR: lee el texto de la pantalla y responde con el modelo de
    chat (no necesita clave de visión multimodal). Corre fuera del hilo de UI."""
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, engine, system, instruction):
        super().__init__()
        self.engine = engine
        self.system = system
        self.instruction = instruction

    def run(self):
        try:
            self.done.emit(self.engine.look_ocr(self.system, self.instruction))
        except Exception as exc:
            self.failed.emit(str(exc))


class VisionPreflightWorker(QThread):
    """Valida al arrancar la clave/proveedor de visión SIN gastar tokens.

    Corre en segundo plano para no bloquear la interfaz; emite el dict que
    devuelve engine.preflight_vision().
    """
    done = pyqtSignal(object)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def run(self):
        try:
            self.done.emit(self.engine.preflight_vision())
        except Exception as exc:
            self.done.emit({"ok": False, "reason": "error", "detail": str(exc)[:200]})