"""MemoryProactive (PR 5, paso 3).

Extrae de Controller los 13 métodos de la fila 3 de §4.1: memoria episódica
y narrativa (observe/note/followup), check-in de ánimo proactivo y explícito,
señal visual de riesgo sostenida, iniciativa, consolidación al arranque y
diagnóstico de memoria histórica.

Service-locator: TODAS las dependencias del host llegan por `ctx` (componentes,
callbacks o estado get/set) — nunca por referencia al Controller. Los módulos
de core (safety, commands, bonding, emotion, memory_consolidation,
core.state.INITIATIVE_LEVELS) y config se importan directamente.
"""
from __future__ import annotations

import threading
import time


def _cfg(name: str, default):
    try:
        import config  # toplevel del proyecto
        return getattr(config, name, default)
    except Exception:
        return default


class MemoryProactive:
    """Estado privado del paquete (interno, aquí, NO en ctx):

    - `_last_visual_risk_ask`: antirrebote del check visual. Solo lo tocan los
      dos métodos de riesgo visual de este director; nada en el host lo lee.
    - `_episodic_lock` / `_story_lock`: exclusión mutua de los hilos de
      observación episódica/narrativa; ningún otro dueño las comparte.

    Estado compartido que SÍ vive en ctx (get/set): pending_checkin,
    last_user_activity, last_companion, last_user_text, autonomy_busy,
    pc_busy, vision_busy — cada uno lo tocan dueños distintos (on_user_message
    en el host y/o futuros directores).
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._last_visual_risk_ask = 0.0
        self._episodic_lock = threading.Lock()
        self._story_lock = threading.Lock()
        # PR 5 paso 11: dueños de los canales de bienestar y actividad. Antes los
        # publicaba Controller; ahora los publica este director en su __init__.
        self.ctx.wellbeing_nudge_due = self.wellbeing_nudge_due
        self.ctx.show_activity = self.show_activity

    # ---------- señales sostenidas (cámara + ánimo) ----------
    def external_mood_signal(self) -> bool:
        """Señal SOSTENIDA no textual (cámara + histórico de ánimo).

        Se la pasamos al cerebro afectivo para que la seguridad pueda subir
        medio nivel cuando lo que se ve lleva un rato sin cuadrar con lo que se
        dice. Reutiliza `safety.detect_risk_from_camera`, que ya exige
        PERSISTENCIA por ambas vías, así que un gesto puntual no dispara nada.

        A prueba de fallos: ante cualquier error, no aporta señal.
        """
        try:
            from core import safety
            return bool(safety.detect_risk_from_camera(self.ctx.camera, self.ctx.memory))
        except Exception:
            return False

    def should_check_visual_risk(self) -> bool:
        """¿Inyectar la directiva de cuidado por señal visual/de ánimo sostenida?

        Combina la señal de cámara con el historial de mood_log (safety decide) y
        aplica un antirrebote: aunque la señal persista, YUE no pregunta '¿cómo
        estás?' en cada mensaje, sino como mucho una vez cada cierto tiempo
        (CAMERA_RISK_ASK_COOLDOWN, 15 min por defecto). Así se evita la alarma
        constante. A prueba de fallos: ante cualquier error, no dispara nada.
        """
        try:
            from core import safety
            if not safety.detect_risk_from_camera(self.ctx.camera, self.ctx.memory):
                return False
        except Exception:
            return False
        ahora = time.time()
        cooldown = float(_cfg("CAMERA_RISK_ASK_COOLDOWN", 900.0))
        if (ahora - self._last_visual_risk_ask) < cooldown:
            return False
        self._last_visual_risk_ask = ahora
        return True

    # ---------- check-in de ánimo (/animo y proactivo) ----------
    def start_mood_checkin(self):
        """Lanza el check-in explícito: pregunta la nota del 1 al 5 y espera."""
        self.ctx.pending_checkin = True
        try:
            self.ctx.autonomy.mark_checkin()
        except Exception as exc:
            print("[checkin] no pude marcar el check-in:", exc)
        from core import commands
        self.ctx.say(commands.MOOD_CHECKIN_PROMPT)

    def resolve_mood_checkin(self, text) -> bool:
        """Interpreta la respuesta al check-in. True si era una nota válida.

        Si lo es, la guarda en mood_log con fuente='checkin' y responde. Si no,
        cierra el check-in en silencio y devuelve False para que el mensaje siga
        su curso normal (se conversa y su ánimo se capta por el texto, como
        siempre). Nunca deja al usuario atrapado en la pregunta.
        """
        from core import commands
        parsed = commands.parse_mood_checkin(text)
        if not parsed:
            self.ctx.pending_checkin = False
            return False
        self.ctx.pending_checkin = False
        try:
            self.ctx.memory.add_mood(
                "checkin",
                parsed["emocion"],
                parsed["intensidad"],
                parsed["texto_origen"],
            )
        except Exception as exc:
            print("[checkin] no pude guardar el ánimo:", exc)
        self.ctx.chat.ensure_input_ready(focus=False)
        self.ctx.say(parsed["reply"])
        return True

    def yue_may_take_initiative(self, minimo="medium"):
        """¿Se permite YUE hablar primero ahora mismo?

        Hasta ahora `initiative` se calculaba y no lo leía nadie: era un campo
        decorativo más. Esta es su razón de existir.

        Si el usuario pidió espacio, `SupportPolicy` pone la necesidad en
        `GIVE_SPACE`, el mapeo la traduce a `initiative = "none"` y aquí se corta
        cualquier intento de YUE de arrancar a hablar. Da igual que el
        temporizador de check-in diga que toca: si alguien acaba de pedir que lo
        dejen en paz, insistir es exactamente lo que no hay que hacer.

        Devuelve True si no hay gestor (degradación: se comporta como antes).
        """
        gestor = self.ctx.state_manager
        if gestor is None:
            return True
        try:
            from core.state import INITIATIVE_LEVELS
            actual = gestor.yue_state().initiative
            return INITIATIVE_LEVELS.index(actual) >= INITIATIVE_LEVELS.index(minimo)
        except Exception:
            return True

    # ---------- memoria episódica emocional ----------
    def episodic_observe(self, text):
        """Detecta/actualiza el episodio que pueda haber en este mensaje.

        Reutiliza el análisis afectivo y de seguridad que YA hizo el cerebro de
        acompañamiento (`ctx.last_companion`): no se vuelve a llamar a ningún
        modelo para saber qué siente. Lo único que se busca aquí es el
        ACONTECIMIENTO: qué es, cuándo, por qué le importa y si merece que YUE
        pregunte después.

        A prueba de fallos y no bloqueante: si algo va mal, la conversación
        sigue exactamente igual.
        """
        if self.ctx.episodic is None:
            # Sin memoria episódica, la narrativa sigue viva por su cuenta: usa
            # sus propias señales deterministas y el afecto ya calculado.
            if self.ctx.story_memory is not None:
                self.story_observe(text)
            return
        resultado = self.ctx.last_companion
        afecto = getattr(resultado, "affect", None)
        decision = getattr(resultado, "decision", None)
        try:
            nivel = int(getattr(getattr(resultado, "safety", None), "level", 0) or 0)
        except Exception:
            nivel = 0

        # El id del mensaje recién insertado, para poder trazar el origen.
        message_id = None
        try:
            message_id = self.ctx.memory.last_message_id()
        except Exception:
            message_id = None

        def _trabajo():
            episode_id = None
            try:
                with self._episodic_lock:
                    salida = self.ctx.episodic.observe(
                        text, affect=afecto, decision=decision,
                        safety_level=nivel, message_id=message_id)
                episode_id = (salida or {}).get("episode_id")
            except Exception as exc:
                print("[episodic] fallo observando el mensaje:", exc)
            # NUEVO (memoria narrativa): va DESPUÉS y en el mismo hilo, a
            # propósito. Así recibe el episodio que se acaba de crear y puede
            # apuntar a él (`source_type='emotional_episode'`) en vez de
            # duplicar su texto, y hereda su `status` e `importance` en lugar
            # de volver a deducirlos.
            self.story_observe(
                text, affect=afecto, safety_level=nivel,
                message_id=message_id, episode_id=episode_id, inline=True)

        if bool(_cfg("EPISODIC_ASYNC", True)):
            threading.Thread(target=_trabajo, daemon=True,
                             name="episodic-observe").start()
        else:
            _trabajo()

    # ---------- memoria narrativa (historias que evolucionan) ----------
    def story_observe(self, text, *, affect=None, safety_level=None,
                      message_id=None, episode_id=None, inline=False):
        """Hace avanzar las HISTORIAS con este mensaje.

        Determinista y barato: no llama a ningún modelo. Detecta a las personas
        de las que habla, encuentra la historia que ya existe (o la crea si de
        verdad lo merece), le añade el acontecimiento y recalcula su peso.

        `inline=True` significa que ya estamos en el hilo de fondo del episodio
        y no hace falta abrir otro.
        """
        if self.ctx.story_memory is None:
            return
        resultado = self.ctx.last_companion
        if affect is None:
            affect = getattr(resultado, "affect", None)
        if safety_level is None:
            try:
                safety_level = int(
                    getattr(getattr(resultado, "safety", None), "level", 0) or 0)
            except Exception:
                safety_level = 0
        if message_id is None:
            try:
                message_id = self.ctx.memory.last_message_id()
            except Exception:
                message_id = None

        def _trabajo():
            try:
                with self._story_lock:
                    self.ctx.story_memory.observe(
                        text, affect=affect, episode_id=episode_id,
                        message_id=message_id, safety_level=int(safety_level or 0))
            except Exception as exc:
                print("[story-memory] fallo observando el mensaje:", exc)

        if inline or not bool(_cfg("EPISODIC_ASYNC", True)):
            _trabajo()
        else:
            threading.Thread(target=_trabajo, daemon=True,
                             name="story-observe").start()

    def story_note_response(self, respuesta):
        """Anota la parte de YUE en la historia, no solo la de él.

        Es lo que separa «tengo un registro sobre ti» de «esto lo vivimos
        juntos»: la historia guarda también qué hizo ella en ese momento.
        Reutiliza la misma etiqueta corta que normaliza la memoria episódica.
        """
        if self.ctx.story_memory is None:
            return
        decision = getattr(self.ctx.last_companion, "decision", None)

        def _trabajo():
            try:
                etiqueta = ""
                if self.ctx.episodic is not None:
                    etiqueta = self.ctx.episodic._normalize_action(respuesta, decision)
                with self._story_lock:
                    self.ctx.story_memory.note_yue_action(etiqueta or "te escuchó")
            except Exception as exc:
                print("[story-memory] no pude anotar lo que hice:", exc)

        if bool(_cfg("EPISODIC_ASYNC", True)):
            threading.Thread(target=_trabajo, daemon=True,
                             name="story-action").start()
        else:
            _trabajo()

    def episodic_note_response(self, respuesta):
        """Completa `yue_action` una vez que YUE ya ha dicho lo suyo.

        El episodio se crea ANTES de que ella responda, así que en ese momento
        no se puede saber qué hizo. Aquí se anota en una etiqueta corta
        («tranquilizó», «practicaron preguntas»); nunca la respuesta entera,
        que ya vive en `messages`.
        """
        if self.ctx.episodic is None:
            return
        decision = getattr(self.ctx.last_companion, "decision", None)

        def _trabajo():
            try:
                with self._episodic_lock:
                    self.ctx.episodic.note_yue_response(respuesta, decision=decision)
            except Exception as exc:
                print("[episodic] no pude anotar lo que hice:", exc)

        if bool(_cfg("EPISODIC_ASYNC", True)):
            threading.Thread(target=_trabajo, daemon=True,
                             name="episodic-action").start()
        else:
            _trabajo()

    def episodic_followup(self):
        """Intenta retomar un acontecimiento pendiente. True si YUE va a hablar.

        Es la PRIMERA opción del check-in proactivo: si hay algo concreto que
        retomar, retomarlo es infinitamente mejor que un «¿cómo va tu día?».
        Todos los frenos (iniciativa, espacio pedido, riesgo reciente, tope de
        una pregunta por episodio) ya los aplica `due_followup`; aquí solo se
        redacta y se dice.
        """
        if self.ctx.episodic is None:
            return False
        try:
            episodio = self.ctx.episodic.due_followup()
        except Exception as exc:
            print("[episodic] fallo buscando seguimientos:", exc)
            return False
        if not episodio:
            return False

        # Se marca ANTES de hablar, a propósito: si algo falla por el camino,
        # preferimos perder una pregunta a repetirla en el siguiente latido.
        self.ctx.episodic.mark_followup_asked(episodio["id"])
        try:
            self.ctx.autonomy.mark_checkin()
        except Exception:
            pass

        respaldo = self.ctx.episodic.fallback_followup_text(episodio)

        # Con motor de IA, lo escribe ella con su propia voz. El prompt le
        # prohíbe explícitamente sonar a recordatorio automático.
        if self.ctx.engine is not None:
            try:
                instruccion = self.ctx.episodic.build_followup_prompt(episodio)
                try:
                    from core import bonding
                    nivel, _, _ = bonding.progress(self.ctx.memory.get_bond_points())
                except Exception:
                    nivel = None
                sistema = self.ctx.system_prompt(nivel)
                mensajes = [
                    {"role": "system", "content": sistema},
                    {"role": "system", "content": instruccion},
                ]
                from engine.ai_worker import AiWorker
                worker = AiWorker(self.ctx.engine, mensajes)
                worker.done.connect(
                    lambda texto, alt=respaldo: self._say_followup(texto or alt))
                worker.failed.connect(
                    lambda _error, alt=respaldo: self._say_followup(alt))
                self.ctx.workers.track(worker)
                return True
            except Exception as exc:
                print("[episodic] no pude redactar el seguimiento:", exc)

        self._say_followup(respaldo)
        return True

    def _say_followup(self, texto):
        """Dice el seguimiento y lo deja en el historial como turno de YUE."""
        limpio = (texto or "").strip()
        if not limpio:
            return
        try:
            from core import emotion
            limpio = emotion.clean_response(limpio)
        except Exception:
            pass
        try:
            self.ctx.memory.add_message("assistant", limpio)
        except Exception:
            pass
        self.ctx.say(limpio)

    def maybe_checkin(self):
        """Evalúa (barato) si YUE debería preguntar el ánimo por iniciativa propia.

        No intrusivo: solo si está habilitado, no hay nada en curso, el usuario
        lleva un rato inactivo (misma lógica que autonomía) y la política de
        Autonomy lo permite (cada X horas, máx. una vez al día). La pregunta es
        natural y conversacional; la respuesta se capta como una charla normal.
        """
        try:
            if not bool(_cfg("CHECKIN_ENABLED", True)):
                return
            if self.ctx.pending_checkin:
                return
            if self.ctx.autonomy_busy or self.ctx.pc_busy or self.ctx.vision_busy:
                return
            if self.ctx.chat.is_user_composing():
                return
            # No interrumpir una clase en modo profesora.
            if self.ctx.modes is not None and self.ctx.modes.current_mode() == MODE_TEACHER:
                return
            # No hablar por encima de la voz de YUE.
            try:
                if self.ctx.speaker.is_speaking:
                    return
            except Exception:
                pass
            # NUEVO: respeta la INICIATIVA decidida por el cerebro central. Si
            # el usuario pidió espacio (GIVE_SPACE → initiative "none"), YUE no
            # pregunta nada aunque el temporizador diga que toca. Un check-in
            # justo después de que alguien pida que lo dejen en paz convierte el
            # acompañamiento en acoso.
            if not self.yue_may_take_initiative("medium"):
                return
            # Requiere inactividad real (misma lógica que la iniciativa autónoma).
            idle = time.time() - self.ctx.last_user_activity
            if idle < _cfg("AUTONOMY_IDLE_SECONDS", 120):
                return
            # NUEVO (memoria episódica): ANTES del check-in genérico, ¿hay algo
            # CONCRETO que retomar? Preguntar «¿y al final cómo te fue con la
            # entrevista?» es lo que distingue acompañar de rellenar silencios.
            # Solo si no hay nada pendiente se cae al «¿cómo va tu día?» de
            # siempre, que sigue intacto.
            #
            # Este camino tiene su propia política de tiempo (una pregunta por
            # episodio, ventana de calma, bloqueo por riesgo), así que no pasa
            # por `should_checkin()`: un acontecimiento no espera 20 horas.
            try:
                if self.episodic_followup():
                    return
            except Exception as exc:
                print("[episodic] fallo en el seguimiento proactivo:", exc)
            # Política de tiempo (cada X horas / máx. una vez al día): en Autonomy.
            if not self.ctx.autonomy.should_checkin():
                return
            self.ctx.autonomy.mark_checkin()
            pregunta = self.ctx.autonomy.checkin_question()
            # La dejamos en memoria como turno de YUE para que la respuesta del
            # usuario fluya con contexto por la conversación normal.
            try:
                self.ctx.memory.add_message("assistant", pregunta)
            except Exception:
                pass
            self.ctx.say(pregunta)
        except Exception as exc:
            print("[checkin] fallo evaluando el check-in proactivo:", exc)

    def wellbeing_nudge_due(self) -> bool:
        """¿Toca el recordatorio DISCRETO de apoyo humano/profesional?

        Condiciones (cualquiera):
          (1) el riesgo textual saltó varias veces en los últimos días, o
          (2) uso muy intensivo: una sesión continua de más de X horas.
        Se espacia con una ventana breve + cooldown persistente (state), para que
        salga "de tanto en tanto" y no en cada mensaje. Se apaga por completo con
        WELLBEING_NUDGE_ENABLED. A prueba de fallos: ante error, no añade nada.

        PR 5 paso 11: extraído de Controller (`_wellbeing_nudge_due`). Las
        marcas de cooldown/ventana viven en el state PERSISTENTE de la memoria
        (`wellbeing_nudge_ts`), no en atributos del host, así que el movimiento
        no cambia el comportamiento: vuelve a funcionar igual entre arranques.
        """
        if not bool(_cfg("WELLBEING_NUDGE_ENABLED", True)):
            return False

        condicion = False
        # (1) riesgo textual repetido en la ventana reciente
        try:
            dias = int(_cfg("WELLBEING_RISK_DAYS", 7))
            minimo = int(_cfg("WELLBEING_MIN_RISK_EVENTS", 2))
            if self.ctx.memory.count_risk_events(dias) >= minimo:
                condicion = True
        except Exception:
            pass
        # (2) sesión continua muy larga
        if not condicion:
            try:
                horas = float(_cfg("WELLBEING_SESSION_HOURS", 3.0))
                gap = float(_cfg("WELLBEING_SESSION_GAP_MIN", 30.0)) * 60.0
                if self.ctx.memory.session_span_seconds(max_gap=gap) >= horas * 3600.0:
                    condicion = True
            except Exception:
                pass

        if not condicion:
            return False

        # Espaciado con ventana + cooldown (marca persistente en 'state').
        ahora = time.time()
        try:
            inicio = float(self.ctx.memory.get_state("wellbeing_nudge_ts", "0") or 0.0)
        except Exception:
            inicio = 0.0
        ventana = float(_cfg("WELLBEING_NUDGE_WINDOW_MIN", 20.0)) * 60.0
        cooldown = float(_cfg("WELLBEING_NUDGE_COOLDOWN_HOURS", 8.0)) * 3600.0

        # Dentro de una ventana abierta: seguimos ofreciéndolo (varios turnos para
        # que YUE lo suelte con naturalidad).
        if inicio and (ahora - inicio) < ventana:
            return True
        # En cooldown tras la última ventana: silencio.
        if inicio and (ahora - inicio) < cooldown:
            return False
        # Abrimos una ventana nueva.
        try:
            self.ctx.memory.set_state("wellbeing_nudge_ts", ahora)
        except Exception:
            pass
        return True

    def show_activity(self):
        """«/actividad» o «mi actividad»: resumen legible de la última semana.

        PR 5 paso 11: extraído de Controller (`_show_activity`).
        """
        resumen = ""
        try:
            resumen = self.ctx.memory.get_activity_summary(7)
        except Exception as exc:
            print("[actividad] no pude leer el resumen:", exc)
        if resumen:
            self.ctx.say(resumen[0].upper() + resumen[1:])
        else:
            self.ctx.say("Todavía no hay mucho en la bitácora de esta semana.")

    # ---------- consolidación de memoria a largo plazo (al arrancar) ----------
    def schedule_memory_consolidation(self):
        """Lanza la consolidación en un hilo de fondo. Best-effort: nunca bloquea
        ni interrumpe el chat; si no toca o falla, no cambia nada."""
        def _worker():
            try:
                from core import memory_consolidation
                resumen = memory_consolidation.consolidate(self.ctx.memory, self.ctx.engine)
                if resumen:
                    print("[memoria] consolidé un periodo del historial en un resumen.")
            except Exception as exc:
                print("[memoria] la consolidación falló (se reintentará):", exc)
            # NUEVO (memoria narrativa): las historias tienen su PROPIO ritmo
            # (STORY_CONSOLIDATION_DAYS) y su propia marca de tiempo, así que
            # se revisan aunque el resumen a largo plazo aún no tocara. Cuando
            # ambas tocan a la vez, `consolidate()` ya llamó a esta y la
            # segunda llamada sale enseguida por «aún no toca»: es idempotente.
            #
            # Su primera mitad ni siquiera necesita modelo: sincroniza las metas
            # y recalcula el peso de cada historia con la evidencia acumulada.
            if self.ctx.story_memory is not None:
                try:
                    informe = self.ctx.story_memory.consolidate()
                    if informe and (informe.get("created") or informe.get("events")):
                        print("[memoria] las historias que sigo con él avanzaron.")
                except Exception as exc:
                    print("[story-memory] la revisión de historias falló:", exc)
        threading.Thread(target=_worker, name="YueMemoryConsolidation",
                         daemon=True).start()

    def diagnose_memory(self, consulta=""):
        """NUEVO (memoria histórica): enseña QUÉ recuerdos encontraría YUE.

        `/recuerdos Andrea volvió a escribirme` responde con los recuerdos
        antiguos que entrarían al prompt y su puntuación. Sin argumento usa el
        último mensaje del usuario. Es una herramienta de diagnóstico: sale por
        el chat como texto plano (sin voz), igual que /diagvoz.
        """
        consulta = (consulta or self.ctx.last_user_text or "").strip()
        if self.ctx.memory_ext is None:
            self.ctx.chat.show_reply(
                "La memoria histórica está desactivada "
                "(MEMORY_RELEVANCE_ENABLED=false) o no se pudo cargar.")
            return
        if not consulta:
            self.ctx.chat.show_reply(
                "Escribe algo que buscar: /recuerdos Andrea volvió a escribirme")
            return
        try:
            hits = self.ctx.memory_ext.search_history(consulta)
        except Exception as exc:
            self.ctx.chat.show_reply(f"No pude buscar en el historial: {exc}")
            return
        if not hits:
            self.ctx.chat.show_reply(
                f"Sin recuerdos por encima del umbral para «{consulta}».\n"
                "Nada de esto llegaría al prompt (que es lo correcto: mejor no "
                "recordar que recordar cualquier cosa).")
            return
        lineas = [f"Recuerdos que YUE usaría para «{consulta}»:"]
        for n, h in enumerate(hits, start=1):
            lineas.append(
                f"{n}. score={h['score']} · similitud={h['similitud']} "
                f"· {h['grado']} · {h['fecha_humana']} ({h['fecha']})\n"
                f"   [{h['role']}] {h['texto']}")
        informe = "\n".join(lineas)
        print("[memory-ext] diagnóstico:\n" + informe)
        self.ctx.chat.show_reply(informe)


MODE_TEACHER = "teacher"