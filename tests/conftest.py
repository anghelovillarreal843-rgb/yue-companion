"""Configuración compartida de las pruebas.

Instala un doble de PyQt5 cuando no está disponible (Linux/CI). YUE es una
aplicación de escritorio Windows y `main.py` importa PyQt5 en la primera línea,
así que sin esto ninguna prueba podría tocar `Controller` fuera de tu equipo.

Si PyQt5 SÍ está instalado (tu máquina), esto no hace absolutamente nada y las
pruebas corren contra el Qt de verdad.

NOTA (baseline PR 1, refactor/arquitectura): estos archivos son "script-style":
ejecutan sus checks a nivel de módulo y terminan con sys.exit() SIN guard
`if __name__ == "__main__"`, por lo que rompen la colección de pytest
(INTERNALERROR SystemExit). Se ejecutan directamente: python tests/archivo.py.
Se excluyen de la colección aquí en lugar de tocar su código (fuera del alcance
del refactor); si en el futuro se convierten a pytest-style, se quitan de la lista.
"""
import sys
import types

collect_ignore = [
    # script-style con sys.exit()/raise SystemExit a nivel de módulo (rompen pytest)
    "test_documento_office.py",
    "test_emociones_av.py",
    "test_jarvis_control.py",
    "test_mejoras.py",
    "test_memory_consolidation.py",
    "test_now_playing.py",
    "test_ordenes_dificiles.py",
    "test_seguridad_control.py",
    "test_story_memory.py",
]

try:  # pragma: no cover - en tu equipo entra por aquí y no se stubbea nada
    import PyQt5  # noqa: F401
except Exception:  # pragma: no cover
    class _Cualquiera:
        """Objeto que acepta cualquier uso sin quejarse."""

        def __init__(self, *a, **k):
            pass

        def __getattr__(self, nombre):
            return _Cualquiera()

        def __call__(self, *a, **k):
            return _Cualquiera()

        def __or__(self, otro):
            return _Cualquiera()

        __ror__ = __or__

    class _ModuloQt(types.ModuleType):
        def __getattr__(self, nombre):
            if nombre.startswith("__"):
                raise AttributeError(nombre)
            return _Cualquiera

    for _nombre in ("PyQt5", "PyQt5.QtCore", "PyQt5.QtGui", "PyQt5.QtWidgets",
                    "PyQt5.QtWebEngineWidgets", "PyQt5.QtWebEngine",
                    "PyQt5.QtWebChannel", "PyQt5.QtMultimedia", "PyQt5.QtNetwork"):
        sys.modules[_nombre] = _ModuloQt(_nombre)

    sys.modules["PyQt5.QtCore"].pyqtSignal = lambda *a, **k: _Cualquiera()
    sys.modules["PyQt5.QtCore"].pyqtSlot = lambda *a, **k: (lambda f: f)
    sys.modules["PyQt5.QtCore"].pyqtProperty = lambda *a, **k: (lambda f: f)
