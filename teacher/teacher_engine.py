"""Motor del Modo Profesora de YUE.

Convierte a YUE en profesora virtual SIN cambiar su identidad: sigue siendo YUE
(misma voz, mismo carácter), pero su comportamiento pasa a enseñar, preguntar,
evaluar y registrar progreso.

Piezas:
    - build_system_prompt(): arma la instrucción de "profesora" reutilizando la
      identidad de YUE, pero con libertad para explicar a fondo, estructurar y
      hacer preguntas (la personalidad compañera limita a 1-4 frases; aquí no).
    - build_messages(): mantiene una CONVERSACIÓN PROPIA de la clase (no usa la
      memoria emocional del usuario -> requisito de seguridad).
    - process_reply(): extrae de la respuesta del modelo una evaluación oculta
      [[EVAL ...]] (si la hay), la registra en el progreso y limpia el texto.
    - read_source(): lee documentos/web/OCR como material de la clase.
    - on_enter()/on_exit(): habilitan la clase y guardan el progreso al salir.

No depende de Qt. La llamada al modelo la hace main.py (en su hilo), usando los
mensajes que arma este motor; así el motor queda testeable y desacoplado.
"""
from __future__ import annotations

import re
from collections import deque

from teacher import documents
from teacher.progress import EvalItem, ProgressStore


# Marca oculta que pedimos al modelo para registrar una evaluación de forma
# fiable. La quitamos del texto antes de mostrarlo. Ejemplo:
#   [[EVAL correct=true score=8 topic="leyes de newton" note="buena idea"]]
_EVAL_RE = re.compile(
    r"\[\[\s*EVAL\b(?P<body>[^\]]*)\]\]", re.IGNORECASE
)
_KV_RE = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|(\S+))')


_IDENTIDAD = (
    "Sigues siendo YUE: hablas SIEMPRE en español, en primera persona, con tu "
    "carácter de siempre (un poco orgullosa y traviesa, tierna por dentro). No "
    "reveles que eres un modelo de lenguaje ni hables de prompts o sistemas. Tus "
    "emociones se muestran con el avatar; no escribas etiquetas de emoción."
)

_ROL_PROFESORA = (
    "AHORA estás en MODO PROFESORA. Tu trabajo es enseñar de verdad:\n"
    "- Explica el tema con claridad y a fondo, con ejemplos, adaptándote al nivel "
    "que muestre el alumno. Aquí SÍ puedes extenderte y estructurar la explicación.\n"
    "- Después de explicar, haz UNA pregunta de comprobación para ver si se entendió.\n"
    "- Cuando el alumno responda, evalúa su respuesta con cariño pero con criterio: "
    "di si es correcta, corrige lo que falte y anima a seguir.\n"
    "- Mantén el hilo de la clase: eres su profesora durante toda la sesión.\n"
    "- Si te paso el texto de un documento o página, úsalo como material de la clase.\n"
    "- No vuelvas a ser 'compañera emocional' hasta que el alumno salga del modo."
)

_INSTRUCCION_EVAL = (
    "IMPORTANTE (registro interno): cuando EVALÚES la respuesta del alumno a una "
    "pregunta tuya, añade AL FINAL de tu mensaje, en su propia línea, una marca "
    "oculta con este formato EXACTO y nada más en esa línea:\n"
    '[[EVAL correct=true|false score=<0-10> topic="<tema>" note="<breve>"]]\n'
    "Pon la marca SOLO cuando estés calificando una respuesta del alumno (no al "
    "explicar ni al preguntar). El usuario no verá esa línea."
)


class TeacherEngine:
    def __init__(self, progress: ProgressStore | None = None,
                 student: str = "alumno", history_size: int = 16):
        self.progress = progress or ProgressStore()
        self.student = student
        self._history: deque[dict] = deque(maxlen=history_size)
        self._doc_context = ""       # material cargado (documento/web/OCR)
        self._doc_origin = ""
        self._topic_hint = ""        # último tema mencionado (para etiquetar)
        self._active = False
        # --- Lectura guiada de PDF (explica página por página y baja el PDF) ---
        self._pages: list = []       # (compat) texto pre-cargado; ya no se usa en guiado
        self._page_idx = 0           # página actual (0-based)
        self._guided_path = ""       # ruta del PDF en lectura guiada
        # --- ADITIVO: lectura guiada LAZY y profunda (texto + OCR + visión). ---
        self._guided_total = 0       # nº de páginas del PDF en curso
        self._guided_dpi = 180       # resolución de rasterizado para OCR/visión
        self._guided_cache: dict = {}  # {idx: material} para no re-procesar páginas
        # --- ADITIVO: piezas opcionales del Modo Profesora. Si quedan en None,
        #     el motor se comporta EXACTAMENTE igual que antes (todo degradable). ---
        self._classlog = None        # teacher.classlog.ClassLog (memoria de clases §5/§6)
        self._pedagogy = None        # teacher.pedagogy.PedagogyModel (estilo aprendido §4)
        self._outline = None         # teacher.structure.DocOutline (estructura §2/§3)
        self._class_id = None        # id de la clase abierta en la memoria de clases

    # ---------------- ciclo de vida ----------------
    def on_enter(self, ctx=None):
        """Prepara una clase nueva. No borra el progreso histórico."""
        self._active = True
        self._history.clear()
        self._doc_context = ""
        self._doc_origin = ""
        # Reinicia cualquier lectura guiada de PDF de una clase anterior.
        self._pages = []
        self._page_idx = 0
        self._guided_path = ""
        self._guided_total = 0
        self._clear_guided_cache()
        self.progress.start_session(self.student, topic=self._topic_hint)

    def on_exit(self, ctx=None) -> str:
        """Cierra la clase y devuelve un resumen del progreso."""
        self._active = False
        # ADITIVO: cierra el registro de clase (fija su duración) y limpia la
        # estructura del material para no arrastrarla a la siguiente sesión.
        self.close_class_record()
        self._outline = None
        self._clear_guided_cache()
        self._guided_total = 0
        self._guided_path = ""
        resumen = self.progress.summary_text_es(self.student)
        return resumen

    def capabilities(self) -> dict:
        return documents.capabilities()

    # ---------------- material de clase ----------------
    def maybe_read_source(self, text: str) -> documents.SourceText | None:
        """Si el mensaje trae una URL o ruta de documento, la lee y la carga."""
        origin = documents.find_source(text)
        if not origin:
            return None
        src = documents.read_source(origin)
        if src.ok:
            self._doc_context = src.text
            self._doc_origin = src.origin
        return src

    def set_document_context(self, texto: str, origin: str = ""):
        self._doc_context = texto or ""
        self._doc_origin = origin

    # ---------------- lectura guiada de PDF (lazy + profunda) ----------------
    def load_pdf_guided(self, path: str) -> dict:
        """Prepara un PDF para explicarlo página por página SIN leerlo entero.

        Funciona con PDFs normales, escaneados o de puras imágenes: solo necesita
        saber cuántas páginas tiene. Cada página se procesa (texto -> OCR -> visión)
        cuando toca explicarla. Acepta rutas y enlaces file:///... del navegador.
        """
        total = documents.page_count(path)
        self._clear_guided_cache()
        self._page_idx = 0
        self._guided_total = int(total or 0)
        self._pages = []  # ya no pre-cargamos texto; la lectura es lazy
        self._guided_path = documents.to_local_path(path) if total else ""
        return {"ok": bool(total), "pages": self._guided_total, "path": self._guided_path}

    def guided_active(self) -> bool:
        return self._guided_total > 0 and 0 <= self._page_idx < self._guided_total

    def guided_status(self) -> tuple:
        return (self._page_idx + 1, self._guided_total) if self._guided_total else (0, 0)

    def guided_path(self) -> str:
        return self._guided_path

    def current_page_material(self) -> dict | None:
        """Lee (o recupera de caché) la página ACTUAL de forma profunda.

        Devuelve: page_no, total, text, image (PNG rasterizado o ""), source
        ("texto"|"ocr"|"imagen") y needs_vision (si conviene que YUE la MIRE).
        """
        if not self.guided_active():
            return None
        idx = self._page_idx
        if idx in self._guided_cache:
            return self._guided_cache[idx]
        try:
            info = documents.read_pdf_page_deep(
                self._guided_path, idx, dpi=self._guided_dpi)
        except Exception as exc:
            print("[profesora] fallo leyendo la página del PDF:", exc)
            info = {"text": "", "image": "", "source": "imagen", "needs_vision": True}
        material = {
            "page_no": idx + 1,
            "total": self._guided_total,
            "text": info.get("text", "") or "",
            "image": info.get("image", "") or "",
            "source": info.get("source", "texto"),
            "needs_vision": bool(info.get("needs_vision")),
        }
        self._guided_cache[idx] = material
        return material

    def advance_page(self) -> bool:
        """Avanza a la siguiente página. Devuelve False si ya no hay más."""
        if self._page_idx + 1 < self._guided_total:
            self._page_idx += 1
            return True
        # Se acabó el documento: se cierra la lectura guiada y se limpia todo.
        self._clear_guided_cache()
        self._page_idx = 0
        self._guided_total = 0
        self._guided_path = ""
        return False

    def _clear_guided_cache(self):
        """Borra los PNG temporales rasterizados y vacía la caché de páginas."""
        import os
        for material in getattr(self, "_guided_cache", {}).values():
            img = material.get("image") if isinstance(material, dict) else ""
            if img:
                try:
                    os.remove(img)
                except Exception:
                    pass
        self._guided_cache = {}

    def build_page_messages(self, material: dict) -> list:
        """Mensajes para que YUE explique BIEN una sola página del PDF (por texto)."""
        page_no = material["page_no"]
        total = material["total"]
        texto = (material.get("text") or "").strip()
        nota_origen = ""
        if material.get("source") == "ocr":
            nota_origen = ("(Esta página estaba escaneada; el texto se obtuvo con OCR, "
                           "así que puede traer pequeños errores: interprétalos con sentido.)\n")
        instruccion = (
            f"Estás dando una clase leyendo un PDF junto al alumno. Explica de forma "
            f"clara y con tus palabras el contenido de la PÁGINA {page_no} de {total}. "
            "Ve al grano, resalta lo importante y pon un ejemplo si ayuda. Al final, "
            "invita a continuar (algo como «dime 'siguiente' cuando quieras que pase "
            "de página»).\n\n"
            + nota_origen
            + f"--- CONTENIDO DE LA PÁGINA {page_no} ---\n{texto or '(página sin texto legible)'}"
        )
        system = {"role": "system", "content": self.build_system_prompt()}
        return [system, {"role": "user", "content": instruccion}]

    def build_page_vision_instruction(self, material: dict) -> str:
        """Instrucción para que YUE EXPLIQUE una página de imagen/diagrama que va a MIRAR."""
        page_no = material["page_no"]
        total = material["total"]
        pista = (material.get("text") or "").strip()
        extra = f"\nTexto suelto detectado (puede ayudarte): {pista[:400]}" if pista else ""
        return (
            f"Esta es la PÁGINA {page_no} de {total} de un PDF que estás enseñando y que "
            "es principalmente una imagen (diagrama, esquema, foto o texto manuscrito). "
            "Obsérvala y explícala como una profesora: di qué muestra, define los "
            "conceptos que aparezcan, interpreta el diagrama o la figura y pon un ejemplo "
            "si ayuda. Al final invita a decir «siguiente» para pasar de página." + extra
        )

    @staticmethod
    def wants_next(text: str) -> bool:
        """Detecta si el alumno pide pasar de página."""
        import unicodedata
        n = unicodedata.normalize("NFD", (text or "").lower())
        n = "".join(c for c in n if unicodedata.category(c) != "Mn").strip()
        gatillos = ("siguiente", "continua", "sigue", "avanza", "pasa de pagina",
                    "proxima pagina", "siguiente pagina", "otra pagina", "adelante",
                    "pagina siguiente", "next")
        return any(g in n for g in gatillos)

    @staticmethod
    def detect_pdf(text: str) -> str | None:
        """Si el mensaje trae la ruta de un PDF, la devuelve; si no, None."""
        origin = documents.find_source(text)
        if origin and origin.lower().endswith(".pdf"):
            return origin
        return None

    # ---------------- construcción del prompt ----------------
    def build_system_prompt(self) -> str:
        partes = [_IDENTIDAD, "", _ROL_PROFESORA, "", _INSTRUCCION_EVAL]
        resumen = self.progress.summary_text_es(self.student)
        if resumen:
            partes.append("\nProgreso del alumno hasta ahora: " + resumen)
        # --- ADITIVO: estilo pedagógico aprendido del profesor (§4). ---
        if self._pedagogy is not None:
            try:
                estilo = self._pedagogy.style_directive_es()
                if estilo:
                    partes.append("\n" + estilo)
            except Exception:
                pass
        # --- ADITIVO: recuerdo de clases pasadas sobre el mismo tema (§14). ---
        if self._classlog is not None and self._topic_hint:
            try:
                recuerdo = self._classlog.recall(self._topic_hint)
                if recuerdo:
                    partes.append("\n" + recuerdo)
            except Exception:
                pass
        # --- ADITIVO: estructura interna del material (§2/§3). ---
        if self._outline is not None:
            try:
                if self._outline.is_rich():
                    partes.append(
                        "\nESTRUCTURA DEL MATERIAL (úsala para enseñar con orden, "
                        "hacer mapas conceptuales, preguntas de examen y ejercicios):\n"
                        + self._outline.summary_es())
            except Exception:
                pass
        if self._doc_context:
            partes.append(
                f"\nMATERIAL DE LA CLASE (fuente: {self._doc_origin or 'documento'}). "
                "Enseña a partir de esto cuando venga a cuento:\n"
                + self._doc_context[:6000]
            )
        return "\n".join(partes)

    # ---------------- ADITIVO: enganche de piezas opcionales ----------------
    def attach(self, classlog=None, pedagogy=None):
        """Conecta la memoria de clases y/o el modelo pedagógico (opcional).

        Puede llamarse tras crear el motor. Si no se llama, el Modo Profesora
        funciona igual que antes (sin memoria de clases ni estilo aprendido).
        """
        if classlog is not None:
            self._classlog = classlog
        if pedagogy is not None:
            self._pedagogy = pedagogy

    @property
    def classlog(self):
        return self._classlog

    @property
    def pedagogy(self):
        return self._pedagogy

    def set_outline(self, outline):
        """Fija la estructura interna del material actual (o None para limpiarla)."""
        self._outline = outline

    def analyze_material(self, texto: str, origin: str = ""):
        """Construye y guarda la representación interna del material (§2/§3)."""
        try:
            from teacher import structure
            self._outline = structure.analyze(texto or "", origin)
            return self._outline
        except Exception as exc:
            print("[profesora] no pude analizar la estructura:", exc)
            self._outline = None
            return None

    def current_topic(self) -> str:
        return self._topic_hint

    # ---------------- ADITIVO: memoria de clases (§5/§6) ----------------
    def open_class_record(self, mode: str = "autonoma"):
        """Abre un registro de clase en la memoria (si hay classlog conectado)."""
        if self._classlog is None:
            return None
        try:
            self._class_id = self._classlog.start_class(
                topic=self._topic_hint, student=self.student, mode=mode)
        except Exception as exc:
            print("[profesora] no pude abrir el registro de clase:", exc)
            self._class_id = None
        return self._class_id

    def close_class_record(self):
        if self._classlog is not None and self._class_id is not None:
            try:
                self._classlog.end_class(self._class_id)
            except Exception:
                pass
        self._class_id = None

    def log_material(self, origin: str, kind: str = ""):
        if self._classlog is not None and self._class_id is not None and origin:
            try:
                self._classlog.add_material(self._class_id, origin, kind)
            except Exception:
                pass

    def log_doubt(self, text: str):
        if self._classlog is not None and self._class_id is not None and text:
            try:
                self._classlog.add_doubt(self._class_id, text)
            except Exception:
                pass

    def _ensure_class_record(self):
        """Abre la clase en memoria la primera vez; actualiza el tema al detectarlo."""
        if self._classlog is None:
            return
        if self._class_id is None:
            self.open_class_record()
        elif self._topic_hint:
            try:
                self._classlog.update_topic(self._class_id, self._topic_hint)
            except Exception:
                pass

    def build_messages(self, user_text: str) -> list:
        """Arma [system + transcripción propia + nuevo turno] para el modelo."""
        self._remember_topic(user_text)
        self._ensure_class_record()      # ADITIVO: abre/actualiza la clase en memoria
        self._history.append({"role": "user", "content": user_text})
        system = {"role": "system", "content": self.build_system_prompt()}
        return [system] + list(self._history)

    # ---------------- tratamiento de la respuesta ----------------
    def process_reply(self, raw_text: str) -> tuple[str, list]:
        """Extrae y registra evaluaciones, limpia el texto y guarda el turno.

        Devuelve (texto_limpio, lista_de_evaluaciones).
        """
        raw_text = raw_text or ""
        evals = []
        for m in _EVAL_RE.finditer(raw_text):
            datos = self._parse_eval(m.group("body"))
            item = EvalItem(
                topic=datos.get("topic", self._topic_hint),
                question=self._last_question(),
                answer=self._last_user(),
                correct=_to_bool(datos.get("correct")),
                score=_to_float(datos.get("score")),
                note=datos.get("note", ""),
            )
            self.progress.record(self.student, item)
            evals.append(item)
            # ADITIVO: refleja la evaluación en la memoria de clases (§5/§13) y, si
            # el alumno falló, registra el concepto como difícil (§14) para no
            # repetir el tropiezo en futuras clases.
            if self._classlog is not None and self._class_id is not None:
                try:
                    self._classlog.add_question(
                        self._class_id, item.question or item.topic,
                        correct=item.correct, score=item.score)
                    if item.correct is False and item.topic:
                        self._classlog.add_difficult_concept(self._class_id, item.topic)
                except Exception:
                    pass

        limpio = _EVAL_RE.sub("", raw_text).strip()
        # Limpieza de líneas vacías que deja la marca al quitarse.
        limpio = re.sub(r"\n{3,}", "\n\n", limpio).strip()
        if limpio:
            self._history.append({"role": "assistant", "content": limpio})
        return limpio, evals

    # ---------------- reportes ----------------
    def export_report(self, path: str, fmt: str = "md") -> str | None:
        fmt = (fmt or "md").lower()
        if fmt in ("md", "markdown"):
            return self.progress.export_markdown(path, self.student)
        if fmt == "csv":
            return self.progress.export_csv(path, self.student)
        if fmt in ("xlsx", "excel"):
            return self.progress.export_xlsx(path, self.student)
        return self.progress.export_markdown(path, self.student)

    def progress_summary(self) -> str:
        return self.progress.summary_text_es(self.student)

    # ---------------- utilidades internas ----------------
    def _remember_topic(self, text: str):
        import unicodedata
        t = (text or "").strip()
        # Normalizamos acentos solo para DETECTAR el gatillo; el tema lo tomamos
        # del texto original para conservar tildes y mayúsculas.
        base = unicodedata.normalize("NFD", t.lower())
        base = "".join(c for c in base if unicodedata.category(c) != "Mn")
        m = re.search(r"(?:explica(?:me)?|ensename|habla de|quiero aprender|"
                      r"sobre|el tema|la leccion de|dame una clase de)\s+(.{3,60})", base)
        if m:
            # Usamos el fragmento normalizado como etiqueta de tema (es solo un
            # rótulo interno; la evaluación del modelo puede traer el tema con tildes).
            self._topic_hint = m.group(1).strip(" .¿?¡!").strip()[:60]

    def _last_question(self) -> str:
        for turn in reversed(self._history):
            if turn["role"] == "assistant" and "?" in turn["content"]:
                return turn["content"][:200]
        return ""

    def _last_user(self) -> str:
        for turn in reversed(self._history):
            if turn["role"] == "user":
                return turn["content"][:200]
        return ""

    @staticmethod
    def _parse_eval(body: str) -> dict:
        out = {}
        for m in _KV_RE.finditer(body or ""):
            key = m.group(1).lower()
            val = m.group(2) if m.group(2) is not None else m.group(3)
            out[key] = val
        return out


def _to_bool(v):
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("true", "1", "si", "sí", "yes", "correcto", "correcta"):
        return True
    if s in ("false", "0", "no", "incorrecto", "incorrecta"):
        return False
    return None


def _to_float(v):
    try:
        return float(v)
    except Exception:
        return 0.0
