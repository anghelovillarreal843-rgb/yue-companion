# Validaciones realizadas

## Automáticas

- Compilación sintáctica de todos los archivos Python: correcta.
- Sintaxis JavaScript de los scripts incluidos en `ui/avatar.html`: correcta.
- Detección y limpieza de etiquetas emocionales: correcta.
- Reacción visual empática ante mensajes tristes: correcta.
- Detección de `Yue, detente`: correcta.
- Eliminación del comando manual de creación: correcta.
- Planes rápidos del PC: abrir y escribir, búsqueda web, copiar, pegar, seleccionar todo, clic derecho y arrastre.
- Bloqueo de instrucciones críticas: correcto.
- Validación de coordenadas proporcionales: correcta.
- Archivos `.env` y `.env.example`: nuevas opciones añadidas sin exponer ni modificar claves privadas.

## Pruebas manuales recomendadas en Windows

### Chat

1. Escribe mientras YUE está pensando.
2. Confirma que la caja no se bloquea.
3. Envía otra pregunta antes de que responda la anterior.
4. Confirma que no aparece después la respuesta antigua.

### Interrupción de voz

1. Di: `Yue, cuéntame una historia larga`.
2. Mientras habla, di: `Yue, espera, ahora dime la hora`.
3. La voz anterior debe detenerse y YUE debe atender la nueva frase.
4. Si el altavoz produce mucho eco, configura:

```text
MIC_BARGE_IN_REQUIRE_WAKE_WORD=true
```

### Emociones

1. Dile algo alegre, triste, sorprendente y cariñoso.
2. Comprueba que el avatar cambia rostro y postura.
3. El chat no debe mostrar `Curiosa`, `Feliz`, `[Triste]` ni otras etiquetas emocionales.

### Iniciativa automática

1. Deja la aplicación inactiva durante el tiempo configurado.
2. Verifica la creación en `data/autonomy/creations/`.
3. Con `AUTONOMY_OPEN_CREATIONS=true`, el archivo debe abrirse automáticamente.
4. El menú del avatar no debe mostrar `Crear una idea nueva ahora`.

### Control del PC

Prueba primero con tareas reversibles:

```text
abre el bloc de notas y escribe Prueba de control visual
abre Chrome y busca Machu Picchu
selecciona todo
Yue, detente
```

Después prueba una orden visual de varios pasos y observa los estados `ciclo 1/6`, `ciclo 2/6`, etc.

Para el control por elementos reales (`click_element`, `click_text`, ventanas), la
verificación por acción, la detección de no-progreso y el aprendizaje continuo, hay
un protocolo aparte en `PRUEBAS_CONTROL_PC.md` y dos scripts:

```powershell
python tests/test_mejoras.py       # 40 comprobaciones automaticas, sin Windows ni LLM
python tests/diagnostico_pc.py     # que funciona de verdad en tu maquina, por capas
```

## Limitación del entorno de modificación

El entorno donde se modificó el proyecto no incluye PyQt5 ni acceso al escritorio real, por lo que no fue posible abrir físicamente la ventana, renderizar el VRM, usar el micrófono ni mover el mouse. La compilación estática y las pruebas del núcleo fueron correctas. Valida el funcionamiento final con:

```powershell
pip install -r requirements.txt
python main.py
```
