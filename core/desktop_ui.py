"""Puente con la UI real del escritorio (pywinauto, backend "uia").

Este módulo es ADITIVO: no reemplaza nada de core/pc_control.py, solo le da
acceso a los controles y ventanas REALES del sistema para que Yue deje de
depender de coordenadas porcentuales inventadas por el modelo de visión.

Todo está pensado para degradar con elegancia: si pywinauto no está instalado
o el sistema no es Windows, las funciones devuelven listas vacías o None y el
controlador sigue usando el camino visual de siempre.
"""
from __future__ import annotations

import platform
import re
import threading
import unicodedata
from difflib import SequenceMatcher

import config

# Cache muy corto de la ventana activa: enumerar UIA es caro (200-600 ms) y en
# un mismo ciclo se consulta varias veces.
_CACHE_TTL = 1.2
_cache_lock = threading.Lock()
_cache: dict = {"stamp": 0.0, "elements": [], "window": ""}

# Ventanas del sistema que NUNCA se cierran, ni con coincidencia exacta.
_SYSTEM_WINDOWS = {
    "program manager", "task manager", "administrador de tareas",
    "windows security", "seguridad de windows", "windows defender",
    "configuracion", "settings", "panel de control", "control panel",
    "explorador de archivos", "start", "inicio", "cortana", "search",
    "buscar", "shell handwriting canvas", "microsoft text input application",
    "yue", "yue companion", "yuecompanion",   # norm() elimina el guion bajo
    "yue · chat", "yue · nota", "yue · burbuja",
}
_SYSTEM_CLASSES = {
    "Shell_TrayWnd", "Progman", "WorkerW", "Windows.UI.Core.CoreWindow",
    "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
}


def norm(text: str) -> str:
    """Minúsculas, sin tildes y sin espacios repetidos."""
    text = (text or "").strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[&_]", "", text)          # aceleradores de menú: "&Archivo"
    # Windows anexa el atajo al nombre: "Explorer (Ctrl+Shift+E)" -> "explorer".
    text = re.sub(r"\s*\((?:ctrl|alt|shift|f\d)[^)]*\)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a: str, b: str) -> float:
    """Parecido difuso entre dos títulos/nombres (0.0 a 1.0).

    La contención solo cuenta si es de PALABRA COMPLETA y proporcional: si no,
    «I» estaría contenida en «Siete» y clicaríamos cualquier cosa (bug real).
    """
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # Nombres de 1-2 caracteres: solo valen si son idénticos.
    if min(len(a), len(b)) <= 2:
        return 0.0
    corto, largo = (a, b) if len(a) <= len(b) else (b, a)
    # "guardar" dentro de "guardar como..." sí; "i" dentro de "siete" no.
    if re.search(rf"(?<!\w){re.escape(corto)}(?!\w)", largo):
        return round(0.62 + 0.32 * (len(corto) / len(largo)), 4)
    valor = SequenceMatcher(None, a, b).ratio()
    # Dos controles que ni empiezan igual casi nunca son el mismo ("File"/"Profile").
    if a[0] != b[0]:
        valor *= 0.9
    return round(valor, 4)


def is_windows() -> bool:
    return platform.system().lower() == "windows"


def available() -> bool:
    """True si podemos usar pywinauto con backend uia."""
    if not is_windows():
        return False
    try:
        import pywinauto  # noqa: F401
        return True
    except Exception:
        return False


def _desktop():
    from pywinauto import Desktop
    return Desktop(backend="uia")


# ---------------------------------------------------------------- ventanas
def list_windows(limit: int = 25) -> list[dict]:
    """Ventanas visibles con título: [{title, class_name, active, rect}]."""
    if not is_windows():
        return []
    windows: list[dict] = []
    try:
        active_title = _active_title_win32()
        for win in _desktop().windows(visible_only=True, enabled_only=True):
            try:
                title = (win.window_text() or "").strip()
                if not title:
                    continue
                rect = win.rectangle()
                if rect.width() < 40 or rect.height() < 40:
                    continue
                windows.append({
                    "title": title[:120],
                    "class_name": _safe(win.class_name),
                    "active": norm(title) == norm(active_title),
                    "rect": [rect.left, rect.top, rect.right, rect.bottom],
                    # El handle es unico; el titulo NO (la Calculadora expone 2).
                    "handle": int(getattr(win, "handle", 0) or 0),
                })
            except Exception:
                continue
            if len(windows) >= limit:
                break
    except Exception as exc:
        print("[desktop-ui] no pude listar ventanas con uia:", exc)
        return _list_windows_fallback(limit)
    return windows or _list_windows_fallback(limit)


def _list_windows_fallback(limit: int = 25) -> list[dict]:
    """Respaldo con pygetwindow cuando pywinauto no está disponible."""
    try:
        import pygetwindow as gw
    except Exception:
        return []
    out: list[dict] = []
    try:
        active = gw.getActiveWindow()
        active_title = active.title if active else ""
        for win in gw.getAllWindows():
            title = (win.title or "").strip()
            if not title or win.width < 40 or win.height < 40:
                continue
            out.append({
                "title": title[:120],
                "class_name": "",
                "active": title == active_title,
                "rect": [win.left, win.top, win.left + win.width, win.top + win.height],
                "handle": int(getattr(win, "_hWnd", 0) or 0),
            })
            if len(out) >= limit:
                break
    except Exception as exc:
        print("[desktop-ui] respaldo pygetwindow falló:", exc)
    return out


def _safe(getter) -> str:
    try:
        return str(getter() or "")
    except Exception:
        return ""


def _active_title_win32() -> str:
    try:
        import win32gui
        return win32gui.GetWindowText(win32gui.GetForegroundWindow()) or ""
    except Exception:
        pass
    try:
        import pygetwindow as gw
        win = gw.getActiveWindow()
        return (win.title if win else "") or ""
    except Exception:
        return ""


def active_window_title() -> str:
    return _active_title_win32()


def focus_window(title: str) -> str:
    """Trae al frente la ventana cuyo título más se parezca. Devuelve el título real."""
    if not title:
        raise RuntimeError("focus_window necesita un título.")
    if not is_windows():
        raise RuntimeError("focus_window solo está disponible en Windows.")
    best, score = None, 0.0
    for win in list_windows(limit=40):
        value = similarity(title, win["title"])
        if value > score:
            best, score = win, value
    if not best or score < 0.55:
        raise RuntimeError(f"No encontré una ventana parecida a «{title}».")
    real = best["title"]
    if _focus_by_handle(best.get("handle", 0), real):
        _invalidate_cache()
        return real
    raise RuntimeError(f"No pude enfocar «{real}».")


def _spec(handle: int, title: str):
    """Especificación de ventana por handle (único) y, si no hay, por título.

    Buscar por título revienta cuando hay varias iguales ("There are 2 elements
    that match the criteria"), que es justo lo que pasa con la Calculadora.
    """
    if handle:
        return _desktop().window(handle=handle)
    return _desktop().window(title=title, found_index=0)


def _focus_by_handle(handle: int, real_title: str) -> bool:
    try:
        win = _spec(handle, real_title)
        try:
            if win.is_minimized():
                win.restore()
        except Exception:
            pass
        win.set_focus()
        return True
    except Exception as exc:
        print("[desktop-ui] set_focus falló, intento con win32:", exc)
    # Respaldo directo con win32: no depende de títulos ni de UIA.
    try:
        import win32con
        import win32gui
        if handle:
            if win32gui.IsIconic(handle):
                win32gui.ShowWindow(handle, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(handle)
            return True
    except Exception as exc:
        print("[desktop-ui] SetForegroundWindow falló:", exc)
    try:
        import pygetwindow as gw
        for win in gw.getWindowsWithTitle(real_title):
            if win.isMinimized:
                win.restore()
            win.activate()
            return True
    except Exception as exc:
        print("[desktop-ui] activate falló:", exc)
    return False


def close_window(title: str) -> str:
    """Cierra una ventana SOLO con coincidencia exacta y nunca del sistema."""
    if not title:
        raise RuntimeError("close_window necesita un título.")
    if not is_windows():
        raise RuntimeError("close_window solo está disponible en Windows.")
    wanted = norm(title)
    if wanted in _SYSTEM_WINDOWS:
        raise RuntimeError(f"No cierro ventanas del sistema: «{title}».")
    match = None
    candidates = []
    for win in list_windows(limit=40):
        current = norm(win["title"])
        if current == wanted:
            match = win
            break
        value = similarity(title, win["title"])
        if value >= 0.78:
            candidates.append((value, win))
    if match is None and candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        if len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= 0.08:
            match = candidates[0][1]
    if not match:
        raise RuntimeError(
            f"No encontré una ventana suficientemente parecida a «{title}» para cerrarla."
        )
    if match.get("class_name") in _SYSTEM_CLASSES:
        raise RuntimeError(f"«{match['title']}» es una ventana del sistema; no la cierro.")
    try:
        _spec(match.get("handle", 0), match["title"]).close()
    except Exception as exc:
        # Respaldo: mensaje WM_CLOSE directo al handle.
        try:
            import win32con
            import win32gui
            if match.get("handle"):
                win32gui.PostMessage(match["handle"], win32con.WM_CLOSE, 0, 0)
            else:
                raise exc
        except Exception:
            raise RuntimeError(f"No pude cerrar «{match['title']}»: {exc}") from exc
    _invalidate_cache()
    return match["title"]


# ---------------------------------------------------------------- controles
def _invalidate_cache():
    with _cache_lock:
        _cache["stamp"] = 0.0


def active_window_elements(limit: int = 40, use_cache: bool = True) -> list[dict]:
    """Hasta `limit` controles visibles de la ventana activa.

    Cada elemento: {name, control_type, rect:[l,t,r,b], center:[x,y]}.
    """
    import time as _time

    if not available():
        return []
    with _cache_lock:
        fresh = _time.time() - _cache["stamp"] < _CACHE_TTL
        if use_cache and fresh and _cache["elements"]:
            return list(_cache["elements"])[:limit]

    elements: list[dict] = []
    window_title = ""
    try:
        from pywinauto import Desktop
        # Si el chat o el avatar de Yue tienen el foco, `active_only=True`
        # devolvería los controles de Yue: por eso se busca la ventana real.
        objetivo = 0
        try:
            objetivo = target_hwnd()
        except Exception:
            objetivo = 0
        if objetivo:
            win = Desktop(backend="uia").window(handle=objetivo)
        else:
            win = Desktop(backend="uia").window(active_only=True)
        window_title = _safe(win.window_text)
        for ctrl in win.descendants():
            try:
                if not ctrl.is_visible() or not ctrl.is_enabled():
                    continue
                name = (ctrl.window_text() or "").strip()
                ctype = _safe(lambda: ctrl.element_info.control_type)
                if not name and ctype not in {"Edit", "Document", "ComboBox"}:
                    continue
                rect = ctrl.rectangle()
                if rect.width() <= 0 or rect.height() <= 0:
                    continue
                elements.append({
                    "name": name[:80],
                    "control_type": ctype[:30],
                    "rect": [rect.left, rect.top, rect.right, rect.bottom],
                    "center": [rect.mid_point().x, rect.mid_point().y],
                })
            except Exception:
                continue
            if len(elements) >= max(limit, 60):
                break
    except Exception as exc:
        print("[desktop-ui] no pude leer los controles de la ventana activa:", exc)
        return []

    with _cache_lock:
        _cache["stamp"] = _time.time()
        _cache["elements"] = elements
        _cache["window"] = window_title
    return elements[:limit]


def find_element_center(
    name: str,
    control_type: str | None = None,
    threshold: float = 0.75,
) -> tuple[int, int, str] | None:
    """Centro REAL del control cuyo nombre se parezca más. (x, y, nombre_real)."""
    if not name:
        return None
    candidates = active_window_elements(limit=200, use_cache=False)
    best, score = None, 0.0
    for item in candidates:
        if control_type and norm(control_type) != norm(item.get("control_type", "")):
            continue
        value = similarity(name, item.get("name", ""))
        # Los botones y elementos clicables ganan un pequeño desempate.
        if item.get("control_type") in {"Button", "MenuItem", "TabItem", "ListItem", "Hyperlink"}:
            value += 0.03
        if value > score:
            best, score = item, value
    if not best or score < threshold:
        return None
    x, y = best["center"]
    return int(x), int(y), best.get("name", "")


def own_window_rects() -> list[list[int]]:
    """Rectángulos de las ventanas de la propia Yue (avatar, chat, burbujas).

    El avatar VRM parpadea y respira: si no lo excluimos de la comparación, la
    pantalla siempre parece haber cambiada y la verificación por acción miente.

    Se identifican por PID del proceso, no por título: las ventanas de Yue son
    frameless y NO tienen título, así que buscarlas por nombre no encontraría nada.
    """
    rects = _own_rects_by_pid()
    if rects:
        return rects
    return _own_rects_by_title()


def _own_rects_by_pid() -> list[list[int]]:
    """Ventanas de nuestro propio proceso (método fiable, vía win32)."""
    if not is_windows():
        return []
    try:
        import os
        import win32gui
        import win32process
    except Exception:
        return []

    propio = os.getpid()
    rects: list[list[int]] = []

    def _visitar(hwnd, _param):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid != propio:
                return True
            izq, arr, der, aba = win32gui.GetWindowRect(hwnd)
            if der - izq > 8 and aba - arr > 8:
                rects.append([izq, arr, der, aba])
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_visitar, None)
    except Exception as exc:
        print("[desktop-ui] no pude enumerar mis propias ventanas:", exc)
        return []
    return rects


def _own_rects_by_title() -> list[list[int]]:
    """Respaldo por título, por si win32gui no está disponible."""
    titulos = tuple(
        norm(t) for t in getattr(config, "PC_VERIFY_IGNORE_TITLES", ("yue_companion", "yue"))
        if str(t).strip()
    )
    if not titulos:
        return []
    rects: list[list[int]] = []
    try:
        for win in list_windows(limit=40):
            titulo = norm(win.get("title", ""))
            if any(titulo == t or titulo.startswith(t + " ") for t in titulos):
                rect = win.get("rect")
                if rect:
                    rects.append(list(rect))
    except Exception as exc:
        print("[desktop-ui] no pude ubicar las ventanas propias:", exc)
    return rects


def describe_windows(windows: list[dict]) -> str:
    """Resumen corto de ventanas para meterlo en el historial del planificador."""
    if not windows:
        return "(no pude leer las ventanas abiertas)"
    partes = []
    for win in windows[:12]:
        marca = " [ACTIVA]" if win.get("active") else ""
        partes.append(f"{win.get('title', '')}{marca}")
    return " | ".join(partes)


def focused_element_name() -> tuple[str, str]:
    """(nombre, control_type) del elemento que tiene el foco de teclado.

    Hacer clic dentro de un cuadro de texto vacío solo dibuja un cursor que
    parpadea: son cuatro píxeles y la comparación de pantalla no los ve, así que
    el clic se marcaba como «sin efecto visible» aunque hubiera funcionado.
    Preguntarle a UIA quién tiene el foco sí lo confirma.
    """
    if not available():
        return ("", "")
    try:
        from pywinauto.uia_defines import IUIA
        elemento = IUIA().iuia.GetFocusedElement()
        nombre = (elemento.CurrentName or "").strip()
        try:
            from pywinauto.uia_defines import IUIA as _I
            tipo = _I().known_control_types.get(elemento.CurrentControlType, "")
        except Exception:
            tipo = ""
        return (nombre[:80], str(tipo)[:30])
    except Exception:
        return ("", "")


def element_has_focus(name: str, threshold: float = 0.75) -> bool:
    """¿El control que pedimos es el que ahora tiene el foco de teclado?"""
    if not name:
        return False
    foco, _tipo = focused_element_name()
    if not foco:
        return False
    return similarity(name, foco) >= threshold


# ======================================================================
# Las ventanas de la propia Yue estorban al mouse (aditivo)
# ======================================================================
# El avatar y el chat son ventanas layered SIEMPRE ENCIMA. Cuando Yue mueve el
# mouse para controlar el PC se choca consigo misma: el clic aterriza en su
# propio avatar, o su chat roba el foco y entonces `active_window_elements` lee
# los controles de Yue en vez de los de la Calculadora. Eso es lo que hace que
# un botón "desaparezca" a mitad de una orden.
_EXTRA_TRANSPARENTE = 0x20        # WS_EX_TRANSPARENT: el mouse la atraviesa
_GWL_EXSTYLE = -20
_estilos_previos: dict[int, int] = {}


def own_hwnds() -> list[int]:
    """Handles de las ventanas visibles de nuestro propio proceso."""
    if not is_windows():
        return []
    try:
        import os
        import win32gui
        import win32process
    except Exception:
        return []
    propio = os.getpid()
    handles: list[int] = []

    def _visitar(hwnd, _param):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == propio:
                handles.append(hwnd)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_visitar, None)
    except Exception:
        return []
    return handles


def _aplicar_click_through(activar: bool) -> int:
    """Cambia el estilo de verdad. SOLO puede correr en el hilo dueño de la UI."""
    try:
        import win32con
        import win32gui
    except Exception:
        return 0
    cambiadas = 0
    for hwnd in own_hwnds():
        try:
            actual = win32gui.GetWindowLong(hwnd, _GWL_EXSTYLE)
            if activar:
                _estilos_previos.setdefault(hwnd, actual)
                nuevo = actual | _EXTRA_TRANSPARENTE | win32con.WS_EX_LAYERED
            else:
                # Se restaura el estilo EXACTO que tenia, no uno "parecido".
                nuevo = _estilos_previos.pop(hwnd, actual & ~_EXTRA_TRANSPARENTE)
            if nuevo != actual:
                win32gui.SetWindowLong(hwnd, _GWL_EXSTYLE, nuevo)
                cambiadas += 1
        except Exception:
            continue
    return cambiadas


def set_click_through(activar: bool) -> int:
    """Hace que las ventanas de Yue dejen pasar el mouse (o lo revierte).

    OJO, esto colgo la app una vez: `SetWindowLong` sobre una ventana de OTRO
    hilo hace que Windows le mande WM_STYLECHANGED con SendMessage y bloquea al
    que llama hasta que el hilo dueno conteste. `execute()` corre en PCWorker
    (un QThread), asi que llamarlo directo era un abrazo mortal con la interfaz.

    Por eso el cambio se encola SIEMPRE en el hilo de Qt via core.ui_bridge, con
    limite de tiempo. Si el puente no esta o la interfaz no contesta, se sigue
    sin el efecto: Yue chocara con su avatar, que es molesto pero no mortal.
    """
    if not is_windows():
        return 0
    if not getattr(config, "PC_CLICK_THROUGH", True):
        return 0
    try:
        from core import ui_bridge
    except Exception:
        return 0
    if not ui_bridge.disponible():
        return 0
    resultado = ui_bridge.run_on_ui_thread(
        lambda: _aplicar_click_through(activar),
        timeout=float(getattr(config, "PC_CLICK_THROUGH_TIMEOUT", 1.0)),
    )
    return int(resultado or 0)


def _is_own_hwnd(hwnd: int) -> bool:
    try:
        import os
        import win32process
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid == os.getpid()
    except Exception:
        return False


def target_hwnd() -> int:
    """Ventana que hay que INSPECCIONAR: la de delante, pero nunca la de Yue.

    Si el chat o el avatar tienen el foco, se baja por el orden Z hasta la
    primera ventana real del usuario. Sin esto, el planificador recibe la lista
    de controles de Yue y jura que el botón «Es igual a» ya no existe.
    """
    if not is_windows():
        return 0
    try:
        import win32con
        import win32gui
    except Exception:
        return 0
    try:
        hwnd = win32gui.GetForegroundWindow()
        if hwnd and not _is_own_hwnd(hwnd):
            return hwnd
        # El foco es nuestro: buscamos la siguiente ventana real.
        siguiente = hwnd
        for _ in range(40):
            siguiente = win32gui.GetWindow(siguiente, win32con.GW_HWNDNEXT)
            if not siguiente:
                break
            if not win32gui.IsWindowVisible(siguiente) or _is_own_hwnd(siguiente):
                continue
            if not (win32gui.GetWindowText(siguiente) or "").strip():
                continue
            izq, arr, der, aba = win32gui.GetWindowRect(siguiente)
            if der - izq > 120 and aba - arr > 80:
                return siguiente
    except Exception:
        pass
    return 0

# ---------------------------------------------------------------- snapshots reversibles
def _process_executable(handle: int) -> str:
    """Ruta del ejecutable dueño de una ventana, sin requerir psutil."""
    if not handle or not is_windows():
        return ""
    try:
        import win32process
        _thread_id, pid = win32process.GetWindowThreadProcessId(int(handle))
        try:
            import psutil
            return str(psutil.Process(pid).exe() or "")
        except Exception:
            pass
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        process = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not process:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size))
            return buffer.value if ok else ""
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    except Exception:
        return ""


def window_snapshot(title: str = "") -> dict | None:
    """Snapshot ligero de una ventana: título, handle, rect y ejecutable."""
    windows = list_windows(limit=50)
    if not windows:
        return None
    wanted = norm(title)
    candidates = []
    for win in windows:
        score = similarity(wanted, win.get("title", "")) if wanted else (1.0 if win.get("active") else 0.0)
        candidates.append((score, win))
    candidates.sort(key=lambda item: item[0], reverse=True)
    score, selected = candidates[0]
    if wanted and score < 0.55:
        return None
    handle = int(selected.get("handle", 0) or 0)
    rect = [int(v) for v in (selected.get("rect") or [0, 0, 0, 0])]
    executable = _process_executable(handle)
    title_real = str(selected.get("title", ""))
    return {
        "title": title_real,
        "handle": handle,
        "rect": rect,
        "executable": executable,
        "active": bool(selected.get("active")),
        # Cerrar una ventana con marcador de cambios sin guardar no se puede
        # revertir con seguridad: reabrir la app no recupera ese contenido.
        "safe_to_reopen": bool(executable and not title_real.lstrip().startswith("*")),
    }


def windows_snapshot(limit: int = 40) -> list[dict]:
    """Lista ligera para comparar el estado antes/después de una acción."""
    return [
        {
            "title": str(w.get("title", "")),
            "rect": [int(v) for v in (w.get("rect") or [])],
            "active": bool(w.get("active")),
        }
        for w in list_windows(limit=limit)
    ]


def move_window(title: str, x: int, y: int, width: int | None = None, height: int | None = None) -> str:
    """Mueve/redimensiona una ventana real por handle."""
    snap = window_snapshot(title)
    if not snap:
        raise RuntimeError(f"No encontré una ventana parecida a «{title}».")
    rect = snap["rect"]
    width = int(width if width is not None else max(120, rect[2] - rect[0]))
    height = int(height if height is not None else max(80, rect[3] - rect[1]))
    try:
        import win32gui
        import win32con
        handle = int(snap.get("handle", 0))
        if win32gui.IsIconic(handle):
            win32gui.ShowWindow(handle, win32con.SW_RESTORE)
        win32gui.MoveWindow(handle, int(x), int(y), width, height, True)
        _invalidate_cache()
        return str(snap["title"])
    except Exception as exc:
        raise RuntimeError(f"No pude mover «{snap['title']}»: {exc}") from exc


def restore_window_snapshot(snapshot: dict) -> str:
    """Restaura posición/tamaño de una ventana que todavía existe."""
    if not isinstance(snapshot, dict):
        raise RuntimeError("Snapshot de ventana inválido.")
    title = str(snapshot.get("title", ""))
    rect = snapshot.get("rect") or []
    if len(rect) != 4:
        raise RuntimeError("El snapshot no contiene una geometría válida.")
    current = window_snapshot(title)
    if not current:
        raise RuntimeError(f"La ventana «{title}» ya no está abierta.")
    return move_window(title, rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])


def reopen_window_snapshot(snapshot: dict, timeout: float = 6.0) -> str:
    """Reabre una app cerrada y recupera su geometría cuando es seguro hacerlo."""
    import os
    import subprocess
    import time
    import threading

    if not isinstance(snapshot, dict) or not snapshot.get("safe_to_reopen"):
        raise RuntimeError(
            "No puedo restaurar esa ventana con seguridad: podría haber contenido sin guardar."
        )
    executable = str(snapshot.get("executable", ""))
    if not executable:
        raise RuntimeError("No conozco el ejecutable de la ventana cerrada.")
    try:
        if hasattr(os, "startfile"):
            os.startfile(executable)
        else:
            subprocess.Popen([executable], shell=False)
    except Exception as exc:
        raise RuntimeError(f"No pude reabrir la aplicación: {exc}") from exc
    deadline = time.monotonic() + max(1.0, float(timeout))
    title = str(snapshot.get("title", ""))
    delay = 0.08
    wake = threading.Event()
    while time.monotonic() < deadline:
        current = window_snapshot(title)
        if current:
            try:
                restore_window_snapshot(snapshot)
            except Exception:
                pass
            return str(current.get("title") or title)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        wake.wait(min(delay, remaining))
        delay = min(0.5, delay * 1.35)
    raise RuntimeError("La aplicación se abrió, pero su ventana no apareció a tiempo.")
