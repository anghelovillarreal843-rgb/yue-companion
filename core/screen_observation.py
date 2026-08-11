"""Resultado estructurado de "mirar la pantalla" + clasificador local barato.

Dos piezas:

  * `ScreenObservation`: lo que la capa conversacional recibe cuando YUE mira la
    pantalla. Sustituye a las variables sueltas (texto por aqui, descripcion por
    alla, errores en la consola) por UN objeto con todo dentro.

  * `classify_screen()`: decide QUE hay en pantalla ANTES de gastar una llamada a
    la IA, usando solo Pillow y el titulo de la ventana activa. De esa
    clasificacion depende si conviene OCR, modelo multimodal o los dos.

Nada de esto necesita red ni modelos: es todo local y barato.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

# Tipos de contenido que YUE distingue.
TIPOS = (
    "texto",            # documento, articulo, chat: casi todo es texto
    "codigo",           # editor/IDE con codigo fuente
    "pdf",              # visor de PDF abierto en pantalla
    "navegador",        # pagina web
    "interfaz",         # ventana de programa, panel de ajustes, explorador
    "fotografia",       # foto abierta (JPG/PNG), visor de imagenes
    "grafico",          # grafica, diagrama, mapa, tabla visual
    "video",            # reproductor / contenido en movimiento
    "juego",            # juego o contenido multimedia a pantalla completa
    "error",            # hay un mensaje de error visible
    "escritorio",       # escritorio vacio o casi
    "mixto",            # texto + contenido visual relevante
    "desconocido",
)

# Que estrategia conviene para cada tipo.
#   "ocr"     -> OCR y responder con el router de TEXTO
#   "vision"  -> modelo multimodal
#   "ambos"   -> OCR + multimodal (el multimodal recibe el OCR como apoyo)
ESTRATEGIA_POR_TIPO = {
    "texto": "ocr",
    "codigo": "ocr",
    "pdf": "ambos",
    "navegador": "ambos",
    "interfaz": "ambos",
    "fotografia": "vision",
    "grafico": "vision",
    "video": "vision",
    "juego": "vision",
    "error": "ambos",
    "escritorio": "vision",
    "mixto": "ambos",
    "desconocido": "ambos",
}


@dataclass
class ScreenObservation:
    """Todo lo que YUE sabe de la pantalla tras mirarla UNA vez."""

    # --- captura ---
    timestamp: float = 0.0
    frame_age: float = 0.0
    monitor: int = 1
    monitors_total: int = 1
    width: int = 0
    height: int = 0

    # --- contenido ---
    ocr_text: str = ""
    visual_description: str = ""
    detected_content_type: str = "desconocido"
    confidence: float = 0.0

    # --- procedencia ---
    provider_used: str = ""
    model_used: str = ""
    vision_available: bool = False
    ocr_available: bool = False
    strategy: str = ""
    cached: bool = False

    # --- contexto y diagnostico ---
    window_title: str = ""
    app: str = ""
    signals: dict = field(default_factory=dict)
    temporal_note: str = ""
    errors: list = field(default_factory=list)
    elapsed: float = 0.0

    # ------------------------------------------------------------------
    def __bool__(self) -> bool:
        """La observacion sirve si al menos hay descripcion visual o texto."""
        return bool((self.visual_description or "").strip() or (self.ocr_text or "").strip())

    @property
    def ocr_chars(self) -> int:
        return len(self.ocr_text or "")

    def has_vision(self) -> bool:
        return bool((self.visual_description or "").strip())

    def has_text(self) -> bool:
        return bool((self.ocr_text or "").strip())

    def as_dict(self) -> dict:
        return {
            "timestamp": round(self.timestamp, 3),
            "frame_age": round(self.frame_age, 3),
            "monitor": self.monitor,
            "monitors_total": self.monitors_total,
            "width": self.width,
            "height": self.height,
            "ocr_text": self.ocr_text,
            "ocr_chars": self.ocr_chars,
            "visual_description": self.visual_description,
            "detected_content_type": self.detected_content_type,
            "confidence": round(float(self.confidence), 3),
            "provider_used": self.provider_used,
            "model_used": self.model_used,
            "vision_available": bool(self.vision_available),
            "ocr_available": bool(self.ocr_available),
            "strategy": self.strategy,
            "cached": bool(self.cached),
            "window_title": self.window_title,
            "app": self.app,
            "temporal_note": self.temporal_note,
            "errors": list(self.errors),
            "elapsed": round(self.elapsed, 3),
        }

    # ------------------------------------------------------------------
    def to_prompt_context(self, max_ocr: int = 5000) -> str:
        """Texto listo para meter en el prompt de la capa conversacional."""
        partes = [
            f"Observacion de pantalla (monitor {self.monitor}/{self.monitors_total}, "
            f"{self.width}x{self.height}, antiguedad {self.frame_age:.2f}s).",
            f"Tipo de contenido detectado: {self.detected_content_type} "
            f"(confianza {self.confidence:.2f}).",
        ]
        if self.window_title:
            partes.append(f"Ventana activa: {self.window_title}")
        if self.temporal_note:
            partes.append(f"Cambio respecto a lo anterior: {self.temporal_note}")
        if self.visual_description:
            partes.append("--- LO QUE SE VE (modelo multimodal) ---\n"
                          + self.visual_description.strip())
        if self.ocr_text:
            partes.append("--- TEXTO EN PANTALLA (OCR, puede tener erratas) ---\n"
                          + self.ocr_text[:max_ocr])
        if not self.visual_description and not self.ocr_text:
            partes.append("No se obtuvo ni descripcion visual ni texto legible.")
        if self.errors:
            partes.append("Incidencias tecnicas (no se las cuentes al usuario "
                          "salvo que pregunte): " + " | ".join(str(e)[:120] for e in self.errors))
        return "\n\n".join(partes)

    def resumen_log(self) -> str:
        return (
            f"tipo={self.detected_content_type} conf={self.confidence:.2f} "
            f"estrategia={self.strategy} vision={'si' if self.has_vision() else 'no'} "
            f"ocr_chars={self.ocr_chars} proveedor={self.provider_used or '-'} "
            f"modelo={self.model_used or '-'} cache={'si' if self.cached else 'no'}"
        )


# ---------------------------------------------------------------- senales
_PALABRAS_ERROR = (
    "error", "excepcion", "exception", "traceback", "failed", "fallo",
    "no se puede", "cannot", "denied", "denegado", "warning", "advertencia",
    "not found", "no encontrado", "syntaxerror", "fatal", "crash",
)
_PALABRAS_CODIGO = (
    "def ", "class ", "import ", "function", "return", "const ", "let ",
    "public ", "private ", "void ", "#include", "print(", "console.log",
    "</", "/>", "=>", "{", "};", "self.", "npm ", "pip ", "git ",
)
_APPS_CODIGO = ("code", "vscode", "pycharm", "intellij", "sublime", "atom",
                "notepad++", "idle", "spyder", "android studio", "eclipse",
                "cmd", "powershell", "terminal", "consola")
_APPS_PDF = ("acrobat", "pdf", "foxit", "sumatra", "edge", "chrome")
_APPS_NAVEGADOR = ("chrome", "firefox", "edge", "brave", "opera", "safari", "vivaldi")
_APPS_VIDEO = ("vlc", "mpv", "youtube", "netflix", "twitch", "media player",
               "potplayer", "wmplayer", "prime video", "disney", "reproductor")
_APPS_IMAGEN = ("fotos", "photos", "visor de imagenes", "irfanview", "paint",
                "gimp", "photoshop", "lightroom", "visor de fotos", "xnview")
_APPS_JUEGO = ("steam", "epic games", "minecraft", "roblox", "league of legends",
               "valorant", "genshin", "fortnite")


def _normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", str(texto or "")).strip().lower()


def active_window_info() -> dict:
    """Titulo y proceso de la ventana activa. Nunca lanza excepcion."""
    titulo, proceso = "", ""
    try:
        import pygetwindow as gw
        activa = gw.getActiveWindow()
        titulo = str(getattr(activa, "title", "") or "").strip()
    except Exception:
        pass
    try:
        import sys
        if sys.platform == "win32":
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            try:
                import psutil
                proceso = psutil.Process(pid.value).name().lower()
            except Exception:
                proceso = ""
    except Exception:
        pass
    return {"title": titulo[:240], "process": proceso}


def image_signals(image) -> dict:
    """Estadisticas visuales baratas para distinguir foto / interfaz / texto.

    * `flat_ratio`   : proporcion de pixeles casi identicos a su vecino.
                       Alto -> interfaz o documento (fondos planos).
                       Bajo -> fotografia (todo tiene textura).
    * `unique_colors`: colores distintos en una miniatura de 64x36.
    * `edge_ratio`   : proporcion de bordes marcados (texto y UI generan muchos).
    * `saturation`   : saturacion media 0..1 (las fotos suelen tenerla mas alta
                       que una interfaz gris de Windows).
    """
    salida = {"flat_ratio": 0.0, "unique_colors": 0, "edge_ratio": 0.0,
              "saturation": 0.0, "brightness": 0.0}
    if image is None:
        return salida
    try:
        from PIL import ImageChops, ImageFilter
        pequena = image.convert("RGB").resize((160, 90))
        gris = pequena.convert("L")

        # Planitud: diferencia con la propia imagen desplazada un pixel.
        desplazada = ImageChops.offset(gris, 1, 0)
        diff = ImageChops.difference(gris, desplazada)
        datos = list(diff.getdata())
        planos = sum(1 for v in datos if v <= 4)
        salida["flat_ratio"] = round(planos / max(1, len(datos)), 4)

        # Bordes marcados.
        bordes = gris.filter(ImageFilter.FIND_EDGES)
        bdatos = list(bordes.getdata())
        salida["edge_ratio"] = round(sum(1 for v in bdatos if v >= 60) / max(1, len(bdatos)), 4)

        # Variedad de color.
        mini = pequena.resize((64, 36))
        salida["unique_colors"] = len(set(mini.getdata()))

        # Saturacion y brillo medios.
        hsv = mini.convert("HSV")
        pixeles = list(hsv.getdata())
        salida["saturation"] = round(sum(p[1] for p in pixeles) / max(1, len(pixeles)) / 255.0, 4)
        salida["brightness"] = round(sum(p[2] for p in pixeles) / max(1, len(pixeles)) / 255.0, 4)
    except Exception as exc:
        print(f"[VISION] no pude calcular senales de imagen: {exc}")
    return salida


def classify_screen(image, ocr_text: str = "", window: dict | None = None,
                    motion: float = 0.0) -> tuple:
    """(tipo, confianza, senales). Clasificacion LOCAL, sin llamar a ninguna IA.

    `motion` es el cambio 0..1 respecto al frame anterior (si se conoce): sirve
    para detectar VIDEO aunque no haya audio del sistema.
    """
    window = window or {}
    titulo = _normalizar(window.get("title", ""))
    proceso = _normalizar(window.get("process", ""))
    contexto = f"{titulo} {proceso}"
    texto = ocr_text or ""
    texto_norm = _normalizar(texto)

    senales = image_signals(image)
    try:
        ancho, alto = image.size
    except Exception:
        ancho, alto = 1920, 1080
    densidad = min(1.0, len(texto) / max(1.0, (ancho * alto) / 900.0))
    senales["text_chars"] = len(texto)
    senales["text_density"] = round(densidad, 4)
    senales["motion"] = round(float(motion), 4)

    candidatos: list = []   # (tipo, puntuacion)

    def sumar(tipo, puntos):
        candidatos.append((tipo, puntos))

    # --- senales por ventana/proceso (muy fiables cuando existen) ---
    if any(a in contexto for a in _APPS_CODIGO):
        sumar("codigo", 0.55)
    if ".pdf" in titulo or any(a in contexto for a in _APPS_PDF) and ".pdf" in titulo:
        sumar("pdf", 0.75)
    elif ".pdf" in contexto:
        sumar("pdf", 0.6)
    if any(a in contexto for a in _APPS_NAVEGADOR):
        sumar("navegador", 0.4)
    if any(a in contexto for a in _APPS_VIDEO):
        sumar("video", 0.5)
    if any(a in contexto for a in _APPS_IMAGEN) or re.search(r"\.(jpg|jpeg|png|webp|bmp|gif)\b", titulo):
        sumar("fotografia", 0.7)
    if any(a in contexto for a in _APPS_JUEGO):
        sumar("juego", 0.55)

    # --- senales por texto ---
    if texto_norm:
        # El peso del error depende de CUANTAS pistas hay y de su fuerza. Una
        # pantalla llena de texto que ademas trae un traceback debe clasificarse
        # como "error" (estrategia "ambos": el OCR da el texto exacto y la vision
        # dice de que programa y que dialogo viene), no como documento normal.
        # Con un peso fijo bajo, la densidad de texto siempre ganaba y la vision
        # nunca llegaba a mirar el error.
        fuertes = ("traceback", "exception", "excepcion", "syntaxerror", "fatal",
                   "crash", "failed", "fallo")
        pistas_error = sum(1 for p in _PALABRAS_ERROR if p in texto_norm)
        if pistas_error:
            peso = 0.40 + min(0.25, 0.08 * pistas_error)
            if any(p in texto_norm for p in fuertes):
                peso += 0.15
            sumar("error", min(0.85, peso))
        pistas_codigo = sum(1 for p in _PALABRAS_CODIGO if p in texto)
        if pistas_codigo >= 3:
            sumar("codigo", 0.5)
        if densidad >= 0.5:
            sumar("texto", 0.55)
        elif densidad >= 0.2:
            sumar("texto", 0.3)

    # --- senales por imagen ---
    plano = senales.get("flat_ratio", 0.0)
    colores = senales.get("unique_colors", 0)
    sat = senales.get("saturation", 0.0)
    bordes = senales.get("edge_ratio", 0.0)

    if plano < 0.45 and colores > 1200:
        # Mucha textura y mucha variedad de color: fotografia o fotograma de
        # video. La SATURACION suma confianza, pero NO puede ser un requisito:
        # una foto en blanco y negro, una escena nocturna o un plano con niebla
        # tienen saturacion baja y seguirian siendo fotografias. Cuando era un
        # requisito, esas pantallas caian en "desconocido".
        puntos = 0.45 + min(0.2, (colores - 1200) / 6000.0)
        if sat > 0.18:
            puntos += 0.15
        sumar("fotografia", puntos)
    if plano >= 0.72 and densidad < 0.08 and colores < 600:
        # Fondos planos, poco texto, poca variedad: grafica, diagrama o escritorio.
        sumar("grafico", 0.35)
        if bordes < 0.05:
            sumar("escritorio", 0.35)
    if plano >= 0.6 and bordes >= 0.06 and densidad >= 0.05:
        sumar("interfaz", 0.4)

    # --- movimiento: video aunque este silenciado ---
    if motion >= 0.30:
        sumar("video", 0.45 + min(0.3, motion))
    elif motion >= 0.15:
        sumar("video", 0.25)

    # --- mezcla: hay texto Y contenido visual con peso ---
    if densidad >= 0.12 and plano < 0.7 and colores > 900:
        sumar("mixto", 0.4)

    if not candidatos:
        # Sin ninguna pista clara. Antes esto caia en "desconocido" incluso
        # cuando la pantalla era claramente contenido VISUAL sin texto, y eso es
        # justo el caso del "perro junto a la bicicleta": hay que mandarlo al
        # modelo multimodal, no responder "no encontre texto para leer".
        if densidad >= 0.1:
            return "texto", 0.35, senales
        if colores > 700 and plano < 0.7:
            return "fotografia", 0.3, senales
        return "desconocido", 0.2, senales

    # Acumula puntuaciones por tipo y coge el mayor.
    acumulado: dict = {}
    for tipo, puntos in candidatos:
        acumulado[tipo] = acumulado.get(tipo, 0.0) + float(puntos)
    tipo, puntos = max(acumulado.items(), key=lambda kv: kv[1])
    confianza = max(0.15, min(0.95, puntos))
    senales["candidatos"] = {k: round(v, 3) for k, v in sorted(
        acumulado.items(), key=lambda kv: -kv[1])[:4]}
    return tipo, round(confianza, 3), senales


def strategy_for(tipo: str, hay_vision: bool, hay_ocr: bool,
                 forzar: str = "") -> str:
    """Decide 'ocr' | 'vision' | 'ambos' segun el tipo y lo que este disponible.

    `forzar` permite imponer una estrategia desde la configuracion o la orden.
    """
    if forzar in ("ocr", "vision", "ambos"):
        estrategia = forzar
    else:
        estrategia = ESTRATEGIA_POR_TIPO.get(tipo, "ambos")
    # Recorte por disponibilidad real: nunca prometemos lo que no hay.
    if not hay_vision and estrategia in ("vision", "ambos"):
        estrategia = "ocr" if hay_ocr else "vision"   # sin OCR, se intenta igual
    if not hay_ocr and estrategia in ("ocr", "ambos"):
        estrategia = "vision" if hay_vision else "ocr"
    return estrategia


def nueva_observacion_vacia(motivo: str = "") -> ScreenObservation:
    obs = ScreenObservation(timestamp=time.time())
    if motivo:
        obs.errors.append(motivo)
    return obs
