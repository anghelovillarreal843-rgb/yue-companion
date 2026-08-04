# -*- mode: python ; coding: utf-8 -*-
# Receta de PyInstaller para empaquetar YUE como ejecutable de Windows.
# Uso:  pyinstaller yue.spec --noconfirm
# Resultado:  dist\YUE\YUE.exe  (carpeta lista para usar / comprimir / distribuir)

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Archivos que la app necesita en tiempo de ejecución (se sirven por http local).
datas = [
    ("ui/avatar.html", "ui"),     # el visor 3D del avatar
    ("assets", "assets"),         # yue.vrm, yue.png y la música
    (".env.example", "."),        # plantilla de configuración
]

# Módulos que PyInstaller a veces no detecta solo.
hiddenimports = []
hiddenimports += collect_submodules("gtts")
hiddenimports += collect_submodules("kokoro_onnx")
hiddenimports += collect_submodules("pyautogui")
hiddenimports += collect_submodules("pyperclip")
hiddenimports += collect_submodules("mediapipe")
hiddenimports += ["cv2", "pygame", "mss", "PIL", "numpy", "requests", "dotenv",
                  "speech_recognition", "kokoro_onnx", "soundfile",
                  "onnxruntime", "espeakng_loader", "pyautogui", "pyperclip"]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YUE",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # sin ventana negra de consola
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/yue.ico" if __import__("os").path.exists("assets/yue.ico") else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="YUE",
)
