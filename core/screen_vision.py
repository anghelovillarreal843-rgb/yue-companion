"""Los ojos de YUE sobre la PANTALLA: decide, captura y pregunta a Gemini.

Este módulo es el cerebro que va ENTRE la orden del usuario y `gemini_vision`.
Su trabajo tiene tres partes:

1. DECIDIR si la orden necesita ojos. Si no los necesita, no se captura nada y
   no se llama a la API: es la optimización más importante, porque evita gastar
   cuota en cada frase del chat.
2. CLASIFICAR qué tipo de mirada hace falta: pantalla normal, documento/PDF,
   vídeo (varios fotogramas) o una imagen concreta.
3. ELEGIR EL CAMINO MÁS EFICIENTE. Si el usuario tiene un PDF abierto y pregunta
   por él, mandamos el ARCHIVO entero (fiel, con todas las páginas) en vez de una
   foto de la ventana. Solo si no localizamos el archivo caemos a la captura.

Todo es ADITIVO: si Gemini no está configurado, `necesita_vision()` sigue
funcionando y quien llama puede usar el camino de siempre (OCR o multimodal
compatible con OpenAI). Nada de esto toca la cámara web.
"""
from __future__ import annotations

import os
import re
import unicodedata


def _norm(texto: str) -> str:
    texto = unicodedata.normalize("NFD", (texto or "").lower().strip())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto)


# ---------------------------------------------------------------------------
# 1) ¿Hace falta mirar? Y si hace falta, ¿mirar QUÉ?
# ---------------------------------------------------------------------------
# Verbos de "mirar" y referencias a lo visible. Se exige que aparezca AMBAS
# cosas o una referencia inequívoca a la pantalla, para no disparar la cámara de
# la API con cualquier frase que lleve "ver".
_RE_VIDEO = re.compile(
    r"\b(video|vídeo|pelicula|peli|serie|clip|escena|animacion|gif|stream|"
    r"transmision|reproduccion|youtube|twitch|netflix)\b"
)
_RE_MOVIMIENTO = re.compile(
    r"\bque (esta|estan) (pasando|haciendo|ocurriendo|sucediendo)\b"
    r"|\bque (ocurre|sucede|pasa) (en|con) (este|ese|el) (video|clip|escena)\b"
    r"|\bdescribe (esta|la) escena\b"
    r"|\bque hace (la|el) (persona|chico|chica|hombre|mujer|gato|perro)\b"
)
_RE_PDF = re.compile(
    r"\b(pdf|documento|informe|articulo|paper|tesis|contrato|manual|libro|"
    r"diapositiva|presentacion|hoja de vida|cv)\b"
)
_RE_LECTURA_DOC = re.compile(
    r"\b(resume|resumen|resumeme|de que trata|de que va|cual es la conclusion|"
    r"conclusiones|busca donde|en que parte|donde habla|lee (esta|la) pagina|"
    r"que dice (ese|este|el) documento)\b"
)
_RE_IMAGEN = re.compile(
    r"\b(imagen|foto|fotografia|grafico|grafica|diagrama|esquema|captura|"
    r"screenshot|dibujo|ilustracion|meme|mapa|tabla)\b"
)
_RE_PANTALLA = re.compile(
    r"\b(pantalla|monitor|escritorio|ventana|ventanas|pestana|pestanas|"
    r"aplicacion|programa|app|navegador|error|codigo|consola|terminal)\b"
)
_RE_VERBO_VER = re.compile(
    r"\b(mira|mirar|miralo|ve|ves|vea|revisa|checa|chequea|observa|fijate|"
    r"analiza|analizame|examina|interpreta|lee|leeme|leelo|describe|"
    r"explica|explicame|dime que|que hay|que ves|que aparece|que dice|"
    r"que muestra|que se ve|echa un vistazo|echa un ojo|identifica)\b"
)
_RE_AQUI = re.compile(r"\b(aqui|aca|esto|esta pantalla|lo que veo|lo que estoy viendo)\b")

# Frases de arranque explícitas: por sí solas ya piden ojos.
_RE_EXPLICITO = re.compile(
    r"\bmira (mi |la )?pantalla\b"
    r"|\bque hay en (mi |la )?pantalla\b"
    r"|\bque ves\b"
    r"|\blee lo que aparece\b"
    r"|\bque error (aparece|hay|tengo|sale)\b"
    r"|\bque aplicacion tengo abierta\b"
    r"|\banaliza (esta|esto|la) (pagina|pantalla|imagen)?\b"
)


def clasificar(texto: str) -> dict:
    """Analiza la orden y devuelve qué mirada hace falta.

    Devuelve {'necesita': bool, 'tipo': 'pantalla'|'pdf'|'video'|'imagen',
              'pregunta': str}. `tipo` solo tiene sentido si `necesita` es True.
    """
    n = _norm(texto)
    resultado = {"necesita": False, "tipo": "pantalla", "pregunta": (texto or "").strip()}
    if not n:
        return resultado

    hay_verbo = bool(_RE_VERBO_VER.search(n))
    hay_referencia = bool(
        _RE_PANTALLA.search(n) or _RE_PDF.search(n) or _RE_IMAGEN.search(n)
        or _RE_VIDEO.search(n) or _RE_AQUI.search(n)
    )
    necesita = bool(
        _RE_EXPLICITO.search(n)
        or _RE_MOVIMIENTO.search(n)
        or (hay_verbo and hay_referencia)
        or (_RE_LECTURA_DOC.search(n) and hay_referencia)
    )
    if not necesita:
        return resultado

    # El orden importa: vídeo gana a imagen, y documento gana a pantalla porque
    # tiene un camino más eficiente (mandar el archivo entero).
    if _RE_VIDEO.search(n) or _RE_MOVIMIENTO.search(n):
        tipo = "video"
    elif _RE_PDF.search(n) or _RE_LECTURA_DOC.search(n):
        tipo = "pdf"
    elif _RE_IMAGEN.search(n) and not _RE_PANTALLA.search(n):
        tipo = "imagen"
    else:
        tipo = "pantalla"

    resultado.update({"necesita": True, "tipo": tipo})
    return resultado


def necesita_vision(texto: str) -> bool:
    """Atajo booleano: ¿esta orden justifica capturar la pantalla?"""
    return bool(clasificar(texto).get("necesita"))


# ---------------------------------------------------------------------------
# 2) Instrucciones para Gemini según el tipo de mirada
# ---------------------------------------------------------------------------
_SISTEMA_BASE = (
    "Eres los ojos de YUE, una asistente de escritorio en español. Recibes "
    "capturas de la PANTALLA del usuario (nunca de su cámara). Describe y "
    "razona SOLO sobre lo que ves de verdad; si algo no se distingue, dilo en "
    "vez de inventarlo. Responde en español neutro, directo y breve: máximo "
    "cuatro frases salvo que te pidan un resumen largo. No enumeres todo lo "
    "visible, responde a lo que te preguntan. Ignora la ventana del propio "
    "avatar de YUE si aparece."
)


def sistema(system_extra: str = "") -> str:
    """Prompt de sistema para la visión. Se puede añadir el de personalidad."""
    return (system_extra.strip() + "\n\n" + _SISTEMA_BASE) if system_extra else _SISTEMA_BASE


def _instruccion(tipo: str, pregunta: str) -> str:
    pregunta = (pregunta or "").strip()
    if tipo == "video":
        base = ("Estás viendo fotogramas seguidos de un vídeo que se reproduce en "
                "la pantalla. Deduce qué ocurre: qué hacen las personas u objetos "
                "y cómo cambia la escena entre fotogramas.")
    elif tipo == "pdf":
        base = ("Estás leyendo un documento. Responde apoyándote en su contenido "
                "real; si citas algo, di en qué página o sección está.")
    elif tipo == "imagen":
        base = ("Estás mirando una imagen que hay en la pantalla (foto, gráfico, "
                "diagrama o interfaz). Explícala de forma útil.")
    else:
        base = ("Estás mirando la pantalla del usuario tal y como está ahora. Si "
                "hay un error, una advertencia o un mensaje importante, cítalo "
                "textualmente.")
    if pregunta:
        return f"{base}\n\nPetición del usuario: «{pregunta}»"
    return base + "\n\nDi lo más relevante que ves ahora mismo."


# ---------------------------------------------------------------------------
# 3) Localizar el documento abierto (camino eficiente para PDFs)
# ---------------------------------------------------------------------------
_EXT_DOC = (".pdf",)
_CARPETAS = ("Downloads", "Descargas", "Documents", "Documentos", "Desktop", "Escritorio")


def documento_abierto() -> str:
    """Ruta del PDF que parece estar abierto en primer plano, o "".

    Estrategia, de más barata a más cara:
      1. El título de la ventana activa suele ser el nombre del archivo
         ("informe.pdf - Adobe Acrobat", "informe - Chrome").
      2. Se busca ese nombre en las carpetas típicas del usuario.
    Nunca lanza: si no lo encuentra, devuelve "" y quien llama usa la captura.
    """
    titulo = ""
    try:
        from core import desktop_ui
        titulo = str(desktop_ui.active_window_title() or "")
    except Exception:
        titulo = ""
    if not titulo:
        return ""

    # ¿El título ya trae una ruta completa? (algunos visores la muestran)
    try:
        from teacher import documents as _docs
        candidato = _docs.find_source(titulo)
        if candidato and os.path.isfile(candidato) and candidato.lower().endswith(_EXT_DOC):
            return candidato
    except Exception:
        pass

    nombre = _nombre_desde_titulo(titulo)
    if not nombre:
        return ""
    return _buscar_en_carpetas(nombre)


def _nombre_desde_titulo(titulo: str) -> str:
    """Saca el nombre de archivo probable del título de una ventana."""
    limpio = titulo.strip()
    # Los visores separan con " - " o " — " el archivo del nombre del programa.
    for sep in (" - ", " — ", " – ", " | "):
        if sep in limpio:
            partes = [p.strip() for p in limpio.split(sep) if p.strip()]
            if partes:
                # El nombre del archivo suele ir primero; si trae extensión, mejor.
                con_ext = [p for p in partes if p.lower().endswith(_EXT_DOC)]
                limpio = con_ext[0] if con_ext else partes[0]
            break
    limpio = re.sub(r"^\(\d+\)\s*", "", limpio).strip()          # "(3) archivo.pdf"
    limpio = re.sub(r"^[•\*]\s*", "", limpio).strip()             # marca de "sin guardar"
    if not limpio or len(limpio) > 160:
        return ""
    return limpio


def _buscar_en_carpetas(nombre: str) -> str:
    """Busca un PDF cuyo nombre case con el del título, en carpetas del usuario."""
    base = nombre[:-4] if nombre.lower().endswith(".pdf") else nombre
    base_n = _norm(base)
    if len(base_n) < 3:
        return ""
    hogar = os.path.expanduser("~")
    for carpeta in _CARPETAS:
        ruta = os.path.join(hogar, carpeta)
        if not os.path.isdir(ruta):
            continue
        try:
            for entrada in os.scandir(ruta):
                if not entrada.is_file():
                    continue
                if not entrada.name.lower().endswith(_EXT_DOC):
                    continue
                if _norm(os.path.splitext(entrada.name)[0]) == base_n:
                    return entrada.path
        except Exception:
            continue
    return ""


# ---------------------------------------------------------------------------
# 4) Ejecución: el punto de entrada que usa main.py
# ---------------------------------------------------------------------------
def mirar(texto_usuario: str = "", system_extra: str = "", tipo: str = "") -> dict:
    """Mira lo que haga falta y devuelve {'texto', 'tipo', 'fuente'}.

    `tipo` fuerza el camino ("pantalla" / "pdf" / "video" / "imagen"); si va
    vacío, se deduce de la orden. Lanza `GeminiVisionError` si la visión falla,
    para que quien llama pueda decidir si degrada a OCR.
    """
    from core import gemini_vision

    info = clasificar(texto_usuario)
    tipo = (tipo or info["tipo"] or "pantalla").lower()
    pregunta = info["pregunta"]
    sys_prompt = sistema(system_extra)
    instruccion = _instruccion(tipo, pregunta)

    if tipo == "video":
        texto = gemini_vision.analizar_video_pantalla(
            instruccion, system=sys_prompt,
            fotogramas=_int_cfg("VISION_VIDEO_FRAMES", 4),
            intervalo=_float_cfg("VISION_VIDEO_INTERVAL", 1.5),
            max_width=_int_cfg("VISION_VIDEO_MAX_WIDTH", 1100),
        )
        return {"texto": texto, "tipo": "video", "fuente": "pantalla (fotogramas)"}

    if tipo == "pdf":
        ruta = documento_abierto()
        if ruta:
            try:
                texto = gemini_vision.analizar_pdf(ruta, instruccion, system=sys_prompt)
                return {"texto": texto, "tipo": "pdf",
                        "fuente": f"archivo: {os.path.basename(ruta)}"}
            except gemini_vision.GeminiVisionError as exc:
                # El archivo existía pero no se pudo mandar entero: seguimos con
                # la captura, que casi siempre basta para "¿qué dice esta página?".
                print("[vision-pantalla] el PDF no se pudo enviar entero:", exc)

    texto = gemini_vision.analizar_pantalla(
        instruccion, system=sys_prompt,
        max_width=_int_cfg("VISION_SCREEN_MAX_WIDTH", 1600),
        quality=_int_cfg("VISION_SCREEN_QUALITY", 78),
    )
    return {"texto": texto, "tipo": tipo, "fuente": "captura de pantalla"}


def mirar_archivo(ruta: str, texto_usuario: str = "", system_extra: str = "") -> dict:
    """Analiza un archivo concreto que el usuario indicó (PDF o imagen)."""
    from core import gemini_vision

    if not ruta or not os.path.isfile(ruta):
        raise gemini_vision.GeminiVisionError(
            f"No encuentro el archivo «{ruta}».", kind="formato",
            hablado="No encuentro ese archivo.")
    es_pdf = ruta.lower().endswith(".pdf")
    instruccion = _instruccion("pdf" if es_pdf else "imagen", texto_usuario)
    sys_prompt = sistema(system_extra)
    if es_pdf:
        texto = gemini_vision.analizar_pdf(ruta, instruccion, system=sys_prompt)
    else:
        texto = gemini_vision.analizar_imagen_archivo(ruta, instruccion, system=sys_prompt)
    return {"texto": texto, "tipo": "pdf" if es_pdf else "imagen",
            "fuente": f"archivo: {os.path.basename(ruta)}"}


# ---------------------------------------------------------------------------
# Ayudas de configuración (tolerantes, como el resto de YUE)
# ---------------------------------------------------------------------------
def _int_cfg(nombre: str, defecto: int) -> int:
    try:
        import config
        return int(getattr(config, nombre, defecto))
    except Exception:
        try:
            return int(float(os.getenv(nombre, defecto)))
        except Exception:
            return defecto


def _float_cfg(nombre: str, defecto: float) -> float:
    try:
        import config
        return float(getattr(config, nombre, defecto))
    except Exception:
        try:
            return float(os.getenv(nombre, defecto))
        except Exception:
            return defecto
