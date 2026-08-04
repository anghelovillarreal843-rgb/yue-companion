"""Diagnóstico en vivo del control de PC de YUE.

Comprueba, por capas y SIN llamar al LLM, qué funciona de verdad en tu máquina.
Así, si una orden falla, sabes si la culpa es de pywinauto, del OCR, de la
verificación o del modelo.

Uso:
    python tests/diagnostico_pc.py                 # solo lectura, no toca nada
    python tests/diagnostico_pc.py --clic "Siete"  # además intenta un clic real
    python tests/diagnostico_pc.py --texto "Archivo"
    python tests/diagnostico_pc.py --orden "presiona f7"
"""
from __future__ import annotations

import argparse
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OK, NO = "  [OK]  ", "  [--]  "


def titulo(texto: str):
    print("\n" + texto)
    print("-" * len(texto))


def capa_0_entorno():
    titulo("0. Entorno")
    print(f"        Sistema: {platform.system()} {platform.release()}")
    print(f"        Python:  {sys.version.split()[0]}")
    for modulo in ("pyautogui", "PIL", "pywinauto", "pygetwindow", "pytesseract"):
        try:
            __import__(modulo)
            print(OK + modulo)
        except Exception as exc:
            print(NO + f"{modulo}: {exc}")
    try:
        import pyautogui
        print(f"        Resolución que ve pyautogui: {pyautogui.size()}")
    except Exception:
        pass


def capa_1_ventanas():
    titulo("1. Ventanas (pywinauto / pygetwindow)")
    from core import desktop_ui

    if not desktop_ui.available():
        print(NO + "pywinauto no disponible: click_element NO funcionará.")
        print("        Instala: pip install pywinauto comtypes  (solo Windows)")
    inicio = time.time()
    ventanas = desktop_ui.list_windows(limit=20)
    print(f"        list_windows() tardó {time.time() - inicio:.2f} s")
    if not ventanas:
        print(NO + "No leí ninguna ventana.")
        return
    print(OK + f"{len(ventanas)} ventanas:")
    for win in ventanas:
        print(f"          {'>' if win.get('active') else ' '} {win['title']}")
    print(f"        Ventana con el foco: «{desktop_ui.active_window_title()}»")


def capa_2_controles():
    titulo("2. Controles de la ventana activa (backend uia)")
    from core import desktop_ui

    inicio = time.time()
    elementos = desktop_ui.active_window_elements(limit=40, use_cache=False)
    tardo = time.time() - inicio
    print(f"        active_window_elements() tardó {tardo:.2f} s")
    if tardo > 1.5:
        print("        (lento: si molesta, baja PC_UI_MAX_ELEMENTS o pon PC_UI_CONTEXT=false)")
    if not elementos:
        print(NO + "Sin controles. Esta ventana no expone UIA o pywinauto falló.")
        print("        Prueba con el Bloc de notas o la Calculadora en primer plano.")
        return
    print(OK + f"{len(elementos)} controles (esto es lo que verá el planificador):")
    for e in elementos[:40]:
        print(f"          {e['control_type']:<14} «{e['name']}»  centro={e['center']}")


def capa_3_ocr():
    titulo("3. OCR")
    from core import screen_text

    motor = screen_text.get_engine()
    if not motor.available():
        print(NO + f"Motor «{motor.name}» no listo: click_text NO funcionará.")
        print("        Instala Tesseract y, si hace falta, pon OCR_TESSERACT_CMD en el .env")
        return
    inicio = time.time()
    texto = screen_text.read_screen()
    print(OK + f"Motor «{motor.name}» listo. Leyó {len(texto)} caracteres en "
               f"{time.time() - inicio:.2f} s")
    print(f"        Muestra: {texto[:180]}…")


def capa_4_diff():
    titulo("4. Verificación de cambios en pantalla")
    from core import desktop_ui, screen_diff

    ignorar = desktop_ui.own_window_rects()
    if ignorar:
        print(OK + f"Enmascaro {len(ignorar)} ventana(s) de la propia Yue: {ignorar}")
    else:
        print("        Yue no está abierta: no hay nada que enmascarar ahora mismo.")
        print("        (con main.py corriendo, el avatar se excluye de la comparación)")

    # OJO: no se imprime NADA entre las dos firmas. Escribir en la terminal
    # cambia la pantalla y falsearía la prueba.
    print("\n        Prueba A · pantalla quieta: no toques nada durante 3 s…")
    time.sleep(1.0)                       # que la terminal termine de pintar
    a = screen_diff.signature(ignorar)
    if not a:
        print(NO + "No pude firmar la pantalla.")
        return
    time.sleep(2.0)
    b = screen_diff.signature(ignorar)
    cambio_quieta = screen_diff.changed(a, b)
    ruido = screen_diff.pixel_diff_ratio(a.thumb, b.thumb)
    print(f"        firma A={a.hash}  B={b.hash}  ruido={ruido:.5f}")
    print((NO if cambio_quieta else OK) + f"quieta -> changed={cambio_quieta} (debería ser False)")
    if cambio_quieta:
        print(f"        Algo se mueve en pantalla. Umbral actual: "
              f"PC_VERIFY_PIXEL_RATIO={getattr(__import__('config'), 'PC_VERIFY_PIXEL_RATIO', 0.0015)}")
        print(f"        Súbelo por encima de {ruido:.4f} en el .env y repite esta prueba.")
        print("        Sospechosos: reloj de la barra, videos, terminal escribiendo, avatar de Yue.")

    print("\n        Prueba B · provoco un cambio real abriendo y cerrando el menú Inicio…")
    time.sleep(1.0)
    cambio = None
    try:
        import pyautogui
        pyautogui.press("win")
        time.sleep(1.2)
        c = screen_diff.signature(ignorar)
        cambio = screen_diff.changed(a, c)
        pyautogui.press("esc")
        time.sleep(0.5)
    except Exception as exc:
        print(f"        No pude usar el teclado ({exc}). Hazlo a mano:")
        print("        MUEVE una ventana ahora. Tienes 5 s…")
        time.sleep(5.0)
        cambio = screen_diff.changed(a, screen_diff.signature(ignorar))
    print((OK if cambio else NO) + f"cambio real -> changed={cambio} (debería ser True)")
    if not cambio_quieta and cambio:
        print(OK + "La verificación por acción es fiable en tu equipo.")
    elif not cambio:
        print("        Si sale False, la verificación no detecta cambios y marcará como")
        print("        fallidas acciones que sí funcionaron. Baja PC_VERIFY_PIXEL_RATIO.")


def capa_5_contexto():
    titulo("5. Contexto completo que recibe el planificador")
    from core.pc_control import PCController

    pc = PCController()
    inicio = time.time()
    info = pc._screen_info()
    print(f"        _screen_info() tardó {time.time() - inicio:.2f} s")
    print(f"        captura: {len(info.get('image_b64', '')) // 1024} KB en base64")
    print(f"        hash: {info.get('hash', '(vacío)')}")
    print(f"        ventana activa: «{info.get('active_window', '')}»")
    print(f"        ventanas: {len(info.get('windows', []))}")
    print(f"        ui_elements: {len(info.get('ui_elements', []))}")
    from core.ai_engine import AIEngine
    print("\n        Texto exacto que se le manda al modelo:")
    print("        " + AIEngine._ui_context_text(info).replace("\n", "\n        ")[:1200])


def capa_6_router():
    titulo("6. Enrutador de órdenes (looks_like_pc_command)")
    from core.pc_control import looks_like_pc_command

    for frase in (
        "abre la calculadora y haz clic en el 7",
        "enfoca la ventana del bloc de notas",
        "cierra la ventana del bloc de notas",
        "que ventanas tengo abiertas",
        "hola, como estas",
    ):
        detectada = looks_like_pc_command(frase)
        esperado = not frase.startswith("hola")
        print((OK if detectada == esperado else NO) + f"«{frase}» -> {detectada}")


def prueba_clic(nombre: str):
    titulo(f"EXTRA · click_element sobre «{nombre}»")
    from core import desktop_ui

    candidatos = desktop_ui.active_window_elements(limit=200, use_cache=False)
    if not candidatos:
        print(NO + "No hay controles en la ventana activa. ¿Es la que querías?")
        return
    ranking = sorted(
        ((desktop_ui.similarity(nombre, e.get("name", "")), e) for e in candidatos),
        key=lambda x: x[0], reverse=True,
    )
    print("        Mejores candidatos (umbral 0.75):")
    for score, e in ranking[:5]:
        marca = "->" if score >= 0.75 else "  "
        print(f"        {marca} {score:.3f}  {e['control_type']:<12} «{e['name']}»")

    encontrado = desktop_ui.find_element_center(nombre)
    if not encontrado:
        print("\n" + NO + "Ninguno supera el umbral: click_element se negaría a clicar.")
        print("        Eso es CORRECTO si el control no está en pantalla. Copia el nombre")
        print("        exacto de la capa 2 y vuelve a probar.")
        return
    x, y, real = encontrado
    print("\n" + OK + f"Elegido «{real}» en ({x}, {y}). Clicando en 3 s… (Ctrl+C para abortar)")
    time.sleep(3)
    from core.pc_control import PCController
    PCController()._click_xy(x, y)
    print("        Clic hecho. ¿Cayó donde debía? Si no, es escalado DPI (ver INTEGRACION.md).")


def prueba_texto(texto: str):
    titulo(f"EXTRA · click_text sobre «{texto}»")
    from core import screen_text

    encontrado = screen_text.find_text(texto)
    if not encontrado:
        print(NO + "El OCR no lo encontró en la pantalla actual.")
        return
    x, y, real = encontrado
    print(OK + f"Encontrado «{real}» en ({x}, {y}). No clico: solo localizo.")


def prueba_orden(orden: str):
    titulo(f"EXTRA · ejecutar «{orden}» sin LLM (solo plan directo)")
    from core.pc_control import PCController

    pc = PCController()
    plan = pc._direct_plan(orden)
    if not plan:
        print(NO + "No hay plan directo: esta orden necesita el planificador visual.")
        return
    print(f"        Plan: {plan}")
    resultado = pc.execute(orden, progress=lambda m: print("        …", m))
    print(f"\n        ok={resultado['ok']}")
    for r in resultado["results"]:
        marca = OK if r["ok"] else NO
        print(marca + f"{r['action']}: {r['detail']}")
    if not resultado["ok"]:
        print("\n        Un 'sin efecto visible' aquí es CORRECTO si la acción de verdad")
        print("        no cambió nada. Es justo lo que antes se daba por bueno.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clic", help="nombre de un control para localizar y clicar")
    parser.add_argument("--texto", help="texto a localizar por OCR")
    parser.add_argument("--orden", help="orden directa a ejecutar (sin LLM)")
    args = parser.parse_args()

    print("=" * 60)
    print(" DIAGNÓSTICO DEL CONTROL DE PC · YUE")
    print(" Pon en primer plano la ventana que quieras inspeccionar.")
    print("=" * 60)

    for capa in (capa_0_entorno, capa_1_ventanas, capa_2_controles, capa_3_ocr,
                 capa_4_diff, capa_5_contexto, capa_6_router):
        try:
            capa()
        except Exception as exc:
            print(NO + f"{capa.__name__} reventó: {exc}")

    if args.clic:
        prueba_clic(args.clic)
    if args.texto:
        prueba_texto(args.texto)
    if args.orden:
        prueba_orden(args.orden)

    print("\n" + "=" * 60)
    print(" Si las capas 1-3 fallan, el LLM no tiene nada que hacer:")
    print(" arregla las dependencias antes de probar órdenes reales.")
    print("=" * 60)


if __name__ == "__main__":
    main()
