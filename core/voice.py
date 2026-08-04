"""Voz de YUE con reproducción interrumpible (barge-in)."""
import os
import re
import tempfile
import threading

from PyQt5.QtCore import QObject, pyqtSignal

import config

_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]", flags=re.UNICODE
)


def _clean_for_speech(text: str) -> str:
    text = _EMOJI.sub("", text)
    text = re.sub(r"[*_`#>~|]", "", text)
    return re.sub(r"\s+", " ", text).strip()


class Speaker(QObject):
    speaking = pyqtSignal(bool)
    # NUEVO (lip-sync real): al empezar a sonar una respuesta, se emite el
    # envelope de amplitud (RMS por bloques) del audio YA sintetizado. La UI
    # (avatar.html) lo lee según el tiempo transcurrido y mueve la boca con la
    # amplitud REAL. Si el payload es None, la UI usa su seno de respaldo.
    #   payload = {"values": [0..1, ...], "block_ms": 40}   ó   None
    lipsync = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.enabled = config.TTS_ENABLED
        self.engine = getattr(config, "TTS_ENGINE", "kokoro")
        self.lang = config.TTS_LANG
        self.tld = config.TTS_TLD
        self._play_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._mixer_ready = False
        self._kokoro = None
        self._kokoro_tried = False
        self._generation = 0
        self._stop_event = threading.Event()
        self._is_speaking = False

    @property
    def is_speaking(self) -> bool:
        with self._state_lock:
            return self._is_speaking

    def _ensure_mixer(self):
        if self._mixer_ready:
            return True
        try:
            import pygame
            pygame.mixer.init()
            self._mixer_ready = True
        except Exception as exc:
            print("[voz] no se pudo iniciar el audio:", exc)
        return self._mixer_ready

    def toggle(self):
        self.enabled = not self.enabled
        if not self.enabled:
            self.stop()
        return self.enabled

    def _get_kokoro(self):
        if self._kokoro is not None:
            return self._kokoro
        if self._kokoro_tried:
            return None
        self._kokoro_tried = True
        try:
            from kokoro_onnx import Kokoro
        except Exception as exc:
            print("[voz] Kokoro no instalado; uso gTTS. Detalle:", exc)
            return None
        model = config.KOKORO_MODEL
        voices = config.KOKORO_VOICES
        if not (os.path.exists(model) and os.path.exists(voices)):
            print("[voz] faltan los modelos de Kokoro; uso gTTS.")
            return None
        try:
            self._kokoro = Kokoro(model, voices)
            print(f"[voz] Kokoro listo · voz={config.KOKORO_VOICE} · lang={config.KOKORO_LANG}")
        except Exception as exc:
            print("[voz] error cargando Kokoro:", exc)
            self._kokoro = None
        return self._kokoro

    def _synth_kokoro(self, text):
        ko = self._get_kokoro()
        if ko is None:
            return None
        try:
            import soundfile as sf
            samples, sr = ko.create(
                text,
                voice=config.KOKORO_VOICE,
                speed=config.KOKORO_SPEED,
                lang=config.KOKORO_LANG,
            )
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            sf.write(path, samples, sr)
            return path
        except Exception as exc:
            print("[voz] Kokoro falló al sintetizar; uso gTTS:", exc)
            return None

    def _synth_edge(self, text):
        """NUEVO: síntesis con Edge-TTS (voz neuronal de Microsoft, requiere internet).

        Genera un MP3 temporal igual que gTTS, así el reproductor pygame no
        necesita ningún cambio. Si falla (sin internet, paquete no instalado),
        devuelve None y _run() cae a Kokoro/gTTS como respaldo.
        """
        try:
            import asyncio
            import edge_tts

            fd, path = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)

            async def _generar():
                com = edge_tts.Communicate(
                    text,
                    voice=config.EDGE_TTS_VOICE,
                    rate=config.EDGE_TTS_RATE,
                    pitch=config.EDGE_TTS_PITCH,
                )
                await com.save(path)

            # Estamos en un hilo trabajador (sin event loop propio), así que
            # asyncio.run es seguro y no toca el hilo principal de Qt.
            asyncio.run(_generar())
            if os.path.getsize(path) > 0:
                return path
            os.remove(path)
            return None
        except Exception as exc:
            print("[voz] Edge-TTS falló; uso respaldo (Kokoro/gTTS):", exc)
            try:
                if 'path' in dir() and path and os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
            return None

    def _synth_gtts(self, text):
        try:
            from gtts import gTTS
            fd, path = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            gTTS(text=text, lang=self.lang, tld=self.tld, slow=False).save(path)
            return path
        except Exception as exc:
            print("[voz] gTTS falló:", exc)
            return None

    def say(self, text: str):
        if not self.enabled:
            return
        clean = _clean_for_speech(text)
        if not clean:
            return

        # Cada respuesta nueva corta inmediatamente la anterior.
        self.stop()
        with self._state_lock:
            generation = self._generation
            stop_event = self._stop_event
        threading.Thread(
            target=self._run,
            args=(clean, generation, stop_event),
            daemon=True,
        ).start()

    def stop(self):
        """Detiene la voz actual sin bloquear la interfaz."""
        with self._state_lock:
            self._generation += 1
            self._stop_event.set()
            self._stop_event = threading.Event()
            was_speaking = self._is_speaking
            self._is_speaking = False
        if self._mixer_ready:
            try:
                import pygame
                pygame.mixer.music.stop()
            except Exception:
                pass
        if was_speaking:
            self.speaking.emit(False)
        # NUEVO: al cortar, limpiamos también el envelope de lip-sync para que
        # la boca no siga leyendo la respuesta que acabamos de interrumpir.
        try:
            self.lipsync.emit(None)
        except Exception:
            pass

    def _set_speaking_if_current(self, value: bool, generation: int) -> bool:
        with self._state_lock:
            if generation != self._generation:
                return False
            changed = self._is_speaking != bool(value)
            self._is_speaking = bool(value)
        if changed:
            self.speaking.emit(bool(value))
        return True

    def _emit_lipsync_if_current(self, payload, generation: int):
        """Emite el envelope (o None) solo si esta reproducción sigue vigente.

        Así una respuesta nueva (barge-in) no ve su envelope pisado por el
        'None' de limpieza de la respuesta anterior.
        """
        with self._state_lock:
            if generation != self._generation:
                return
        try:
            self.lipsync.emit(payload)
        except Exception as exc:
            print("[voz] no pude emitir lip-sync:", exc)

    def _amplitude_envelope(self, path, block_ms: int = 40):
        """Envelope de amplitud (RMS por bloques de ~block_ms) del audio ya
        sintetizado, normalizado a [0..1].

        Devuelve {"values": [...], "block_ms": N} o None. Si devuelve None, la
        UI cae a su comportamiento de seno (plan B): nunca rompe la voz.

        Cadena de lectura (robusta, tres vías):
          1) soundfile  -> WAV/FLAC/OGG (Kokoro produce WAV) y MP3 si el
             libsndfile es reciente.
          2) wave (stdlib) -> WAV puro, sin depender de soundfile.
          3) pygame.Sound + sndarray -> cubre MP3/OGG si SDL_mixer lo decodifica.
        numpy es obligatorio en el proyecto, así que el cálculo RMS siempre está.
        """
        try:
            import numpy as np
        except Exception:
            return None

        samples = None
        sr = None

        # 1) soundfile (float32 directo)
        try:
            import soundfile as sf
            data, sr = sf.read(path, dtype="float32", always_2d=False)
            samples = data
        except Exception:
            samples = None

        # 2) WAV por stdlib
        if samples is None and str(path).lower().endswith(".wav"):
            try:
                import wave
                with wave.open(path, "rb") as w:
                    sr = w.getframerate()
                    nch = w.getnchannels()
                    sw = w.getsampwidth()
                    raw = w.readframes(w.getnframes())
                dtype = {1: np.uint8, 2: np.int16, 4: np.int32}.get(sw)
                if dtype is not None:
                    arr = np.frombuffer(raw, dtype=dtype).astype(np.float32)
                    if sw == 1:                       # 8-bit PCM es unsigned
                        arr = arr - 128.0
                    arr /= float(1 << (8 * sw - 1))
                    if nch > 1:
                        arr = arr.reshape(-1, nch)
                    samples = arr
            except Exception:
                samples = None

        # 3) pygame Sound + sndarray (MP3/OGG de Edge-TTS / gTTS)
        if samples is None:
            try:
                import pygame
                if self._ensure_mixer():
                    snd = pygame.mixer.Sound(path)
                    arr = pygame.sndarray.array(snd).astype(np.float32)
                    mx = float(np.max(np.abs(arr))) or 1.0
                    samples = arr / mx
                    init = pygame.mixer.get_init()
                    sr = init[0] if init else 22050
            except Exception:
                samples = None

        if samples is None or not sr or sr <= 0:
            return None

        try:
            a = np.asarray(samples, dtype=np.float32)
            if a.ndim > 1:                            # estéreo -> mono
                a = a.mean(axis=1)
            if a.size == 0:
                return None
            block = max(1, int(sr * block_ms / 1000.0))
            n = a.size // block
            if n <= 0:
                return None
            a = a[: n * block].reshape(n, block)
            rms = np.sqrt(np.mean(a * a, axis=1) + 1e-9)
            # Techo robusto (percentil 95) para no saturar la boca con picos
            # sueltos, y una curva suave que realza el nivel de habla normal.
            ref = float(np.percentile(rms, 95)) or float(np.max(rms)) or 1.0
            env = np.clip(rms / (ref + 1e-9), 0.0, 1.0)
            env = np.power(env, 0.65)
            return {"values": [round(float(x), 3) for x in env],
                    "block_ms": int(block_ms)}
        except Exception:
            return None

    def _run(self, text, generation, stop_event):
        path = None
        with self._play_lock:
            if stop_event.is_set() or generation != self._generation:
                return
            if not self._set_speaking_if_current(True, generation):
                return
            try:
                if not self._ensure_mixer() or stop_event.is_set():
                    return
                # NUEVO: Edge-TTS es el motor principal; si falla, la cadena
                # de respaldo original (Kokoro -> gTTS) sigue intacta.
                if self.engine == "edge":
                    path = self._synth_edge(text)
                    if path is None and not stop_event.is_set():
                        path = self._synth_kokoro(text)
                if stop_event.is_set() or generation != self._generation:
                    return
                if self.engine == "kokoro":
                    path = self._synth_kokoro(text)
                if stop_event.is_set() or generation != self._generation:
                    return
                if path is None:
                    path = self._synth_gtts(text)
                if path is None or stop_event.is_set() or generation != self._generation:
                    return

                import pygame
                # NUEVO (lip-sync real): calculamos el envelope ANTES de sonar
                # y lo emitimos justo al arrancar la reproducción, para que la
                # UI tome ese instante como t0 y lea la amplitud real. Si no se
                # puede calcular, va None y la UI usa su seno de respaldo.
                env = self._amplitude_envelope(path)
                pygame.mixer.music.load(path)
                pygame.mixer.music.play()
                self._emit_lipsync_if_current(env, generation)
                while pygame.mixer.music.get_busy():
                    if stop_event.is_set() or generation != self._generation:
                        pygame.mixer.music.stop()
                        break
                    pygame.time.wait(35)
            except Exception as exc:
                print("[voz] error:", exc)
            finally:
                self._set_speaking_if_current(False, generation)
                # Suelta el envelope de esta respuesta (si no la relevó otra):
                # así la boca no se queda leyendo amplitudes viejas.
                self._emit_lipsync_if_current(None, generation)
                if path:
                    try:
                        import pygame
                        pygame.mixer.music.unload()
                    except Exception:
                        pass
                    try:
                        os.remove(path)
                    except Exception:
                        pass
