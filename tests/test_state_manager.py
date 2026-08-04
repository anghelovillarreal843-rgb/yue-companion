"""Pruebas del núcleo de estado de YUE (FASE 2).

    python -m pytest tests/test_state_manager.py
    python tests/test_state_manager.py
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.state import (
    EventBus, Resource, ResourceManager, ResourceBusy,
    YueStateManager, Priority,
)


# ----------------------------- EventBus -----------------------------------
def test_eventbus_entrega_y_cancela():
    bus = EventBus()
    recibido = []
    off = bus.subscribe("hola", lambda **d: recibido.append(d))
    assert bus.publish("hola", x=1) == 1
    assert recibido == [{"x": 1}]
    off()
    bus.publish("hola", x=2)
    assert recibido == [{"x": 1}]  # ya no llega tras cancelar


def test_eventbus_aisla_errores():
    bus = EventBus()
    llegaron = []
    bus.subscribe("e", lambda **d: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.subscribe("e", lambda **d: llegaron.append(1))
    ok = bus.publish("e")
    assert ok == 1              # el que sí funciona recibió el evento
    assert llegaron == [1]      # el error de uno no tumbó al otro


# --------------------------- ResourceManager ------------------------------
def test_recurso_un_solo_dueno():
    rm = ResourceManager()
    t1 = rm.acquire(Resource.CAMERA, "clasico", priority=40)
    assert t1 is not None
    # Otro con prioridad igual/menor NO puede tomar la misma cámara.
    t2 = rm.acquire(Resource.CAMERA, "vision_v3", priority=40)
    assert t2 is None
    assert rm.owner_of(Resource.CAMERA) == "clasico"


def test_recurso_expropiacion_por_prioridad():
    rm = ResourceManager()
    expropiado = []
    rm.bus.subscribe("recurso_expropiado", lambda **d: expropiado.append(d["previous_owner"]))
    rm.acquire(Resource.CAMERA, "clasico", priority=40)
    # Alguien con MÁS prioridad sí se queda la cámara y avisa al anterior.
    t = rm.acquire(Resource.CAMERA, "seguridad", priority=100)
    assert t is not None
    assert rm.owner_of(Resource.CAMERA) == "seguridad"
    assert expropiado == ["clasico"]


def test_recurso_busy_raise_opcional():
    rm = ResourceManager()
    rm.acquire(Resource.MICROPHONE, "voz", priority=70)
    try:
        rm.acquire(Resource.MICROPHONE, "otro", priority=50, raise_on_busy=True)
        assert False, "debió lanzar ResourceBusy"
    except ResourceBusy as e:
        assert e.holder == "voz"


def test_recurso_context_manager_libera():
    rm = ResourceManager()
    with rm.acquire_ctx(Resource.SPEAKERS, "musica", priority=40) as tok:
        assert tok is not None
        assert rm.owner_of(Resource.SPEAKERS) == "musica"
    # Al salir del with queda libre.
    assert rm.is_free(Resource.SPEAKERS)


def test_recurso_concurrencia_sin_corromper():
    """Muchos hilos peleando por la cámara: nunca hay dos dueños a la vez."""
    rm = ResourceManager()
    exitos = []
    lock = threading.Lock()

    def worker(i):
        tok = rm.acquire(Resource.CAMERA, f"w{i}", priority=40)
        if tok:
            with lock:
                exitos.append(i)
            time.sleep(0.001)
            rm.release(tok)

    hilos = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    # Al terminar todos, la cámara queda libre y hubo al menos un ganador.
    assert rm.is_free(Resource.CAMERA)
    assert len(exitos) >= 1


# --------------------------- YueStateManager ------------------------------
def test_estado_emocion_actualiza_avatar_base():
    sm = YueStateManager()
    sm.set_emotion("feliz", 0.9, source="test")
    st = sm.get_state()
    assert st.emotion_primary == "feliz"
    assert st.intensity == 0.9
    assert st.avatar_state == "sonreir"  # emoción -> animación base


def test_avatar_arbitraje_ignora_menor_prioridad():
    sm = YueStateManager()
    # La conversación pone al avatar a "hablar".
    assert sm.request_avatar("hablar", Priority.CONVERSATION, source="chat")
    # La música (prioridad menor) intenta ponerlo a "bailar": debe IGNORARSE.
    aplicado = sm.request_avatar("bailar", Priority.MEDIA, source="musica")
    assert aplicado is False
    assert sm.get_state().avatar_state == "hablar"


def test_avatar_arbitraje_mayor_prioridad_gana():
    sm = YueStateManager()
    sm.request_avatar("bailar", Priority.MEDIA, source="musica")
    # El modo profesora (mayor prioridad) sí manda.
    assert sm.request_avatar("explicar", Priority.TEACHER, source="profesora")
    assert sm.get_state().avatar_state == "explicar"


def test_avatar_ttl_se_relaja_con_tick():
    sm = YueStateManager()
    sm.set_emotion("triste", 0.6, source="test")   # base = cabizbaja
    sm.request_avatar("saludar", Priority.USER, source="user", ttl=0.05)
    assert sm.get_state().avatar_state == "saludar"
    time.sleep(0.08)
    sm.tick()  # ya caducó -> vuelve a la emoción base
    assert sm.get_state().avatar_state == "cabizbaja"


def test_estado_notifica_suscriptores():
    sm = YueStateManager()
    vistos = []
    off = sm.subscribe(lambda st: vistos.append(st.emotion_primary))
    sm.set_emotion("enojado", source="test")
    assert "enojado" in vistos
    off()
    sm.set_emotion("feliz", source="test")
    assert "feliz" not in vistos  # tras cancelar, ya no recibe


def test_update_campos_sueltos():
    sm = YueStateManager()
    sm.update(mic_state="escuchando", camera_state="activa", teacher_mode=True)
    st = sm.get_state()
    assert st.mic_state == "escuchando"
    assert st.camera_state == "activa"
    assert st.teacher_mode is True


if __name__ == "__main__":
    fallos = []
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            try:
                _f()
                print("  OK  " + _n)
            except Exception as _e:
                print("  FALLO  " + _n + f"  ·  {_e}")
                fallos.append(_n)
    print("\n" + ("TODO OK" if not fallos else f"FALLOS: {fallos}"))
    sys.exit(1 if fallos else 0)
