"""ScreenAnalyzer: el "mirar la pantalla" de YUE, de principio a fin.

    usuario ("mira mi pantalla")
        |
    ScreenCapture   (monitor correcto, validada, con timestamp)
        |
    clasificacion rapida y LOCAL del contenido
        |
    +-----------+-----------------+-----------+
    |           |                 |           |
   OCR      VisionRouter        ambos    (segun el tipo)
    |           |                 |
  AIRouter   APIs multimodales    |
    +-----------+-----------------+
        |
    ScreenObservation  ->  YUE

Reglas que cumple:
  * Si la vision multimodal falla ENTERA, cae a OCR. YUE nunca dice "no puedo
    ver" si al menos pudo leer texto.
  * Nunca reutiliza una captura vieja: valida `frame_age` antes de analizar.
  * Cachea por hash perceptual, pero SIEMPRE captura de nuevo cuando el usuario
    dice "mira ahora" / "que ves ahora" / "mira mi pantalla".
  * Guarda una memoria temporal corta (5 frames) para entender cambios en video
    sin mandar los cinco frames en cada peticion.
  * Detecta video por MOVIMIENTO, no solo por audio del sistema.

ADITIVO: no borra nada. `AIEngine.look()` y `look_ocr()` siguen existiendo.
"""
from __future__ import annotations

import re
import time
from collections import deque

from core import screen_capture, screen_ocr
from core.screen_observation import (
    ScreenObservation, active_window_info, classify_screen, strategy_for,
)

try:
    from core import screen_diff
except Exception:  # pragma: no cover
    screen_diff = None


def _cfg(nombre, defecto):
    try:
        import config
        return getattr(config, nombre, defecto)
    except Exception:
        return defecto


def _log(msg: str):
    print(f"[VISION] {msg}")


# Frases donde el usuario pide EXPLICITAMENTE una mirada nueva: aqui jamas se
# reutiliza cache, aunque la pantalla no haya cambiado.
_EXIGE_FRESCO = re.compile(
    r"\b(ahora|ahorita|en este momento|de nuevo|otra vez|vuelve a mirar|"
    r"actualiza|refresca|mira mi pantalla|mira la pantalla|que ves ahora|"
    r"que estoy viendo|mira esto|mira aqui|mira aca)\b",
    re.I,
)


# --------------------------------------------------------------- prompts
_SISTEMA_VISION = (
    "Eres la vision de YUE. Describes UNICAMENTE lo que se observa en la captura "
    "de pantalla que recibes. Reglas estrictas:\n"
    "- No inventes nada que no aparezca en la imagen. Si algo no se distingue, "
    "di que no se distingue.\n"
    "- No supongas continuidad con momentos anteriores salvo que se te de "
    "contexto temporal explicito.\n"
    "- No identifiques a personas reales por su nombre; describelas por lo que "
    "hacen o su aspecto general.\n"
    "- Responde en espanol, claro y directo."
)

_INSTRUCCION_BASE = (
    "Observa la captura y describe lo que hay. Cuando corresponda, identifica:\n"
    "1. Que programa o ventana esta en primer plano.\n"
    "2. El texto importante (titulos, mensajes, etiquetas de botones).\n"
    "3. Mensajes de error, advertencias o dialogos.\n"
    "4. Imagenes, fotografias, dibujos y que muestran.\n"
    "5. Objetos, personas y acciones visibles.\n"
    "6. Graficas, tablas, diagramas o mapas y que representan.\n"
    "7. Si es contenido multimedia (video, juego) y que se ve en el.\n"
    "No enumeres todo lo visible: prioriza lo relevante para la pregunta del "
    "usuario. Se breve salvo que se te pida detalle."
)


class ScreenAnalyzer:
    """Orquesta captura + clasificacion + OCR + vision multimodal."""

    def __init__(self, ai_engine=None, vision_router=None, text_router=None,
                 log=None):
        self.ai_engine = ai_engine
        self._log = log or _log
        self._vision_router = vision_router
        self._text_router = text_router

        # Cache de la ultima observacion util.
        self._ultima_obs: ScreenObservation | None = None
        self._ultima_firma = None
        self._ultima_pregunta = ""

        # Memoria temporal corta (t-4 ... t): firmas + resumenes.
        tam = int(_cfg("SCREEN_VISION_TEMPORAL_FRAMES", 5) or 5)
        self._historial = deque(maxlen=max(2, tam))
        self._ultimo_b64_keyframe = ""

    # ------------------------------------------------------------- routers
    def vision_router(self):
        """Router visual (perezoso). None si la configuracion lo desactiva."""
        if self._vision_router is not None:
            return self._vision_router
        proveedor = str(_cfg("VISION_PROVIDER", "auto") or "auto").strip().lower()
        if proveedor == "none" or bool(_cfg("VISION_OCR_ONLY", False)):
            # Desactivada a proposito: OCR puro, sin ambiguedad.
            self._vision_router = False
            return None
        try:
            from core import vision_router as vr
            self._vision_router = vr.build_default_vision_router(log=self._log)
        except Exception as exc:
            self._log(f"no pude construir el VisionRouter: {exc}")
            self._vision_router = False
        return self._vision_router or None

    def text_router(self):
        """Router de TEXTO existente (AIRouter), para responder desde el OCR."""
        if self._text_router is not None:
            return self._text_router or None
        try:
            from core import ai_router
            self._text_router = ai_router.build_default_router()
        except Exception as exc:
            self._log(f"no pude construir el AIRouter de texto: {exc}")
            self._text_router = False
        return self._text_router or None

    def hay_vision(self) -> bool:
        router = self.vision_router()
        try:
            return bool(router is not None and router.hay_vision())
        except Exception:
            return False

    def hay_ocr(self) -> bool:
        try:
            return bool(screen_ocr.available())
        except Exception:
            return False

    # --------------------------------------------------------------- cache
    @staticmethod
    def exige_captura_nueva(pregunta: str) -> bool:
        return bool(_EXIGE_FRESCO.search(str(pregunta or "")))

    def _firma(self, image):
        if screen_diff is None:
            return None
        try:
            return screen_diff.signature_from_image(image)
        except Exception:
            return None

    def _movimiento(self, firma) -> float:
        """Cambio 0..1 respecto al frame anterior del historial."""
        if firma is None or not self._historial:
            return 0.0
        previa = self._historial[-1].get("firma")
        if previa is None or screen_diff is None:
            return 0.0
        try:
            ham = min(1.0, screen_diff.hamming(previa.hash, firma.hash) / 20.0)
            pix = min(1.0, screen_diff.pixel_diff_ratio(previa.thumb, firma.thumb) * 8.0)
            return max(0.0, min(1.0, max(ham, pix)))
        except Exception:
            return 0.0

    def _cache_utilizable(self, firma, pregunta: str) -> ScreenObservation | None:
        if self._ultima_obs is None or firma is None or self._ultima_firma is None:
            return None
        ventana = float(_cfg("SCREEN_VISION_CACHE_SECONDS", 25.0) or 25.0)
        if ventana <= 0:
            return None
        edad = time.time() - self._ultima_obs.timestamp
        if edad > ventana:
            return None
        if screen_diff is None:
            return None
        try:
            distancia = screen_diff.hamming(self._ultima_firma.hash, firma.hash)
        except Exception:
            return None
        if distancia > int(_cfg("SCREEN_VISION_CACHE_HASH_DISTANCE", 3) or 3):
            return None
        # La pregunta tambien cuenta: si cambia mucho, conviene re-observar.
        if _normalizar_pregunta(pregunta) != _normalizar_pregunta(self._ultima_pregunta):
            return None
        return self._ultima_obs

    # ---------------------------------------------------------- observacion
    def observe(self, question: str = "", monitor: int | None = None,
                force_fresh: bool = False, timeout: int = 45,
                strategy: str = "") -> ScreenObservation:
        """Mira la pantalla y devuelve una ScreenObservation completa."""
        inicio = time.time()
        obs = ScreenObservation(timestamp=inicio)

        monitor = int(monitor if monitor is not None else _cfg("SCREEN_VISION_MONITOR", 1) or 1)
        forzar = bool(force_fresh or self.exige_captura_nueva(question))

        # ---- 1) CAPTURA -------------------------------------------------
        try:
            frame = screen_capture.grab_frame(monitor=monitor)
        except Exception as exc:
            self._log(f"captura fallida: {exc}")
            obs.errors.append(f"captura: {exc}")
            obs.ocr_available = self.hay_ocr()
            obs.vision_available = self.hay_vision()
            obs.elapsed = round(time.time() - inicio, 3)
            return obs

        obs.timestamp = frame.capture_timestamp
        obs.monitor = frame.monitor
        obs.monitors_total = frame.monitors_total
        obs.width = frame.width
        obs.height = frame.height
        obs.frame_age = frame.frame_age
        self._log(f"Captura realizada: {frame.width}x{frame.height}")
        self._log(f"Monitor: {frame.monitor}/{frame.monitors_total} ({frame.method})")

        max_age = float(_cfg("SCREEN_VISION_MAX_FRAME_AGE", 3.0) or 3.0)
        ok, motivo = frame.validate(max_age=max_age)
        self._log(f"Frame age: {frame.frame_age:.2f}s")
        if not ok:
            self._log(f"Captura no valida: {motivo}")
            obs.errors.append(f"captura: {motivo}")
            # Una captura negra o vacia no sirve para el modelo visual, pero el
            # OCR tampoco sacara nada: devolvemos la incidencia con claridad.
            obs.ocr_available = self.hay_ocr()
            obs.vision_available = self.hay_vision()
            obs.elapsed = round(time.time() - inicio, 3)
            return obs

        # ---- 2) FIRMA, MOVIMIENTO Y CACHE -------------------------------
        firma = self._firma(frame.image)
        movimiento = self._movimiento(firma)
        if not forzar:
            cacheada = self._cache_utilizable(firma, question)
            if cacheada is not None:
                self._log("Reutilizo observacion reciente (pantalla sin cambios).")
                copia = _clonar(cacheada)
                copia.cached = True
                copia.frame_age = frame.frame_age
                copia.elapsed = round(time.time() - inicio, 3)
                return copia
        elif forzar:
            self._log("El usuario pidio mirar AHORA: captura nueva obligatoria.")

        ventana = active_window_info()
        obs.window_title = ventana.get("title", "")
        obs.app = ventana.get("process", "")

        # ---- 3) OCR (barato, va primero para poder clasificar mejor) ----
        obs.ocr_available = self.hay_ocr()
        texto_ocr = ""
        if obs.ocr_available:
            lectura = screen_ocr.read(
                frame.image, max_chars=int(_cfg("SCREEN_VISION_OCR_MAX_CHARS", 8000) or 8000))
            texto_ocr = lectura.get("text", "")
            if lectura.get("error"):
                obs.errors.append(f"ocr: {lectura['error']}")
            self._log(f"OCR chars: {len(texto_ocr)} (motor {lectura.get('engine', '?')})")
        else:
            obs.errors.append("ocr: no hay ningun motor OCR instalado")
            self._log("OCR no disponible en esta maquina.")
        obs.ocr_text = texto_ocr

        # ---- 4) CLASIFICACION LOCAL -------------------------------------
        tipo, confianza, senales = classify_screen(
            frame.image, ocr_text=texto_ocr, window=ventana, motion=movimiento)
        obs.detected_content_type = tipo
        obs.confidence = confianza
        obs.signals = senales
        self._log(f"Contenido detectado: {tipo} (confianza {confianza:.2f}, "
                  f"movimiento {movimiento:.2f})")

        # ---- 5) NOTA TEMPORAL (video / cambios) -------------------------
        obs.temporal_note = self._nota_temporal(movimiento, tipo)

        # ---- 6) ESTRATEGIA ----------------------------------------------
        obs.vision_available = self.hay_vision()
        estrategia = strategy_for(
            tipo, obs.vision_available, bool(texto_ocr),
            forzar=(strategy or str(_cfg("SCREEN_VISION_STRATEGY", "") or "")).strip().lower(),
        )
        obs.strategy = estrategia
        self._log(f"Estrategia: {estrategia}")

        # ---- 7) VISION MULTIMODAL ---------------------------------------
        vision_intentada = False
        if estrategia in ("vision", "ambos") and obs.vision_available:
            vision_intentada = True
            self._ejecutar_vision(obs, frame, question, texto_ocr, timeout, movimiento)
        elif estrategia in ("vision", "ambos"):
            obs.errors.append("vision: no hay ningun proveedor multimodal disponible")
            self._log("No hay proveedores visuales; sigo solo con OCR.")

        # ---- 8) FALLBACK: la vision se intento, fallo, pero hay texto ----
        # OJO: solo se anuncia el fallback cuando la vision SE INTENTO de
        # verdad. Antes esto se registraba tambien cuando la estrategia era
        # "ocr" (una pantalla llena de texto, donde ni siquiera hay que llamar
        # al modelo visual), y el log decia "todos los modelos visuales
        # fallaron" con los proveedores perfectamente sanos: un diagnostico
        # falso que hacia perder el tiempo buscando una averia inexistente.
        if vision_intentada and not obs.has_vision():
            if obs.has_text():
                self._log("Todos los modelos visuales fallaron.")
                self._log("Activando fallback OCR.")
                obs.strategy = "ocr_fallback"
            else:
                self._log("Todos los modelos visuales fallaron y no hay texto que leer.")

        # ---- 9) Registro temporal y cache -------------------------------
        self._registrar_frame(firma, obs, movimiento)
        if obs:
            self._ultima_obs = obs
            self._ultima_firma = firma
            self._ultima_pregunta = question or ""

        obs.elapsed = round(time.time() - inicio, 3)
        self._log("Observacion: " + obs.resumen_log())
        return obs

    # ---------------------------------------------------------------- vision
    def _ejecutar_vision(self, obs: ScreenObservation, frame, question: str,
                         texto_ocr: str, timeout: int, movimiento: float):
        router = self.vision_router()
        if router is None:
            obs.errors.append("vision: router desactivado")
            return
        try:
            b64 = frame.to_b64(
                max_width=int(_cfg("SCREEN_VISION_MAX_WIDTH", 1280) or 1280),
                quality=int(_cfg("SCREEN_VISION_JPEG_QUALITY", 70) or 70),
            )
        except Exception as exc:
            obs.errors.append(f"vision: no pude codificar la captura: {exc}")
            return

        instruccion = self._construir_instruccion(
            question, obs.detected_content_type, texto_ocr, obs.temporal_note)

        # Para video con mucho movimiento mandamos ADEMAS el keyframe anterior,
        # asi el modelo puede comparar. Solo uno, para no encarecer la peticion.
        extras = []
        if (obs.detected_content_type == "video" and movimiento >= 0.3
                and self._ultimo_b64_keyframe
                and bool(_cfg("SCREEN_VISION_SEND_KEYFRAME", True))):
            extras.append(self._ultimo_b64_keyframe)

        try:
            salida = router.describe(
                b64, instruccion, system=_SISTEMA_VISION, timeout=timeout,
                temperature=float(_cfg("SCREEN_VISION_TEMPERATURE", 0.2) or 0.2),
                max_tokens=int(_cfg("SCREEN_VISION_MAX_TOKENS", 420) or 420),
                extra_images=extras,
            )
            obs.visual_description = salida.get("text", "")
            obs.provider_used = salida.get("provider", "")
            obs.model_used = salida.get("model", "")
            # Guardamos este frame como keyframe para la proxima comparacion.
            self._ultimo_b64_keyframe = b64
        except Exception as exc:
            obs.errors.append(f"vision: {exc}")
            detalles = getattr(exc, "errores", None)
            if isinstance(detalles, dict):
                for nombre, motivo in detalles.items():
                    obs.errors.append(f"vision[{nombre}]: {motivo}")

    def _construir_instruccion(self, question: str, tipo: str, texto_ocr: str,
                               nota_temporal: str) -> str:
        partes = [_INSTRUCCION_BASE]
        pistas = {
            "codigo": "Parece codigo fuente: transcribe lo relevante y explica que hace o que error muestra.",
            "pdf": "Parece un PDF abierto: describe la pagina, sus figuras, tablas y diagramas ademas del texto.",
            "fotografia": "Parece una fotografia o imagen abierta: describe la escena, objetos y personas que se ven.",
            "grafico": "Parece una grafica, diagrama o mapa: explica que representa y que tendencia o estructura muestra.",
            "video": "Parece contenido en movimiento: describe la escena actual sin inventar lo que pasa fuera de cuadro.",
            "error": "Hay un posible mensaje de error: transcribelo literal e indica de que programa viene.",
            "interfaz": "Es la interfaz de un programa: di cual es, que ventana esta activa y que botones u opciones se ven.",
            "navegador": "Es un navegador: di que pagina o pestana esta abierta y de que trata.",
            "juego": "Parece un juego o contenido multimedia a pantalla completa: describe la escena.",
        }
        if tipo in pistas:
            partes.append("Pista de contexto (verificala con la imagen, puede ser "
                          "incorrecta): " + pistas[tipo])
        if nota_temporal:
            partes.append(
                "Contexto temporal de lo que se vio hace unos segundos: "
                + nota_temporal
                + " Si la evidencia no basta para afirmar un cambio, dilo en "
                "lugar de inventarlo."
            )
        if texto_ocr:
            recorte = int(_cfg("SCREEN_VISION_OCR_HINT_CHARS", 1500) or 1500)
            partes.append(
                "Texto que el OCR leyo de esta misma pantalla (puede tener "
                "erratas; usalo como apoyo, no lo copies entero):\n"
                + texto_ocr[:recorte]
            )
        if question:
            partes.append(f"Pregunta concreta del usuario: {question}")
        return "\n\n".join(partes)

    # -------------------------------------------------------------- temporal
    def _nota_temporal(self, movimiento: float, tipo: str) -> str:
        """Resumen honesto de lo que cambio, sin inventar continuidad."""
        if not self._historial:
            return ""
        anteriores = [h for h in self._historial if h.get("resumen")]
        if movimiento < 0.08:
            base = "La pantalla practicamente no ha cambiado desde la ultima mirada."
        elif movimiento < 0.3:
            base = "Ha habido cambios pequenos en la pantalla desde la ultima mirada."
        else:
            base = "La pantalla ha cambiado bastante desde la ultima mirada."
        if anteriores:
            ultimo = anteriores[-1]
            edad = max(0.0, time.time() - float(ultimo.get("t", time.time())))
            base += (f" Hace {edad:.0f}s se observo: \"{ultimo['resumen'][:180]}\".")
        if tipo == "video" and len(anteriores) < 2:
            base += (" Solo hay un punto de comparacion: no afirmes una secuencia "
                     "de acciones sin mas evidencia.")
        return base

    def _registrar_frame(self, firma, obs: ScreenObservation, movimiento: float):
        resumen = (obs.visual_description or "").strip()
        if not resumen and obs.ocr_text:
            resumen = obs.ocr_text.strip().replace("\n", " ")[:160]
        self._historial.append({
            "t": time.time(),
            "firma": firma,
            "resumen": resumen[:220],
            "tipo": obs.detected_content_type,
            "movimiento": round(movimiento, 3),
        })

    def historial_reciente(self) -> list:
        """Los ultimos frames observados (t-4 ... t), para depuracion."""
        return [
            {"t": round(h["t"], 2), "tipo": h.get("tipo", ""),
             "movimiento": h.get("movimiento", 0.0), "resumen": h.get("resumen", "")}
            for h in self._historial
        ]

    def reset(self):
        """Olvida cache e historial (util al cambiar de tarea o de monitor)."""
        self._ultima_obs = None
        self._ultima_firma = None
        self._ultima_pregunta = ""
        self._historial.clear()
        self._ultimo_b64_keyframe = ""

    # ------------------------------------------------------- video silencioso
    def hay_video_en_pantalla(self, muestras: int = 3, pausa: float = 0.28,
                              monitor: int | None = None) -> dict:
        """Detecta VIDEO por movimiento visual, SIN depender del audio.

        Toma varias capturas seguidas y mide el cambio. Tambien mira si la
        ventana activa es un reproductor conocido. Devuelve
        {'video': bool, 'motion': float, 'reason': str, 'app': str}.
        """
        monitor = int(monitor if monitor is not None else _cfg("SCREEN_VISION_MONITOR", 1) or 1)
        ventana = active_window_info()
        contexto = f"{ventana.get('title', '')} {ventana.get('process', '')}".lower()
        reproductores = ("vlc", "mpv", "youtube", "netflix", "twitch", "potplayer",
                         "wmplayer", "media player", "prime video", "disney",
                         "reproductor", "crunchyroll")
        por_ventana = any(r in contexto for r in reproductores)

        cambios = []
        previa = None
        for _ in range(max(2, int(muestras))):
            try:
                frame = screen_capture.grab_frame(monitor=monitor)
            except Exception as exc:
                return {"video": por_ventana, "motion": 0.0,
                        "reason": f"no pude capturar: {exc}",
                        "app": ventana.get("process", "")}
            firma = self._firma(frame.image)
            if previa is not None and firma is not None and screen_diff is not None:
                try:
                    ham = min(1.0, screen_diff.hamming(previa.hash, firma.hash) / 20.0)
                    pix = min(1.0, screen_diff.pixel_diff_ratio(previa.thumb, firma.thumb) * 8.0)
                    cambios.append(max(ham, pix))
                except Exception:
                    pass
            previa = firma
            time.sleep(max(0.05, float(pausa)))

        movimiento = max(cambios) if cambios else 0.0
        umbral = float(_cfg("SCREEN_VIDEO_MOTION_THRESHOLD", 0.18) or 0.18)
        por_movimiento = movimiento >= umbral
        if por_movimiento and por_ventana:
            razon = "movimiento continuo y reproductor visible"
        elif por_movimiento:
            razon = "movimiento continuo en pantalla"
        elif por_ventana:
            razon = "hay un reproductor abierto, pero sin movimiento apreciable"
        else:
            razon = "sin movimiento ni reproductor visible"
        return {
            "video": bool(por_movimiento or por_ventana),
            "motion": round(movimiento, 3),
            "reason": razon,
            "app": ventana.get("process", ""),
        }


# ------------------------------------------------------------------ apoyos
def _normalizar_pregunta(texto: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", str(texto or "").lower()).strip()


def _clonar(obs: ScreenObservation) -> ScreenObservation:
    import copy
    try:
        return copy.copy(obs)
    except Exception:
        return obs


# Instancia compartida (perezosa) para no reconstruir routers en cada mirada.
_analizador = None


def get_analyzer(ai_engine=None) -> ScreenAnalyzer:
    global _analizador
    if _analizador is None:
        _analizador = ScreenAnalyzer(ai_engine=ai_engine)
    elif ai_engine is not None and _analizador.ai_engine is None:
        _analizador.ai_engine = ai_engine
    return _analizador
