# YUE — Compañera de escritorio con IA

YUE es una compañera virtual de escritorio: un avatar (VRM 3D o PNG) que vive
en tu pantalla, conversa contigo por texto y voz, te escucha, mira lo que
haces (solo cuando se lo pides), puede controlar tu PC con tu permiso y te
acompaña con gestos, música y recuerdos.

- **Conversación**: Groq (chat y respuestas rápidas, siempre en español).
- **Voz**: Edge-TTS (neuronal, online) como motor principal; Kokoro (neuronal,
  local, sin internet) y gTTS (respaldo) como alternativas.
- **Escucha**: micrófono con reconocimiento continuo, barge-in e interrupción.
- **Control del PC**: clics, escritura, aplicaciones, archivos y Office, con
  permisos granulares y verificación por pantalla.
- **Modo Profesora**: lee PDF, DOCX, PPTX y EPUB, explica y guía con auto-avance.
- **Visión**: pantalla (OCR/visión), cámara (rostro, emociones, postura) y
  memoria de lo que ve, con privacidad por diseño.
- **Memoria**: conversaciones, hechos, episodios, estados de ánimo y
  consolidación a largo plazo (SQLite en `data/yue.db`).
- **Creación**: genera imágenes (FLUX), vídeos y música según tu ánimo.

---

## Stack

- **App**: Python · PyQt5 5.15 · PyQtWebEngine 5.15 (avatar 3D VRM)
- **Conversación**: Groq (modelo por defecto `qwen/qwen3.6-27b`)
- **Voz**: `edge-tts` ≥ 6.1 · `kokoro-onnx` ≥ 0.4.4 · `gTTS` ≥ 2.5 · `PyAudio` ≥ 0.2.13
- **Audio**: pygame ≥ 2.5
- **Visión/OCR**: opencv-python ≥ 4.10 · mediapipe ≥ 0.10.21,<0.11 · pytesseract ≥ 0.3.10 (binario Tesseract aparte)
- **Documentos**: pymupdf ≥ 1.24 · pypdf ≥ 4.0 · pdfplumber ≥ 0.11 · python-docx · python-pptx · ebooklib
- **Control de PC**: pyautogui · pyperclip · pywinauto (solo Windows) · pywin32 (solo Windows)
- **Base de datos**: SQLite (`data/yue.db`)

Dependencias exactas: ver [`requirements.txt`](requirements.txt).
Runtime probado: **Python 3.14 (Arch Linux)**. No hay versión fijada en el
repo; con Python ≤ 3.12 ten en cuenta que `mediapipe` y `opencv` tienen wheels
oficiales y `Pillow` debe quedar por debajo de 12 (lo exige `requirements.txt`).

---

## Requisitos previos

- Python 3.10+ y `pip`.
- **Tesseract OCR** (para leer texto de pantalla e imágenes):

Linux (Debian/Ubuntu):

```bash
sudo apt install tesseract-ocr tesseract-ocr-spa
```

Linux (Arch):

```bash
sudo pacman -S tesseract tesseract-data-spa
```

macOS:

```bash
brew install tesseract tesseract-lang
```

Windows:

```powershell
winget install UB-Mannheim.TesseractOCR
```

  Durante la instalación de Windows, marca el paquete de idioma *Spanish*.
  Si el binario no queda en el PATH, pon su ruta en `OCR_TESSERACT_CMD` del `.env`.

- **FFmpeg** (para generación de vídeo/recuerdos y procesamiento de audio):

Linux (Debian/Ubuntu):

```bash
sudo apt install ffmpeg
```

Linux (Arch):

```bash
sudo pacman -S ffmpeg
```

macOS:

```bash
brew install ffmpeg
```

Windows:

```powershell
winget install Gyan.FFmpeg
```

---

## Instalación

1. Clona el repositorio:

```bash
git clone https://github.com/anghelovillarreal843-rgb/yue-companion.git
cd yue-companion
```

2. Crea y activa el entorno virtual con el `python` del sistema:

Linux / macOS:

```bash
python -m venv venv
source venv/bin/activate
```

Windows (PowerShell):

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

Windows (CMD):

```cmd
python -m venv venv
venv\Scripts\activate.bat
```

Todos los comandos siguientes se ejecutan con el venv activado (verificable
con `which python` → debe apuntar dentro de `venv/`).

3. Instala las dependencias:

```bash
pip install -r requirements.txt
```

  En distribuciones con PEP 668 (Debian 12+, Ubuntu 23.10+, Arch) este paso
  puede rechazarse con `externally-managed-environment`; si trabajas fuera del
  venv, instala igualmente dentro de él (o con `--break-system-packages` bajo
  tu responsabilidad).

4. Configura las variables de entorno:

```bash
cp .env.example .env
```

  Y edita `.env` con al menos tu `GROQ_API_KEY`. YUE lee el `.env` que vive
  junto al proyecto (verifica `load_dotenv()` en `config.py`).

5. Descarga los modelos de voz local (solo si usarás Kokoro):

```bash
wget -O assets/kokoro/kokoro-v1.0.onnx \
  "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
wget -O assets/kokoro/voices-v1.0.bin \
  "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
```

  Luego pon `TTS_ENGINE=kokoro` en el `.env`. Con `edge` no se necesita nada
  extra, pero la voz requiere internet.

---

## Ejecutar

```bash
python main.py
```

Para listar los micrófonos disponibles (y su índice para `MIC_DEVICE_INDEX`):

```bash
python main.py --mics
```

YUE levanta además un servidor local de recursos en `127.0.0.1` con un puerto
libre elegido al azar (sirve `avatar.html` para el avatar 3D; el puerto se
imprime en consola como `[avatar] servidor local en http://127.0.0.1:<puerto>`).

---

## Tests

```bash
python -m pytest tests/ -q
```

Nota verificada: 4 tests de `test_affect_eval.py` requieren el corpus
`tests/eval/afecto_eval.jsonl`, que no viaja en el repositorio (no incluido);
fallan con `FileNotFoundError` y no afectan al resto de la suite.

---

## Empaquetado

- **Ejecutable Windows (PyInstaller)**: `pyinstaller yue.spec --noconfirm`
  (produce `dist\YUE\YUE.exe`).
- **Zip limpio para distribuir** (sin `.env`, secretos ni datos personales):

```bash
python -m tools.build_release
python -m tools.build_release --version X.Y.Z
```

---

## Variables de entorno

Las variables se cargan desde el `.env` junto al proyecto; todas tienen valor
por defecto en `config.py`, así que YUE arranca aunque falten. Las principales:

| Variable | Por defecto | Descripción |
| --- | --- | --- |
| `GROQ_API_KEY` | *(vacía)* | Clave de Groq, proveedor de conversación (obligatoria para hablar) |
| `GROQ_MODEL` | `qwen/qwen3.6-27b` | Modelo de chat |
| `TTS_ENGINE` | `edge` | Motor de voz: `edge` (online) / `kokoro` (local) |
| `EDGE_TTS_VOICE` | `es-MX-DaliaNeural` | Voz neuronal Edge (es-PE-CamilaNeural, etc.) |
| `KOKORO_VOICE` | `ef_dora` | Voz local Kokoro en español |
| `MIC_ENABLED` | `true` | Escucha continua por micrófono |
| `CAMERA_ENABLED` | `true` | Cámara local (rostro/emociones) |
| `VISION_ENABLED` | `true` | Visión de pantalla |
| `USE_VRM` | `true` | Avatar 3D VRM (si falla, usa el PNG) |
| `CHAT_MOSTRAR_RESPUESTAS` | `false` | Mostrar las respuestas en el chat por escrito |
| `CHAT_ANTIDUP_WINDOW_S` | `20` | Anti-saturación: reenviar el mismo texto dentro de esta ventana se rechaza |
| `CHAT_BURST_MAX` | `4` | Máximo de envíos en `CHAT_BURST_WINDOW_S` (12 s) antes de frenar |

La lista completa (más de 400 variables, con sus valores por defecto) está en
[`.env.example`](.env.example).

---

## Estructura

```
yue-companion/
├── main.py            # punto de entrada (Controller + QApplication)
├── config.py          # lectura tolerante del .env y defaults de todo
├── requirements.txt   # dependencias exactas
├── yue.spec           # receta PyInstaller (Windows)
├── core/              # voz, memoria, visión de pantalla, control de PC, IA
├── engine/            # directores/orquestadores: diálogo, voz, PC, profesor
├── ui/                # chat, avatar (desktop_pet), burbuja, tema
├── vision/            # sistema de percepción por cámara (V3, MediaPipe)
├── teacher/           # modo Profesora (lectura guiada, progreso)
├── voice_flow/        # barge-in y filtrado de audio
├── mode_manager/      # modos de YUE (Compañera / Profesora)
├── platforms/         # adaptadores por SO (windows/linux)
├── contracts/         # puertos/contratos de integración
├── tools/             # build_release, clean_project, secret_scanner
├── tests/             # suite pytest (humo, unidad e integración)
└── data/              # yue.db (memoria), recuerdos, logs (no versionado)
```

---

## Notas de la plataforma

- **Control de PC**: pywinauto/COM solo aplican en Windows; en Linux se usan
  los adaptadores de `platforms/linux`.
- **Visión por cámara (V3)**: apagada por defecto (`VISION_V3_ENABLED=false`).
  DeepFace/psutil son opcionales y solo hacen falta si la activas.
- **OCR de cámara**: opcional (PaddleOCR/EasyOCR); sin ellos YUE usa Tesseract
  como respaldo y avisa si no puede leer.
