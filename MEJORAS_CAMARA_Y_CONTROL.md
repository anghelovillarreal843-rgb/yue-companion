# Mejoras aplicadas

## Visión de pantalla

- Se eliminó del menú del avatar la opción **Ver / dejar de ver mi pantalla**.
- La visión queda habilitada al iniciar YUE.
- No realiza comentarios periódicos ni enumera todo lo que ve.
- Solo captura la pantalla durante una orden de control o al usar `/mira`.

## Cámara automática

- Busca una webcam automáticamente al iniciar.
- Muestra una insignia verde **CÁMARA** mientras está conectada.
- Procesa los fotogramas localmente y en memoria.
- No guarda fotos ni videos, no reconoce identidades y no envía imágenes de cámara a la IA.
- Detecta señales descriptivas: sonrisa, boca abierta, ojos cerrados, cejas elevadas, brazos levantados, manos cerca del rostro e inclinación corporal.
- Usa MediaPipe cuando está instalado y OpenCV como respaldo básico.
- Comandos: `/camara`, `¿qué expresión tengo?`, `¿qué ves por la cámara?`.
- NUEVO: estima cómo se siente la persona (contenta, triste, sorprendida, molesta, pensativa) a partir de su expresión, siempre como impresión y no como certeza.
- NUEVO: espejo empático — YUE acompaña con la cara del avatar (si te ve triste se preocupa; no imita el enfado). Ver `CAMBIOS_EMOCIONES.md`.

## Control del PC

- `abre la calculadora y haz clic en el 3` se resuelve directamente: abrir/enfocar, esperar y pulsar el control **Tres**.
- Si una aplicación ya está abierta, se enfoca en vez de abrir otra copia.
- `cierra la ventana de Calculadora` usa la acción segura `close_window`.
- El planificador recibe instrucciones para no repetir `open_app` cuando ya funcionó.
- Se corrigió el alto mínimo del chat para reducir los avisos `QWindowsWindow::setGeometry`.

## Instalación

```powershell
pip install -r requirements.txt
python main.py
```

La primera ejecución puede descargar los modelos oficiales de MediaPipe en `data/models/`.
