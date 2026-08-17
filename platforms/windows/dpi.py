"""Ajustes DPI de Windows (movido desde main.py en PR 2)."""

from __future__ import annotations

import sys


def enable_dpi_awareness() -> bool:
    """Declara DPI awareness antes de crear ventanas o tomar capturas."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        # PER_MONITOR_AWARE_V2 (Windows 10 Creators Update+).
        try:
            if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return True
        except Exception:
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
            return True
        except Exception:
            pass
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            return True
        except Exception:
            return False
    except Exception:
        return False