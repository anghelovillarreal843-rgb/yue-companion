"""Pruebas del sistema de VISION DE PANTALLA de YUE.

    python -m pytest tests/test_screen_vision.py -v
    python tests/test_screen_vision.py

Cubre los 20 casos pedidos:

     1. Captura valida.                  11. Pantalla con mucho texto.
     2. Captura negra.                   12. Video con movimiento.
     3. Captura vacia.                   13. Video sin audio.
     4. Monitor inexistente.             14. Captura antigua.
     5. Vision provider funcionando.     15. Modelo que no admite imagenes.
     6. Primer proveedor visual fallando.16. HTTP 429.
     7. Segundo proveedor toma el relevo.17. Timeout.
     8. Todos los proveedores fallando.  18. HTTP 500.
     9. Fallback OCR funcionando.        19. Respuesta vacia.
    10. Imagen sin texto.                20. API visual caida.

Ninguna prueba necesita internet: la funcion de envio del router se inyecta.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from core import screen_capture
from core.screen_capture import ScreenFrame
from core.screen_observation import ScreenObservation, classify_screen, strategy_for
from core.vision_router import (
    AllVisionProvidersFailed, VisionProvider, VisionProviderError, VisionRouter,
    _clasificar_http, default_vision_providers,
)


# --------------------------------------------------------------- utilidades
def _prov(nombre, clave="k", modelo="m", **kw):
    return VisionProvider(name=nombre, base_url="http://x", api_key=clave,
                          model=modelo, **kw)


def _silencio(_msg):
    """Log mudo para que la salida de las pruebas quede limpia."""


def _router(providers, send_fn):
    return VisionRouter(providers, send_fn=send_fn, log=_silencio)


def _imagen(color=(120, 120, 120), size=(800, 450)):
    return Image.new("RGB", size, color)


def _imagen_con_texto(lineas=28):
    """Simula una pantalla de documento: fondo blanco con muchas rayas oscuras."""
    from PIL import ImageDraw
    img = Image.new("RGB", (1280, 720), (250, 250, 250))
    dib = ImageDraw.Draw(img)
    for i in range(lineas):
        y = 20 + i * 24
        dib.rectangle([60, y, 1180, y + 9], fill=(30, 30, 30))
    return img


def _imagen_fotografia():
    """Simula una fotografia: ruido de color, sin zonas planas."""
    import random
    rnd = random.Random(7)
    img = Image.new("RGB", (640, 360))
    img.putdata([(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
                 for _ in range(640 * 360)])
    return img


def _frame(image, edad=0.0, monitor=1, total=1):
    return ScreenFrame(image, monitor=monitor, method="test",
                       timestamp=time.time() - edad, monitors_total=total)


# =========================================================== 1..4  CAPTURA
def test_01_captura_valida():
    """Una captura normal pasa la validacion y produce un JPEG con contenido."""
    frame = _frame(_imagen((90, 140, 200)))
    ok, motivo = frame.validate(max_age=3.0)
    assert ok, motivo
    assert frame.width == 800 and frame.height == 450
    assert frame.monitor == 1
    assert frame.frame_age < 1.0
    b64 = frame.to_b64()
    assert isinstance(b64, str) and len(b64) > 500


def test_02_captura_negra():
    """Una pantalla completamente negra se detecta y NO se manda al modelo."""
    frame = _frame(Image.new("RGB", (800, 450), (0, 0, 0)))
    assert frame.is_black() is True
    ok, motivo = frame.validate()
    assert ok is False
    assert "negra" in motivo.lower()


def test_02b_foto_oscura_no_es_captura_negra():
    """Una imagen oscura PERO con variacion no debe confundirse con un fallo."""
    from PIL import ImageDraw
    img = Image.new("RGB", (800, 450), (2, 2, 2))
    ImageDraw.Draw(img).rectangle([10, 10, 400, 300], fill=(180, 160, 90))
    frame = _frame(img)
    assert frame.is_black() is False
    assert frame.validate()[0] is True


def test_03_captura_vacia():
    """Dimensiones imposibles o imagen None se rechazan sin reventar."""
    assert _frame(Image.new("RGB", (4, 4))).is_empty() is True
    vacio = ScreenFrame(None, monitor=1)
    assert vacio.is_empty() is True
    ok, motivo = vacio.validate()
    assert ok is False and "vac" in motivo.lower()


def test_04_monitor_inexistente(monkeypatch):
    """Pedir el monitor 9 en un equipo de 1 pantalla cae al 1, no revienta."""
    class _FakeShot:
        size = (640, 360)
        bgra = bytes(640 * 360 * 4)

    class _FakeMSS:
        monitors = [{"left": 0, "top": 0, "width": 640, "height": 360},
                    {"left": 0, "top": 0, "width": 640, "height": 360}]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def grab(self, mon):
            return _FakeShot()

    import types
    fake = types.ModuleType("mss")
    fake.mss = _FakeMSS
    monkeypatch.setitem(sys.modules, "mss", fake)

    frame = screen_capture.grab_frame(monitor=9)
    assert frame.monitor == 1                    # cayo al monitor principal
    assert frame.width == 640 and frame.height == 360


def test_04b_listado_de_monitores_no_revienta():
    """list_monitors() nunca lanza excepcion, aunque no haya display."""
    salida = screen_capture.list_monitors()
    assert isinstance(salida, list)


# ================================================= 5..8  ROUTER DE VISION
def test_05_vision_provider_funcionando():
    """El primer proveedor visual responde: se usa y se registra el exito."""
    def send(p, *a, **kw):
        return f"veo la pantalla ({p.name})"

    r = _router([_prov("gemini"), _prov("openrouter")], send)
    salida = r.describe("BASE64", "que ves")
    assert salida["provider"] == "gemini"
    assert salida["attempts"] == 1
    assert "veo la pantalla" in salida["text"]


def test_06_primer_proveedor_falla():
    """Si el primero falla, se penaliza y NO se vuelve a intentar de inmediato."""
    def send(p, *a, **kw):
        if p.name == "gemini":
            raise VisionProviderError("sin cupo", kind="quota")
        return "descripcion de respaldo"

    p1, p2 = _prov("gemini"), _prov("openrouter")
    r = _router([p1, p2], send)
    r.describe("B", "que ves")
    assert p1.fails == 1
    assert p1.cooldown_until > time.time()
    assert p1.usable(time.time()) is False


def test_07_segundo_proveedor_toma_el_relevo():
    """El relevo es inmediato: sin pausas y con el resultado del segundo."""
    def send(p, *a, **kw):
        if p.name == "gemini":
            raise VisionProviderError("429", kind="quota")
        return "aqui esta la descripcion"

    r = _router([_prov("gemini"), _prov("openrouter")], send)
    salida = r.describe("B", "que ves")
    assert salida["provider"] == "openrouter"
    assert salida["attempts"] == 2
    assert salida["text"] == "aqui esta la descripcion"


def test_08_todos_los_proveedores_fallan():
    """Con toda la fila caida se lanza AllVisionProvidersFailed con el detalle."""
    def send(p, *a, **kw):
        raise VisionProviderError("caido", kind="server")

    r = _router([_prov("gemini"), _prov("openrouter"), _prov("groq")], send)
    try:
        r.describe("B", "que ves")
        assert False, "deberia haber lanzado AllVisionProvidersFailed"
    except AllVisionProvidersFailed as exc:
        assert len(exc.errores) == 3
        assert all("server" in v for v in exc.errores.values())


def test_08b_sin_bucles_infinitos():
    """Cada proveedor se prueba UNA vez por peticion; hay tope duro de intentos."""
    llamadas = {"n": 0}

    def send(p, *a, **kw):
        llamadas["n"] += 1
        raise VisionProviderError("nope", kind="error")

    fila = [_prov(f"p{i}") for i in range(12)]
    r = VisionRouter(fila, send_fn=send, log=_silencio, max_attempts=4)
    try:
        r.describe("B", "x")
    except AllVisionProvidersFailed:
        pass
    assert llamadas["n"] == 4, f"se hicieron {llamadas['n']} llamadas"


def test_08c_proveedor_sin_clave_no_entra_a_la_fila():
    """Un proveedor sin API key ni se intenta (y se explica en el reporte)."""
    def send(p, *a, **kw):
        return "ok"

    sin_clave = _prov("openai", clave="")
    r = _router([sin_clave, _prov("gemini")], send)
    salida = r.describe("B", "x")
    assert salida["provider"] == "gemini"
    assert sin_clave.fails == 0


# ======================================== 9..11  OCR, IMAGEN SIN TEXTO, TEXTO
def test_09_fallback_ocr_funcionando():
    """Vision caida + OCR con texto = la observacion SIGUE siendo util.

    Este es el criterio central: YUE nunca debe decir "no puedo ver" si al
    menos pudo leer texto de la pantalla.
    """
    obs = ScreenObservation(
        timestamp=time.time(), width=1920, height=1080,
        ocr_text="Error 404: pagina no encontrada",
        visual_description="",             # toda la vision multimodal fallo
        detected_content_type="error", confidence=0.7,
        vision_available=False, ocr_available=True, strategy="ocr_fallback",
        errors=["vision: todos los modelos visuales fallaron"],
    )
    assert bool(obs) is True               # la observacion sirve
    assert obs.has_text() is True
    assert obs.has_vision() is False
    contexto = obs.to_prompt_context()
    assert "TEXTO EN PANTALLA" in contexto
    assert "404" in contexto


def test_09b_sin_vision_y_sin_ocr_la_observacion_es_vacia():
    """Solo cuando NO hay ni texto ni descripcion se considera fallo total."""
    obs = ScreenObservation(timestamp=time.time(), errors=["captura: negra"])
    assert bool(obs) is False


def test_10_imagen_sin_texto():
    """Una fotografia sin texto NO se clasifica como documento.

    Es el caso del perro junto a la bicicleta: debe ir al modelo multimodal,
    no responder "no encontre texto para leer".
    """
    tipo, conf, senales = classify_screen(_imagen_fotografia(), ocr_text="")
    assert tipo in ("fotografia", "video"), f"tipo={tipo} senales={senales}"
    assert senales["text_chars"] == 0
    estrategia = strategy_for(tipo, hay_vision=True, hay_ocr=True)
    assert estrategia == "vision"


def test_11_pantalla_con_mucho_texto():
    """Una pantalla llena de texto prioriza OCR (mas barato y mas exacto)."""
    texto = "palabra " * 1200
    tipo, conf, senales = classify_screen(_imagen_con_texto(), ocr_text=texto)
    assert tipo in ("texto", "codigo", "interfaz"), f"tipo={tipo}"
    assert senales["text_density"] > 0.2
    assert strategy_for("texto", hay_vision=True, hay_ocr=True) == "ocr"


def test_11b_codigo_prioriza_ocr():
    """Codigo fuente: OCR prioritario (el texto exacto importa mas que la foto)."""
    codigo = "def main():\n    import os\n    return os.getcwd()\nclass A:\n    print('x')"
    tipo, _conf, _s = classify_screen(
        _imagen_con_texto(), ocr_text=codigo,
        window={"title": "main.py - Visual Studio Code", "process": "code.exe"})
    assert tipo == "codigo"
    assert strategy_for(tipo, True, True) == "ocr"


def test_11c_interfaz_usa_ambos():
    """Texto + graficos (una interfaz) usa OCR Y modelo multimodal."""
    assert strategy_for("interfaz", hay_vision=True, hay_ocr=True) == "ambos"
    assert strategy_for("pdf", hay_vision=True, hay_ocr=True) == "ambos"


def test_11d_sin_vision_la_estrategia_cae_a_ocr():
    """Sin ningun proveedor multimodal, todo se resuelve con OCR."""
    assert strategy_for("fotografia", hay_vision=False, hay_ocr=True) == "ocr"
    assert strategy_for("pdf", hay_vision=False, hay_ocr=True) == "ocr"


# ============================================ 12..13  VIDEO (con y sin audio)
def test_12_video_con_movimiento():
    """Mucho movimiento entre frames = contenido de video."""
    tipo, conf, senales = classify_screen(
        _imagen((40, 40, 60)), ocr_text="", motion=0.62)
    assert tipo == "video", f"tipo={tipo} senales={senales}"
    assert senales["motion"] == 0.62
    assert strategy_for(tipo, True, True) == "vision"


def test_13_video_sin_audio():
    """Un video SILENCIADO se detecta igual: por movimiento, no por sonido."""
    from core.media_companion import MediaCompanion

    mc = MediaCompanion.__new__(MediaCompanion)          # sin arrancar hilos
    mc._silent_video_score = 0.0
    mc._silent_video_since = 0.0

    ahora = time.time()
    # Movimiento sostenido durante varias muestras, sin una sola nota de audio.
    for i in range(12):
        mc._track_silent_video(0.45, ahora + i * 0.5)
    final = ahora + 11 * 0.5
    assert mc._silent_video_since > 0.0
    assert mc.silent_video_detected(min_seconds=1.0, now=final) is True

    # Pantalla quieta: no hay video.
    mc._silent_video_score = 0.0
    mc._silent_video_since = 0.0
    for i in range(12):
        mc._track_silent_video(0.01, ahora + i * 0.5)
    assert mc.silent_video_detected(min_seconds=1.0, now=final) is False

    # Un UNICO cambio brusco (abrir un menu) y luego quietud NO es video: la
    # media movil tarda en decaer, pero el frame actual ya no se mueve.
    mc._silent_video_score = 0.0
    mc._silent_video_since = 0.0
    mc._track_silent_video(1.0, ahora)              # el golpe
    for i in range(1, 12):
        mc._track_silent_video(0.0, ahora + i * 0.5)  # pantalla quieta
    assert mc.silent_video_detected(min_seconds=1.0, now=final) is False


def test_13b_memoria_temporal_de_frames():
    """La memoria temporal guarda resumenes y NO inventa continuidad."""
    from collections import deque
    from core.media_companion import MediaCompanion, VisualAnalysis

    mc = MediaCompanion.__new__(MediaCompanion)
    mc._frame_memory = deque(maxlen=5)

    for i, texto in enumerate(["chico sentado", "chico sentado", "chico de pie"]):
        mc._remember_frame(VisualAnalysis(timestamp=time.time(), summary=texto), 0.3)
    resumen = mc._temporal_brief()
    assert "chico sentado" in resumen and "chico de pie" in resumen
    assert resumen.count("- hace") == 3

    # La instruccion incluye el historial y la orden de no inventar.
    from core.media_companion import MediaSource
    instr = MediaCompanion._vision_instruction(0.4, MediaSource(), resumen)
    assert "cambio_respecto_antes" in instr
    assert "sin evidencia suficiente" in instr
    # Sin historial, no se pide comparacion temporal.
    assert "Observaciones ANTERIORES" not in MediaCompanion._vision_instruction(
        0.4, MediaSource(), "")


def test_13c_sin_evidencia_no_se_guarda_como_hecho():
    """Si el modelo dice que no tiene evidencia, el campo temporal queda vacio."""
    from core.media_companion import parse_visual_response
    v = parse_visual_response(
        '{"resumen":"una escena","cambio_respecto_antes":"sin evidencia suficiente"}')
    assert v.temporal_change == ""
    v2 = parse_visual_response(
        '{"resumen":"una escena","cambio_respecto_antes":"antes estaba sentado, ahora de pie"}')
    assert "sentado" in v2.temporal_change


# ==================================================== 14  CAPTURA ANTIGUA
def test_14_captura_antigua():
    """Un frame viejo NO se analiza: YUE no describe lo de hace un rato."""
    viejo = _frame(_imagen(), edad=9.0)
    assert viejo.is_fresh(3.0) is False
    ok, motivo = viejo.validate(max_age=3.0)
    assert ok is False
    assert "antigua" in motivo.lower()

    reciente = _frame(_imagen(), edad=0.1)
    assert reciente.is_fresh(3.0) is True
    assert reciente.validate(max_age=3.0)[0] is True


def test_14b_mirar_ahora_ignora_la_cache():
    """«mira mi pantalla» / «que ves ahora» obligan siempre a capturar de nuevo."""
    from core.screen_analyzer import ScreenAnalyzer

    for frase in ("mira mi pantalla", "que ves ahora", "mira esto",
                  "vuelve a mirar", "mira la pantalla otra vez"):
        assert ScreenAnalyzer.exige_captura_nueva(frase) is True, frase
    for frase in ("como te llamas", "cuentame un chiste", "que tal la musica"):
        assert ScreenAnalyzer.exige_captura_nueva(frase) is False, frase


# ================================== 15..20  CLASIFICACION DE FALLOS HTTP
def test_15_modelo_que_no_admite_imagenes():
    """Un modelo sin vision se marca y se DESCARTA para el resto de la sesion."""
    kind, _ = _clasificar_http(400, "This model does not support image input")
    assert kind == "no_vision"

    def send(p, *a, **kw):
        if p.name == "malo":
            raise VisionProviderError("no image", kind="no_vision")
        return "descripcion buena"

    malo, bueno = _prov("malo"), _prov("bueno")
    r = _router([malo, bueno], send)
    salida = r.describe("B", "x")
    assert salida["provider"] == "bueno"
    # Marca permanente: ya no se considera multimodal.
    assert malo.supports_vision is False
    assert malo.usable(time.time()) is False


def test_16_http_429():
    kind, _ = _clasificar_http(429, "Rate limit reached")
    assert kind == "quota"
    kind2, _ = _clasificar_http(400, "You exceeded your current quota")
    assert kind2 == "quota"


def test_17_timeout():
    """Un timeout de red salta al siguiente proveedor con enfriamiento corto."""
    def send(p, *a, **kw):
        if p.name == "lento":
            raise VisionProviderError("se agoto el tiempo", kind="timeout")
        return "listo"

    lento = _prov("lento")
    r = _router([lento, _prov("rapido")], send)
    salida = r.describe("B", "x")
    assert salida["provider"] == "rapido"
    assert lento.last_kind == "timeout"
    # El enfriamiento por timeout es corto: se puede reintentar pronto.
    assert 0 < (lento.cooldown_until - time.time()) <= 60


def test_18_http_500():
    kind, _ = _clasificar_http(500, "Internal Server Error")
    assert kind == "server"
    assert _clasificar_http(503, "Service Unavailable")[0] == "server"


def test_19_respuesta_vacia():
    """Una respuesta vacia cuenta como fallo y activa el relevo."""
    def send(p, *a, **kw):
        return "" if p.name == "mudo" else "por fin una descripcion"

    mudo = _prov("mudo")
    r = _router([mudo, _prov("hablador")], send)
    salida = r.describe("B", "x")
    assert salida["provider"] == "hablador"
    assert mudo.last_kind == "empty"


def test_19b_respuesta_incompatible():
    """Un JSON sin `choices` se clasifica como respuesta incompatible."""
    def send(p, *a, **kw):
        raise VisionProviderError("respuesta incompatible", kind="bad_response")

    p1 = _prov("raro")
    r = _router([p1], send)
    try:
        r.describe("B", "x")
    except AllVisionProvidersFailed:
        pass
    assert p1.last_kind == "bad_response"


def test_20_api_visual_caida():
    """Proveedor inalcanzable (DNS/red): se clasifica como network y se releva."""
    def send(p, *a, **kw):
        if p.name == "caido":
            raise VisionProviderError("Connection refused", kind="network")
        return "descripcion desde el respaldo"

    caido = _prov("caido")
    r = _router([caido, _prov("vivo")], send)
    salida = r.describe("B", "x")
    assert salida["provider"] == "vivo"
    assert caido.last_kind == "network"


def test_20b_clave_invalida_se_enfria_mucho():
    """Una API key mala no se reintenta cada pocos segundos."""
    kind, _ = _clasificar_http(401, "Invalid API key")
    assert kind == "auth"
    assert _clasificar_http(403, "Forbidden")[0] == "auth"

    def send(p, *a, **kw):
        raise VisionProviderError("clave mala", kind="auth")

    p1 = _prov("malaclave")
    r = _router([p1], send)
    try:
        r.describe("B", "x")
    except AllVisionProvidersFailed:
        pass
    assert (p1.cooldown_until - time.time()) > 600


def test_20c_modelo_retirado():
    kind, _ = _clasificar_http(404, "model_not_found")
    assert kind == "model_gone"
    assert _clasificar_http(400, "has been decommissioned")[0] == "model_gone"


# ============================================== EXTRAS: fila y configuracion
def test_extra_la_fila_visual_es_independiente_de_la_de_texto():
    """VisionRouter y AIRouter NO comparten lista de modelos."""
    from core.ai_router import default_providers

    visuales = {p.name for p in default_vision_providers(getenv=lambda k, d="": {
        "GEMINI_API_KEY": "a", "OPENROUTER_API_KEY": "b", "CEREBRAS_API_KEY": "c",
    }.get(k, d))}
    textuales = {p.name for p in default_providers(getenv=lambda k, d="": {
        "GEMINI_API_KEY": "a", "OPENROUTER_API_KEY": "b", "CEREBRAS_API_KEY": "c",
    }.get(k, d))}
    # Cerebras sirve para texto pero NO acepta imagenes: no debe estar en la fila
    # visual solo porque tenga clave.
    assert "cerebras" in textuales
    assert "cerebras" not in visuales


def test_extra_todos_los_modelos_visuales_declaran_supports_vision():
    """Ningun proveedor entra a la fila sin declarar explicitamente que ve."""
    for p in default_vision_providers(getenv=lambda k, d="": "clave"):
        assert p.supports_vision is True, p.name
        assert p.model, p.name


def test_extra_vision_provider_elegido_va_primero_pero_hay_respaldo():
    """VISION_PROVIDER=openrouter lo pone delante SIN quitar los demas."""
    entorno = {
        "VISION_PROVIDER": "openrouter",
        "GEMINI_API_KEY": "a", "OPENROUTER_API_KEY": "b", "GROQ_API_KEY": "c",
    }
    fila = default_vision_providers(getenv=lambda k, d="": entorno.get(k, d))
    disponibles = [p.name for p in fila if p.usable(time.time())]
    assert disponibles[0] == "openrouter"
    assert "gemini" in disponibles and "groq" in disponibles


def test_extra_proveedor_custom_va_el_primero():
    """Un endpoint propio completo tiene prioridad sobre el catalogo."""
    entorno = {
        "VISION_BASE_URL": "http://mi-servidor/v1",
        "VISION_API_KEY": "x", "VISION_MODEL": "mi-modelo",
        "GEMINI_API_KEY": "a",
    }
    fila = default_vision_providers(getenv=lambda k, d="": entorno.get(k, d))
    assert fila[0].name == "custom"
    assert fila[0].model == "mi-modelo"


def test_extra_observacion_estructurada_completa():
    """La ScreenObservation lleva TODOS los campos que pide la arquitectura."""
    obs = ScreenObservation()
    for campo in ("timestamp", "frame_age", "monitor", "width", "height",
                  "ocr_text", "visual_description", "detected_content_type",
                  "confidence", "provider_used", "model_used",
                  "vision_available", "ocr_available", "errors"):
        assert hasattr(obs, campo), campo
    assert isinstance(obs.as_dict(), dict)


def test_extra_reset_cooldowns_no_revive_a_los_que_no_ven():
    """Reactivar la fila no devuelve a la vida a un modelo que no acepta imagenes."""
    def send(p, *a, **kw):
        raise VisionProviderError("no image", kind="no_vision")

    p1 = _prov("ciego")
    r = _router([p1], send)
    try:
        r.describe("B", "x")
    except AllVisionProvidersFailed:
        pass
    r.reset_cooldowns()
    assert p1.usable(time.time()) is False      # sigue descartado, y con razon
    r.reset_cooldowns(incluir_no_vision=True)
    p1.supports_vision = True                   # rehabilitado a mano
    assert p1.usable(time.time()) is True


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))


# ======================== REGRESIONES encontradas en integracion =========
def test_reg_error_en_pantalla_usa_ocr_Y_vision():
    """Una pantalla con un traceback debe clasificarse como 'error', no 'texto'.

    Regresion real: con mucho texto en pantalla, la densidad ganaba siempre y un
    traceback se trataba como documento normal (estrategia 'ocr'). El OCR daba el
    texto, pero la vision nunca miraba y YUE no podia decir DE QUE PROGRAMA venia
    el error. Ahora la senal de error pesa segun cuantas pistas hay y su fuerza.
    """
    texto = ("Traceback (most recent call last)\n"
             "FileNotFoundError: no such file 'datos.csv'\n" + ("linea de texto " * 400))
    tipo, conf, _s = classify_screen(_imagen_con_texto(), ocr_text=texto)
    assert tipo == "error", f"tipo={tipo} conf={conf}"
    assert strategy_for(tipo, hay_vision=True, hay_ocr=True) == "ambos"


def test_reg_texto_normal_no_se_marca_como_error():
    """Un documento corriente NO debe dispararse como 'error' por una palabra."""
    tipo, _c, _s = classify_screen(_imagen_con_texto(), ocr_text="palabra " * 1200)
    assert tipo != "error"


def test_reg_no_se_anuncia_fallback_si_la_vision_no_se_intento():
    """No decir 'todos los modelos visuales fallaron' cuando no se llamo a ninguno.

    Regresion real: con estrategia 'ocr' (pantalla llena de texto) la vision ni
    se intenta, pero el log anunciaba igualmente que toda la fila visual habia
    fallado. Eso hacia parecer averiadas unas APIs perfectamente sanas.
    """
    from core.screen_analyzer import ScreenAnalyzer

    logs = []
    llamadas = {"n": 0}

    def send(p, *a, **kw):
        llamadas["n"] += 1
        return "no deberia llamarse"

    router = VisionRouter([_prov("gemini")], send_fn=send, log=lambda m: None)
    an = ScreenAnalyzer(vision_router=router, log=logs.append)

    # Forzamos estrategia 'ocr' y una pantalla de puro texto.
    import core.screen_analyzer as sa
    from core.screen_capture import ScreenFrame as _SF
    original_grab = sa.screen_capture.grab_frame
    original_read = sa.screen_ocr.read
    original_avail = sa.screen_ocr.available
    try:
        sa.screen_capture.grab_frame = lambda monitor=1: _SF(
            _imagen_con_texto(), monitor=1, method="test", timestamp=time.time())
        sa.screen_ocr.available = lambda: True
        sa.screen_ocr.read = lambda img, max_chars=8000: {
            "text": "palabra " * 1200, "chars": 9600, "engine": "fake",
            "elapsed": 0.0, "error": ""}
        obs = an.observe(question="lee esto", force_fresh=True, strategy="ocr")
    finally:
        sa.screen_capture.grab_frame = original_grab
        sa.screen_ocr.read = original_read
        sa.screen_ocr.available = original_avail

    assert obs.strategy == "ocr"
    assert llamadas["n"] == 0, "no se debe llamar a ningun proveedor visual"
    juntos = " ".join(logs).lower()
    assert "todos los modelos visuales fallaron" not in juntos, juntos
    assert bool(obs) is True
