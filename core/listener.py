"""Escucha continua con interrupción de voz y filtro reforzado de eco."""
from __future__ import annotations

import re
import time
import unicodedata
from collections import deque  # NUEVO: historial de frases TTS con ventana temporal
from difflib import SequenceMatcher

from PyQt5.QtCore import QObject, pyqtSignal

import config

# NUEVO (interrupción por voz fiable): capa aditiva que decide, de forma más
# permisiva y rápida, si lo oído mientras YUE habla debe cortarla. Si el paquete
# no está o falla, el listener conserva su lógica anterior intacta.
try:
    from voice_flow import BargeInGate
except Exception:  # pragma: no cover - degradación elegante
    BargeInGate = None


def list_microphones():
    try:
        import speech_recognition as sr
        return list(enumerate(sr.Microphone.list_microphone_names()))
    except Exception as exc:
        print("[oido] no pude listar micrófonos:", exc)
        return []


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


def _truthy_cfg(nombre: str, defecto: bool) -> bool:
    """NUEVO: lee un booleano de config con respaldo, tolerando bool o texto."""
    val = getattr(config, nombre, defecto)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "si", "sí", "yes", "on")


class VoiceListener(QObject):
    heard = pyqtSignal(str)
    status = pyqtSignal(str)
    barge_in = pyqtSignal(str)
    # NUEVO: texto captado por el micro MIENTRAS suena media (típicamente la letra
    # de la canción o el diálogo de un vídeo). No es una orden; se emite aparte
    # para que YUE pueda comentar «qué tal la canción» con su contenido real.
    media_heard = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.enabled = config.MIC_ENABLED
        self._wanted_enabled = config.MIC_ENABLED
        self.lang = config.MIC_LANG
        self.device_index = config.MIC_DEVICE_INDEX
        self._recognizer = None
        self._mic = None
        self._stop_fn = None
        self._speaking = False
        self._suppress_until = 0.0
        self._last_tts = ""
        self._last_tts_until = 0.0
        # NUEVO (petición 3): tras dejar de hablar, mantenemos un rato de "vigilancia
        # de eco" (no bloqueo total): se sigue comparando contra el TTS reciente y se
        # descartan fragmentos muy cortos, que casi siempre son cola de su propia voz.
        self._echo_tail_until = 0.0
        self._self_listen_tail = float(getattr(config, "MIC_SELF_LISTEN_TAIL", 2.5))
        self._speaking_echo_overlap = float(getattr(config, "MIC_SPEAKING_ECHO_OVERLAP", 0.34))
        # NUEVO (petición 2): ¿hay vídeo/música sonando por los altavoces? Lo pone
        # main.py conectándolo al SystemAudioReactor. Mientras esté activo, el micro
        # no toma ese audio como si le hablaras tú.
        self._media_playing = False
        self._media_guard = _truthy_cfg("MIC_MEDIA_GUARD_ENABLED", True)
        self._media_guard_wake = _truthy_cfg("MIC_MEDIA_GUARD_REQUIRE_WAKE_WORD", True)
        self._media_guard_min_chars = int(getattr(config, "MIC_MEDIA_GUARD_MIN_CHARS", 8))
        # NUEVO (bug 2): historial de frases TTS como (texto_normalizado, timestamp).
        # Antes _last_tts solo guardaba la ÚLTIMA frase; si Yue decía varias frases
        # seguidas, los ecos de frases anteriores ya no se detectaban.
        self._tts_history = deque()
        self._tts_history_window = 30.0  # segundos de vigencia de cada entrada
        self._wake_words = tuple(
            _norm(x) for x in getattr(config, "MIC_WAKE_WORDS", ("yue", "oye yue")) if _norm(x)
        )
        # NUEVO (interrupción por voz fiable): puerta de barge-in. Aditiva: si el
        # paquete voice_flow no está disponible, queda en None y el callback usa
        # exclusivamente la lógica de filtrado anterior.
        self._barge_gate = None
        if BargeInGate is not None and getattr(config, "MIC_BARGE_IN_SMART", True):
            try:
                self._barge_gate = BargeInGate()
            except Exception as exc:
                print("[oido] no pude iniciar la puerta de interrupción:", exc)
                self._barge_gate = None

        # NUEVO (arreglo "no me escucha"): latido y contadores para diagnóstico.
        # _last_audio_ts se actualiza cada vez que el hilo de fondo procesa audio
        # (aunque no se reconozca nada). Sirve al /diagvoz y al vigilante para
        # saber si el micrófono sigue vivo.
        self._last_audio_ts = 0.0
        self._last_error = ""
        self._start_attempts = 0
        # NUEVO (arreglo "tarda en escucharme"): hilo trabajador que reconoce el
        # audio por internet SIN bloquear la captura del micrófono. Se enciende en
        # start() y se apaga en stop(). Si _async_reco está en false, se reconoce
        # en el mismo hilo (comportamiento anterior).
        self._async_reco = _truthy_cfg("MIC_ASYNC_RECOGNITION", True)
        self._reco_queue = None
        self._reco_thread = None
        # NUEVO (arreglo "no me escucha"): vigilante que reintenta abrir el micro
        # si debía estar escuchando pero el arranque falló (dispositivo ocupado,
        # etc.). Aditivo: si algo falla, el listener sigue igual que antes.
        self._watchdog = None
        try:
            secs = int(getattr(config, "MIC_WATCHDOG_SECONDS", 20))
            if secs > 0:
                from PyQt5.QtCore import QTimer
                self._watchdog = QTimer(self)
                self._watchdog.setInterval(secs * 1000)
                self._watchdog.timeout.connect(self._watchdog_tick)
                self._watchdog.start()
        except Exception as exc:  # pragma: no cover - degradación elegante
            print("[oido] no pude iniciar el vigilante del micrófono:", exc)
            self._watchdog = None

    def set_tts_text(self, text: str):
        self._last_tts = _norm(text)
        self._last_tts_until = time.time() + 20.0
        # NUEVO (bug 2): además de _last_tts (se mantiene por compatibilidad),
        # acumulamos cada frase en el historial con su timestamp.
        if self._last_tts:
            self._tts_history.append((self._last_tts, time.time()))
        self._prune_tts_history()

    def _prune_tts_history(self):
        """NUEVO (bug 2): descarta entradas del historial más viejas que la ventana."""
        limit = time.time() - self._tts_history_window
        while self._tts_history and self._tts_history[0][1] < limit:
            self._tts_history.popleft()

    def _active_tts_texts(self):
        """NUEVO (bug 2): textos TTS aún vigentes (dentro de la ventana de 30 s)."""
        self._prune_tts_history()
        return [entry[0] for entry in self._tts_history]

    def set_speaking(self, value):
        """Mantiene el micrófono activo para poder interrumpir a Yue."""
        was_speaking = self._speaking  # NUEVO (bug 1): estado previo para detectar la transición
        self._speaking = bool(value)
        # NUEVO (bug 1): al terminar de hablar (True -> False) activamos el cooldown.
        # _suppress_until existía y el callback ya lo respetaba, pero nadie lo
        # actualizaba, así que la "cola" del audio de Yue (reverberación, buffer
        # del micrófono) entraba como si fuera voz del usuario.
        if was_speaking and not self._speaking:
            self._suppress_until = time.time() + config.MIC_ECHO_COOLDOWN
            # NUEVO (petición 3): tras el cooldown duro, dejamos una cola de
            # vigilancia (más larga) donde seguimos filtrando eco aunque el micro
            # ya vuelva a escuchar, para cazar la reverberación de su propia voz.
            self._echo_tail_until = time.time() + max(
                config.MIC_ECHO_COOLDOWN, self._self_listen_tail
            )
        if not self._wanted_enabled:
            return
        if self._speaking and getattr(config, "MIC_BARGE_IN_ENABLED", True):
            self.status.emit("escuchando interrupciones")
        else:
            self.status.emit("escuchando")

    def set_media_playing(self, value):
        """NUEVO (petición 2): main.py avisa aquí cuando hay vídeo/música sonando.

        No apaga el micro (podrías querer interrumpir con «Yue…»), pero activa un
        filtro para que el diálogo o la letra que salen por los altavoces no se
        escriban en el chat como si te estuviera oyendo a ti.
        """
        self._media_playing = bool(value)

    def start(self):
        if not self.enabled or not self._wanted_enabled or self._stop_fn:
            return
        self._start_attempts += 1
        try:
            import speech_recognition as sr
        except Exception as exc:
            self._last_error = f"import SpeechRecognition: {exc}"
            self.status.emit("sin reconocimiento de voz (instala SpeechRecognition)")
            print("[oido] SpeechRecognition no disponible:", exc)
            return
        try:
            self._recognizer = sr.Recognizer()
            self._recognizer.energy_threshold = config.MIC_ENERGY_THRESHOLD
            self._recognizer.dynamic_energy_threshold = config.MIC_DYNAMIC
            self._recognizer.pause_threshold = config.MIC_PAUSE
            self._recognizer.non_speaking_duration = min(0.5, config.MIC_PAUSE)

            self._mic = (
                sr.Microphone(device_index=self.device_index)
                if self.device_index is not None else sr.Microphone()
            )
            with self._mic as source:
                self._recognizer.adjust_for_ambient_noise(source, duration=0.35)
            # NUEVO (arreglo "no me escucha"): el tope del umbral ahora es
            # configurable (MIC_ENERGY_MAX). Antes estaba fijo en *4 (=1000 por
            # defecto); si tras el ajuste al ruido quedaba muy alto, la voz normal
            # nunca lo cruzaba y parecía que el micro estaba muerto. El valor por
            # defecto (1000) es idéntico al comportamiento anterior.
            ceiling = max(
                int(getattr(config, "MIC_ENERGY_MAX", 1000)),
                config.MIC_ENERGY_THRESHOLD,
            )
            umbral_ajustado = self._recognizer.energy_threshold
            self._recognizer.energy_threshold = min(umbral_ajustado, ceiling)
            if umbral_ajustado > ceiling:
                print(
                    f"[oido] el ruido ambiente subió el umbral a {umbral_ajustado:.0f}; "
                    f"lo limito a {ceiling} (MIC_ENERGY_MAX). Si aún no te escucha, "
                    f"baja MIC_ENERGY_MAX en el .env."
                )
            print(
                f"[oido] umbral={self._recognizer.energy_threshold:.0f} "
                f"dinámico={config.MIC_DYNAMIC} micro={self.device_index}"
            )
            # NUEVO (arreglo "tarda en escucharme"): enciende el hilo trabajador
            # que reconocerá el audio en segundo plano, para no bloquear la captura.
            self._start_worker()
            self._stop_fn = self._recognizer.listen_in_background(
                self._mic,
                self._callback,
                phrase_time_limit=config.MIC_PHRASE_LIMIT,
            )
            self._last_error = ""
            self.status.emit("escuchando")
        except Exception as exc:
            self._last_error = f"apertura del micrófono: {exc}"
            self.status.emit("no pude abrir el micrófono (revisa PyAudio / permisos)")
            print("[oido] error de micrófono:", exc)

    def _sliding_similarity(self, heard: str, spoken: str) -> float:
        h = heard.split()
        s = spoken.split()
        if not h or not s:
            return 0.0
        width = max(1, len(h))
        best = 0.0
        for start in range(0, max(1, len(s) - width + 1)):
            chunk = " ".join(s[start:start + width + 2])
            best = max(best, SequenceMatcher(None, heard, chunk).ratio())
        return best

    def _matches_tts(self, heard: str, spoken: str, label: str) -> bool:
        """NUEVO (bug 2): comparación de eco contra UNA frase TTS.

        Es exactamente la misma lógica que ya se usaba contra _last_tts
        (ratio global, ventana deslizante, solape de tokens y contención),
        extraída a un helper para poder aplicarla a cada entrada del historial.
        """
        if not spoken:
            return False
        ratio = SequenceMatcher(None, heard, spoken).ratio()
        window_ratio = self._sliding_similarity(heard, spoken)
        heard_tokens = set(heard.split())
        tts_tokens = set(spoken.split())
        token_overlap = len(heard_tokens & tts_tokens) / max(1, len(heard_tokens))
        contained = heard in spoken or spoken in heard
        threshold = config.MIC_ECHO_SIMILARITY
        if contained or ratio >= threshold or window_ratio >= threshold or token_overlap >= 0.82:
            print(
                f"[oido] eco descartado [{label}] (global={ratio:.2f}, "
                f"ventana={window_ratio:.2f}, tokens={token_overlap:.2f})"
            )
            return True
        return False

    def _tts_token_overlap(self, heard: str) -> float:
        """NUEVO (bug 3): máximo solape de tokens del texto oído contra
        _last_tts y todas las entradas vigentes del historial TTS.
        Se usa para endurecer el filtro durante _speaking."""
        heard_tokens = set(heard.split())
        if not heard_tokens:
            return 0.0
        candidates = list(self._active_tts_texts())
        if self._last_tts and time.time() <= self._last_tts_until:
            candidates.append(self._last_tts)
        best = 0.0
        for spoken in candidates:
            overlap = len(heard_tokens & set(spoken.split())) / len(heard_tokens)
            best = max(best, overlap)
        return best

    def _looks_like_echo(self, text: str) -> bool:
        heard = _norm(text)
        if len(heard) < 4:
            return False

        # Comparación original contra _last_tts (se conserva por compatibilidad).
        if not (time.time() > self._last_tts_until or not self._last_tts):
            if self._matches_tts(heard, self._last_tts, "ultima"):
                return True

        # NUEVO (bug 2): comparar contra TODAS las entradas vigentes del historial.
        # Así, si Yue encadenó varias frases, el eco de cualquiera de los últimos
        # 30 segundos también se descarta, no solo el de la última.
        for spoken in self._active_tts_texts():
            if spoken == self._last_tts:
                continue  # ya comparada arriba, evitamos trabajo duplicado
            if self._matches_tts(heard, spoken, "historial"):
                return True
        return False

    def _has_wake_word(self, text: str) -> bool:
        heard = _norm(text)
        return any(re.search(rf"\b{re.escape(word)}\b", heard) for word in self._wake_words)

    def _callback(self, recognizer, audio):
        # NUEVO (arreglo "no me escucha"): latido. Que lleguemos aquí prueba que el
        # micro capta audio y el hilo de fondo está vivo, aunque luego no se
        # reconozca nada. Lo usamos en /diagvoz para distinguir "no capta sonido"
        # de "capta pero no reconoce / lo descarta un filtro".
        self._last_audio_ts = time.time()
        if time.time() < self._suppress_until or not self._wanted_enabled:
            return
        # NUEVO (arreglo "tarda en escucharme"): NO reconocemos aquí. El
        # reconocimiento de Google es una llamada por internet y, hecha en este
        # hilo, BLOQUEA la captura del micro hasta terminar. Encolamos el audio y
        # volvemos AL INSTANTE a escuchar; el hilo trabajador reconoce en orden.
        if self._async_reco and self._reco_queue is not None:
            try:
                self._reco_queue.put_nowait((recognizer, audio))
                return
            except Exception:
                # Cola llena (el reconocedor no da abasto): lo procesamos aquí
                # mismo como respaldo, para no perder nunca lo que dijiste.
                pass
        self._process(recognizer, audio)

    def _recognition_worker(self):
        """NUEVO (arreglo "tarda en escucharme"): consume la cola de audios y los
        reconoce uno a uno (FIFO), en su propio hilo, sin frenar la captura."""
        # NUEVO (arreglo crash "NoneType has no attribute task_done"): fijamos la
        # cola en una variable local. Antes usábamos self._reco_queue directamente y,
        # si se limpiaba (=None) al apagar mientras el hilo procesaba un audio, el
        # get()/task_done() reventaba el hilo. Con la referencia local seguimos
        # trabajando sobre la MISMA cola aunque el atributo pase a None.
        q = self._reco_queue
        if q is None:
            return
        while True:
            item = q.get()
            try:
                if item is None:  # centinela de parada
                    return
                recognizer, audio = item
                try:
                    self._process(recognizer, audio)
                except Exception as exc:
                    print("[oido] error en el hilo de reconocimiento:", exc)
            finally:
                q.task_done()

    def _start_worker(self):
        """NUEVO (arreglo "tarda en escucharme"): enciende el hilo trabajador."""
        if not self._async_reco:
            return
        if self._reco_thread is not None and self._reco_thread.is_alive():
            return
        try:
            import queue
            import threading
            self._reco_queue = queue.Queue(maxsize=8)
            self._reco_thread = threading.Thread(
                target=self._recognition_worker, name="yue-reco", daemon=True
            )
            self._reco_thread.start()
        except Exception as exc:
            print("[oido] no pude iniciar el hilo de reconocimiento (uso el modo directo):", exc)
            self._async_reco = False
            self._reco_queue = None
            self._reco_thread = None

    def _stop_worker(self):
        """NUEVO (arreglo "tarda en escucharme"): apaga el hilo trabajador (sin
        bloquear; es daemon). Deja la cola lista para un futuro arranque."""
        q = self._reco_queue
        if q is not None:
            try:
                q.put_nowait(None)  # centinela para que el hilo salga
            except Exception:
                pass
        self._reco_thread = None
        self._reco_queue = None

    def _process(self, recognizer, audio):
        """Reconocimiento + filtros (eco/media/barge-in) + emisión de señales.
        Antes vivía dentro de _callback; ahora corre en el hilo trabajador para no
        bloquear la captura del micrófono. La lógica interna es idéntica."""
        try:
            import speech_recognition as sr
            text = recognizer.recognize_google(audio, language=self.lang)
        except sr.UnknownValueError:
            return
        except sr.RequestError as exc:
            self._last_error = f"red/reconocedor: {exc}"
            self.status.emit("no llego al reconocedor (¿sin internet?)")
            print("[oido] error de red:", exc)
            return
        except Exception as exc:
            self._last_error = f"reconocimiento: {exc}"
            print("[oido] error reconociendo:", exc)
            return

        text = (text or "").strip()
        if len(text) < 2 or self._looks_like_echo(text):
            return

        now = time.time()
        heard_norm_full = _norm(text)

        # NUEVO (petición 3): cola de vigilancia de eco justo después de que Yue
        # deje de hablar. Su voz sigue reverberando o quedando en el buffer del
        # micro un instante; aquí cazamos esos restos que el filtro normal deja pasar.
        if not self._speaking and now < self._echo_tail_until:
            min_short = max(2, getattr(config, "MIC_BARGE_IN_MIN_CHARS", 5)) * 2
            if len(heard_norm_full) < min_short:
                print(f"[oido] descartado (cola de auto-escucha, muy corto): {text!r}")
                return
            if self._tts_token_overlap(heard_norm_full) >= self._speaking_echo_overlap:
                print(f"[oido] descartado (cola de auto-escucha, eco parcial): {text!r}")
                return

        # NUEVO (petición 2): si hay vídeo/música sonando, el diálogo o la letra que
        # salen por los altavoces NO deben tomarse como que le hablas tú. Para que
        # una orden tuya sí pase mientras ves algo, dila con la palabra clave («Yue…»).
        if self._media_playing and self._media_guard:
            tiene_wake = self._has_wake_word(text)
            if self._media_guard_wake and not tiene_wake:
                # Guardamos la letra/diálogo antes de descartar la orden: sirve para
                # que YUE pueda comentar la canción/el vídeo si se lo preguntas.
                try:
                    if len(heard_norm_full) >= 6:
                        self.media_heard.emit(text)
                except Exception:
                    pass
                print(f"[oido] descartado (media sonando, di «Yue» para hablarme): {text!r}")
                return
            if not tiene_wake and len(heard_norm_full) < self._media_guard_min_chars:
                print(f"[oido] descartado (media sonando, fragmento corto): {text!r}")
                return

        if self._speaking:
            if not getattr(config, "MIC_BARGE_IN_ENABLED", True):
                return
            # NUEVO (interrupción por voz fiable): si la puerta de barge-in está
            # activa, ella decide. El eco FUERTE ya quedó descartado arriba por
            # `_looks_like_echo`; aquí la puerta deja pasar las interrupciones
            # reales (órdenes tipo «para/espera/oye» al instante, o voz con poco
            # solape con el TTS) que antes se tiraban por "muy corto" o "eco
            # parcial". Así, hablarle por voz corta a YUE igual que escribirle.
            if self._barge_gate is not None:
                overlap_gate = self._tts_token_overlap(heard_norm_full)
                require_wake_g = getattr(config, "MIC_BARGE_IN_REQUIRE_WAKE_WORD", False)
                if require_wake_g and not self._has_wake_word(text):
                    return
                decision = self._barge_gate.should_interrupt(text, overlap_gate)
                if not decision.interrupt:
                    print(f"[oido] descartado durante habla ({decision.reason}): {text!r}")
                    return
                print(f"[oido] interrupción detectada ({decision.reason}): {text!r}")
                self.barge_in.emit(text)
                print(f"[oido] te escuché: {text!r}")
                self.heard.emit(text)
                return
            require_wake = getattr(config, "MIC_BARGE_IN_REQUIRE_WAKE_WORD", False)
            min_chars = max(2, getattr(config, "MIC_BARGE_IN_MIN_CHARS", 5))
            # NUEVO (bug 3): mientras Yue habla exigimos MÁS longitud (el doble)
            # porque los fragmentos cortos son casi siempre pedazos de su propio TTS.
            min_chars_speaking = min_chars * 2
            heard_norm = heard_norm_full
            if len(heard_norm) < min_chars_speaking:
                print(f"[oido] descartado durante habla (muy corto): {text!r}")
                return
            # NUEVO (bug 3 / petición 3): si lo oído comparte demasiados tokens con
            # lo que Yue dijo (historial + última frase), lo tratamos como eco parcial.
            # El umbral ahora es configurable (MIC_SPEAKING_ECHO_OVERLAP) y algo más
            # estricto que antes, para que se le escape menos su propia voz.
            overlap = self._tts_token_overlap(heard_norm)
            if overlap >= self._speaking_echo_overlap:
                print(f"[oido] descartado durante habla (eco parcial, tokens={overlap:.2f}): {text!r}")
                return
            if require_wake and not self._has_wake_word(text):
                return
            print(f"[oido] interrupción detectada: {text!r}")
            self.barge_in.emit(text)

        print(f"[oido] te escuché: {text!r}")
        self.heard.emit(text)

    def _watchdog_tick(self):
        """NUEVO (arreglo "no me escucha"): reintenta abrir el micro si debía estar
        escuchando pero no arrancó. No toca un micro que ya funciona (si _stop_fn
        existe, no hace nada), así que no interrumpe una escucha sana."""
        if not self._wanted_enabled:
            return
        if self._stop_fn is not None:
            return  # ya está escuchando; no molestamos
        # Estaba pedido pero no corre: el arranque falló antes. Reintentamos.
        self.enabled = True
        print("[oido] el micro no estaba activo; reintento abrirlo (vigilante)…")
        self.start()

    def diagnose(self, sample_seconds: float = 1.2) -> str:
        """NUEVO (arreglo "no me escucha"): informe legible del estado del micro.

        Explica en lenguaje claro por qué YUE podría no estar oyéndote: micro
        apagado, dispositivo equivocado, umbral demasiado alto, sin internet para
        el reconocedor, o un filtro (media/eco) descartando lo que dices.
        No lanza excepciones: cualquier fallo se refleja como texto en el informe.
        """
        lineas = []
        ahora = time.time()

        # 1) Estado lógico
        if not self._wanted_enabled or not self.enabled:
            lineas.append("• El micrófono está APAGADO. Actívalo (botón de micro o /mic).")
        else:
            lineas.append("• El micrófono está encendido (activado).")

        corriendo = self._stop_fn is not None
        lineas.append(
            "• Hilo de escucha: " + ("activo ✔" if corriendo else "NO activo ✘")
        )
        if self._start_attempts:
            lineas.append(f"• Intentos de arranque: {self._start_attempts}.")
        if self._last_error:
            lineas.append(f"• Último error: {self._last_error}.")

        # 2) Dispositivo
        try:
            import speech_recognition as sr
            nombres = sr.Microphone.list_microphone_names()
            if self.device_index is None:
                lineas.append(f"• Micro: por defecto del sistema ({len(nombres)} disponibles).")
            elif 0 <= self.device_index < len(nombres):
                lineas.append(f"• Micro #{self.device_index}: {nombres[self.device_index]}")
            else:
                lineas.append(
                    f"• ¡Ojo! MIC_DEVICE_INDEX={self.device_index} no existe "
                    f"(hay {len(nombres)}). Déjalo vacío o usa uno válido (python main.py --mics)."
                )
        except Exception as exc:
            lineas.append(f"• No pude listar micrófonos ({exc}). ¿Está instalado PyAudio?")

        # 3) Umbral actual
        if self._recognizer is not None:
            lineas.append(
                f"• Umbral de energía: {self._recognizer.energy_threshold:.0f} "
                f"(dinámico={config.MIC_DYNAMIC}, tope MIC_ENERGY_MAX="
                f"{getattr(config, 'MIC_ENERGY_MAX', 1000)}). "
                "Si no te oye, baja MIC_ENERGY_THRESHOLD y/o MIC_ENERGY_MAX en el .env."
            )

        # 4) Filtros que podrían estar tragándose tu voz
        if self._media_playing and self._media_guard:
            aviso = "• FILTRO ACTIVO: hay media sonando; "
            if self._media_guard_wake:
                aviso += "el micro solo te hace caso si dices «Yue…» primero."
            else:
                aviso += f"se ignoran frases de menos de {self._media_guard_min_chars} letras."
            aviso += " (MIC_MEDIA_GUARD_*)"
            lineas.append(aviso)
        if ahora < self._suppress_until:
            lineas.append("• Cooldown anti-eco activo (justo tras hablar YUE); es momentáneo.")

        # 5) ¿Llega audio? Latido o muestreo en vivo
        if corriendo:
            if self._last_audio_ts:
                lineas.append(
                    f"• Último audio captado hace {ahora - self._last_audio_ts:.1f}s. "
                    "Habla ahora: si este número no baja, el micro no capta sonido."
                )
            else:
                lineas.append(
                    "• Aún no ha llegado NADA de audio desde que arrancó. "
                    "Prueba a hablar; si sigue en cero, revisa el micro del sistema."
                )
        else:
            # No está corriendo: podemos abrir el micro un momento para medir energía
            # sin pelear por el dispositivo con el hilo de fondo.
            try:
                import speech_recognition as sr
                rec = sr.Recognizer()
                mic = (
                    sr.Microphone(device_index=self.device_index)
                    if self.device_index is not None else sr.Microphone()
                )
                with mic as source:
                    audio = rec.record(source, duration=max(0.3, float(sample_seconds)))
                import audioop
                energia = audioop.rms(audio.frame_data, audio.sample_width)
                lineas.append(f"• Nivel captado en {sample_seconds:.1f}s de prueba: {energia}.")
                if energia < 30:
                    lineas.append(
                        "  → Casi silencio total: el micro no está entrando sonido a la app "
                        "(dispositivo equivocado, mute por hardware o permiso del sistema)."
                    )
                else:
                    lineas.append("  → El micro SÍ capta sonido; el problema está en el reconocimiento/filtros.")
            except Exception as exc:
                lineas.append(f"• No pude medir el micro en vivo ({exc}).")

        # 6) Internet (el reconocedor de Google lo necesita)
        lineas.append(
            "• Nota: el reconocimiento usa Google por internet; sin conexión no "
            "entiende aunque el micro funcione."
        )
        return "\n".join(lineas)

    def _stop_background_only(self):
        if self._stop_fn:
            try:
                self._stop_fn(wait_for_stop=False)
            except Exception:
                pass
            self._stop_fn = None
        # NUEVO (arreglo "tarda en escucharme"): apagamos el hilo trabajador.
        self._stop_worker()

    def stop(self):
        self._wanted_enabled = False
        self.enabled = False
        self._stop_background_only()

    def shutdown(self):
        self.stop()

    def toggle(self):
        if self._wanted_enabled:
            self.stop()
            return False
        self._wanted_enabled = True
        self.enabled = True
        self.start()
        return True
