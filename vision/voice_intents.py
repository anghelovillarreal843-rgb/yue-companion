"""Intenciones de voz y texto para la visión (punto 12 del pedido).

Convierte lo que dice o escribe la persona en una intención concreta. Funciona
IGUAL desde el micrófono y desde el chat escrito, porque solo trabaja con texto.

Intenciones cubiertas:
    activar/desactivar cámara, qué ves, describe mi habitación, lee este texto,
    lee solo el título, no leas la pantalla, qué estoy sosteniendo, sigue mi
    mano, cuántos dedos muestro, qué estoy haciendo, no analices mis emociones,
    no guardes observaciones, olvida lo que viste, modo privacidad, solo analiza
    cuando te lo pida, estado de visión, capacidades.

Es Python puro (solo `re` y `unicodedata`), así que se prueba con una lista de
frases y sin cámara.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def _norm(text: str) -> str:
    """Minúsculas, sin tildes y sin signos: para comparar sin sorpresas."""
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[¿?¡!.,;:]", " ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


@dataclass
class Intent:
    name: str
    confidence: float = 1.0
    argument: str = ""

    def __bool__(self) -> bool:
        return bool(self.name)


# (intención, patrones). El ORDEN importa: lo más específico primero.
PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # --- privacidad (lo primero: son órdenes de apagar cosas) -----------
    # OJO al orden: "quita el modo privacidad" contiene "modo privacidad", así
    # que la variante de APAGAR tiene que comprobarse ANTES que la de encender.
    ("modo_privacidad_off", (
        r"\b(quita|apaga|desactiva|sal de|salir de|sal del|quitar) (el )?modo privacidad\b",
        r"\bya no.*modo privacidad\b",
        r"\bmodo privacidad (off|apagado|desactivado)\b",
    )),
    ("modo_privacidad_on", (
        r"\bmodo privacidad\b",
        r"\bactiva(r)? (el )?modo privacidad\b",
        r"\bentra(r)? en (modo )?privacidad\b",
    )),
    ("olvidar_observaciones", (
        r"\bolvida (lo que viste|lo que has visto|lo que viste por la camara)\b",
        r"\bborra (lo que viste|las observaciones|lo observado)\b",
        r"\bolvida(te)? de lo que (viste|observaste)\b",
    )),
    ("no_guardar_eventos", (
        r"\bno guardes (nada|observaciones|eventos|lo que ves)\b",
        r"\bdeja de guardar (observaciones|eventos)\b",
    )),
    ("no_emociones", (
        r"\bno analices (mis )?emociones\b",
        r"\bdeja de (analizar|leer|estimar) (mis )?emociones\b",
        r"\bno (leas|estimes) (mi estado de animo|mis emociones)\b",
    )),
    ("si_emociones", (
        r"\b(vuelve a|puedes) analizar (mis )?emociones\b",
        r"\bactiva (el analisis de |las )?emociones\b",
    )),
    ("no_ocr", (
        r"\bno leas (la pantalla|los textos|nada de lo que|lo que aparece)\b",
        r"\bdesactiva (el )?ocr\b",
        r"\bdeja de leer (textos|la pantalla)\b",
    )),
    ("si_ocr", (
        r"\bactiva (el )?ocr\b",
        r"\b(vuelve a|puedes) leer (los )?textos\b",
    )),
    ("no_objetos", (
        r"\bno (detectes|reconozcas|analices) objetos\b",
        r"\bdesactiva (el reconocimiento de |los )?objetos\b",
    )),
    ("solo_bajo_peticion", (
        r"\bsolo analiza cuando te lo pida\b",
        r"\bsolo (mira|analiza|observa) si te lo pido\b",
        r"\banaliza solo (bajo peticion|cuando te lo pida)\b",
    )),
    ("analisis_continuo", (
        r"\bpuedes (analizar|mirar) (siempre|todo el tiempo|cuando quieras)\b",
        r"\bdesactiva.*solo bajo peticion\b",
    )),

    # --- cámara ---------------------------------------------------------
    ("camara_off", (
        r"\b(apaga|desactiva|cierra|quita) (la )?camara\b",
        r"\bdeja de (usar|ver por) la camara\b",
        r"\bno me veas\b",
    )),
    ("camara_on", (
        r"\b(activa|enciende|prende|abre) (la )?camara\b",
        r"\busa la camara\b",
        r"\bmirame por la camara\b",
    )),

    # --- lectura de texto ------------------------------------------------
    ("leer_titulo", (
        r"\blee (solamente |solo )?(el )?titulo\b",
        r"\bque dice (el )?titulo\b",
    )),
    ("leer_texto", (
        r"\blee (lo que|esto|este|esta|el texto|el papel|la hoja|el cartel|el documento)\b",
        r"\bque dice (este|esta|el) (documento|papel|hoja|cartel|texto|pantalla)\b",
        r"\bpuedes leer (esto|este texto|lo que te muestro)\b",
        r"\blee lo que estoy mostrando\b",
        r"\bque dice aqui\b",
    )),

    # --- descripción -----------------------------------------------------
    ("describir_habitacion", (
        r"\bdescribe (mi |la |esta )?(habitacion|cuarto|sala|oficina|escena|lugar|entorno)\b",
        r"\bque hay (en (mi|la) (habitacion|cuarto|sala))\b",
        r"\bcomo (esta|se ve) (mi |la )?(habitacion|cuarto)\b",
    )),
    # --- ADITIVO: preguntas directas sobre el estado visual --------------
    # Antes solo existía "qué ves", que devolvía la descripción de la
    # habitación. Estas cuatro se contestan del `vision_state` en curso, sin
    # pasar por el modelo de lenguaje: la respuesta es instantánea y no puede
    # inventarse nada, porque sale del dato medido.
    ("cuantas_personas", (
        r"\bcuanta(s)? persona(s)?\b",
        r"\bcuanta(s)? gente\b",
        r"\bcuanto(s)? somos\b",
        r"\bhay alguien (mas )?(aqui|conmigo|en la sala|en el cuarto)\b",
        r"\bestoy solo\b",
    )),
    ("me_miras", (
        r"\bme (estas )?(mirando|viendo)\b",
        r"\bme ves ahora\b",
        r"\bestas mirando(me)?\b",
        r"\bpuedes verme ahora\b",
    )),
    ("mi_postura", (
        r"\bestoy (sentado|sentada|parado|parada|de pie|acostado|acostada)\b",
        r"\bcomo estoy (sentado|parado|de pie)\b",
        r"\bque postura (tengo|estoy)\b",
        r"\bcomo estoy sentado\b",
    )),
    ("que_objetos", (
        r"\bque objeto(s)? (ves|hay|tengo|reconoces)\b",
        r"\bque cosas ves\b",
        r"\bque tengo (en la mano|aqui|encima)\b",
        r"\bque hay (en la mesa|sobre la mesa)\b",
    )),
    ("que_ves", (
        r"\bque ves\b", r"\bque estas viendo\b", r"\bque alcanzas a ver\b",
        r"\bme ves\b", r"\bpuedes verme\b", r"\bque miras\b",
    )),

    # --- manos y acciones -------------------------------------------------
    ("contar_dedos", (
        r"\bcuantos dedos\b", r"\bque numero (hago|muestro) con (los dedos|la mano)\b",
    )),
    ("que_sostengo", (
        r"\bque (estoy )?(sosteniendo|tengo en la mano|agarro|llevo en la mano)\b",
        r"\bque es esto que tengo\b",
    )),
    ("seguir_mano", (
        r"\bsigue mi mano\b", r"\brastrea mi mano\b", r"\bmira mi mano\b",
    )),
    ("que_hago", (
        r"\bque (estoy )?(haciendo|hago)\b",
        r"\bque crees que estoy haciendo\b",
    )),
    ("como_estoy", (
        r"\bcomo (me )?(ves|notas)\b",
        r"\bque cara tengo\b", r"\bcomo crees que estoy\b",
    )),

    # --- diagnóstico ------------------------------------------------------
    ("estado_vision", (
        r"\bestado de (la )?vision\b", r"\bcomo esta la camara\b",
        r"\bque tal va la vision\b",
    )),
    ("capacidades", (
        r"\bque puedes ver\b", r"\bque capacidades\b",
        r"\bque sabes hacer con la camara\b",
    )),
)

_COMPILED = tuple((name, tuple(re.compile(p) for p in pats)) for name, pats in PATTERNS)


def detect(text: str) -> Intent:
    """Devuelve la intención de visión que mejor encaja, o una vacía."""
    norm = _norm(text)
    if not norm:
        return Intent("")
    for name, patterns in _COMPILED:
        for pattern in patterns:
            if pattern.search(norm):
                return Intent(name=name, confidence=0.9, argument=norm)
    return Intent("")


def is_vision_request(text: str) -> bool:
    """True si la frase pide EXPLÍCITAMENTE que YUE mire o lea algo."""
    intent = detect(text)
    return intent.name in {
        "que_ves", "describir_habitacion", "leer_texto", "leer_titulo",
        "que_sostengo", "contar_dedos", "que_hago", "seguir_mano", "como_estoy",
    }


def handle(engine, text: str, state=None) -> str | None:
    """Ejecuta la intención sobre un `PerceptionEngine`. None si no hay intención.

    Devuelve SIEMPRE una frase en español lista para que YUE la diga.

    `state` (ADITIVO, opcional) es el `LiveVisionState` compartido. Si se pasa,
    las preguntas directas ("¿cuántas personas hay?", "¿me estás mirando?") se
    contestan del estado que ya se está bombeando, sin pedir otro snapshot.
    """
    intent = detect(text)
    if not intent:
        return None
    if engine is None:
        if intent.name in {"camara_on"}:
            return ("El sistema de visión por cámara está apagado. "
                    "Actívalo con VISION_MP_ENABLED=true en el .env.")
        return None

    name = intent.name
    privacy = engine.privacy

    # --- privacidad ---------------------------------------------------
    if name == "modo_privacidad_on":
        privacy.set_privacy_mode(True)
        engine.events.set_muted(True)
        return "Modo privacidad activado: no analizo nada de la cámara hasta que me digas."
    if name == "modo_privacidad_off":
        privacy.set_privacy_mode(False)
        engine.events.set_muted(False)
        return "Salí del modo privacidad. Vuelvo a mirar solo lo necesario."
    if name == "olvidar_observaciones":
        n = engine.forget()
        return f"Listo, olvidé lo que había observado ({n} anotaciones borradas)."
    if name == "no_guardar_eventos":
        privacy.set_save_events(False)
        return "De acuerdo, no guardo ninguna observación visual."
    if name == "no_emociones":
        privacy.set_feature("emotions", False)
        return "Perfecto, dejo de estimar emociones por la cámara."
    if name == "si_emociones":
        privacy.set_feature("emotions", True)
        return "Vuelvo a estimar el ánimo, siempre como una impresión aproximada."
    if name == "no_ocr":
        privacy.set_feature("ocr", False)
        return "No leeré ningún texto por la cámara."
    if name == "si_ocr":
        privacy.set_feature("ocr", True)
        return "Vuelvo a poder leer textos cuando me lo pidas."
    if name == "no_objetos":
        privacy.set_feature("objects", False)
        return "Desactivé el reconocimiento de objetos."
    if name == "solo_bajo_peticion":
        privacy.set_only_on_request(True)
        return "A partir de ahora solo analizo cuando me lo pidas."
    if name == "analisis_continuo":
        privacy.set_only_on_request(False)
        return "Vale, vuelvo a observar de forma continua, con lo mínimo necesario."

    # --- cámara --------------------------------------------------------
    if name == "camara_off":
        engine.stop()
        return "Apagué la cámara y liberé el dispositivo."
    if name == "camara_on":
        ok = engine.start()
        return ("Encendí la cámara; el análisis es local y no guardo imágenes."
                if ok else "No conseguí abrir ninguna cámara disponible.")

    # A partir de aquí todo requiere cámara encendida.
    if not engine.running:
        return "La cámara no está encendida ahora mismo. Dime «activa la cámara» si quieres."

    # Una petición explícita abre la ventana de análisis.
    privacy.request_analysis()

    # --- lectura --------------------------------------------------------
    if name in {"leer_texto", "leer_titulo"}:
        if not privacy.allows("ocr"):
            return "Tengo la lectura de textos desactivada."
        result = engine.read_text(only_title=(name == "leer_titulo"))
        if result is None or not result.text:
            return ("No consigo leer nada estable todavía. Acércalo un poco, "
                    "sujétalo quieto y que le dé luz.")
        nivel = "con buena confianza" if result.confidence >= 0.75 else "aunque no estoy del todo segura"
        return f'Leo esto {nivel}: "{result.text}"'

    # --- ADITIVO: respuestas directas desde el estado vivo ----------------
    # Se construyen del snapshot ya medido, así que YUE no puede inventarse la
    # respuesta ni tiene que consultar al modelo de lenguaje para saber algo
    # que la cámara está viendo en este instante.
    if name in {"cuantas_personas", "me_miras", "mi_postura", "que_objetos"}:
        from vision.live_state import LiveVisionState
        vista = state if state is not None else LiveVisionState()
        if state is None:
            vista.update_from_snapshot(engine.snapshot())
        datos = vista.get()

        if name == "cuantas_personas":
            n = int(datos.get("personas", {}).get("count", 0))
            if n == 0:
                return "Ahora mismo no distingo a nadie frente a la cámara."
            if n == 1:
                principal = datos["personas"].get("principal") or {}
                extra = ""
                if principal.get("posicion"):
                    extra = f", {principal['posicion']} del encuadre"
                return f"Veo a una sola persona{extra}."
            return f"Cuento {n} personas frente a la cámara."

        if name == "me_miras":
            mirada = datos.get("mirada", {})
            if not datos.get("personas", {}).get("hay_persona"):
                return "Tengo la cámara encendida, pero ahora no te veo en el encuadre."
            if mirada.get("mira_a_yue"):
                seg = mirada.get("segundos_mirando", 0.0)
                if seg >= 2.0:
                    return f"Sí, te veo mirándome desde hace {seg:.0f} segundos."
                return "Sí, te estoy viendo y parece que me miras."
            return "Te veo, pero parece que estás mirando hacia otro lado."

        if name == "mi_postura":
            postura = datos.get("postura", {})
            if not postura.get("visible"):
                return ("No alcanzo a ver tu cuerpo, solo el rostro. "
                        "Échate un poco atrás si quieres que estime la postura.")
            frase = f"Diría que estás {postura.get('estado')}"
            if postura.get("brazos_cruzados"):
                frase = "Diría que estás con los brazos cruzados"
            elif postura.get("mano_levantada"):
                frase += ", con una mano levantada"
            if postura.get("movimiento_raw") == "moving":
                frase += ", y te veo moverte"
            return frase + ". Es una estimación, puedo equivocarme."

        if name == "que_objetos":
            objetos = [o["etiqueta"] for o in datos.get("objetos", [])]
            if not objetos:
                return "Ahora mismo no reconozco ningún objeto claro en el encuadre."
            unicos = list(dict.fromkeys(objetos))[:6]
            if len(unicos) == 1:
                return f"Reconozco {unicos[0]}."
            return "Reconozco " + ", ".join(unicos[:-1]) + f" y {unicos[-1]}."

    # --- descripción -----------------------------------------------------
    if name == "describir_habitacion":
        return engine.describe_room()
    if name == "que_ves":
        snapshot = engine.snapshot()
        count = int((snapshot.get("presence") or {}).get("count", 0))
        if count == 0:
            return "La cámara está activa, pero ahora mismo no distingo a nadie."
        base = engine.describe_room()
        return base

    # --- manos y acciones -------------------------------------------------
    if name == "contar_dedos":
        return engine.count_fingers()
    if name == "que_sostengo":
        return engine.what_am_i_holding()
    if name == "seguir_mano":
        privacy.set_feature("hands", True)
        privacy.request_analysis(30.0)
        return "Vale, sigo tus manos durante un rato."
    if name == "que_hago":
        return engine.what_am_i_doing()
    if name == "como_estoy":
        est = engine.affective_estimate()
        if est.get("affective_state", "undetermined") == "undetermined":
            return "No consigo hacerme una idea clara de cómo te sientes ahora mismo."
        return est.get("safe_description", "Es solo una impresión, pero algo noto.")

    # --- diagnóstico -------------------------------------------------------
    if name == "estado_vision":
        status = engine.camera.status()
        return (f"Cámara {'activa' if status.active else 'inactiva'} "
                f"(índice {status.index}, {status.real_fps:.0f} FPS reales). "
                + privacy.describe_es())
    if name == "capacidades":
        return engine.capabilities(refresh=True).describe_es()
    return None
