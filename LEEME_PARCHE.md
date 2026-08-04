# Expresiones del avatar · capa de gestos

## Qué le pasaba

El avatar **solo tenía idle**: senos continuos (`sway`, `bounce`, `nod`, `tilt`)
mezclados con `damp()`. Es suave, pero está en bucle: nunca *hace* nada, solo
oscila. Y las expresiones eran valores clavados (`happy:0.88` fijo mientras dure
la emoción), o sea una máscara puesta, no una emoción que llega.

## Qué se añadió

Una capa de **gestos**: movimientos puntuales con arco (anticipación → acción →
asentamiento) que se suman encima del idle. 14 gestos, cada uno de 0.8s a 2.4s.

```
asentir · negar · ladear · sobresalto · encoger · suspiro · reír
asomarse · retroceder · mirar_lejos · estirarse · celebrar · pensar · desanimo
```

Solo cabeza, cuello, pecho, hombros, columna y cadera. **Nada de manos a la cara
ni brazos cruzados**: el VRM no tiene IK y esos gestos atraviesan el cuerpo. Los
14 son cosas que tu modelo puede hacer bien de verdad.

**Ahora la emoción llega en vez de solo estar:**

| Emoción | Gesto |
|---|---|
| excited | celebrar |
| surprised | sobresalto |
| sad | desanimo |
| shy / love | mirar_lejos (+ ladear) |
| curious | asomarse + ladear |
| focused | pensar |
| sleepy | estirarse |
| relaxed / bored | suspiro |

Además: pico de expresión al llegar la emoción (1.28×) y luego asentamiento;
gestos pequeños automáticos mientras habla (cada 1.6-4s) y en reposo (cada 6-14s);
y micro-sacadas en los ojos, que dan saltitos en vez de derivar como un radar.

## Por qué NO se puede trabar

Cuatro seguros, y los cuatro están verificados con `node tests/test_gestos.js`:

1. **Toda curva vale exactamente 0 en u=0 y u=1.** Al acabar aporta cero: la pose
   vuelve sola. Comprobado en los 14.
2. **Los gestos se suman DESPUÉS del `damp`, sobre una base guardada aparte**
   (`base`). Esto era el riesgo real: `damp(hueso.rotation.x, ...)` lee la
   rotación actual, que ya llevaba el gesto sumado → se realimenta y la pose
   deriva. Con la base separada, el gesto es un delta limpio.
3. **Tope por canal** (`TOPES`) y **clamp anatómico final** en cabeza y cuello.
   Aunque se junten seis gestos con gain 1.5, no se sale.
4. **Un gesto del mismo nombre reemplaza al anterior**; máximo 6 simultáneos.

Medido con los 14 gestos disparándose cada 12 frames a intensidad 1.5:

```
picos: X=15.7°  Y=19.2°  Z=15.5°     (topes: 23° / 30° / 22°)
```

Expresivo, y lejos del límite. Nunca «excesivo».

## Ajustes

```ini
AVATAR_GESTURE_GAIN=1.0    # 0 = como antes, 1 = normal, 1.5 = teatral
```

Si lo ves poco, sube a 1.3. Si lo ves demasiado, baja a 0.6. En `0` queda
exactamente el comportamiento anterior.

Desde Python, para momentos concretos:

```python
self.pet.play_gesture("asentir")        # al confirmar una orden
self.pet.play_gesture("pensar", 0.8)    # mientras planifica
```

## Probar

```powershell
node tests/test_gestos.js
```
