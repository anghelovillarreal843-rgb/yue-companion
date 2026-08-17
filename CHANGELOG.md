# Changelog

Todas las entradas reflejan cambios verificados en el repositorio.

## [Unreleased]

### Added

- Documentación del repositorio: `README.md` completo (instalación, ejecución,
  variables de entorno, tests y empaquetado) y `CHANGELOG.md`.
- `.env.example` con el valor por defecto real de cada variable leída en
  `config.py` (base de `env_EJEMPLO.txt` + 104 variables suplementarias que el
  código lee y la plantilla anterior no documentaba).
- Motor de voz Kokoro funcional: `kokoro-onnx` instalado y modelos
  (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) descargados en `assets/kokoro/`
  (se activa con `TTS_ENGINE=kokoro` en el `.env`).
- `edge-tts` instalado como motor de voz neuronal online (`TTS_ENGINE=edge`).

### Changed

- Chat reestilizado con paleta sobria morada (superficies sólidas, sin
  transparencias ni neones): `ui/theme.py`, `ui/foot_chat.py`,
  `ui/speech_bubble.py`, `ui/floating_text.py`.
- Menú contextual del avatar rediseñado con la misma paleta
  (`ui/desktop_pet.py`).
- Voz del chat: `EDGE_TTS_VOICE=es-MX-DaliaNeural` (original del proyecto).

### Added

- Anti-saturación del chat: los duplicados recientes y las ráfagas de envíos
  se rechazan con aviso antes de llegar a la IA (`CHAT_ANTIDUP_WINDOW_S`,
  `CHAT_ANTIDUP_MEM`, `CHAT_BURST_WINDOW_S`, `CHAT_BURST_MAX` en el `.env`).
- Indicador "Yue está escribiendo…" en el chat mientras responde.
- Botón de enviar convertible en spinner de carga: al pulsarlo mientras YUE
  contesta, se detiene la respuesta (`chat.cancel_request` conectado a
  `_interrupt_response` en `main.py`).

### Fixed

- El texto flotante que aparece al escribir ya no queda cortado ni detrás de
  otras ventanas: se encaja dentro de la pantalla y se eleva al mostrarse
  (`ui/floating_text.py`). Mismo tratamiento para el globo de diálogo
  (`ui/speech_bubble.py`).
