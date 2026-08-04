"""Buscar en YouTube y quedarse con el vídeo, no con la lista de resultados.

Módulo ADITIVO. El problema: abrir
`https://www.youtube.com/results?search_query=...` deja al usuario mirando una
lista; nadie ha reproducido nada. Para REPRODUCIR hace falta el id del vídeo
(`/watch?v=XXXXXXXXXXX`), y eso normalmente exige la API de datos de Google con
su clave y su cuota.

Se puede sin clave: la página de resultados trae los ids incrustados en un JSON
dentro del propio HTML. Se pide la página, se saca el primer `videoId` y se abre
la URL de reproducción. Si YouTube cambia el formato o no hay internet, se cae
con elegancia a la página de resultados de siempre: peor, pero nunca roto.

Solo usa `requests`, que ya está en requirements.txt.
"""
from __future__ import annotations

import re
import urllib.parse

from core import activity

# NUEVO (comentario con contexto): buzón de "qué suena ahora" para que
# system_audio pueda comentar sabiendo el título. Aditivo y degradable: si el
# módulo no está, resolver sigue funcionando igual que antes.
try:
    from core import now_playing
except Exception:  # pragma: no cover - respaldo si el módulo falta
    now_playing = None

# 11 caracteres exactos: el formato de id de YouTube desde siempre.
_RE_VIDEO_ID = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')
# Los Shorts y los anuncios también traen videoId; estos marcadores ayudan a
# reconocer un resultado de vídeo normal.
_CABECERAS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}


def url_busqueda(consulta: str) -> str:
    """Página de resultados (el respaldo de toda la vida)."""
    return ("https://www.youtube.com/results?search_query="
            + urllib.parse.quote_plus(consulta.strip()))


def url_video(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def primer_video_id(consulta: str, timeout: float = 8.0) -> str:
    """Id del primer vídeo de la búsqueda, o "" si no se puede averiguar."""
    consulta = (consulta or "").strip()
    if not consulta:
        return ""
    try:
        import requests
        resp = requests.get(url_busqueda(consulta), headers=_CABECERAS, timeout=timeout)
        resp.raise_for_status()
        ids = _RE_VIDEO_ID.findall(resp.text)
    except Exception as exc:
        print("[youtube] no pude resolver el vídeo:", exc)
        return ""
    if not ids:
        print("[youtube] la página no traía ningún id (¿cambió el formato?)")
        return ""
    # El primero repetido suele ser el resultado real; los sueltos del final
    # son recomendaciones de la barra lateral.
    return ids[0]


def resolver(consulta: str, timeout: float = 8.0) -> tuple[str, bool]:
    """(url, es_reproducible).

    es_reproducible=True -> se abrirá el vídeo directamente.
    es_reproducible=False -> solo pudimos dejar la búsqueda hecha.
    """
    vid = primer_video_id(consulta, timeout=timeout)
    breve = (consulta or "").strip()[:80]
    if vid:
        activity.log("video", f"puse un video: {breve}", origen="youtube")
        # NUEVO: solo cuando SÍ vamos a reproducir algo, dejamos el título en el
        # buzón para que YUE lo comente sabiendo qué suena. En una búsqueda sin
        # reproducción no suena nada, así que no tocamos el buzón.
        if now_playing is not None:
            try:
                now_playing.set_now_playing(breve)
            except Exception:
                pass
        return (url_video(vid), True)
    activity.log("video", f"busqué un video: {breve}", origen="youtube")
    return (url_busqueda(consulta), False)
