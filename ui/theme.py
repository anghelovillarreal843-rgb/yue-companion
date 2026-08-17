"""Tema visual de YUE: paleta morada sobria, sin neones ni transparencias.

Morado oscuro profundo sobre superficies solidas (nada de cristal translucido,
nada de blur) con un lila lavanda suave como unico acento. Diseñado para
descansar la vista en conversaciones largas y lejos del tipico aspecto
"chatbot de IA" (cian/magenta neon sobre vidrio difuminado).
"""

# --- Paleta (todo solido, sin alfa) ---
BG0 = "#14111d"      # fondo mas oscuro
BG1 = "#1b1727"      # panel
BG2 = "#242032"      # panel claro
GLASS = "#1e1a2c"    # superficie del chat (opaca)
ACCENT = "#b9a3e8"   # lila lavanda suave, unico color de acento
ACCENT2 = "#8f7fc0"  # morado medio (matices secundarios)
MAGENTA = "#c05a72"  # rosa terroso (hover de botones de cierre)
TEXT = "#efeaf9"
TEXT_DIM = "#a89bc4"
LINE = "#332c4d"     # bordes suaves, nunca luminosos

FONT_UI = "Segoe UI"
FONT_MONO = "Consolas"

# Degradado de acento (morado medio -> lila) para botones/titulos
ACCENT_GRAD = (f"qlineargradient(x1:0,y1:0,x2:1,y2:0,"
               f"stop:0 {ACCENT2}, stop:1 {ACCENT})")


def card_qss(name="card"):
    return f"""
        QFrame#{name}{{
            background: qlineargradient(x1:0,y1:0,x2:0.5,y2:1,
                        stop:0 #242032, stop:1 #1b1727);
            border: 1px solid {LINE};
            border-radius: 20px;
        }}
    """


def primary_button_qss():
    return f"""
        QPushButton{{
            color:#1c1630; border:none; border-radius:13px; font-weight:bold;
            padding:8px 14px;
            background:{ACCENT_GRAD};
        }}
        QPushButton:hover{{
            background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                       stop:0 #a08ed0, stop:1 #c7b4f0);
        }}
        QPushButton:pressed{{ background:{ACCENT}; }}
        QPushButton:disabled{{ background:#2a2440; color:#7d74a0; }}
    """


def ghost_button_qss():
    return f"""
        QPushButton{{
            color:{ACCENT}; background:rgba(185,163,232,16);
            border:1px solid rgba(185,163,232,80); border-radius:11px;
            padding:6px 12px; font-weight:600;
        }}
        QPushButton:hover{{ background:rgba(185,163,232,32); color:#f2edff; }}
        QPushButton:pressed{{ background:rgba(185,163,232,50); }}
    """


def close_button_qss():
    return (f"QPushButton{{color:{ACCENT2};background:rgba(143,127,192,22);border:none;"
            f"border-radius:15px;font-weight:bold;font-size:14px;}}"
            f"QPushButton:hover{{background:{MAGENTA};color:white;}}")
