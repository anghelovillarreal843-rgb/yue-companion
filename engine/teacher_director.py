"""TeacherDirector (PR 5, paso 6).

Extrae de Controller el paquete de la Profesora: el sistema de MODOS
(companion/teacher), el motor de clases y su almacén de progreso, la memoria de
clase y la pedagogía aprendida del profesor humano, la lectura guiada de PDF con
auto-avance, los comandos especiales, el asistente docente y la
observación/participación durante una clase ajena.

Crea y publica en ctx los componentes que consumen el host y otros directores:
ctx.modes, ctx.teacher, ctx.teacher_progress, ctx.class_log, ctx.pedagogy,
ctx.teacher_participation.

Direcciones del paquete:
- main -> director (público): setup_modes(), apply_mode_switch(route),
  on_files_dropped(paths).
- director -> ctx (dependencias): modes, teacher, engine, chat, speaker, pc,
  state_manager, workers, say, avatar_emotion, user_float.
- ctx -> director (hooks del cerebro central): ctx.ai_failed (fallos de IA),
  ctx.interrupt_response (cancela una respuesta en curso).
- ctx -> director (estado compartido get/set): ctx.chat_request_id,
  ctx.teacher_autoadvance_pending (el host lo resetea al interrumpir).
"""
from __future__ import annotations

import os

from PyQt5.QtCore import QTimer

from engine.ai_worker import AiWorker
from engine.teacher_worker import PdfPageVisionWorker
from mode_manager import DEFAULT_MODE, ModeManager, ModeSpec
from teacher import ClassLog, PedagogyModel, ProgressStore, TeacherEngine
from teacher import assistant as teacher_assistant
from teacher import participation as teacher_participation

# Identificadores de modo (evitan cadenas sueltas por el código).
MODE_COMPANION = DEFAULT_MODE          # "companion"
MODE_TEACHER = "teacher"


def _cfg(name: str, default):
    """Lee config sin conocer a Controller (mismo patrón que PCDirector)."""
    try:
        import config  # toplevel del proyecto
        return getattr(config, name, default)
    except Exception:
        return default


class TeacherDirector:
    """Estado interno: `_autoadvance` y `_page_explaining`.

    Estado compartido (en ctx, get/set): chat_request_id,
    teacher_autoadvance_pending. Componentes propios publicados en ctx: modes,
    teacher, teacher_progress, class_log, pedagogy, teacher_participation.
    Callbacks del host: ctx.say, ctx.ai_failed, ctx.interrupt_response.
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._autoadvance = bool(_cfg("TEACHER_AUTOADVANCE_DEFAULT", True))
        self._page_explaining = False

    # ---------- MODOS (companion por defecto, teacher bajo demanda) ----------
    def setup_modes(self):
        """Crea el gestor de modos y registra companion + teacher. Aditivo."""
        self.modes = ModeManager()
        self.ctx.modes = self.modes

        # Progreso de la profesora en un almacén propio (NO toca la memoria emocional).
        try:
            prog_dir = os.path.join(str(_cfg("DATA_DIR", ".")), "teacher")
            prog_path = os.path.join(prog_dir, "progress.json")
        except Exception:
            prog_path = None
        self.teacher_progress = ProgressStore(path=prog_path)
        self.teacher = TeacherEngine(progress=self.teacher_progress, student="alumno")

        # NUEVO: memoria de clases (§5/§6/§13) y modelo pedagógico aprendido del
        # profesor (§4). Ambos en almacenes propios; si algo falla, el Modo
        # Profesora sigue funcionando igual (todo aditivo y degradable).
        try:
            teach_dir = os.path.join(str(_cfg("DATA_DIR", ".")), "teacher")
            self.class_log = ClassLog(path=os.path.join(teach_dir, "classes.db"))
            self.pedagogy = PedagogyModel(path=os.path.join(teach_dir, "pedagogy.json"))
        except Exception as exc:
            print("[profesora] sin memoria de clases/pedagogía persistente:", exc)
            self.class_log = ClassLog(path=None)
            self.pedagogy = PedagogyModel(path=None)
        self.teacher.attach(classlog=self.class_log, pedagogy=self.pedagogy)

        # Política de participación activa durante la clase de un profesor humano (§8).
        self.teacher_participation = teacher_participation.ParticipationPolicy()

        self._register_modes()
        self.modes.subscribe(self._on_mode_event)
        # Indicador inicial: modo compañera.
        meta = self.modes.current_meta()
        self._update_mode_indicator(meta.get("emoji", "🤍"), meta.get("label", "Compañera"))

        # Todos los componentes quedan a disposición del host y de otros directores.
        self.ctx.teacher_progress = self.teacher_progress
        self.ctx.teacher = self.teacher
        self.ctx.class_log = self.class_log
        self.ctx.pedagogy = self.pedagogy
        self.ctx.teacher_participation = self.teacher_participation

    def _register_modes(self):
        # --- Companion: modo por defecto (apoyo emocional). Su flujo vive en
        #     on_user_message; aquí solo se registra su identidad e indicador. ---
        self.modes.register_mode(ModeSpec(
            id=MODE_COMPANION, label="Compañera", emoji="🤍", is_default=True,
        ))

        # --- Teacher: profesora virtual, activable por voz o texto. ---
        self.modes.register_mode(ModeSpec(
            id=MODE_TEACHER, label="Profesora", emoji="📚",
            activation_phrases=(
                "activa el modo profesora", "activa el modo profesor",
                "modo profesora", "modo profesor", "modo ensenanza", "modo maestra",
                "quiero una clase", "dame una clase", "me das una clase",
                "ensename", "quiero aprender", "inicia una clase", "empieza una clase",
                "comienza el modo profesora", "actua como profesora", "se mi profesora",
            ),
            activation_keywords=(
                ("modo", "profesor"), ("modo", "maestra"), ("modo", "ensenanza"),
                ("quiero", "aprender"), ("quiero", "clase"), ("dame", "clase"),
                ("das", "clase"), ("actua", "profesora"), ("inicia", "clase"),
                ("empieza", "clase"), ("comienza", "clase"), ("se", "profesora"),
            ),
            deactivation_phrases=(
                "salir del modo profesora", "sal del modo profesora",
                "terminar clase", "termina la clase", "finalizar clase",
                "finaliza la clase", "desactiva modo profesora", "acaba la clase",
                "regresa al modo companera",
            ),
            deactivation_keywords=(
                ("terminar", "clase"), ("termina", "clase"), ("finalizar", "clase"),
                ("finaliza", "clase"), ("acaba", "clase"), ("salir", "profesora"),
                ("desactiva", "profesora"),
            ),
            activation_message=(
                "📚 Modo Profesora activado. Ahora puedo enseñarte cualquier tema, leer "
                "documentos y páginas web, evaluarte y registrar tu progreso. También "
                "puedo ayudarte como docente: dime «crea un examen/plan/rúbrica sobre…», "
                "«corrige este trabajo», «aprende de mi profesor» para observar una clase, "
                "o «estadísticas» para ver cómo va todo. Para salir: «Yue, salir del modo "
                "profesora»."
            ),
            deactivation_message=(
                "📚 Clase finalizada. Guardé el progreso. Vuelvo a ser tu compañera "
                "de siempre. ¿Hay algo más en lo que pueda ayudarte?"
            ),
            handler=self.teacher_process,
            on_enter=self.teacher.on_enter,
            on_exit=self.teacher.on_exit,
        ))

    def _on_mode_event(self, event):
        """Cambio de modo: actualiza indicador y actúa en el momento de entrada."""
        from mode_manager.mode_events import ENTER
        try:
            if getattr(event, "kind", None) is not ENTER:
                return
            meta = self.modes.current_meta()
            self._update_mode_indicator(meta.get("emoji", ""), meta.get("label", ""))
            self.ctx.avatar_emotion("one", 0.95, 7000, priority=0.9999,
                                    source="modes")
            if self.modes.current_mode() == MODE_TEACHER:
                self._propose_teacher_mode()
            else:
                self.ctx.say(meta.get("leaving_message", ""))
                try:
                    self.pedagogy.set_observing(False)
                except Exception:
                    pass
                try:
                    self.teacher_participation.set_active(False)
                except Exception:
                    pass
        except Exception as exc:
            print("[modos] evento no procesado:", exc)

    def _propose_teacher_mode(self):
        """La entrada al Modo Profesora compite de forma justa en el arbitraje.

        Es una petición con la prioridad más alta (80, como las órdenes de PC)
        y sin expiración: se anuncia cuando le toque y persiste hasta entonces.
        """
        try:
            from core.state.tangibles import YuleProposal
            caract = bool(getattr(getattr(self.ctx, "chat", None), "is_user_composing", lambda: False)())
            self.ctx.state_manager.withdraw("profesora", reason="nueva_propuesta")
            self.ctx.state_manager.propose(
                YuleProposal(actuante="profesora", prioridad=80, ttl=None,
                             caracter_urgencia=caract))
        except Exception:
            pass

    def _update_mode_indicator(self, emoji, label):
        """El chat refleja el modo activo con su emoji y nombre."""
        try:
            self.ctx.chat.set_mode(emoji, label)
        except Exception:
            pass

    # ---------- Conexión con el flujo conversacional ----------
    def apply_mode_switch(self, route):
        """Tras activar/desactivar un modo: YUE lo anuncia. El indicador ya se
        actualiza por el evento de modo."""
        if route.reply:
            self.ctx.say(route.reply)

    # ---------- Motor de la Profesora ----------
    def teacher_process(self, text):
        """Procesa un mensaje como parte de una clase (no como compañera emocional)."""
        # (a0) NUEVO: comandos especiales del Modo Profesora (aprender del profesor,
        #      generar material docente, corregir trabajos, estadísticas). Si uno
        #      de ellos maneja el mensaje, no seguimos con el flujo de enseñanza.
        try:
            if self._teacher_special_commands(text):
                return
        except Exception as exc:
            print("[profesora] comando especial falló:", exc)

        # (a1) NUEVO: si YUE está OBSERVANDO a un profesor humano (§4/§8), este
        #      mensaje es una intervención del docente: la aprende y quizá participa.
        _ped = getattr(self, "pedagogy", None)
        _part = getattr(self, "teacher_participation", None)
        if (_ped is not None and _ped.is_observing()) or (_part is not None and _part.is_active()):
            self._teacher_observe_and_participate(text)
            return

        # (a) Si hay una lectura guiada de PDF en curso y el alumno pide avanzar,
        #     YUE baja el PDF una pantalla y explica la siguiente página.
        if self.teacher.guided_active() and self.teacher.wants_next(text):
            self._teacher_next_page()
            return

        # (b) Si el mensaje trae la RUTA de un PDF, arranca la lectura guiada:
        #     lo abre en pantalla y lo explica página por página, bajándolo ella.
        pdf_path = self.teacher.detect_pdf(text)
        if pdf_path:
            self._teacher_start_guided_pdf(pdf_path)
            return

        # (c) Otras fuentes (Word, EPUB, imagen, página web): lectura completa.
        try:
            src = self.teacher.maybe_read_source(text)
        except Exception as exc:
            src = None
            print("[profesora] error leyendo fuente:", exc)
        if src is not None and not src.ok:
            self.ctx.say("Quería leer eso, pero no pude: " + (src.note or "formato no disponible"))
            return
        if src is not None and src.ok:
            self.ctx.chat_set_status(f"Leí el material ({src.kind}). Preparando la clase…")
            # NUEVO: construye la representación interna del material (§2/§3) y lo
            # registra en la memoria de la clase (§5). Aditivo y a prueba de fallos.
            try:
                self.teacher.analyze_material(src.text, src.origin)
                self.teacher.log_material(src.origin, src.kind)
            except Exception as exc:
                print("[profesora] no pude analizar/registrar el material:", exc)

        messages = self.teacher.build_messages(text)
        request_id = self.ctx.chat_request_id
        self.ctx.chat_set_status("La profesora está preparando la clase…")
        worker = AiWorker(self.ctx.engine, messages)
        worker.done.connect(lambda answer, rid=request_id: self._on_teacher_done(rid, answer))
        worker.failed.connect(lambda error, rid=request_id: self.ctx.ai_failed(rid, error))
        self.ctx.workers.track(worker)

    # ---------- Documento arrastrado al chat (NUEVO) ----------
    def on_files_dropped(self, paths):
        """Un PDF/documento soltado sobre el chat: YUE lo lee en Modo Profesora.

        Reutiliza todo lo existente: activa el modo si hace falta y, según el tipo,
        arranca la lectura guiada del PDF o la lectura completa del material.
        """
        exts = getattr(self.ctx.chat, "DROP_EXTS", (".pdf", ".docx", ".pptx", ".epub",
                                                    ".txt", ".md", ".png", ".jpg", ".jpeg"))
        docs = [p for p in (paths or []) if str(p).lower().endswith(exts)]
        if not docs:
            self.ctx.say("Solo puedo leer documentos: PDF, Word, PowerPoint, EPUB, TXT o imágenes.")
            return
        path = docs[0]
        self.ctx.interrupt_response()   # por si estaba hablando en ese momento

        # La lectura de material vive en el Modo Profesora: lo activamos si aún no lo está.
        try:
            if self.modes is not None and self.modes.current_mode() != MODE_TEACHER:
                route = self.modes.activate(MODE_TEACHER)
                if route.switched:
                    self.apply_mode_switch(route)
        except Exception as exc:
            print("[profesora] no pude activar el modo al soltar el documento:", exc)

        nombre = os.path.basename(path)
        if len(docs) > 1:
            self.ctx.say(f"Me pasaste {len(docs)} archivos; empiezo por «{nombre}».")

        # PDF -> lectura guiada (lo abre y lo explica página por página, bajándolo ella).
        if path.lower().endswith(".pdf"):
            self._teacher_start_guided_pdf(path)
        else:
            # Word/PPT/EPUB/imagen/TXT -> lectura completa por el flujo normal de clase.
            self.teacher_process("lee y enséñame este documento: " + path)

    # ---- Lectura guiada de PDF (YUE abre el PDF y lo explica bajándolo) ----
    def _teacher_start_guided_pdf(self, path):
        info = self.teacher.load_pdf_guided(path)
        local = info.get("path") or path
        if not info["ok"]:
            # page_count == 0: el PDF está protegido/dañado o no se pudo abrir. Como
            # último recurso lo abrimos y lo MIRAMOS con la visión de YUE.
            try:
                self.ctx.pc.open_path(local)
            except Exception as exc:
                print("[profesora] no pude abrir el PDF:", exc)
            self.ctx.say(
                "No pude abrir ese PDF para leerlo (quizá está protegido o dañado). "
                "Lo abrí y voy a mirarlo con mis ojos para explicártelo."
            )
            QTimer.singleShot(1800, self.ctx.glance)
            return
        # Abre el PDF con el visor predeterminado usando la ruta ya resuelta.
        try:
            self.ctx.pc.open_path(local)
        except Exception as exc:
            print("[profesora] no pude abrir el PDF:", exc)
        self.ctx.say(
            f"Listo, abrí el PDF: tiene {info['pages']} página(s). Lo voy a escanear "
            "completo —incluso si está escaneado o tiene imágenes— y te lo explico "
            "página por página, pasando yo sola a la siguiente. Dime «pausa» cuando "
            "quieras que me detenga, o «siguiente» para apurarme."
        )
        # Damos tiempo a que el visor abra y tome el foco antes de explicar.
        QTimer.singleShot(1600, self._teacher_explain_current_page)

    def _teacher_vision_available(self) -> bool:
        """¿Hay un modelo de visión configurado para MIRAR páginas de imagen?"""
        try:
            if _cfg("VISION_OCR_ONLY", False):
                return False
            base, key, model = self.ctx.engine._vision_endpoint()
            return bool(base and key and model)
        except Exception:
            return False

    def _teacher_explain_current_page(self):
        material = self.teacher.current_page_material()
        if not material:
            self.ctx.say("Ya terminamos el documento. ¿Quieres que repasemos algo?")
            return
        # Marca esta respuesta como explicación de página: al terminar de hablarla,
        # el auto-avance podrá pasar sola a la siguiente (si está activado).
        self._page_explaining = True

        if material.get("needs_vision") and material.get("image") and self._teacher_vision_available():
            self.teacher.set_document_context(
                material.get("text", ""), f"PDF página {material['page_no']} (imagen)")
            system = self.teacher.build_system_prompt()
            instruccion = self.teacher.build_page_vision_instruction(material)
            request_id = self.ctx.chat_request_id
            self.ctx.chat_set_status(
                f"Observando la página {material['page_no']}/{material['total']} (imagen)…")
            worker = PdfPageVisionWorker(self.ctx.engine, system, instruccion, material["image"])
            worker.done.connect(lambda answer, rid=request_id: self._on_teacher_done(rid, answer))
            worker.failed.connect(lambda error, rid=request_id: self.ctx.ai_failed(rid, error))
            self.ctx.workers.track(worker)
            return

        # Páginas con texto (embebido u OCR): explicación normal por texto. Si es una
        # imagen y NO hay visión, igual usamos lo que el OCR haya podido rescatar.
        self.teacher.set_document_context("", "")
        messages = self.teacher.build_page_messages(material)
        self.teacher.set_document_context(material["text"], f"PDF página {material['page_no']}")
        request_id = self.ctx.chat_request_id
        estado = f"Explicando la página {material['page_no']}/{material['total']}"
        if material.get("source") == "ocr":
            estado += " (escaneada, leída con OCR)"
        self.ctx.chat_set_status(estado + "…")
        worker = AiWorker(self.ctx.engine, messages)
        worker.done.connect(lambda answer, rid=request_id: self._on_teacher_done(rid, answer))
        worker.failed.connect(lambda error, rid=request_id: self.ctx.ai_failed(rid, error))
        self.ctx.workers.track(worker)

    def _teacher_next_page(self):
        # YUE baja el PDF una pantalla (ella misma) y pasa a la siguiente página.
        self._pdf_scroll_down()
        if not self.teacher.advance_page():
            self.ctx.say(
                "Esa era la última página; terminamos el documento. Si quieres, te "
                "hago un resumen o te tomo unas preguntas."
            )
            return
        QTimer.singleShot(450, self._teacher_explain_current_page)

    def _pdf_scroll_down(self):
        """Envía «Avance de página» al visor del PDF que esté enfocado (mejor esfuerzo)."""
        try:
            self.ctx.pc._pyautogui_action({"action": "press", "key": "pagedown"})
        except Exception as exc:
            print("[profesora] no pude desplazar el PDF:", exc)

    def _on_teacher_done(self, request_id, text):
        if request_id != self.ctx.chat_request_id:
            return
        self.ctx.chat_set_status("")
        # Extrae y registra la evaluación oculta (si la hay) y limpia el texto.
        clean, evals = self.teacher.process_reply(text)
        if not clean:
            clean = "Sigamos con la clase. ¿Qué te gustaría repasar?"
        self.ctx.say(clean)
        # NUEVO: si acabo de explicar una página del PDF y el auto-avance está
        # activo, programo el paso a la siguiente en cuanto termine de hablar.
        fue_pagina = self._page_explaining
        self._page_explaining = False
        if (fue_pagina and self._autoadvance
                and self.teacher.guided_active()):
            self._teacher_schedule_autoadvance()

    # ---------- Auto-avance de la lectura guiada (NUEVO) ----------
    def _teacher_schedule_autoadvance(self):
        """Arranca la espera para pasar sola a la siguiente página."""
        self.ctx.teacher_autoadvance_pending = True
        QTimer.singleShot(700, self._teacher_autoadvance_tick)

    def _teacher_cancel_autoadvance(self):
        """Detiene cualquier auto-avance pendiente (al interrumpir o pausar)."""
        self.ctx.teacher_autoadvance_pending = False

    def _teacher_autoadvance_tick(self):
        """Espera a que YUE termine de hablar (y a que no estorbe) para avanzar."""
        if not self.ctx.teacher_autoadvance_pending:
            return  # se canceló (interrupción del usuario, pausa, salida de modo…)
        if not self._autoadvance or not self.teacher.guided_active():
            self.ctx.teacher_autoadvance_pending = False
            return
        # No avanzar si estás escribiendo/preguntando o si hay una orden en curso.
        if self.ctx.chat.is_user_composing() or self.ctx.pc_busy or self.ctx.vision_busy:
            QTimer.singleShot(1200, self._teacher_autoadvance_tick)
            return
        # Aún hablando: esperamos a que termine de explicar la página.
        try:
            hablando = bool(self.ctx.speaker.is_speaking)
        except Exception:
            hablando = False
        if hablando:
            QTimer.singleShot(700, self._teacher_autoadvance_tick)
            return
        # Terminó de hablar: pausa de lectura y luego pasa de página.
        self.ctx.teacher_autoadvance_pending = False
        pausa = int(_cfg("TEACHER_AUTOADVANCE_PAUSE_MS", 2500))
        QTimer.singleShot(max(300, pausa), self._teacher_autoadvance_do)

    def _teacher_autoadvance_do(self):
        """Verificación final y avance real a la siguiente página."""
        if not self._autoadvance or not self.teacher.guided_active():
            return
        if self.ctx.chat.is_user_composing() or self.ctx.pc_busy or self.ctx.vision_busy:
            return
        try:
            if self.ctx.speaker.is_speaking:
                return
        except Exception:
            pass
        self._teacher_next_page()

    # ---------- Comandos especiales del Modo Profesora ----------
    def _teacher_special_commands(self, text):
        """Reconoce y ejecuta comandos del Modo Profesora. Devuelve True si manejó
        el mensaje. Todo aditivo: si nada casa, devuelve False y sigue el flujo normal.
        """
        import unicodedata
        n = unicodedata.normalize("NFD", (text or "").lower())
        n = "".join(c for c in n if unicodedata.category(c) != "Mn").strip()

        # --- Auto-avance de la lectura de PDF: pausar / reanudar ---
        pausar = ("pausa la lectura", "no avances", "no pases de pagina", "no sigas sola",
                  "para de avanzar", "deja de avanzar", "espera ahi", "quedate ahi",
                  "quedate aqui", "modo manual", "no pases sola", "espera un momento",
                  "no cambies de pagina")
        reanudar = ("modo automatico", "avanza sola", "sigue sola", "sigue leyendo sola",
                    "continua sola", "lee tu sola", "pasa sola", "sigue tu sola",
                    "sigue leyendo tu", "avanza tu sola", "continua leyendo sola")
        if any(g in n for g in reanudar):
            self._autoadvance = True
            if self.teacher.guided_active():
                self.ctx.say("Va, sigo yo sola pasando de página. Dime «pausa» cuando quieras parar.")
                self._teacher_next_page()
            else:
                self.ctx.say("Listo, cuando leamos un PDF iré pasando de página sola.")
            return True
        if any(g in n for g in pausar):
            self._autoadvance = False
            self._teacher_cancel_autoadvance()
            self.ctx.say("Ok, me quedo aquí. Dime «siguiente» para pasar, o «sigue sola» para que continúe yo.")
            return True

        # --- (§4/§8) Empezar/terminar de observar al profesor humano ---
        obs_on = ("aprende de mi profesor", "observa la clase", "observa al profesor",
                  "aprende del profesor", "escucha la clase", "modo observacion",
                  "vas a observar", "aprende como enseno", "aprende como enseña")
        obs_off = ("deja de observar", "termina de observar", "ya no observes",
                   "para de aprender", "deja de escuchar la clase", "fin de la observacion")
        if any(g in n for g in obs_off):
            self.pedagogy.set_observing(False)
            self.teacher_participation.set_active(False)
            rep = self.pedagogy.report_text_es()
            self.ctx.say("Listo, dejo de observar. " + rep)
            return True
        if any(g in n for g in obs_on):
            self.pedagogy.set_observing(True)
            self.teacher_participation.set_active(True)
            self.ctx.say(
                "De acuerdo, voy a observar cómo enseña el profesor para aprender su "
                "estilo, y participaré solo cuando sea oportuno. Cuando termines, dime "
                "«deja de observar»."
            )
            return True

        # --- (§4) Contar qué he aprendido del profesor ---
        if ("que aprendiste" in n and "profesor" in n) or "modelo pedagogico" in n \
                or "como enseña mi profesor" in n or "como enseno mi profesor" in n:
            self.ctx.say(self.pedagogy.report_text_es())
            return True

        # --- (§13) Estadísticas educativas ---
        if any(g in n for g in ("estadisticas", "como voy", "como vamos", "mi progreso",
                                 "mi rendimiento", "resumen de clases", "como van los alumnos")):
            try:
                self.ctx.say(self.class_log.stats_text_es())
            except Exception:
                self.ctx.say(self.teacher.progress_summary())
            return True

        # --- (§11/§12) Asistente docente: generar material o corregir trabajos ---
        spec = teacher_assistant.detect_task(text)
        if spec is not None:
            self._teacher_run_assistant(spec, text)
            return True

        return False

    def _teacher_run_assistant(self, spec, text):
        """Genera material docente (§11) o corrige un trabajo (§12) con el modelo."""
        # Si hay una fuente (documento/página) en el mensaje, la usamos como material
        # o como el propio trabajo a corregir.
        material = ""
        try:
            src = self.teacher.maybe_read_source(text)
            if src is not None and src.ok:
                material = src.text
                self.teacher.log_material(src.origin, src.kind)
        except Exception as exc:
            print("[profesora] no pude leer la fuente para la tarea:", exc)

        # Recuerdo de clases pasadas y estilo aprendido, para un resultado informado.
        memoria = ""
        estilo = ""
        try:
            if spec.topic:
                memoria = self.class_log.recall(spec.topic)
            estilo = self.pedagogy.style_directive_es()
        except Exception:
            pass

        messages = teacher_assistant.build_messages(
            spec, text, material=material, memory_hint=memoria, style_hint=estilo)
        request_id = self.ctx.chat_request_id
        self.ctx.chat_set_status(f"Preparando {spec.label}…")
        worker = AiWorker(self.ctx.engine, messages)
        worker.done.connect(lambda answer, rid=request_id: self._on_teacher_assist_done(rid, answer, spec))
        worker.failed.connect(lambda error, rid=request_id: self.ctx.ai_failed(rid, error))
        self.ctx.workers.track(worker)

    def _on_teacher_assist_done(self, request_id, text, spec):
        if request_id != self.ctx.chat_request_id:
            return
        self.ctx.chat_set_status("")
        salida = (text or "").strip() or f"No pude preparar {spec.label} esta vez."
        # Recordatorio de rol: YUE asiste, no reemplaza al docente (§12/§15). Solo
        # para la corrección, que es donde importa la decisión final del profesor.
        if spec.task == "correccion":
            salida += "\n\n(Recuerda: esto es una propuesta de apoyo; la calificación final la decides tú.)"
        self.ctx.say(salida)

    # ---------- Observación del profesor y participación activa (§4/§8) ----------
    def _teacher_observe_and_participate(self, text):
        """Aprende del profesor humano y, si es oportuno, interviene brevemente."""
        # 1) Aprender el estilo (§4): incorpora la intervención al modelo pedagógico.
        try:
            self.pedagogy.ingest(text)
        except Exception as exc:
            print("[profesora] no pude aprender de la intervención:", exc)

        # 2) Decidir si conviene participar (§8), sin interrumpir de más.
        try:
            self.teacher_participation.note_teacher_turn()
            decision = self.teacher_participation.decide(text)
        except Exception:
            decision = teacher_participation.Decision(False)
        if not decision.should:
            return

        estilo = ""
        try:
            estilo = self.pedagogy.style_directive_es()
        except Exception:
            pass
        contexto = self.teacher.current_topic()
        messages = teacher_participation.build_intervention_messages(
            text, decision, recent_context=contexto, style_hint=estilo)
        self.teacher_participation.note_intervened()
        request_id = self.ctx.chat_request_id
        self.ctx.chat_set_status("Yue va a aportar algo a la clase…")
        worker = AiWorker(self.ctx.engine, messages)
        worker.done.connect(lambda answer, rid=request_id: self._on_teacher_done(rid, answer))
        worker.failed.connect(lambda error, rid=request_id: self.ctx.ai_failed(rid, error))
        self.ctx.workers.track(worker)