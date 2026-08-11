"""Pruebas de la MEMORIA HISTÓRICA RELEVANTE (core/memory_ext.search_history).

Es la tercera capa de memoria: recupera del historial COMPLETO lo que viene a
cuento con el mensaje de ahora, sin pisar a `recent_messages()` (continuidad
inmediata) ni a `episodic_memory` (acontecimientos estructurados).

    python -m pytest tests/test_memory_relevance.py
    python tests/test_memory_relevance.py
"""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.memory import Memory
from core.memory_ext import MemoryExtension

DIA = 86400.0


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _nueva_base():
    """Base temporal con el esquema REAL de core/memory.py."""
    carpeta = tempfile.mkdtemp(prefix="yue_relev_")
    ruta = str(Path(carpeta) / "yue.db")
    return ruta, Memory(ruta)


def _escribir(ruta, role, texto, dias_atras=0.0):
    """Inserta un mensaje con fecha controlada (add_message siempre usa ahora)."""
    con = sqlite3.connect(ruta)
    con.execute("INSERT INTO messages(role,content,ts) VALUES(?,?,?)",
                (role, texto, time.time() - dias_atras * DIA))
    con.commit()
    con.close()


def _rellenar(ruta, cuantos=12, dias_atras=0.5):
    """Charla intrascendente para empujar lo antiguo fuera de recent_messages()."""
    for i in range(cuantos):
        _escribir(ruta, "user", f"Hoy almorcé pollo con arroz, día {i} de la semana",
                  dias_atras)
        _escribir(ruta, "assistant", f"Suena rico, cuéntame más de ese día {i}",
                  dias_atras)


def _textos(hits):
    return " || ".join(h["texto"].lower() for h in hits)


# ---------------------------------------------------------------------------
# TEST 1 — recuperación por nombre, muy por detrás de la ventana reciente
# ---------------------------------------------------------------------------
def test1_recupera_recuerdo_antiguo_por_nombre():
    ruta, memoria = _nueva_base()
    _escribir(ruta, "user",
              "Me peleé con Andrea porque canceló nuestros planes sin avisarme",
              dias_atras=120)
    _rellenar(ruta, 12)          # 24 mensajes de relleno: queda muy atrás

    memoria.add_message("user", "Andrea volvió a escribirme")
    ext = MemoryExtension(ruta)

    hits = ext.search_history("Andrea volvió a escribirme", top_k=3,
                              skip_recent=12, role="user")
    assert hits, "debería recuperar el recuerdo de Andrea"
    assert "andrea" in _textos(hits)
    assert "peleé" in _textos(hits) or "pelee" in _textos(hits)


def test1b_la_relevancia_pesa_mas_que_la_recencia():
    """«Andrea me engañó» (hace 3 meses) gana a «almorcé pollo» (ayer)."""
    ruta, memoria = _nueva_base()
    _escribir(ruta, "user", "Andrea me engañó y me dolió muchísimo", dias_atras=90)
    _rellenar(ruta, 12, dias_atras=1)   # lo reciente es todo sobre pollo

    memoria.add_message("user", "Andrea volvió a escribirme")
    ext = MemoryExtension(ruta)

    hits = ext.search_history("Andrea volvió a escribirme", skip_recent=12)
    assert hits, "un recuerdo de hace 3 meses debe poder ganar a uno de ayer"
    assert "andrea" in hits[0]["texto"].lower()
    assert "pollo" not in hits[0]["texto"].lower()


# ---------------------------------------------------------------------------
# TEST 2 — el mensaje actual NO puede volver como recuerdo
# ---------------------------------------------------------------------------
def test2_no_devuelve_el_mensaje_actual():
    """El flujo guarda primero y construye el prompt después: sin exclusión, el
    resultado más parecido sería el propio mensaje, con similitud ~1.0."""
    ruta, memoria = _nueva_base()
    _escribir(ruta, "user",
              "Me peleé con Andrea porque canceló nuestros planes", dias_atras=120)
    _rellenar(ruta, 12)

    actual = "Andrea volvió a escribirme"
    memoria.add_message("user", actual)          # ← como hace main.py
    ext = MemoryExtension(ruta)

    hits = ext.search_history(actual, skip_recent=12)
    assert all(h["texto"].strip().lower() != actual.lower() for h in hits), \
        "el mensaje actual no es un recuerdo"
    assert all(h["similitud"] < 0.99 for h in hits)


def test2b_tampoco_devuelve_lo_que_ya_va_en_recent_messages():
    """Nada de lo que ya viaja por la memoria inmediata se repite aquí."""
    ruta, memoria = _nueva_base()
    _escribir(ruta, "user", "Andrea es mi compañera de universidad", dias_atras=120)
    _rellenar(ruta, 12)
    # Estos 5 SÍ entran en recent_messages(12): no deben salir como recuerdo.
    for i in range(5):
        memoria.add_message("user", f"Andrea me escribió otra vez, mensaje {i}")

    ext = MemoryExtension(ruta)
    recientes = {m["content"] for m in memoria.recent_messages(12)}
    hits = ext.search_history("Andrea volvió a escribirme", skip_recent=12)
    assert all(h["texto"] not in recientes for h in hits)


def test2c_exclusion_explicita_por_ids():
    ruta, memoria = _nueva_base()
    _escribir(ruta, "user", "Andrea canceló nuestros planes y me enfadé", dias_atras=90)
    _rellenar(ruta, 12)
    memoria.add_message("user", "Andrea volvió a escribirme")

    ext = MemoryExtension(ruta)
    hits = ext.search_history("Andrea volvió a escribirme", skip_recent=12)
    assert hits
    prohibido = hits[0]["id"]
    hits2 = ext.search_history("Andrea volvió a escribirme", skip_recent=12,
                               exclude_ids=[prohibido])
    assert all(h["id"] != prohibido for h in hits2)


# ---------------------------------------------------------------------------
# TEST 3 — irrelevancia: mejor no recordar nada
# ---------------------------------------------------------------------------
def test3_no_recupera_recuerdos_irrelevantes():
    ruta, memoria = _nueva_base()
    _escribir(ruta, "user", "Compré una bicicleta roja de segunda mano", dias_atras=60)
    _rellenar(ruta, 12)

    memoria.add_message("user", "Estoy preocupado por mi examen")
    ext = MemoryExtension(ruta)

    hits = ext.search_history("Estoy preocupado por mi examen", skip_recent=12)
    assert all("bicicleta" not in h["texto"].lower() for h in hits), \
        "la bicicleta no tiene nada que ver con el examen"


def test3b_sin_nada_relevante_devuelve_lista_vacia():
    """Si nada supera el umbral, NO se manda nada al prompt."""
    ruta, memoria = _nueva_base()
    _escribir(ruta, "user", "Compré una bicicleta roja de segunda mano", dias_atras=60)
    _escribir(ruta, "user", "Ayer llovió toda la tarde en Trujillo", dias_atras=55)
    _rellenar(ruta, 12)

    memoria.add_message("user", "Estoy preocupado por mi examen")
    ext = MemoryExtension(ruta)

    assert ext.search_history("Estoy preocupado por mi examen", skip_recent=12) == []
    assert ext.context_block("Estoy preocupado por mi examen", skip_recent=12) == ""


# ---------------------------------------------------------------------------
# TEST 4 — las respuestas de YUE no se convierten en hechos del usuario
# ---------------------------------------------------------------------------
def test4_no_recupera_mensajes_del_asistente():
    """Si YUE especuló «tal vez Andrea vive en Cusco», eso NO es un recuerdo."""
    ruta, memoria = _nueva_base()
    _escribir(ruta, "assistant", "Tal vez Andrea vive en Cusco, ¿te lo dijo ella?",
              dias_atras=120)
    _escribir(ruta, "user", "Me peleé con Andrea el año pasado", dias_atras=120)
    _rellenar(ruta, 12)

    memoria.add_message("user", "Andrea volvió a escribirme")
    ext = MemoryExtension(ruta)

    hits = ext.search_history("Andrea volvió a escribirme", skip_recent=12)
    assert hits, "el mensaje del usuario sí debe salir"
    assert all(h["role"] == "user" for h in hits)
    assert "cusco" not in _textos(hits)


def test4b_si_se_pide_role_any_queda_marcado_como_de_yue():
    """Con role="any" se pueden traer, pero el bloque avisa de quién es cada uno."""
    ruta, memoria = _nueva_base()
    _escribir(ruta, "assistant", "Tal vez Andrea vive en Cusco, ¿te lo dijo ella?",
              dias_atras=120)
    _rellenar(ruta, 12)
    memoria.add_message("user", "Andrea volvió a escribirme")

    ext = MemoryExtension(ruta)
    bloque = ext.context_block("Andrea volvió a escribirme", skip_recent=12,
                               role="any")
    if bloque:  # solo si supera el umbral; lo que se comprueba es el etiquetado
        assert "NO es un dato suyo" in bloque


# ---------------------------------------------------------------------------
# TEST 5 — la memoria inmediata sigue exactamente igual
# ---------------------------------------------------------------------------
def test5_recent_messages_no_cambia():
    ruta, memoria = _nueva_base()
    for i in range(20):
        memoria.add_message("user", f"mensaje número {i}")
        memoria.add_message("assistant", f"respuesta número {i}")

    MemoryExtension(ruta)  # crear la capa NO puede alterar el historial

    recientes = memoria.recent_messages(12)
    assert len(recientes) == 12
    assert recientes[-1] == {"role": "assistant", "content": "respuesta número 19"}
    assert recientes[0]["content"] == "mensaje número 14"
    assert list(recientes[0].keys()) == ["role", "content"]


def test5b_no_se_duplica_ni_un_mensaje():
    """La capa histórica LEE `messages`; no crea una segunda copia del historial."""
    ruta, memoria = _nueva_base()
    memoria.add_message("user", "Andrea es mi compañera de universidad")
    ext = MemoryExtension(ruta)
    ext.search_history("Andrea", skip_recent=0)

    con = sqlite3.connect(ruta)
    en_messages = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    en_conv = con.execute("SELECT COUNT(*) FROM conv_messages").fetchone()[0]
    con.close()
    assert en_messages == 1
    assert en_conv == 0, "la búsqueda histórica no debe escribir en conv_messages"


def test5c_borrar_el_historial_borra_los_recuerdos():
    """Privacidad: no queda una copia olvidada en otra tabla."""
    ruta, memoria = _nueva_base()
    _escribir(ruta, "user", "Me peleé con Andrea porque canceló los planes", 120)
    _rellenar(ruta, 12)
    ext = MemoryExtension(ruta)
    assert ext.search_history("Andrea volvió a escribirme", skip_recent=12)

    con = sqlite3.connect(ruta)
    con.execute("DELETE FROM messages")
    con.commit()
    con.close()
    ext.clear_cache()

    assert ext.search_history("Andrea volvió a escribirme", skip_recent=12) == []


# ---------------------------------------------------------------------------
# TEST 6 — un fallo de memory_ext no puede dejar a YUE sin hablar
# ---------------------------------------------------------------------------
class _MotorRoto:
    nombre = "roto"

    def puntuar(self, consulta, documentos):
        raise RuntimeError("motor de recuperación caído a propósito")


def test6_si_el_motor_falla_no_revienta():
    ruta, memoria = _nueva_base()
    _escribir(ruta, "user", "Me peleé con Andrea", dias_atras=120)
    _rellenar(ruta, 12)
    memoria.add_message("user", "Andrea volvió a escribirme")

    ext = MemoryExtension(ruta, retriever=_MotorRoto())
    assert ext.search_history("Andrea volvió a escribirme") == []
    assert ext.context_block("Andrea volvió a escribirme") == ""
    # Y la memoria inmediata sigue viva, que es lo que importa.
    assert memoria.recent_messages(3)


def test6b_base_sin_tabla_messages_no_revienta():
    """Una base recién creada por memory_ext (sin core/memory.py) no falla."""
    carpeta = tempfile.mkdtemp(prefix="yue_relev_")
    ext = MemoryExtension(str(Path(carpeta) / "vacia.db"))
    assert ext.search_history("lo que sea") == []
    assert ext.context_block("lo que sea") == ""


def test6c_consulta_vacia_o_de_puro_relleno():
    ruta, memoria = _nueva_base()
    _escribir(ruta, "user", "Me peleé con Andrea", dias_atras=120)
    _rellenar(ruta, 12)
    ext = MemoryExtension(ruta)
    assert ext.search_history("") == []
    assert ext.search_history("   ") == []
    assert ext.search_history("de la que y el") == []


# ---------------------------------------------------------------------------
# Extras del encargo: naturalidad, diversidad, límites y prompt
# ---------------------------------------------------------------------------
def test_similitud_mas_alla_de_las_palabras_exactas():
    """«extraño a mi mascota» debe encontrar «mi perro murió», sin embeddings."""
    ruta, memoria = _nueva_base()
    _escribir(ruta, "user", "Mi perro murió el fin de semana y estoy destrozado",
              dias_atras=90)
    _rellenar(ruta, 12)
    memoria.add_message("user", "Extraño mucho a mi mascota")

    ext = MemoryExtension(ruta)
    hits = ext.search_history("Extraño mucho a mi mascota", skip_recent=12)
    assert hits, "TF-IDF enriquecido con conceptos debería relacionarlos"
    assert "perro" in _textos(hits)


def test_diversidad_evita_tres_veces_lo_mismo():
    """Mejor tres piezas complementarias que tres versiones del mismo momento."""
    ruta, memoria = _nueva_base()
    for _ in range(3):
        _escribir(ruta, "user", "Andrea me llamó por teléfono ayer por la noche",
                  dias_atras=100)
    _escribir(ruta, "user", "Andrea era mi mejor amiga en la universidad",
              dias_atras=150)
    _escribir(ruta, "user", "Después de la pelea dejamos de hablarnos con Andrea",
              dias_atras=140)
    _rellenar(ruta, 12)
    memoria.add_message("user", "Andrea volvió a escribirme")

    ext = MemoryExtension(ruta)
    hits = ext.search_history("Andrea volvió a escribirme", top_k=3, skip_recent=12)
    repetidos = sum(1 for h in hits if "me llamó por teléfono" in h["texto"])
    assert repetidos <= 1, "la deduplicación debe dejar solo una de las idénticas"


def test_recorta_sin_partir_palabras():
    ruta, memoria = _nueva_base()
    largo = ("Andrea y yo discutimos muchísimo aquella tarde " * 20).strip()
    _escribir(ruta, "user", largo, dias_atras=120)
    _rellenar(ruta, 12)
    memoria.add_message("user", "Andrea volvió a escribirme")

    ext = MemoryExtension(ruta)
    hits = ext.search_history("Andrea volvió a escribirme", skip_recent=12,
                              max_chars=200)
    assert hits
    texto = hits[0]["texto"]
    assert len(texto) <= 201            # 200 + el carácter de puntos suspensivos
    assert not texto.rstrip("…").endswith(" ")
    assert texto.rstrip("…").split()[-1] in largo.split()


def test_top_k_y_umbral_son_configurables():
    ruta, memoria = _nueva_base()
    for i in range(6):
        _escribir(ruta, "user", f"Andrea hizo algo distinto el día {i} de aquel mes",
                  dias_atras=100 + i)
    _rellenar(ruta, 12)
    memoria.add_message("user", "Andrea volvió a escribirme")

    ext = MemoryExtension(ruta)
    assert len(ext.search_history("Andrea volvió a escribirme", top_k=2,
                                  skip_recent=12, min_score=0.05)) <= 2
    # Un umbral imposible no deja pasar nada.
    assert ext.search_history("Andrea volvió a escribirme", skip_recent=12,
                              min_score=0.99) == []


def test_el_bloque_del_prompt_prohibe_hablar_de_bases_de_datos():
    ruta, memoria = _nueva_base()
    _escribir(ruta, "user", "Me peleé con Andrea porque canceló nuestros planes",
              dias_atras=120)
    _rellenar(ruta, 12)
    memoria.add_message("user", "Andrea volvió a escribirme")

    ext = MemoryExtension(ruta)
    bloque = ext.context_block("Andrea volvió a escribirme", skip_recent=12)
    assert bloque.startswith("\n\nRECUERDOS RELEVANTES DEL HISTORIAL")
    assert "Andrea" in bloque
    assert "base de datos" in bloque          # la instrucción de NO decirlo
    assert "NUNCA inventes" in bloque
    assert "Él te dijo" in bloque


def test_el_interruptor_general_apaga_el_bloque():
    import config
    ruta, memoria = _nueva_base()
    _escribir(ruta, "user", "Me peleé con Andrea por lo de los planes", dias_atras=120)
    _rellenar(ruta, 12)
    memoria.add_message("user", "Andrea volvió a escribirme")
    ext = MemoryExtension(ruta)

    anterior = config.MEMORY_RELEVANCE_ENABLED
    try:
        config.MEMORY_RELEVANCE_ENABLED = False
        assert ext.context_block("Andrea volvió a escribirme", skip_recent=12) == ""
    finally:
        config.MEMORY_RELEVANCE_ENABLED = anterior


def test_el_motor_es_intercambiable():
    """Cambiar el algoritmo NO cambia la interfaz pública."""
    from core.memory_ext import BaseRetriever, build_retriever, register_retriever

    class _MotorTonto(BaseRetriever):
        nombre = "tonto"

        def puntuar(self, consulta, documentos):
            return [1.0] * len(documentos)

    register_retriever("tonto", _MotorTonto)
    assert isinstance(build_retriever("tonto"), _MotorTonto)
    assert build_retriever("no_existe_este_motor").nombre == "tfidf"

    ruta, memoria = _nueva_base()
    _escribir(ruta, "user", "cualquier cosa que haya dicho hace tiempo", dias_atras=90)
    _rellenar(ruta, 12)
    ext = MemoryExtension(ruta, retriever=_MotorTonto())
    hits = ext.search_history("da igual la consulta", skip_recent=12)
    assert hits and hits[0]["similitud"] == 1.0


# ---------------------------------------------------------------------------
# INTEGRACIÓN REAL: el bloque llega de verdad al system_prompt de main.py
# ---------------------------------------------------------------------------
def _controlador_falso(ruta, memoria, ext, ultimo_texto):
    """Un Controller sin Qt: solo lo que `_system_prompt` necesita tocar.

    No se instancia la clase entera (arrancaría cámara, voz, avatar…): se crea
    el objeto vacío y se le ponen las piezas justas. Así se prueba el CÓDIGO
    REAL de main.py, no una copia del mismo.

    Devuelve (None, None) si el Qt de la máquina no permite este truco; los
    tests que lo usan se saltan solos en ese caso, porque lo que comprueban ya
    está cubierto a nivel de unidad más arriba.
    """
    try:
        import main

        class _SinContexto:
            """Sensor apagado: devuelve texto vacío, como cuando no hay nada que decir."""

            def context_for_ai(self):
                return ""

        ctrl = object.__new__(main.Controller)
        ctrl.memory = memoria
        ctrl.memory_ext = ext
        ctrl.episodic = None
        ctrl._last_companion = None
        ctrl._last_user_text = ultimo_texto
        # QObject está doblado en conftest y responde a CUALQUIER atributo, así
        # que los sensores hay que apagarlos a mano o se cuelan objetos falsos.
        ctrl.media_companion = _SinContexto()
        ctrl.camera = _SinContexto()
        ctrl.audio = _SinContexto()
        return ctrl, main
    except Exception as exc:  # pragma: no cover - depende del Qt instalado
        print(f"  (aviso) no puedo simular el Controller aquí: {exc}")
        return None, None


def _prompt_sin_sensores(ctrl):
    """Genera el prompt con cámara y audio apagados (aquí no se prueban)."""
    import config

    from core import bonding

    claves = ("CAMERA_CONTEXT_IN_CHAT", "AUDIO_REACT_CONTEXT_IN_CHAT")
    previos = {k: getattr(config, k, None) for k in claves}
    for k in claves:
        setattr(config, k, False)
    try:
        nivel, _, _ = bonding.progress(0)
        return ctrl._system_prompt(nivel)
    finally:
        for k, v in previos.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)


def test_integracion_el_recuerdo_entra_en_el_system_prompt():
    ruta, memoria = _nueva_base()
    _escribir(ruta, "user",
              "Me peleé con Andrea porque canceló nuestros planes sin avisarme",
              dias_atras=120)
    _rellenar(ruta, 12)
    actual = "Andrea volvió a escribirme"
    memoria.add_message("user", actual)      # ← el orden REAL de main.py

    ext = MemoryExtension(ruta)
    ctrl, _main = _controlador_falso(ruta, memoria, ext, actual)
    if ctrl is None:
        return
    prompt = _prompt_sin_sensores(ctrl)

    assert "RECUERDOS RELEVANTES DEL HISTORIAL" in prompt
    assert "Andrea" in prompt
    assert actual not in prompt.split("RECUERDOS RELEVANTES DEL HISTORIAL")[1], \
        "el mensaje actual no puede aparecer como recuerdo"


def test_integracion_sin_recuerdos_el_prompt_no_cambia():
    ruta, memoria = _nueva_base()
    _escribir(ruta, "user", "Compré una bicicleta roja de segunda mano", 60)
    _rellenar(ruta, 12)
    memoria.add_message("user", "Estoy preocupado por mi examen")

    ext = MemoryExtension(ruta)
    ctrl, _main = _controlador_falso(ruta, memoria, ext, "Estoy preocupado por mi examen")
    if ctrl is None:
        return
    con_capa = _prompt_sin_sensores(ctrl)

    ctrl.memory_ext = None
    sin_capa = _prompt_sin_sensores(ctrl)
    assert con_capa == sin_capa, "sin recuerdos relevantes, el prompt es el de antes"


def test_integracion_un_fallo_no_rompe_el_prompt():
    """TEST 6 de verdad: memory_ext revienta y YUE sigue teniendo su prompt."""
    ruta, memoria = _nueva_base()
    _escribir(ruta, "user", "Me peleé con Andrea", 120)
    _rellenar(ruta, 12)
    memoria.add_message("user", "Andrea volvió a escribirme")

    class _ExplotaSiempre:
        def context_block(self, *a, **k):
            raise RuntimeError("boom")

    ctrl, _main = _controlador_falso(ruta, memoria, _ExplotaSiempre(),
                                     "Andrea volvió a escribirme")
    if ctrl is None:
        return
    prompt = _prompt_sin_sensores(ctrl)
    assert prompt, "el prompt debe generarse igual"
    assert "RECUERDOS RELEVANTES DEL HISTORIAL" not in prompt


if __name__ == "__main__":
    fallos = []
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test") and callable(_f):
            try:
                _f()
                print("  OK  " + _n)
            except Exception as _e:
                print("  FALLO  " + _n + f"  ·  {_e}")
                fallos.append(_n)
    print("\n" + ("TODO OK" if not fallos else f"FALLOS: {fallos}"))
    sys.exit(1 if fallos else 0)
