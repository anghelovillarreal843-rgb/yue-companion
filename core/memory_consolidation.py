"""Consolidación de memoria a largo plazo: comprime el historial VIEJO en
resúmenes cortos y persistentes que vuelven a entrar al prompt.

NUEVO · Esta capa es también el punto donde arranca la MEMORIA NARRATIVA
(`core/story_memory.py`): al final de `consolidate()` se llama a
`consolidate_stories()`, que hace evolucionar las historias persistentes. Son
dos cosas distintas y COEXISTEN: `memoria_larga` sigue guardando el resumen
histórico difuso de siempre, y Story Memory guarda los hilos concretos de su
vida. Ni se sustituyen ni se pisan.

El problema: hoy el prompt se arma solo con lo reciente (12 mensajes crudos, 20
hechos manuales, ánimo/actividad de 7 días). Pasado ese horizonte, todo sigue en
SQLite pero YUE no vuelve a leerlo: a escala de meses/años su memoria es casi
inexistente. Esta capa NO toca lo que ya funciona; se suma encima.

Filosofía igual que el resto del proyecto: ADITIVO y BEST-EFFORT. Si no hay clave
de IA, si la llamada falla o tarda, no consolida esta vez y lo reintenta la
próxima. Nunca borra mensajes ni hechos: la consolidación es un resumen ADICIONAL,
no una migración destructiva. No corre en el hilo de UI (main.py la lanza en un
hilo de fondo).
"""
from __future__ import annotations

import time

try:
    import config
except Exception:  # pragma: no cover
    config = None


_ESTADO_KEY = "ultima_consolidacion"
_MAX_MENSAJES = 300      # tope de mensajes que rendimos al LLM (los más recientes)
_MAX_CHARS = 6000        # tope de caracteres del bloque de conversación
_TIMEOUT_DEF = 20.0      # segundos para la llamada al LLM (un resumen, no un chat)


def _cfg(nombre: str, defecto):
    return getattr(config, nombre, defecto) if config is not None else defecto


def _dias() -> float:
    try:
        return float(_cfg("MEMORY_CONSOLIDATION_DAYS", 7))
    except Exception:
        return 7.0


def _render_conversacion(mensajes: list[dict]) -> str:
    """Convierte los mensajes del periodo en un bloque de texto acotado.

    Tomamos los MÁS RECIENTES del periodo (los últimos son los más informativos
    del arco) y cortamos por longitud para no mandar un prompt gigantesco.
    """
    trozos = []
    total = 0
    for m in reversed(mensajes[-_MAX_MENSAJES:]):
        rol = "Tú" if m.get("role") == "user" else "YUE"
        contenido = str(m.get("content", "")).strip().replace("\n", " ")
        if not contenido:
            continue
        if len(contenido) > 300:
            contenido = contenido[:300] + "…"
        linea = f"{rol}: {contenido}"
        total += len(linea)
        if total > _MAX_CHARS:
            break
        trozos.append(linea)
    trozos.reverse()  # volver a orden cronológico
    return "\n".join(trozos)


def _parsear(texto: str) -> tuple[str, list[str]]:
    """Separa RESUMEN y HECHOS del texto del LLM. Robusto a que falten marcas."""
    resumen = (texto or "").strip()
    hechos: list[str] = []
    bajo = resumen.lower()
    if "hechos:" in bajo:
        idx = bajo.rindex("hechos:")
        bloque_hechos = resumen[idx + len("hechos:"):].strip()
        resumen = resumen[:idx].strip()
        if bloque_hechos and bloque_hechos not in ("-", "—", "ninguno", "n/a"):
            for h in bloque_hechos.replace("\n", "|").split("|"):
                h = h.strip(" -–—•\t").strip()
                if h and h.lower() not in ("ninguno", "n/a", "-"):
                    hechos.append(h)
    # Quitar una posible etiqueta "RESUMEN:" al principio.
    for etiqueta in ("resumen:", "resumen :"):
        if resumen.lower().startswith(etiqueta):
            resumen = resumen[len(etiqueta):].strip()
            break
    return resumen, hechos[:2]


def _guardar_hechos_auto(memory, hechos: list[str]) -> None:
    """Añade 1-2 hechos estables detectados, evitando duplicar los existentes.
    Best-effort: cualquier fallo aquí NO afecta al resumen ya guardado."""
    if not hechos:
        return
    try:
        existentes = {f.strip().lower() for f in memory.get_facts(50)}
    except Exception:
        existentes = set()
    for h in hechos:
        if len(h) < 3 or len(h) > 160:
            continue
        if h.strip().lower() in existentes:
            continue
        try:
            memory.add_fact(h)
            existentes.add(h.strip().lower())
        except Exception:
            pass


def consolidate(memory, ai_engine) -> str | None:
    """Consolida el historial viejo en un resumen persistente. Devuelve el resumen
    guardado, o None si esta vez no tocaba / no se pudo (sin romper nada).

    Pasos: (a) ¿pasó suficiente tiempo?; (b) traer mensajes del periodo; (c) pedir
    al LLM un resumen de 3-5 líneas (y 0-2 hechos estables); (d) guardar el resumen,
    los hechos y actualizar la marca de última consolidación.
    """
    dias = _dias()
    if dias <= 0:
        return None  # desactivado por configuración

    ahora = time.time()

    # (a) ¿ya pasó suficiente tiempo desde la última consolidación?
    ts_inicio = 0.0
    try:
        ultima = memory.get_state(_ESTADO_KEY)
    except Exception:
        ultima = None
    if ultima:
        try:
            ts_inicio = float(ultima)
        except (TypeError, ValueError):
            ts_inicio = 0.0
        if (ahora - ts_inicio) < dias * 86400.0:
            return None  # aún no toca

    # Sin motor o sin clave: no consolida esta vez (se reintenta la próxima).
    if ai_engine is None or not getattr(ai_engine, "api_key", None):
        return None

    # (b) mensajes del periodo [ts_inicio, ahora].
    try:
        mensajes = memory.messages_between(ts_inicio, ahora)
    except Exception:
        return None
    if not mensajes:
        return None  # nada que resumir; no tocamos el estado, reintenta luego

    conversacion = _render_conversacion(mensajes)
    if not conversacion.strip():
        return None

    # (c) resumen vía LLM (mismo motor que el resto del proyecto).
    prompt = (
        "Resume esta conversación entre un usuario y su IA compañera YUE para su "
        "memoria a largo plazo. Escribe en español, 3-5 líneas: temas recurrentes, "
        "tono general y cualquier hecho notable. NO transcribas literalmente ni "
        "incluyas datos sensibles innecesarios. Después, si detectas 1-2 datos "
        "ESTABLES sobre el usuario (nombre, una preferencia duradera…), añádelos.\n\n"
        "Devuelve exactamente este formato:\n"
        "RESUMEN: <3-5 líneas>\n"
        "HECHOS: <0-2 datos estables separados por ' | ', o '-' si no hay>\n\n"
        "Conversación:\n" + conversacion
    )
    timeout = float(_cfg("MEMORY_CONSOLIDATION_TIMEOUT", _TIMEOUT_DEF))
    try:
        salida = ai_engine.chat([{"role": "user", "content": prompt}], timeout=timeout)
    except Exception:
        return None  # falló/tardó: no guarda, no toca estado -> reintenta luego

    resumen, hechos = _parsear(salida)
    if not resumen or len(resumen) < 8:
        return None  # respuesta vacía/rara: mejor reintentar que guardar basura

    # (d) persistir el resumen, los hechos automáticos y la marca de tiempo.
    try:
        memory.add_long_term_summary(ts_inicio, ahora, resumen)
    except Exception:
        return None  # si ni siquiera podemos guardar, no marcamos como hecho
    _guardar_hechos_auto(memory, hechos)
    try:
        memory.set_state(_ESTADO_KEY, ahora)
    except Exception:
        pass  # el resumen ya está guardado; a lo sumo se reintentará antes de tiempo

    # (e) NUEVO · MEMORIA NARRATIVA. Aprovechamos que ya toca consolidar para
    # hacer evolucionar las HISTORIAS (core/story_memory.py). Es una capa
    # aparte y ADITIVA: el resumen de arriba se guarda igual, con o sin esto.
    # Si Story Memory está desactivada, falla o ni siquiera existe el módulo,
    # esta función devuelve exactamente lo mismo que devolvía antes.
    consolidate_stories(memory, ai_engine)
    return resumen


def consolidate_stories(memory, ai_engine, *, force: bool = False) -> dict | None:
    """Hace evolucionar las historias persistentes. Best-effort y opcional.

    Vive aquí para que main.py siga teniendo UN solo punto de entrada de
    consolidación, pero la lógica entera está en `core/story_memory.py`: este
    módulo NO se convierte en un monolito.

    Devuelve el informe de la consolidación narrativa, o None si no se pudo.
    Nunca lanza: un problema con las historias no puede impedir que la memoria
    a largo plazo de siempre siga funcionando.
    """
    if not _cfg("STORY_MEMORY_ENABLED", True):
        return None
    try:
        from core.story_memory import StoryMemory
    except Exception as exc:  # pragma: no cover - módulo ausente
        print("[story-memory] no disponible:", exc)
        return None
    try:
        capa = StoryMemory(memory=memory, engine=ai_engine)
        return capa.consolidate(force=force)
    except Exception as exc:
        print("[story-memory] la consolidación narrativa falló:", exc)
        return None
