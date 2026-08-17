"""Detección de la ventana/proceso foreground vía Win32.

Único dueño de la llamada nativa GetForegroundWindow: media_companion y
screen_observation la consumen a través de PlatformController.foreground_app().
"""

from __future__ import annotations


def get_foreground_app() -> tuple[str, str]:
    """(titulo, proceso) de la ventana activa vía Win32. Nunca lanza."""
    titulo, proceso = "", ""
    try:
        import ctypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            import win32gui

            titulo = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            titulo = ""
        if pid.value:
            try:
                import psutil

                proceso = psutil.Process(pid.value).name().lower()
            except Exception:
                proceso = ""
    except Exception:
        pass
    return (titulo[:240], proceso)