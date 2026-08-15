"""Re-export temporal (PR 2 → PR 6).

El código real vive en platforms/windows/office.py.  Durante la
transición los consumidores aún pueden importar desde aquí; en PR 5/6
se consume a través de PlatformController y este módulo se elimina.
"""
from platforms.windows.office import *  # noqa: F401,F403