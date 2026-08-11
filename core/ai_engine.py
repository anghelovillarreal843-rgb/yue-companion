"""Motor de IA: habla con Groq (API compatible con OpenAI) usando la key del .env."""
import requests

import config
from core import ai_fallback
from core.ai_fallback import GroqError, ModelGoneError


class AIEngine:
    def __init__(self):
        self.base = config.GROQ_BASE_URL
        self.model = config.GROQ_MODEL
        self.vision_model = config.GROQ_VISION_MODEL
        self.api_key = config.GROQ_API_KEY
        self.temperature = config.TEMPERATURE
        # Router Groq-only. Permite varias credenciales autorizadas para relevo
        # operativo, sin cambiar de proveedor. Los 429 no rotan de clave.
        self._router = None
        try:
            from core import ai_router
            self._ai_router_mod = ai_router
            self._router = ai_router.build_default_router()
        except Exception:
            self._router = None

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def chat(self, messages, timeout=60, model=None):
        """Conversación de Yue usando exclusivamente Groq.

        Para chat normal usa el pool de claves configurado. Un 429/rate-limit se
        respeta y NO se intenta con otra clave. Si una credencial fue revocada,
        carece de permisos o hay un fallo de red/5xx, puede probar la siguiente.
        """
        if model is None and self._router is not None:
            try:
                disponibles = self._router.available()
            except Exception:
                disponibles = []
            if disponibles:
                try:
                    salida = self._router.chat(
                        messages, timeout=timeout, temperature=self.temperature)
                    texto = self._depurar(salida.get("text") or "")
                    # NUEVO: si aun así contestó en inglés, se pide UNA vez más
                    # con la orden de idioma delante. Si vuelve a fallar, se
                    # devuelve lo que haya (nunca deja a YUE muda).
                    if texto and bool(getattr(config, "FORZAR_ESPANOL", True)):
                        try:
                            from core import text_sanitizer
                            if text_sanitizer.parece_ingles(texto):
                                print("[ia] la respuesta salió en inglés; la pido en español.")
                                en_espanol = self._reintento_en_espanol(messages, timeout)
                                texto = en_espanol or texto
                        except Exception:
                            pass
                    if texto:
                        return texto
                except self._ai_router_mod.AllProvidersFailed as exc:
                    detalle = " ".join(str(v) for v in exc.errores.values()).lower()
                    if "model_gone" in detalle:
                        # El modelo fue retirado: resuelve uno vigente con la
                        # primera clave válida y reintenta la fila una sola vez.
                        key = next(iter(getattr(config, "GROQ_API_KEYS", ())), self.api_key)
                        ai_fallback.olvidar("text")
                        vivo = ai_fallback.resolve_model(
                            self.base, key, self.model, "text",
                            extras=tuple(config.GROQ_MODEL_FALLBACKS),
                        )
                        if vivo and vivo != self.model:
                            self.model = vivo
                            self._router.set_model(vivo)
                            salida = self._router.chat(
                                messages, timeout=timeout, temperature=self.temperature)
                            texto = self._depurar(salida.get("text") or "")
                            if texto:
                                return texto
                    if "quota" in detalle or "límite" in detalle or "429" in detalle:
                        raise RuntimeError(
                            "Groq alcanzó su límite/cuota. Yue respetará el límite y "
                            "volverá a funcionar cuando Groq permita nuevas solicitudes."
                        ) from exc
                    raise RuntimeError(str(exc)) from exc

        # Compatibilidad para llamadas que fuerzan un modelo concreto.
        if not self.api_key:
            raise RuntimeError("Falta GROQ_API_KEY en el archivo .env")
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        # NUEVO: mismos ajustes de velocidad que en el router (sin razonamiento
        # visible y con tope de tokens). Ver core/groq_tuning.py.
        try:
            from core import groq_tuning
            groq_tuning.aplicar_ajustes(payload)
        except Exception:
            pass
        try:
            data = ai_fallback.post_chat(self.base, self.api_key, payload, timeout)
        except ModelGoneError:
            ai_fallback.olvidar("text")
            vivo = ai_fallback.resolve_model(
                self.base, self.api_key, payload["model"], "text",
                extras=tuple(config.GROQ_MODEL_FALLBACKS),
            )
            if vivo == payload["model"] or not vivo:
                raise
            payload["model"] = vivo
            self.model = vivo
            data = ai_fallback.post_chat(self.base, self.api_key, payload, timeout)
        text = (data["choices"][0]["message"]["content"] or "").strip()
        text = self._depurar(text)
        return text or "...(me quedé sin palabras, no me malinterpretes)"

    # ------------------------------------------------------------------
    # LIMPIEZA DE LA RESPUESTA  [ADITIVO]
    # ------------------------------------------------------------------
    @staticmethod
    def _depurar(texto: str) -> str:
        """Quita el monólogo interno del modelo (<think>…</think> y similares).

        Es la causa de que YUE soltara párrafos en inglés: los modelos de
        razonamiento escriben su análisis dentro del contenido y nadie lo
        recortaba, así que la voz lo leía tal cual.
        """
        try:
            from core import text_sanitizer
            return text_sanitizer.limpiar(texto)
        except Exception:
            return (texto or "").strip()

    def _reintento_en_espanol(self, messages, timeout):
        """Segunda (y última) oportunidad cuando la respuesta salió en inglés.

        Repite la misma petición añadiendo una orden tajante de idioma. Solo se
        dispara si de verdad parecía inglés, así que en el uso normal no añade
        ni una milésima de espera.
        """
        refuerzo = list(messages) + [{
            "role": "system",
            "content": (
                "IDIOMA OBLIGATORIO: responde ÚNICAMENTE en español de España/"
                "Latinoamérica. No escribas ni una palabra en inglés, no muestres "
                "tu razonamiento ni etiquetas como <think>. Solo la respuesta "
                "hablada de YUE, corta y natural."
            ),
        }]
        try:
            salida = self._router.chat(
                refuerzo, timeout=timeout, temperature=self.temperature)
            return self._depurar((salida.get("text") or ""))
        except Exception as exc:
            print("[ia] el reintento en español tampoco salió:", exc)
            return ""

    def look(self, system, image_b64, instruction, timeout=60):
        """Manda una IMAGEN al modelo con visión y devuelve el comentario.

        AMPLIADO: primero se prueba el VisionRouter (varios proveedores
        multimodales en fila, con relevo automático). Si el router no está
        disponible o falla entero, se usa el camino de UN endpoint de siempre,
        así nunca hay regresión con lo que ya funcionaba.
        """
        # --- 1) Camino nuevo: fila de proveedores visuales ---------------
        router = self._vision_router()
        if router is not None:
            try:
                salida = router.describe(
                    image_b64, instruction, system=system, timeout=timeout,
                    temperature=float(getattr(config, "SCREEN_VISION_TEMPERATURE", 0.2)),
                    max_tokens=int(getattr(config, "SCREEN_VISION_MAX_TOKENS", 420)),
                )
                texto = self._depurar(salida.get("text") or "")
                if texto:
                    return texto
            except Exception as exc:
                print(f"[VISION] la fila visual no respondió ({exc}); pruebo el endpoint único.")

        # --- 2) Camino de siempre: un solo endpoint -----------------------
        if not self.api_key:
            raise RuntimeError("Falta GROQ_API_KEY en el archivo .env")

        content = [
            {"type": "text", "text": instruction},
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        ]
        v_base, v_key, v_model = self._vision_endpoint()
        if not v_base or not v_key:
            raise GroqError(
                "La visión multimodal en la nube está desactivada. Yue mantiene OCR y "
                "percepción local; usa VISION_OCR_ONLY=true para leer texto de pantalla."
            )
        payload = {
            "model": v_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "temperature": self.temperature,
            "max_completion_tokens": 200,
        }
        # Si el modelo de visión no existe o no está en la cuenta, lo DESCARTAMOS y
        # probamos el siguiente candidato (scout -> maverick -> …). Si no queda
        # ninguno en Groq, avisamos de forma clara para configurar otro proveedor.
        intentos = 0
        while True:
            if not payload["model"]:
                raise GroqError(
                    "Groq no tiene un modelo de visión configurado para este proyecto. "
                    "La alternativa sin otras APIs es VISION_OCR_ONLY=true."
                )
            try:
                data = ai_fallback.post_chat(v_base, v_key, payload, timeout)
                break
            except ModelGoneError:
                ai_fallback.descartar("vision", payload["model"])
                intentos += 1
                nuevo = self._vision_endpoint(forzar=True)[2]
                if not nuevo or nuevo == payload["model"] or intentos > 5:
                    raise GroqError(
                        "Ninguno de los modelos de visión de tu proveedor funcionó. "
                        "Revisa en el .env que VISION_MODEL sea un modelo con visión válido "
                        "de tu proveedor (o deja VISION_MODEL vacío para autodetectarlo) y "
                        "que VISION_API_KEY tenga acceso a ese modelo."
                    )
                payload["model"] = nuevo
        return self._depurar(data["choices"][0]["message"]["content"] or "")

    def look_ocr(self, system, instruction, timeout=45, max_chars=6000):
        """Visión SIN modelo multimodal: lee el TEXTO de la pantalla por OCR y deja
        que el modelo de CHAT (Groq, que sí funciona) responda a partir de él.

        Ideal cuando no hay clave de visión válida: sirve para PDFs, documentos,
        páginas web y cualquier pantalla con texto. No describe fotos ni imágenes
        sin texto (para eso hace falta el modelo multimodal).

        AMPLIADO: ahora usa `core/screen_ocr.py`, que encadena pytesseract con la
        cadena PaddleOCR -> EasyOCR -> Tesseract del paquete `vision/`. Antes solo
        sabía usar pytesseract y, sin el binario de Tesseract instalado, daba
        "OCR no disponible" aunque la máquina tuviera EasyOCR.
        """
        from core import screen_ocr
        # 1) ¿Hay ALGÚN motor OCR listo? Si no, mensaje claro.
        if not screen_ocr.available():
            raise RuntimeError(
                "OCR no disponible: no hay ningún motor instalado. Instala uno de "
                "estos: Tesseract-OCR ('winget install UB-Mannheim.TesseractOCR'; si "
                "no queda en el PATH, pon la ruta a tesseract.exe en OCR_TESSERACT_CMD "
                "del .env), o 'pip install easyocr', o 'pip install paddleocr'."
            )
        # 2) Captura robusta con el MISMO método que ya funciona (mss -> PIL).
        imagen = None
        try:
            from core import screen_capture as vision
            imagen = vision.grab_frame().image
        except Exception as exc:
            print("[ocr] captura mss/PIL falló, intento el método propio del OCR:", exc)
        # 3) OCR sobre la imagen capturada.
        lectura = screen_ocr.read(imagen, max_chars=max_chars)
        texto = (lectura.get("text") or "").strip()
        if not texto:
            raise RuntimeError(
                "OCR sin texto: no encontré texto legible en la pantalla ahora mismo. "
                "Abre en primer plano lo que quieres que lea (un PDF, documento o página)."
            )
        print(f"[VISION] OCR chars: {len(texto)} (motor {lectura.get('engine', '?')})")
        user = (
            f"{instruction}\n\n"
            "A continuación va el TEXTO que hay ahora mismo en la pantalla del "
            "usuario, leído por OCR (puede tener pequeños errores de lectura). "
            "Básate SOLO en esto:\n\n"
            f"--- TEXTO EN PANTALLA (OCR) ---\n{texto[:max_chars]}"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self.chat(messages, timeout=timeout)

    # ------------------------------------------------------------------
    # VISIÓN DE PANTALLA (camino nuevo, con router propio)  [ADITIVO]
    # ------------------------------------------------------------------
    def _vision_router(self):
        """Router de VISIÓN (solo modelos multimodales). None si está apagado."""
        cacheado = getattr(self, "_vrouter", None)
        if cacheado is not None:
            return cacheado or None
        proveedor = str(getattr(config, "VISION_PROVIDER", "auto") or "auto").lower()
        if proveedor == "none" or bool(getattr(config, "VISION_OCR_ONLY", False)):
            self._vrouter = False          # apagado A PROPÓSITO, sin ambigüedad
            return None
        try:
            from core import vision_router
            self._vrouter = vision_router.build_default_vision_router()
        except Exception as exc:
            print(f"[VISION] no pude construir el VisionRouter: {exc}")
            self._vrouter = False
        return self._vrouter or None

    def vision_router_report(self) -> list:
        """Estado de la fila visual (sin exponer claves). Para /diagvision."""
        router = self._vision_router()
        if router is None:
            return []
        try:
            return router.report()
        except Exception:
            return []

    def look_screen(self, question: str = "", system: str = "", monitor=None,
                    force_fresh: bool = False, timeout: int = None):
        """MIRA la pantalla de verdad y devuelve una `ScreenObservation`.

        Este es el camino recomendado: captura validada -> clasificación del
        contenido -> OCR y/o modelo multimodal con relevo -> fallback a OCR.
        No lanza excepción: la observación trae los errores dentro.
        """
        from core.screen_analyzer import get_analyzer
        analizador = get_analyzer(ai_engine=self)
        return analizador.observe(
            question=question, monitor=monitor, force_fresh=force_fresh,
            timeout=int(timeout or getattr(config, "SCREEN_VISION_TIMEOUT", 45)),
        )

    def answer_from_observation(self, observation, system: str, question: str = "",
                                timeout: int = 45) -> str:
        """Convierte una ScreenObservation en la respuesta hablada de YUE.

        Si hubo descripción visual, se apoya en ella; si solo hubo OCR, responde
        igualmente con el router de TEXTO. Así YUE nunca dice "no puedo ver"
        cuando al menos pudo leer algo.
        """
        if observation is None:
            return ""
        if not observation:
            # Ni visión ni texto: mensaje honesto y accionable.
            motivos = " | ".join(str(e)[:140] for e in (observation.errors or []))
            print(f"[VISION] observación vacía: {motivos or 'sin detalle'}")
            return ""

        peticion = question or "Cuéntame qué estoy viendo en la pantalla."
        user = (
            "Esto es lo que acabas de observar en la pantalla del usuario. "
            "Responde en español, breve y útil, basándote SOLO en esta "
            "observación. No inventes nada que no aparezca aquí. Si algo no se "
            "pudo determinar, dilo con naturalidad.\n\n"
            f"{observation.to_prompt_context()}\n\n"
            f"Petición del usuario: {peticion}"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self.chat(messages, timeout=timeout)

    def _vision_endpoint(self, forzar: bool = False):
        """(base, key, modelo) para las llamadas con imagen.

        Este proyecto está configurado sin proveedores visuales externos.
        Si VISION_PROVIDER=none o VISION_OCR_ONLY=true devuelve vacío y el
        analizador usa OCR/percepción local.
        """
        if str(getattr(config, "VISION_PROVIDER", "none")).lower() == "none" \
                or bool(getattr(config, "VISION_OCR_ONLY", False)):
            return ("", "", "")
        try:
            v_base, v_key, v_model = config.vision_endpoint()
        except Exception:
            return ("", "", "")
        if not v_base or not v_key:
            return ("", "", "")
        # Resolución automática del modelo para cualquier proveedor: consulta su
        # /models, corrige el nombre si no coincide y respeta la lista negra de
        # modelos que ya fallaron. Si la lista no es concluyente, deja el del .env.
        if forzar:
            ai_fallback.olvidar("vision")
        resuelto = ai_fallback.resolve_model(
            v_base, v_key, v_model, "vision",
            extras=tuple(config.GROQ_VISION_FALLBACKS) if v_base == self.base else (),
        )
        v_model = resuelto or v_model
        if v_base == self.base:
            self.vision_model = v_model
        return (v_base, v_key, v_model)

    def vision_diag_info(self) -> dict:
        """Información NO secreta del endpoint de visión, para diagnóstico."""
        try:
            v_base, v_key, v_model = self._vision_endpoint()
        except Exception as exc:
            return {"ok": False, "detalle": f"error resolviendo endpoint: {exc}"}
        host = ""
        try:
            from urllib.parse import urlparse
            host = urlparse(v_base).netloc or v_base
        except Exception:
            host = v_base
        return {
            "ok": bool(v_base and v_key),
            "host": host or "(vacío)",
            "modelo": v_model or "(sin modelo)",
            "clave": "sí" if v_key else "no",
        }

    def preflight_vision(self) -> dict:
        """Valida el PROVEEDOR y la CLAVE de visión al arrancar, sin gastar tokens
        ni capturar pantalla. En la configuración actual confirma si la visión
        multimodal está desactivada y deja OCR como alternativa local.

        Devuelve un dict con: ok, provider, host, model, reason, detail.
        """
        prov = getattr(config, "VISION_PROVIDER", "auto")
        if prov == "none":
            return {"ok": False, "provider": "none", "reason": "disabled",
                    "detail": "VISION_PROVIDER=none (visión multimodal desactivada a "
                              "propósito; YUE sigue mirando la pantalla por OCR)"}
        # NUEVO: con el VisionRouter, "hay visión" ya no depende de un endpoint
        # único. Si al menos un proveedor multimodal tiene clave, la visión está
        # viva aunque el primero de la fila esté caído.
        router = self._vision_router()
        if router is not None:
            try:
                disponibles = router.available()
            except Exception:
                disponibles = []
            if disponibles:
                nombres = ", ".join(f"{p.name}/{p.model}" for p in disponibles[:4])
                return {
                    "ok": True, "provider": prov, "status": 200,
                    "host": "VisionRouter",
                    "model": nombres,
                    "detail": f"{len(disponibles)} proveedor(es) multimodal(es) en fila",
                }
        # Usamos el endpoint declarado en el .env (sin resolver modelo por red, para
        # no disparar una segunda llamada ni el mensaje confuso de /models).
        try:
            v_base, v_key, v_model = config.vision_endpoint()
        except Exception as exc:
            return {"ok": False, "provider": prov, "reason": "error",
                    "detail": f"error leyendo config de visión: {exc}"}
        # Igual que look(): si la visión no trae credenciales propias, reutiliza las
        # del chat (que sí suelen funcionar).
        if (not v_base or not v_key) and self.api_key:
            v_base = self.base
            v_key = self.api_key
            v_model = v_model or self.vision_model or config.GROQ_VISION_MODEL
        host = ""
        try:
            from urllib.parse import urlparse
            host = urlparse(v_base).netloc or v_base
        except Exception:
            host = v_base
        if not v_base or not v_key:
            return {"ok": False, "provider": prov, "host": host or "(vacío)",
                    "model": v_model or "(sin modelo)", "reason": "no_key",
                    "detail": "no hay URL o clave de visión configurada"}
        res = ai_fallback.preflight(v_base, v_key)
        res.update({"provider": prov, "host": host,
                    "model": v_model or "(auto)"})
        return res

    def hay_vision(self) -> bool:
        """¿Hay de verdad un proveedor con visión utilizable ahora mismo?"""
        # 1) La fila visual manda: si algún proveedor multimodal está disponible,
        #    hay visión aunque Groq se haya quedado sin modelos con imagen.
        router = self._vision_router()
        if router is not None:
            try:
                if router.hay_vision():
                    return True
            except Exception:
                pass
        # 2) Camino antiguo (un solo endpoint), por compatibilidad.
        v_base, v_key, v_model = self._vision_endpoint()
        if not v_base or not v_key or not v_model:
            return False
        if v_base == self.base:                      # en Groq hay que comprobarlo
            return ai_fallback.hay_vision(v_base, v_key)
        return True

    def image_prompt(self, source_text, mood="neutral", timeout=40):
        """Convierte un texto fuente en un prompt visual para FLUX.

        Devuelve una descripcion de escena (en ingles, que es como FLUX rinde
        mejor) calida y esperanzadora, fiel a lo que la persona sintio. Si la IA
        no esta disponible, cae a un prompt sencillo construido localmente."""
        guide = (
            "You turn a source text into ONE vivid image-generation prompt "
            "for the FLUX model. Write in English, 30-55 words, a single paragraph, "
            "no preamble, no quotes, no lists. Describe a beautiful, evocative scene "
            "that captures the FEELING of the entry. Even for sad entries, keep a "
            "gentle thread of warmth and hope (soft light, dawn, a small comfort). "
            "Include art style, lighting, mood and color palette. Do NOT include text, "
            "letters, logos or watermarks in the image. Never show self-harm, violence "
            "or anything disturbing; reframe pain as tender, safe and hopeful imagery."
        )
        mood_hint = {
            "feliz": "bright, joyful, warm golden light",
            "enamorado": "tender, rosy, glowing, romantic warmth",
            "tranquilo": "calm, soft pastel, peaceful",
            "neutral": "balanced, serene, natural light",
            "triste": "melancholic but comforting, soft blue hour with a warm distant light",
            "estresado": "tense softened into calm, soothing cool tones easing toward warm",
            "enojado": "intense energy transformed into a cathartic, hopeful sunrise",
        }.get(mood, "serene, hopeful")
        try:
            msgs = [
                {"role": "system", "content": guide},
                {"role": "user", "content":
                 f"Mood: {mood} ({mood_hint}).\nSource text:\n{source_text}\n\nPrompt:"},
            ]
            out = self.chat(msgs, timeout=timeout)
            out = out.strip().strip('"').replace("\n", " ").strip()
            if out:
                return out + ", cinematic, highly detailed, no text, no watermark"
        except Exception as e:
            print("[recuerdo] no pude pedir el prompt a la IA:", e)
        # Respaldo local
        snippet = (source_text or "").strip().replace("\n", " ")[:120]
        return (f"A dreamy, hopeful illustration evoking this feeling: {snippet}. "
                f"{mood_hint}, soft cinematic lighting, highly detailed, no text, no watermark")

    def plan_pc_task(
        self, instruction, screen_info, history=None, max_actions=4, timeout=45,
        full_plan=False, execution_context=None, available_actions=None,
    ):
        """Analiza el objetivo completo y devuelve un plan JSON estructurado.

        ``full_plan=True`` se usa por el motor autónomo: obliga a razonar sobre
        toda la petición antes de ejecutar y permite declarar dependencias.
        """
        import json
        import re

        if not self.api_key:
            raise RuntimeError("Falta GROQ_API_KEY en el archivo .env")

        max_actions = max(1, min(int(max_actions), 24 if full_plan else 12))
        # Si no hay OCR instalado, no le ofrecemos click_text: gastaría ciclos
        # intentando una acción que siempre fallaría en esta máquina.
        try:
            from core.screen_text import get_engine
            ocr_listo = get_engine().available()
        except Exception:
            ocr_listo = False
        texto_ocr = (
            'Si el objetivo es texto visible que NO está en ui_elements (una web, un PDF, '
            'un icono con etiqueta), usa click_text{text} con el texto exacto. '
            if ocr_listo else
            'NO uses click_text: esta máquina no tiene OCR instalado y siempre fallaría. '
        )
        alcance_plan = (
            f'Genera como máximo {max_actions} acciones para TODO el plan restante. '
            if full_plan else f'Genera como máximo {max_actions} acciones para ESTE ciclo. '
        )
        catalogo_registrado = json.dumps(available_actions or [], ensure_ascii=False, separators=(",", ":"))
        catalogo_prompt = (
            'CATÁLOGO DINÁMICO DE ACCIONES REGISTRADAS (fuente de verdad; no inventes acciones): '
            + catalogo_registrado + '. '
            if available_actions else ''
        )
        schema = (
            'Devuelve SOLO JSON válido con esta forma exacta: '
            '{"objective":"...","done":false,"summary":"...","actions":[...]}. '
            'Analiza PRIMERO la petición completa. No ejecutes ni reduzcas toda la orden a una sola búsqueda. '
            'Cada elemento de actions debe ser una acción ATÓMICA e independiente. '
            'Puedes asignar id y depends_on:[ids] para expresar dependencias; usa parallel_group y parallel=true '
            'solo si dos tareas son realmente independientes y no comparten teclado, mouse, portapapeles ni foco. '
            'CADA paso del array es un objeto con la clave "action" (el nombre de la '
            'acción) y sus parámetros AL MISMO NIVEL. Ejemplo literal de respuesta válida: '
            '{"objective":"usar la calculadora","done":false,"summary":"abro la calculadora y pulso el 7","actions":'
            '[{"id":"abrir_calc","action":"open_app","name":"calculadora"},'
            '{"id":"pulsar_7","action":"click_element","name":"Siete","control_type":"Button","depends_on":["abrir_calc"]}]}. '
            'NUNCA uses la acción como clave ({"click_element":{"name":"Siete"}} es '
            'INCORRECTO), ni "type"/"tool" en lugar de "action", ni camelCase. '
            'Si la tarea ya está completada en la pantalla, usa done=true y actions=[]. '
            + catalogo_prompt +
            'Acciones base permitidas: open_app{name}, open_url{url}, open_path{path}, '
            'search_web{query}, type_text{text}, press{key,repeat}, hotkey{keys:[...]}, '
            'click{x_pct,y_pct,button,target?}, right_click{x_pct,y_pct,target?}, '
            'double_click{x_pct,y_pct,button}, move{x_pct,y_pct,duration}, '
            'drag{from_x_pct,from_y_pct,to_x_pct,to_y_pct,duration,button}, '
            'scroll{amount}, hscroll{amount}, wait{seconds}, screenshot{name}, '
            'click_element{name,control_type?}, '
            + ('click_text{text}, ' if ocr_listo else '')
            + 'focus_window{title}, list_windows{}, close_window{title}, '
            'move_window{title,x,y,width?,height?}, office_create{app}, '
            'office_write{app,text,title?,bold?}, office_save{app,path?}, office_read{app}. '
            'Todas las coordenadas son proporciones entre 0 y 1. '
            'PREFIERE SIEMPRE click_element sobre click por coordenadas: click_element '
            'busca el control real por su nombre visible (coincidencia difusa) y clica '
            'en su centro exacto. Usa el campo "ui_elements" que te doy para copiar el '
            'nombre TAL CUAL aparece ahí, y control_type solo si te ayuda a desambiguar. '
            + texto_ocr +
            'Usa click{x_pct,y_pct,target} SOLO como último recurso: antes debes comprobar '
            'que NO existe ningún control coincidente en ui_elements y que click_text tampoco '
            'puede localizar el objetivo. Si conoces el nombre o texto, NO uses coordenadas. '
            'Incluye target para auditar por qué caíste a coordenadas. Usa focus_window{title} '
            'antes de escribir en una ventana '
            'que no tiene el foco (mira "windows" y "active_window"), list_windows si '
            'necesitas saber qué hay abierto, y close_window solo con el título EXACTO '
            'y nunca sobre ventanas del sistema. '
            + alcance_plan +
            'No insertes sleep ni esperas fijas después de abrir aplicaciones: el motor verifica ventanas, procesos y archivos dinámicamente. '
            'No uses shell, terminal, borrado, instalaciones, credenciales, pagos, '
            'cambios de seguridad, envío de mensajes sin pedido explícito, cierre forzado '
            'ni acciones destructivas. No inventes que un clic funcionó: usa la captura. '
            'Si en el historial ves "DIAGNÓSTICO acción fallida", sigue su alternativa '
            'explícita y no repitas el mismo método. Si ves "sin efecto visible", esa acción '
            'NO hizo nada: cambia de estrategia. Si ves "paso descartado", ese paso estaba '
            'malformado: revisa el formato. '
            'En el historial, "[OK]" = ese paso YA se hizo y funcionó: NO lo repitas, '
            'sigue con el siguiente. "[FALLÓ]" = no funcionó, prueba otro camino. '
            'Muchas veces NO recibirás imagen de la pantalla: guíate por "ui_elements" '
            '(los controles reales) y por el texto OCR de la ventana activa, que te dice '
            'lo que se ve de verdad. Comprueba ahí si tu paso anterior surtió efecto '
            'antes de repetirlo. '
            'TECLADO PRIMERO cuando el destino ya tiene el foco: para escribir números o '
            'una operación en la calculadora usa type_text{text:"785*64"} y '
            'press{key:"enter"} en UN paso, en vez de diez click_element seguidos. '
            'Para Bloc de notas usa focus_window y type_text. Para Word, Excel o PowerPoint '
            'PREFIERE SIEMPRE office_create/office_write/office_save/office_read: usan COM nativo, '
            'son más rápidos y no dependen del foco. Crear, escribir y guardar deben ser acciones separadas '
            'con depends_on; office_write nunca debe crear ni guardar por sí misma. No simules Ctrl+N ni escritura de Office salvo '
            'que el resultado indique explícitamente que COM falló y se usó el fallback. '
            'Interpreta la INTENCIÓN, no el pie de la letra: si te piden «escribe un resumen '
            'sobre X», «hazme una carta» o «redacta un correo», debes REDACTAR tú ese '
            'contenido real y escribirlo con office_write si el destino es Office, o type_text en otras apps; nunca teclees literalmente la frase '
            'de la orden («un resumen sobre X»). '
            'Cuando la tarea ya esté hecha (el texto OCR lo confirma), responde done=true. '
            'Prioriza teclado cuando sea fiable y controles reales cuando estén visibles.'
        )
        # Las lecciones aprendidas van siempre delante y no las desplaza el historial.
        historial = [str(item) for item in (history or [])]
        lecciones = [item for item in historial if item.startswith("Lección aprendida:")]
        pasos = [item for item in historial if not item.startswith("Lección aprendida:")]
        history_text = "\n".join(lecciones[:3] + pasos[-12:])
        contexto = self._ui_context_text(screen_info)
        user_text = (
            f"Pantalla actual: {screen_info.get('width')}x{screen_info.get('height')}.\n"
            f"{contexto}"
            f"Objetivo original: {instruction}\n"
            f"Pasos ya ejecutados:\n{history_text or '(ninguno)'}\n"
            f"Contexto temporal del agente: {execution_context or {}}\n"
            + ("Produce el plan completo restante con acciones atómicas, dependencias y paralelismo seguro."
               if full_plan else "Decide si ya terminó o produce solo el siguiente bloque corto de acciones.")
        )
        content = [{"type": "text", "text": user_text}]
        image_b64 = screen_info.get("image_b64") or ""
        if image_b64:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
            })
        # La visión puede vivir en otro proveedor; el texto siempre en Groq.
        # Si ya sabemos que la visión no responde, no gastamos una llamada
        # perdida (y ~1s de latencia) en cada ciclo del control del PC.
        if getattr(self, "_vision_muerta", False):
            image_b64 = ""
        v_base, v_key, v_model = self._vision_endpoint() if image_b64 else ("", "", "")
        usar_imagen = bool(image_b64 and v_base and v_key and v_model)
        payload = {
            "model": v_model if usar_imagen else self.model,
            "messages": [
                {"role": "system", "content": schema},
                {"role": "user", "content": content if usar_imagen else user_text},
            ],
            "temperature": 0.05,
            "max_completion_tokens": 1400,
        }
        if usar_imagen:
            try:
                respuesta = ai_fallback.post_chat(v_base, v_key, payload, timeout)
            except GroqError as exc:
                # Cualquier fallo del lado visual (modelo retirado, sin acceso, o
                # un modelo que no acepta imágenes) degrada a texto en vez de
                # tumbar la orden: ui_elements y windows siguen describiendo la
                # pantalla, así que Yue puede seguir trabajando.
                print("[ia] la visión falló; planifico solo con texto:", exc)
                self._vision_muerta = True
                usar_imagen = False
        if not usar_imagen:
            payload["model"] = self.model
            payload["messages"][1] = {"role": "user", "content": user_text}
            try:
                respuesta = ai_fallback.post_chat(self.base, self.api_key, payload, timeout)
            except ModelGoneError:
                vivo = ai_fallback.resolve_model(
                    self.base, self.api_key, self.model, "text",
                    extras=tuple(config.GROQ_MODEL_FALLBACKS),
                )
                if vivo == payload["model"]:
                    raise
                payload["model"] = vivo
                self.model = vivo
                respuesta = ai_fallback.post_chat(self.base, self.api_key, payload, timeout)
        text = (respuesta["choices"][0]["message"]["content"] or "").strip()
        data = self._parse_plan_json(text)
        if isinstance(data, list):
            return {"done": False, "summary": "", "actions": data[:max_actions]}
        if not isinstance(data, dict):
            raise RuntimeError("La IA devolvió un plan de formato inválido")
        return {
            "objective": str(data.get("objective", instruction))[:500],
            "done": bool(data.get("done", False)),
            "summary": str(data.get("summary", ""))[:500],
            "actions": data.get("actions", [])[:max_actions],
        }

    def plan_pc_execution(
        self, instruction, screen_info, history=None, max_actions=24, timeout=45,
        execution_context=None, available_actions=None,
    ):
        """Plan completo para el nuevo motor autónomo (API estable del planner)."""
        return self.plan_pc_task(
            instruction=instruction,
            screen_info=screen_info,
            history=history,
            max_actions=max_actions,
            timeout=timeout,
            full_plan=True,
            execution_context=execution_context,
            available_actions=available_actions,
        )

    @staticmethod
    def _parse_plan_json(text: str):
        """Interpreta el JSON del plan aguantando lo que el modelo suele romper.

        Si falla, lanza un RuntimeError con un mensaje entendible (antes se
        escapaba crudo un "Expecting ':' delimiter: line 1 column 99") y deja el
        texto original en la consola para poder diagnosticarlo.
        """
        import json
        import re

        crudo = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
        intentos = [crudo]

        # Bloque con las llaves CONTADAS: corta en la que cierra el objeto, no en
        # la última del texto. El modelo suele añadir un "}" de más al final
        # ({...}}), y un regex glotón se lo tragaba y reventaba el JSON entero.
        balanceado = AIEngine._bloque_json_balanceado(crudo)
        if balanceado:
            intentos.append(balanceado)

        # Solo el bloque entre la primera { y la última }.
        match = re.search(r"\{.*\}", crudo, re.S)
        if match:
            intentos.append(match.group(0))

        for base in list(intentos):
            reparado = base
            reparado = reparado.replace("\u201c", '"').replace("\u201d", '"')  # comillas curvas
            reparado = reparado.replace("\u2018", "'").replace("\u2019", "'")
            reparado = re.sub(r",\s*([}\]])", r"\1", reparado)                 # comas colgantes
            reparado = re.sub(r"\bNone\b", "null", reparado)                   # dialecto Python
            reparado = re.sub(r"\bTrue\b", "true", reparado)
            reparado = re.sub(r"\bFalse\b", "false", reparado)
            if reparado != base:
                intentos.append(reparado)

        for intento in intentos:
            try:
                return json.loads(intento)
            except Exception:
                continue

        print("[ia] plan ilegible; texto crudo del modelo:")
        print("    " + (text or "(vacío)")[:400].replace("\n", "\n    "))
        raise RuntimeError(
            "La IA devolvió un plan que no es JSON válido. Se reintentará en el "
            "siguiente ciclo."
        )


    @staticmethod
    def _bloque_json_balanceado(texto: str) -> str:
        """Primer objeto JSON completo contando llaves, ignorando las de strings.

        Sirve para el fallo más frecuente del modelo: cerrar de más
        ({"done":false,...}}). Corta exactamente donde el objeto termina.
        """
        inicio = texto.find("{")
        if inicio < 0:
            return ""
        profundidad = 0
        en_texto = False
        escapado = False
        for i in range(inicio, len(texto)):
            car = texto[i]
            if en_texto:
                if escapado:
                    escapado = False
                elif car == "\\":
                    escapado = True
                elif car == '"':
                    en_texto = False
                continue
            if car == '"':
                en_texto = True
            elif car == "{":
                profundidad += 1
            elif car == "}":
                profundidad -= 1
                if profundidad == 0:
                    return texto[inicio:i + 1]
        return ""

    @staticmethod
    def _ui_context_text(screen_info) -> str:
        """Formatea 'windows' y 'ui_elements' para el prompt del planificador."""
        import json

        partes = []
        activa = str(screen_info.get("active_window", "") or "")
        ventanas = screen_info.get("windows") or []
        if ventanas:
            listado = [
                f"{w.get('title', '')}{' [ACTIVA]' if w.get('active') else ''}"
                for w in ventanas[:18]
            ]
            partes.append("Ventanas abiertas: " + " | ".join(listado))
        if activa:
            partes.append(f"Ventana con el foco: {activa}")
        elementos = screen_info.get("ui_elements") or []
        if elementos:
            compacto = [
                {"name": e.get("name", ""), "type": e.get("type", e.get("control_type", ""))}
                for e in elementos[:40]
            ]
            partes.append(
                "Controles visibles de la ventana activa (usa estos nombres EXACTOS en "
                "click_element): " + json.dumps(compacto, ensure_ascii=False)
            )
        leido = str(screen_info.get("screen_text", "") or "")
        if leido:
            partes.append(
                "Texto que se VE ahora mismo en la ventana activa (leído por OCR; es tu "
                "única forma de comprobar si tus pasos anteriores surtieron efecto, "
                "mira aquí antes de repetir nada): " + leido
            )
        return ("\n".join(partes) + "\n") if partes else ""

    def plan_pc_actions(self, instruction, screen_info, timeout=45):
        """Compatibilidad con versiones anteriores."""
        return self.plan_pc_task(instruction, screen_info, max_actions=12, timeout=timeout)["actions"]

    def is_reachable(self):
        try:
            requests.get(f"{self.base}/models", headers=self._headers(), timeout=8)
            return True
        except Exception:
            return False
