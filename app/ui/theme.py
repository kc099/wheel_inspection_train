"""Color palette and shared style constants.

Mirrors App.xaml from the original WPF app (see README_PYTHON_PORT.md section 2).
Reproduce these hex values exactly.
"""

# --- Backgrounds ---
WINDOW_BG = "#C8E6C9"        # Window / header background (light green)
CARD_BG = "#FFFFFF"          # Card / frame background
CARD_BORDER = "#AFCDB1"      # Card border
IMAGE_BG = "#222222"         # Image area background
FRAME_BG = "#F3F6EC"         # Inner result frame (light)
STATUS_BAR_BG = "#2E7D32"    # Status bar background (dark green)

# --- Text ---
TITLE_TEXT = "#0052CC"       # Title text (blue)
TEXT_PRIMARY = "#1B3A17"     # Primary text
TEXT_MUTED = "#5F6F52"       # Muted text
TEXT_ON_LIGHT = "#3A4A2E"    # Text on light
PLACEHOLDER = "#BBBBBB"      # Placeholder text

# --- Buttons ---
BTN_NORMAL = "#1B5E20"
BTN_NORMAL_HOVER = "#2E7D32"
BTN_NORMAL_PRESSED = "#124017"
BTN_TEXT = "#FFFFFF"

BTN_DANGER = "#C62828"       # Primary/danger ("Run Inspection")
BTN_DANGER_HOVER = "#D84343"
BTN_DANGER_PRESSED = "#A51F1F"

# --- Status dots ---
STATUS_OK = "#4CAF50"        # green
STATUS_WARN = "#FFA726"      # amber
STATUS_ERROR = "#E53935"     # red

# Recognized-row highlight for the All-Models grid
ROW_HIGHLIGHT = "#DFF0C7"
ROW_ALT = "#F3F6EC"


def normal_button_qss() -> str:
    return f"""
        QPushButton {{
            background-color: {BTN_NORMAL};
            color: {BTN_TEXT};
            border: none;
            border-radius: 5px;
            font-size: 13px;
            font-weight: 600;
        }}
        QPushButton:hover {{ background-color: {BTN_NORMAL_HOVER}; }}
        QPushButton:pressed {{ background-color: {BTN_NORMAL_PRESSED}; }}
        QPushButton:disabled {{ background-color: #9DB39F; color: #E8F1E9; }}
    """


def danger_button_qss() -> str:
    return f"""
        QPushButton {{
            background-color: {BTN_DANGER};
            color: {BTN_TEXT};
            border: none;
            border-radius: 5px;
            font-size: 15px;
            font-weight: 700;
        }}
        QPushButton:hover {{ background-color: {BTN_DANGER_HOVER}; }}
        QPushButton:pressed {{ background-color: {BTN_DANGER_PRESSED}; }}
    """


def card_qss() -> str:
    return f"""
        QFrame#Card {{
            background-color: {CARD_BG};
            border: 1px solid {CARD_BORDER};
            border-radius: 8px;
        }}
    """
