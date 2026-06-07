"""The look — the exact Wavmaster palette (DECISIONS D9) applied via Qt QSS,
plus the colored health-dot icons used in the sidebar and Options.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from ..plugins.result import DOT_COLOR, Status

# Wavmaster RGB palette (captured from wavmaster-dev/ui.py)
GREEN = (80, 230, 150)     # on / active / playing / favourited
BLUE = (90, 170, 255)      # play / now-playing
VIOLET = (185, 130, 245)   # accent
RED = (205, 50, 45)        # stop / error
AMBER = (210, 150, 60)     # warning / degraded
GREY = (120, 120, 120)     # off / disabled

def _rgb(c: tuple[int, int, int]) -> str:
    return f"rgb({c[0]},{c[1]},{c[2]})"


def _hex(c: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % (c[0], c[1], c[2])


def _mix(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _luminance(c) -> float:
    return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255


def _contrast(c) -> str:
    """'black' or 'white' — whichever reads on a given background color."""
    return "black" if _luminance(c) > 0.55 else "white"


def _mk(bg, text, accent, on, on_text, dark):
    """Build a full palette from a terminal theme's key colors (bg/fg/green/blue), deriving the
    panel/groove/scroll shades. Lets us add Alacritty-style palettes compactly. `accent` stays the
    'on/active' (green-family) highlight so lit==on holds; `on`/`on_text` are selection/hover."""
    W, K = (255, 255, 255), (0, 0, 0)
    if dark:
        panel, panel2 = _mix(bg, W, 0.06), _mix(bg, W, 0.13)
        groove, scroll, scroll_hi = _mix(bg, K, 0.35), _mix(bg, W, 0.22), _mix(bg, W, 0.34)
    else:
        panel, panel2 = _mix(bg, W, 0.55), _mix(bg, K, 0.07)
        groove, scroll, scroll_hi = _mix(bg, K, 0.18), _mix(bg, K, 0.14), _mix(bg, K, 0.26)
    return dict(dark=dark, bg=bg, panel=panel, panel2=panel2, text=text, on=on, on_text=on_text,
                accent=accent, groove=groove, scroll=scroll, scroll_hi=scroll_hi)


# Each theme = a structural palette. `accent` is the "on/active" highlight (a green-family
# color per theme, so "lit = on" stays true everywhere); `on`/`on_text` are the selection +
# hover colors; `dark` drives is_dark(). The original Wavmaster dark/light are unchanged.
THEMES: dict[str, dict] = {
    "dark":  dict(dark=True,  bg=(28, 30, 33),  panel=(37, 40, 44),  panel2=(44, 48, 53),
                  text=(224, 226, 229), on=(40, 95, 60),  on_text="white", accent=GREEN,
                  groove=(18, 20, 23),  scroll=(70, 75, 82),  scroll_hi=(98, 104, 112)),
    "light": dict(dark=False, bg=(236, 238, 241), panel=(251, 251, 252), panel2=(225, 228, 233),
                  text=(28, 30, 33),    on=(70, 200, 130), on_text="black", accent=GREEN,
                  groove=(148, 154, 162), scroll=(178, 183, 190), scroll_hi=(150, 155, 163)),
    "dracula": dict(dark=True, bg=(40, 42, 54), panel=(52, 55, 70), panel2=(68, 71, 90),
                    text=(248, 248, 242), on=(98, 114, 164), on_text="white", accent=(80, 250, 123),
                    groove=(33, 34, 44), scroll=(68, 71, 90), scroll_hi=(98, 114, 164)),
    "nord": dict(dark=True, bg=(46, 52, 64), panel=(59, 66, 82), panel2=(67, 76, 94),
                 text=(216, 222, 233), on=(94, 129, 172), on_text="white", accent=(163, 190, 140),
                 groove=(39, 44, 53), scroll=(76, 86, 106), scroll_hi=(94, 129, 172)),
    "solarized-dark": dict(dark=True, bg=(0, 43, 54), panel=(7, 54, 66), panel2=(0, 33, 43),
                           text=(147, 161, 161), on=(38, 139, 210), on_text="white", accent=(133, 153, 0),
                           groove=(0, 25, 33), scroll=(88, 110, 117), scroll_hi=(101, 123, 131)),
    "solarized-light": dict(dark=False, bg=(238, 232, 213), panel=(253, 246, 227), panel2=(221, 214, 193),
                            text=(88, 110, 117), on=(38, 139, 210), on_text="white", accent=(133, 153, 0),
                            groove=(213, 206, 185), scroll=(204, 196, 172), scroll_hi=(184, 176, 149)),
    "gruvbox-dark": dict(dark=True, bg=(40, 40, 40), panel=(60, 56, 54), panel2=(80, 73, 69),
                         text=(235, 219, 178), on=(69, 133, 136), on_text="white", accent=(184, 187, 38),
                         groove=(29, 32, 33), scroll=(80, 73, 69), scroll_hi=(102, 92, 84)),
    # --- terminal palettes, from the Alacritty theme repo (accent = each theme's green) ---
    "xterm":          _mk(bg=(0, 0, 0),       text=(255, 255, 255), accent=(0, 205, 0),    on=(92, 92, 255),   on_text="white", dark=True),
    "ubuntu":         _mk(bg=(48, 10, 36),    text=(238, 238, 236), accent=(138, 226, 52), on=(52, 101, 164),  on_text="white", dark=True),
    "tokyo-night":    _mk(bg=(26, 27, 38),    text=(169, 177, 214), accent=(158, 206, 106), on=(122, 162, 247), on_text="black", dark=True),
    "monokai-pro":    _mk(bg=(45, 42, 46),    text=(255, 241, 243), accent=(173, 218, 120), on=(168, 169, 235), on_text="black", dark=True),
    "gruvbox-light":  _mk(bg=(251, 241, 199), text=(60, 56, 54),    accent=(152, 151, 26), on=(69, 133, 136),  on_text="white", dark=False),
    "dark-pastels":   _mk(bg=(44, 44, 44),    text=(220, 220, 204), accent=(114, 213, 163), on=(154, 184, 215), on_text="black", dark=True),
    "cyberpunk-neon": _mk(bg=(0, 11, 30),     text=(10, 189, 198),  accent=(0, 255, 0),    on=(113, 28, 145),  on_text="white", dark=True),
    # --- retro phosphor / CRT palettes, from clausqr/claude-themes (colors only; the CRT
    #     scanline/glow/flicker EFFECTS don't port — just the palettes) ---
    "amber":          _mk(bg=(10, 5, 0),      text=(255, 176, 0),   accent=(255, 176, 0),   on=(255, 176, 0),  on_text="black", dark=True),
    "green-phosphor": _mk(bg=(0, 26, 0),      text=(51, 255, 51),   accent=(51, 255, 51),   on=(51, 255, 51),  on_text="black", dark=True),
    "white-phosphor": _mk(bg=(10, 10, 10),    text=(230, 230, 230), accent=(230, 230, 230), on=(200, 200, 200), on_text="black", dark=True),
    "cga":            _mk(bg=(0, 0, 0),       text=(255, 255, 255), accent=(85, 255, 255),  on=(85, 255, 255), on_text="black", dark=True),
    "crt":            _mk(bg=(0, 26, 0),      text=(57, 255, 85),   accent=(57, 255, 85),   on=(57, 255, 85),  on_text="black", dark=True),
    # Synthwave ships a generated wallpaper (sunset + neon grid) — see ui/wallpaper.py.
    "synthwave":     {**_mk(bg=(13, 11, 30),  text=(248, 248, 255), accent=(5, 217, 232),   on=(255, 42, 109), on_text="white", dark=True),
                      "wallpaper": "synthwave"},
}

# display label for the View → Theme menu (dict insertion order = menu order)
THEME_LABELS: dict[str, str] = {
    "dark": "Dark", "light": "Light", "dracula": "Dracula", "nord": "Nord",
    "solarized-dark": "Solarized Dark", "solarized-light": "Solarized Light",
    "gruvbox-dark": "Gruvbox Dark", "gruvbox-light": "Gruvbox Light",
    "xterm": "xterm", "ubuntu": "Ubuntu", "tokyo-night": "Tokyo Night",
    "monokai-pro": "Monokai Pro", "dark-pastels": "Dark Pastels", "cyberpunk-neon": "Cyberpunk Neon",
    "amber": "Amber", "green-phosphor": "Green Phosphor", "white-phosphor": "White Phosphor",
    "cga": "CGA", "crt": "CRT Green", "synthwave": "Synthwave",
}


def _theme(mode: str) -> dict:
    return THEMES.get(mode, THEMES["dark"])


def is_dark(mode: str) -> bool:
    return _theme(mode).get("dark", True)


def theme_wallpaper(mode: str) -> str:
    """The theme's built-in wallpaper spec: 'synthwave' (a generated background), a filename, or
    '' for none. Used as the default wallpaper when the user hasn't picked their own image."""
    return str(_theme(mode).get("wallpaper", "") or "")


def dialog_colors(mode: str) -> tuple[str, str]:
    """(card-background css, body-text hex) for the styled dialogs, per theme."""
    p = _theme(mode)
    return _rgb(p["panel"]), _hex(p["text"])


# ---- user themes: shareable JSON files in ~/.config/streamtuner-ng/themes/ ----
# a theme JSON carries these nine [r,g,b] colors, plus on_text (str) and dark (bool).
_THEME_RGB_KEYS = ("bg", "panel", "panel2", "text", "on", "accent", "groove", "scroll", "scroll_hi")
BUILTIN_THEMES = tuple(THEMES)        # the ones we ship — users can't overwrite these


def validate_theme(d: dict) -> dict:
    """Normalize a raw theme dict (from JSON) into our color dict, or raise ValueError."""
    if not isinstance(d, dict):
        raise ValueError("theme must be a JSON object")
    out: dict = {}
    for k in _THEME_RGB_KEYS:
        v = d.get(k)
        if (not isinstance(v, (list, tuple)) or len(v) != 3
                or not all(isinstance(x, int) and 0 <= x <= 255 for x in v)):
            raise ValueError(f"'{k}' must be [r, g, b] with three 0–255 integers")
        out[k] = tuple(v)
    out["on_text"] = str(d.get("on_text", "white"))
    out["dark"] = bool(d.get("dark", True))
    wp = d.get("wallpaper")
    if isinstance(wp, str) and wp.strip():
        out["wallpaper"] = wp.strip()        # optional: 'synthwave' or an image filename in the themes folder
    return out


def register_theme(tid: str, raw: dict) -> str:
    """Validate + add a theme to the live registry (overwrites a same-id user theme). Returns id."""
    THEMES[tid] = validate_theme(raw)
    THEME_LABELS[tid] = str(raw.get("name") or tid).strip() or tid
    return tid


def theme_export_dict(mode: str) -> dict:
    """A theme as a plain JSON-able dict (for Export / templating)."""
    p = _theme(mode)
    d: dict = {"name": THEME_LABELS.get(mode, mode), "dark": bool(p.get("dark", True)),
               "on_text": p["on_text"]}
    for k in _THEME_RGB_KEYS:
        d[k] = list(p[k])
    if p.get("wallpaper"):
        d["wallpaper"] = p["wallpaper"]
    return d


def user_theme_dir(config):
    from pathlib import Path
    d = Path(config.dir) / "themes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_theme_template(folder) -> None:
    """Drop a (non-loaded) example theme so authors have a starting point; refreshed each launch."""
    import json
    try:
        sample = theme_export_dict("dracula")
        sample["name"] = "My Theme (copy me, drop the underscore)"
        (folder / "_example.json").write_text(json.dumps(sample, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001 — a read-only themes dir must never crash startup
        pass


def load_user_themes(config) -> list[tuple[str, str]]:
    """Register every themes/*.json (skips _-prefixed templates). Returns [(file, error)]; a bad
    theme file is skipped, never crashes startup (the airlock principle, applied to themes)."""
    import json
    errors: list[tuple[str, str]] = []
    folder = user_theme_dir(config)
    _ensure_theme_template(folder)
    for path in sorted(folder.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            register_theme(path.stem, json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            errors.append((path.name, f"{type(e).__name__}: {e}"))
    return errors


def stylesheet(mode: str = "dark", translucent: bool = False) -> str:
    """`translucent=True` makes the panel surfaces semi-transparent so a wallpaper painted behind
    the central widget shows through. Text and the selection color stay opaque for readability."""
    p = _theme(mode)

    def _surf(c, a):                          # surface color; alpha<1 only when a wallpaper is active
        return f"rgba({c[0]},{c[1]},{c[2]},{a:.2f})" if a < 1.0 else _rgb(c)
    A = 0.80 if translucent else 1.0
    bg = _rgb(p["bg"])                         # window background stays opaque (the wallpaper is the central widget)
    panel, panel2 = _surf(p["panel"], A), _surf(p["panel2"], A)
    text, on, on_text = _rgb(p["text"]), _rgb(p["on"]), p["on_text"]
    # The lit/active color (checked buttons, volume slider/handle) = the theme's OWN highlight (`on`),
    # so "lit" matches the theme — never a generic green. The Wavmaster dark/light keep their bright
    # green accent (that's their identity / the green=on heritage).
    _lit = p["accent"] if mode in ("dark", "light") else p["on"]
    green, green_text = _rgb(_lit), _contrast(_lit)
    groove = _rgb(p["groove"])
    scroll, scroll_hi = _rgb(p["scroll"]), _rgb(p["scroll_hi"])
    return f"""
    QMainWindow, QDialog {{ background: {bg}; }}
    QWidget {{ color: {text}; font-size: 14px; }}
    QMenuBar {{ background: {panel2}; color: {text}; }}
    QMenu {{ background: {panel}; color: {text}; border: 1px solid rgb(120,120,120); padding: 4px; }}
    QMenu::item {{ padding: 5px 26px 5px 14px; border-radius: 4px; }}
    QMenuBar::item:selected, QMenu::item:selected {{ background: {on}; color: {on_text}; }}
    QMenu::separator {{ height: 1px; background: {panel2}; margin: 5px 8px; }}
    QComboBox QAbstractItemView {{ border: 1px solid rgb(120,120,120); background: {panel}; }}
    QStatusBar {{ background: {panel}; color: {text}; }}
    QLabel#paneHeader {{ background: {panel2}; color: {text}; padding: 5px 8px; font-weight: bold; }}
    QToolBar {{ background: {panel2}; border: none; spacing: 6px; padding: 5px; }}
    QWidget#playerBar {{ background: {panel2}; }}
    QLabel#artBox {{ background: {panel}; border: 1px solid rgb(120,120,120); border-radius: 6px; }}

    QListWidget, QTableView, QTreeView {{
        background: {panel}; border: 1px solid {panel2};
        selection-background-color: {on}; selection-color: {on_text}; outline: 0;
    }}
    QHeaderView::section {{
        background: {panel2}; color: {text};
        padding: 4px; border: none; border-right: 1px solid {bg};
    }}
    QListWidget::item {{ padding: 6px 8px; }}
    QListWidget::item:selected {{ background: {on}; color: {on_text}; }}

    QLineEdit {{
        background: {panel}; color: {text}; border: 1px solid {bg};
        border-radius: 6px; padding: 5px 8px;
    }}
    QPushButton, QToolButton {{
        background: {panel2}; border: 1px solid rgba(128,128,128,0.45); border-radius: 6px;
        padding: 6px 12px; color: {text};
    }}
    QPushButton:hover, QToolButton:hover {{ background: {on}; color: {on_text}; border-color: {on}; }}
    QPushButton:checked, QToolButton:checked {{ background: {green}; color: {green_text}; }}
    QSlider::groove:horizontal {{ height: 6px; background: {groove}; border-radius: 3px; }}
    QSlider::sub-page:horizontal {{ height: 6px; background: {green}; border-radius: 3px; }}
    QSlider::handle:horizontal {{
        width: 14px; margin: -5px 0; border-radius: 7px; background: {green};
        border: 1px solid {groove};
    }}
    QScrollBar:vertical {{ background: {bg}; width: 12px; margin: 0; border: none; }}
    QScrollBar::handle:vertical {{ background: {scroll}; min-height: 28px; border-radius: 5px; margin: 2px; }}
    QScrollBar::handle:vertical:hover {{ background: {scroll_hi}; }}
    QScrollBar:horizontal {{ background: {bg}; height: 12px; margin: 0; border: none; }}
    QScrollBar::handle:horizontal {{ background: {scroll}; min-width: 28px; border-radius: 5px; margin: 2px; }}
    QScrollBar::handle:horizontal:hover {{ background: {scroll_hi}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; background: none; border: none; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QCheckBox {{ spacing: 8px; }}
    QTabBar::tab {{ background: {panel}; padding: 7px 14px; }}
    QTabBar::tab:selected {{ background: {on}; color: {on_text}; }}
    """


def palette(mode: str = "dark"):
    """A QPalette matching the theme. Widgets the stylesheet doesn't explicitly paint (e.g. a
    dialog's tab pane) fall back to this instead of the OS palette — important on Windows, where
    dark mode would otherwise bleed a dark background into our LIGHT theme (and vice-versa)."""
    from PySide6.QtGui import QPalette

    p = _theme(mode)

    def c(key: str) -> QColor:
        return QColor(*p[key])

    pal = QPalette()
    pal.setColor(QPalette.Window, c("bg"))
    pal.setColor(QPalette.WindowText, c("text"))
    pal.setColor(QPalette.Base, c("panel"))
    pal.setColor(QPalette.AlternateBase, c("panel2"))
    pal.setColor(QPalette.Text, c("text"))
    pal.setColor(QPalette.Button, c("panel2"))
    pal.setColor(QPalette.ButtonText, c("text"))
    pal.setColor(QPalette.ToolTipBase, c("panel"))
    pal.setColor(QPalette.ToolTipText, c("text"))
    pal.setColor(QPalette.Highlight, c("on"))
    pal.setColor(QPalette.HighlightedText, QColor(p["on_text"]))
    dim = c("text")
    dim.setAlpha(110)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, dim)
    return pal


def status_icon(status: Status, size: int = 14) -> QIcon:
    """A filled circle in the health-status color (the sidebar/Options dot)."""
    rgb = DOT_COLOR.get(status, GREY)
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(*rgb))
    p.drawEllipse(2, 2, size - 4, size - 4)
    p.end()
    return QIcon(pm)


def channel_icon(favicon_path, size: int = 18, emoji: str = "") -> QIcon:
    """The service's favicon — or an emoji / faint neutral disc when there's no logo.
    No health dot: the sidebar stays clean; per-plugin health lives in Options → Plugins."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    fav = QPixmap(favicon_path) if (favicon_path and os.path.exists(favicon_path)) else None
    if fav is not None and not fav.isNull():
        p.drawPixmap(0, 0, fav.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    elif emoji:
        f = p.font()
        f.setPointSizeF(size * 0.72)
        p.setFont(f)
        p.drawText(QRect(0, 0, size, size), Qt.AlignCenter, emoji)
    else:                                       # no favicon yet & no emoji: faint neutral disc
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(140, 140, 140, 80))
        p.drawEllipse(2, 2, size - 4, size - 4)
    p.end()
    return QIcon(pm)


def color(rgb: tuple[int, int, int]) -> QColor:
    return QColor(*rgb)
