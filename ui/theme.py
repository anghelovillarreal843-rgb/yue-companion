"""Tema visual de YUE: paleta holografica y estilos reutilizables.

Una estetica futurista y elegante (cristal oscuro + neon cian/violeta) que
comparten el chat y los globos de dialogo.
"""

# --- Paleta ---
BG0 = "#08060f"      # fondo mas oscuro
BG1 = "#0d0a1c"      # panel
BG2 = "#161033"      # panel claro
GLASS = "rgba(18, 14, 38, 215)"
CYAN = "#7df9ff"
VIOLET = "#b388ff"
MAGENTA = "#e0567a"
TEXT = "#ece7f5"
TEXT_DIM = "#9b8fc0"
LINE = "rgba(150, 120, 230, 120)"

FONT_UI = "Segoe UI"
FONT_MONO = "Consolas"

# Degradado de acento (cian -> violeta) para botones/titulos
ACCENT_GRAD = (f"qlineargradient(x1:0,y1:0,x2:1,y2:0,"
               f"stop:0 {VIOLET}, stop:1 {CYAN})")


def card_qss(name="card"):
    return f"""
        QFrame#{name}{{
            background: qlineargradient(x1:0,y1:0,x2:0.5,y2:1,
                        stop:0 #100b22, stop:1 #0a0717);
            border: 1px solid {LINE};
            border-radius: 20px;
        }}
    """


def primary_button_qss():
    return f"""
        QPushButton{{
            color:#06121a; border:none; border-radius:13px; font-weight:bold;
            padding:8px 14px;
            background:{ACCENT_GRAD};
        }}
        QPushButton:hover{{
            background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                       stop:0 #c6a4ff, stop:1 #9ffbff);
        }}
        QPushButton:pressed{{ background:{CYAN}; }}
        QPushButton:disabled{{ background:#3a3357; color:#8a82aa; }}
    """


def ghost_button_qss():
    return f"""
        QPushButton{{
            color:{CYAN}; background:rgba(125,249,255,16);
            border:1px solid rgba(125,249,255,90); border-radius:11px;
            padding:6px 12px; font-weight:600;
        }}
        QPushButton:hover{{ background:rgba(125,249,255,38); color:#eafdff; }}
        QPushButton:pressed{{ background:rgba(125,249,255,60); }}
    """


def close_button_qss():
    return (f"QPushButton{{color:{VIOLET};background:rgba(180,140,255,28);border:none;"
            f"border-radius:15px;font-weight:bold;font-size:14px;}}"
            f"QPushButton:hover{{background:{MAGENTA};color:white;}}")
