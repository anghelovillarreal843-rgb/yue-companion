"""Re-export temporal (PR 2 → PR 6).

El código real vive en platforms/windows/desktop_ui.py.  Durante la
transición los consumidores aún pueden importar desde aquí; en PR 5/6
se migran a PlatformController y este módulo se elimina.
"""
from platforms.windows.desktop_ui import *  # noqa: F401,F403
from platforms.windows.desktop_ui import _active_title_win32  # noqa: F401