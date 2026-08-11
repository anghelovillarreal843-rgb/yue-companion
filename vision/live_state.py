"""Estado vivo de percepción visual: el `vision_state` que consulta TODA YUE.

Punto 10 del pedido ("memoria temporal"). Es la ÚNICA estructura que el resto
del proyecto necesita leer para saber qué está viendo YUE ahora mismo:

    vision_state = {
        "personas", "emociones", "objetos", "gestos", "texto",
        "postura", "mirada", "escena", "camara", "actualizado"
    }

Diferencias con lo que ya existía (y por qué hacía falta esto):

  - `PerceptionEngine.snapshot()` ya devolvía casi toda la información, pero con
    claves en inglés y con la forma interna de cada tracker. Nadie fuera del
    paquete `vision/` la consumía.
  - `VisionStateFusion` tenía la forma correcta, pero solo se alimentaba desde
    el camino CLÁSICO del controlador, que queda desactivado en cuanto el motor
    de percepción arranca. O sea: estaba vivo pero vacío.

Este módulo cierra ese hueco. Toma el snapshot del motor (que ya está filtrado
y suavizado) y lo traduce a español una sola vez, guardando además señales
derivadas que ningún módulo suelto puede calcular porque necesitan HISTORIA:

  - segundos seguidos de contacto visual  -> `mirada.segundos_mirando`
  - cuánto lleva una persona presente     -> `personas.segundos_presente`
  - objetos que acaban de aparecer        -> `objetos_nuevos`
  - si la emoción CAMBIÓ                  -> `emociones.cambio`

Es Python puro y seguro entre hilos: lo escribe el hilo de percepción y lo leen
el hilo de Qt, el motor de diálogo y la capa reactiva, todos a la vez.

NO guarda imágenes. Nunca. Solo datos derivados.
"""
from __future__ import annotations

import threading
import time
from typing import Any

# --- traducciones -----------------------------------------------------------
# Se centralizan aquí para que YUE hable SIEMPRE igual, venga el dato de donde
# venga (motor de percepción, adaptador clásico o pruebas).

EMOCIONES_ES: dict[str, str] = {
    "happy": "feliz",
    "sad": "triste",
    "surprised": "sorprendido",
    "angry": "enojado",
    "tired": "cansado",
    "confused": "confundido",
    "tense": "tenso",
    "focused": "concentrado",
    "neutral": "neutral",
    "undetermined": "indeterminado",
}

POSTURAS_ES: dict[str, str] = {
    "standing": "parado",
    "sitting": "sentado",
    "lying": "acostado",
    "crouching": "agachado",
    "leaning_left": "inclinado a la izquierda",
    "leaning_right": "inclinado a la derecha",
    "arms_crossed": "con los brazos cruzados",
    "unknown": "desconocida",
}

MOVIMIENTO_ES: dict[str, str] = {
    "still": "quieto",
    "moving": "en movimiento",
}

ATENCION_ES: dict[str, str] = {
    "attentive": "atento",
    "distracted": "distraído",
    "absent": "ausente",
    "unknown": "sin datos",
}

POSICION_ES: dict[str, str] = {
    "left": "a la izquierda",
    "center": "al centro",
    "right": "a la derecha",
}


def distancia_es(relative_size: float) -> str:
    """Distancia aproximada a partir del tamaño relativo del rostro.

    No es una medida métrica y no pretende serlo: es la única estimación honesta
    que se puede hacer con una webcam sin calibrar. Los cortes salen de rostros
    típicos a 640x480: ~4% del encuadre a un brazo de distancia.
    """
    a = float(relative_size or 0.0)
    if a <= 0.0:
        return "desconocida"
    if a >= 0.13:
        return "muy cerca"
    if a >= 0.055:
        return "cerca"
    if a >= 0.018:
        return "a distancia normal"
    return "lejos"


class LiveVisionState:
    """El `vision_state` compartido. Un único objeto por instancia de YUE."""

    def __init__(self, *, gaze_hold: float = 0.0) -> None:
        self._lock = threading.RLock()
        self._state: dict[str, Any] = _estado_vacio()

        # --- señales derivadas que necesitan historia -------------------
        self._mirando_desde: float = 0.0        # inicio del contacto visual actual
        self._presente_desde: float = 0.0       # inicio de la presencia actual
        self._ultima_emocion: str = "neutral"
        self._emocion_desde: float = 0.0
        self._objetos_vistos: dict[str, float] = {}   # etiqueta -> primera vez
        self._ultimo_gesto: tuple[str, str, float] = ("", "", 0.0)  # gesto, mano, ts
        self._gaze_hold = float(gaze_hold)

    # ==================================================================
    # Escritura (la llama el motor de percepción, en su hilo)
    # ==================================================================
    def update_from_snapshot(self, snapshot: dict, now: float | None = None) -> dict:
        """Traduce un snapshot del motor y actualiza el estado. Devuelve el nuevo."""
        now = time.time() if now is None else float(now)
        if not snapshot:
            return self.get()

        camara = snapshot.get("camera", {}) or {}
        presencia = snapshot.get("presence", {}) or {}
        atencion = snapshot.get("attention", {}) or {}
        afecto = snapshot.get("affective", {}) or {}
        objetos = snapshot.get("objects", []) or []
        manos = snapshot.get("hands", []) or []
        room = snapshot.get("room", {}) or {}
        texto = snapshot.get("text") or {}
        postura = snapshot.get("pose", {}) or {}
        acciones = snapshot.get("actions", []) or []

        with self._lock:
            personas = self._traducir_personas(presencia, now)
            mirada = self._traducir_mirada(atencion, now)
            emociones = self._traducir_emociones(afecto, now)
            objetos_es, nuevos = self._traducir_objetos(objetos, now)

            estado = {
                "camara": {
                    "activa": bool(camara.get("active", False)),
                    "fps": float(camara.get("fps", 0.0) or 0.0),
                    "indice": camara.get("index"),
                },
                "personas": personas,
                "emociones": emociones,
                "mirada": mirada,
                "manos": self._traducir_manos(manos),
                "gestos": self._traducir_gestos(now),
                "postura": self._traducir_postura(postura, acciones),
                "objetos": objetos_es,
                "objetos_nuevos": nuevos,
                "texto": self._traducir_texto(texto, now),
                "escena": self._traducir_escena(room),
                "acciones": self._traducir_acciones(acciones),
                "privacidad": snapshot.get("privacy", {}) or {},
                "actualizado": now,
            }
            self._state = estado
            return dict(estado)

    def registrar_gesto(self, gesto: str, mano: str = "unknown",
                        now: float | None = None) -> None:
        """Anota el último gesto reconocido (lo llama la capa reactiva).

        Se guarda aparte del snapshot porque un gesto es PUNTUAL: si esperáramos
        al siguiente refresco del snapshot ya habría desaparecido.
        """
        now = time.time() if now is None else float(now)
        with self._lock:
            self._ultimo_gesto = (str(gesto or ""), str(mano or "unknown"), now)
            if isinstance(self._state.get("gestos"), dict):
                self._state["gestos"] = self._traducir_gestos(now)

    # ==================================================================
    # Traductores internos (siempre bajo el lock)
    # ==================================================================
    def _traducir_personas(self, presencia: dict, now: float) -> dict:
        gente = presencia.get("people", []) or []
        count = int(presencia.get("count", len(gente)))

        if count > 0:
            if not self._presente_desde:
                self._presente_desde = now
        else:
            self._presente_desde = 0.0

        principal = None
        for p in gente:
            if p.get("is_primary"):
                principal = p
                break
        if principal is None and gente:
            principal = gente[0]

        detalle = None
        if principal:
            detalle = {
                "id": principal.get("person_id"),
                "posicion": POSICION_ES.get(principal.get("position", ""), "al centro"),
                "posicion_raw": principal.get("position", "center"),
                "distancia": distancia_es(principal.get("relative_size", 0.0)),
                "tamano_relativo": float(principal.get("relative_size", 0.0) or 0.0),
                "acercandose": principal.get("approaching", "stable"),
                "visible_desde": float(principal.get("visible_for", 0.0) or 0.0),
                "caja": principal.get("bounding_box"),
            }

        return {
            "hay_persona": count > 0,
            "count": count,
            "principal": detalle,
            "lista": list(gente),
            "segundos_presente": round(now - self._presente_desde, 2) if self._presente_desde else 0.0,
        }

    def _traducir_mirada(self, atencion: dict, now: float) -> dict:
        mira = bool(atencion.get("looking_at_camera", False))
        if mira:
            if not self._mirando_desde:
                self._mirando_desde = now
        else:
            self._mirando_desde = 0.0
        segundos = round(now - self._mirando_desde, 2) if self._mirando_desde else 0.0
        estado = atencion.get("state", "unknown")
        return {
            "mira_a_yue": mira,
            "segundos_mirando": segundos,
            "estado": estado,
            "estado_es": ATENCION_ES.get(estado, "sin datos"),
            "proporcion_atencion": float(atencion.get("attention_ratio", 0.0) or 0.0),
            "ausente_desde": float(atencion.get("absent_for", 0.0) or 0.0),
        }

    def _traducir_emociones(self, afecto: dict, now: float) -> dict:
        estado = afecto.get("affective_state", "undetermined")
        conf = float(afecto.get("confidence", 0.0) or 0.0)
        cambio = estado != self._ultima_emocion
        if cambio:
            self._ultima_emocion = estado
            self._emocion_desde = now
        return {
            "emocion": EMOCIONES_ES.get(estado, estado),
            "emocion_raw": estado,
            "confianza": round(conf, 3),
            "certeza": afecto.get("certainty", "low"),
            "descripcion": afecto.get("safe_description", ""),
            "senales": list(afecto.get("signals", []) or []),
            "cambio": bool(cambio),
            "desde_hace": round(now - self._emocion_desde, 2) if self._emocion_desde else 0.0,
        }

    def _traducir_objetos(self, objetos: list, now: float) -> tuple[list, list]:
        salida, nuevos = [], []
        vistos_ahora = set()
        for o in objetos:
            etiqueta = o.get("translated_label") or o.get("label", "")
            if not etiqueta:
                continue
            vistos_ahora.add(etiqueta)
            if etiqueta not in self._objetos_vistos:
                self._objetos_vistos[etiqueta] = now
                nuevos.append(etiqueta)
            salida.append({
                "etiqueta": etiqueta,
                "etiqueta_raw": o.get("label", ""),
                "confianza": round(float(o.get("confidence", 0.0) or 0.0), 3),
                "id": o.get("object_id"),
                "visto_desde": round(now - self._objetos_vistos[etiqueta], 1),
            })
        # Olvida lo que ya no está para que "nuevo" vuelva a significar nuevo.
        for etiqueta in list(self._objetos_vistos):
            if etiqueta not in vistos_ahora and (now - self._objetos_vistos[etiqueta]) > 30.0:
                self._objetos_vistos.pop(etiqueta, None)
        return salida, nuevos

    def _traducir_manos(self, manos: list) -> dict:
        detalle = []
        for m in manos:
            lado = m.get("hand", "unknown")
            detalle.append({
                "lado": {"left": "izquierda", "right": "derecha"}.get(lado, "desconocida"),
                "lado_raw": lado,
                "dedos": int(m.get("finger_count", 0) or 0),
                "dedos_extendidos": list(m.get("fingers_extended", []) or []),
                "palma": m.get("palm_orientation", "unknown"),
                "gesto": m.get("gesture_es") or m.get("gesture", "none"),
                "cerca_del_rostro": bool(m.get("near_face", False)),
                "movimiento": m.get("motion", "still"),
                "sostiene": m.get("holding", ""),
                "muneca": m.get("wrist"),
            })
        return {
            "count": len(detalle),
            "lista": detalle,
            "dedos_totales": sum(d["dedos"] for d in detalle),
        }

    def _traducir_gestos(self, now: float) -> dict:
        gesto, mano, ts = self._ultimo_gesto
        if not gesto or (now - ts) > 20.0:
            return {"ultimo": "", "mano": "", "hace": 0.0, "reciente": False}
        return {
            "ultimo": gesto,
            "mano": {"left": "izquierda", "right": "derecha"}.get(mano, ""),
            "mano_raw": mano,
            "hace": round(now - ts, 2),
            "reciente": (now - ts) <= 5.0,
        }

    def _traducir_postura(self, postura: dict, acciones: list) -> dict:
        estado = postura.get("state", "unknown")
        # Las acciones pueden precisar la postura mejor que los landmarks sueltos
        # (p. ej. brazos cruzados es una acción sostenida, no una pose puntual).
        claves = {a.get("action", "") for a in acciones}
        if "arms_crossed" in claves:
            estado = "arms_crossed"
        elif "possible_fall" in claves or "lying" in claves:
            estado = "lying"
        return {
            "estado": POSTURAS_ES.get(estado, estado),
            "estado_raw": estado,
            "visible": bool(postura.get("visible", False)),
            "brazo_izq_arriba": bool(postura.get("left_arm_raised", False)),
            "brazo_der_arriba": bool(postura.get("right_arm_raised", False)),
            "mano_levantada": bool(postura.get("left_arm_raised", False))
                              or bool(postura.get("right_arm_raised", False)),
            "brazos_cruzados": estado == "arms_crossed",
            "movimiento": MOVIMIENTO_ES.get(postura.get("movement", "still"), "quieto"),
            "movimiento_raw": postura.get("movement", "still"),
        }

    @staticmethod
    def _traducir_texto(texto: dict, now: float) -> dict:
        if not texto or not texto.get("text"):
            return {"texto": "", "hay_texto": False, "confianza": 0.0, "hace": 0.0}
        ts = float(texto.get("timestamp", now) or now)
        return {
            "texto": texto.get("text", ""),
            "hay_texto": True,
            "confianza": round(float(texto.get("confidence", 0.0) or 0.0), 3),
            "idioma": texto.get("language", ""),
            "estable": bool(texto.get("stable", False)),
            "hace": round(max(0.0, now - ts), 1),
        }

    @staticmethod
    def _traducir_escena(room: dict) -> dict:
        return {
            "descripcion": room.get("description", ""),
            "lugar": room.get("scene_type", "desconocido"),
            "confianza_lugar": float(room.get("scene_confidence", 0.0) or 0.0),
            "iluminacion": room.get("lighting", ""),
            "cambios": list(room.get("changes", []) or []),
        }

    @staticmethod
    def _traducir_acciones(acciones: list) -> list:
        return [
            {
                "accion": a.get("action_es") or a.get("action", ""),
                "accion_raw": a.get("action", ""),
                "confianza": round(float(a.get("confidence", 0.0) or 0.0), 3),
                "duracion": float(a.get("duration", 0.0) or 0.0),
            }
            for a in (acciones or [])
        ]

    # ==================================================================
    # Lectura (la usa TODO el resto de YUE)
    # ==================================================================
    def get(self) -> dict:
        """Copia superficial del estado. Segura para leer desde cualquier hilo."""
        with self._lock:
            estado = dict(self._state)
        estado["actualizado_hace"] = round(
            max(0.0, time.time() - float(estado.get("actualizado", 0.0) or 0.0)), 2
        ) if estado.get("actualizado") else float("inf")
        return estado

    def fresco(self, max_age: float = 5.0) -> bool:
        """False si el estado está viejo (cámara caída, percepción parada…)."""
        with self._lock:
            ts = float(self._state.get("actualizado", 0.0) or 0.0)
        return bool(ts) and (time.time() - ts) <= float(max_age)

    # --- atajos de conveniencia (lo que el resto de YUE pregunta de verdad) ---
    def hay_persona(self) -> bool:
        return bool(self.get().get("personas", {}).get("hay_persona"))

    def cuantas_personas(self) -> int:
        return int(self.get().get("personas", {}).get("count", 0))

    def emocion(self) -> str:
        return str(self.get().get("emociones", {}).get("emocion", "indeterminado"))

    def mira_a_yue(self) -> bool:
        return bool(self.get().get("mirada", {}).get("mira_a_yue"))

    def segundos_mirando(self) -> float:
        return float(self.get().get("mirada", {}).get("segundos_mirando", 0.0))

    def objetos(self) -> list[str]:
        return [o["etiqueta"] for o in self.get().get("objetos", [])]

    def ve_objeto(self, *etiquetas: str) -> bool:
        vistos = {e.lower() for e in self.objetos()}
        return any(e.lower() in vistos for e in etiquetas)

    def postura(self) -> str:
        return str(self.get().get("postura", {}).get("estado", "desconocida"))

    def texto_leido(self) -> str:
        return str(self.get().get("texto", {}).get("texto", ""))

    # --- resumen legible (para logs, /reporte y el prompt del modelo) --------
    def resumen(self) -> str:
        s = self.get()
        if not s.get("camara", {}).get("activa"):
            return "La cámara no está activa."
        partes: list[str] = []
        personas = s.get("personas", {})
        if not personas.get("hay_persona"):
            partes.append("no veo a nadie")
        else:
            n = personas.get("count", 1)
            partes.append("veo 1 persona" if n == 1 else f"veo {n} personas")
            principal = personas.get("principal") or {}
            if principal.get("posicion"):
                partes.append(f"{principal['posicion']}, {principal.get('distancia', '')}".strip(", "))
            postura = s.get("postura", {})
            if postura.get("visible"):
                partes.append(f"postura: {postura.get('estado')}")
            emo = s.get("emociones", {})
            if emo.get("emocion_raw") not in ("undetermined", "neutral", None):
                partes.append(f"ánimo aparente: {emo.get('emocion')}")
            if s.get("mirada", {}).get("mira_a_yue"):
                partes.append("me está mirando")
        objetos = self.objetos()
        if objetos:
            partes.append("objetos: " + ", ".join(objetos[:5]))
        return "; ".join(p for p in partes if p) + "."

    def reset(self) -> None:
        with self._lock:
            self._state = _estado_vacio()
            self._mirando_desde = 0.0
            self._presente_desde = 0.0
            self._objetos_vistos.clear()
            self._ultimo_gesto = ("", "", 0.0)
            self._ultima_emocion = "neutral"
            self._emocion_desde = 0.0


def _estado_vacio() -> dict:
    """Estado inicial: la forma completa, todo apagado. Nunca devuelve None."""
    return {
        "camara": {"activa": False, "fps": 0.0, "indice": None},
        "personas": {"hay_persona": False, "count": 0, "principal": None,
                     "lista": [], "segundos_presente": 0.0},
        "emociones": {"emocion": "indeterminado", "emocion_raw": "undetermined",
                      "confianza": 0.0, "certeza": "low", "descripcion": "",
                      "senales": [], "cambio": False, "desde_hace": 0.0},
        "mirada": {"mira_a_yue": False, "segundos_mirando": 0.0, "estado": "unknown",
                   "estado_es": "sin datos", "proporcion_atencion": 0.0,
                   "ausente_desde": 0.0},
        "manos": {"count": 0, "lista": [], "dedos_totales": 0},
        "gestos": {"ultimo": "", "mano": "", "hace": 0.0, "reciente": False},
        "postura": {"estado": "desconocida", "estado_raw": "unknown", "visible": False,
                    "brazo_izq_arriba": False, "brazo_der_arriba": False,
                    "mano_levantada": False, "brazos_cruzados": False,
                    "movimiento": "quieto", "movimiento_raw": "still"},
        "objetos": [],
        "objetos_nuevos": [],
        "texto": {"texto": "", "hay_texto": False, "confianza": 0.0, "hace": 0.0},
        "escena": {"descripcion": "", "lugar": "desconocido", "confianza_lugar": 0.0,
                   "iluminacion": "", "cambios": []},
        "acciones": [],
        "privacidad": {},
        "actualizado": 0.0,
    }
