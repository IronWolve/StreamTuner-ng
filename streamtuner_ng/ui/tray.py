"""System tray (radiotray-ng-style, DECISIONS D18). A header with what is playing, Play/Stop, Mute,
Favorites quick-play, show/hide, quit. The icon is a chosen emoji (a music note by
default — a logo doesn't read at tray size). Returns None when no tray host is available
(e.g. GNOME without an AppIndicator extension) so the app stays a normal window.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget, QWidgetAction

# selectable tray icons: key -> (emoji, label). The note is the default.
TRAY_ICONS: dict[str, tuple[str, str]] = {
    "note":       ("🎵", "Music note"),
    "notes":      ("🎶", "Music notes"),
    "speaker":    ("🔊", "Speaker"),
    "radio":      ("📻", "Radio"),
    "headphones": ("🎧", "Headphones"),
    "antenna":    ("📡", "Broadcast"),
}
DEFAULT_TRAY_ICON = "note"
MUTED_EMOJI = "🔇"            # the tray icon and the Mute entry show this while the sound is muted
UNMUTED_EMOJI = "🔊"


def emoji_icon(emoji: str, size: int = 64) -> QIcon:
    """Render an emoji glyph to a QIcon — sharp at tray size, unlike a shrunk logo."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    f = p.font()
    f.setPointSizeF(size * 0.72)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, emoji)
    p.end()
    return QIcon(pm)


def tray_icon(config) -> QIcon:
    key = config.get("tray_icon", DEFAULT_TRAY_ICON)
    emoji = TRAY_ICONS.get(key, TRAY_ICONS[DEFAULT_TRAY_ICON])[0]
    return emoji_icon(emoji)


def _star(cx: float, cy: float, r: float) -> QPolygonF:
    import math
    points = []
    for i in range(10):
        radius = r if i % 2 == 0 else r * 0.46
        angle = -math.pi / 2 + i * math.pi / 5
        points.append(QPointF(cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return QPolygonF(points)


def _menu_icon(name: str, color: str, ratio: float, size: int = 16) -> QIcon:
    """Thin outline icons (drawn here, in a 24-unit grid with round ends) at the menu's screen pixel density."""
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size / 24, size / 24)
    pen = QPen(QColor(color), 2.0)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    path = QPainterPath()
    speaker = QPolygonF([QPointF(x, y) for x, y in ((3.5, 9.5), (7, 9.5), (11.5, 5.5), (11.5, 18.5), (7, 14.5), (3.5, 14.5))])
    if name == "play":
        path.addPolygon(QPolygonF([QPointF(7, 4.5), QPointF(7, 19.5), QPointF(19.5, 12)]))
        path.closeSubpath()
    elif name == "stop":
        path.addRoundedRect(QRectF(6, 6, 12, 12), 2, 2)
    elif name == "favorite":
        path.addPolygon(_star(12, 12.6, 9.2))
        path.closeSubpath()
    elif name == "favorites":
        for cx, cy in ((12, 7), (6.6, 16.4), (17.4, 16.4)):
            path.addPolygon(_star(cx, cy, 4.4))
            path.closeSubpath()
    elif name == "window":
        path.addRoundedRect(QRectF(3, 5, 18, 14), 2.5, 2.5)
        path.moveTo(3, 9.5)
        path.lineTo(21, 9.5)
        path.moveTo(6.6, 7.3)
        path.lineTo(6.7, 7.3)
    elif name in ("volume", "volume-off"):
        path.addPolygon(speaker)
        path.closeSubpath()
        path.moveTo(15, 9.2)
        path.quadTo(17.4, 12, 15, 14.8)
        if name == "volume":
            path.moveTo(17.9, 6.6)
            path.quadTo(22, 12, 17.9, 17.4)
        else:
            path.moveTo(4, 4)
            path.lineTo(20, 20)
    else:                                              # quit: a cross
        path.moveTo(6.5, 6.5)
        path.lineTo(17.5, 17.5)
        path.moveTo(17.5, 6.5)
        path.lineTo(6.5, 17.5)
    painter.drawPath(path)
    painter.end()
    icon = QIcon(pixmap)
    icon.addPixmap(pixmap, QIcon.Disabled)
    return icon


HEADER_WIDTH = 286             # the header block is this wide (px) where Qt draws the menu: 300 with its padding and border
WRAP_CHARS = 42                # and the title wraps at about this many characters where it is plain text
TITLE_LINES = 3


def wrap_title(text: str, width: int = WRAP_CHARS, lines: int = TITLE_LINES) -> list[str]:
    """A long song title cut into at most ``lines`` lines at word breaks, the last one ending in … when cut."""
    import textwrap
    parts = textwrap.wrap(" ".join(str(text).split()), width=width, break_long_words=True, break_on_hyphens=False)
    if len(parts) > lines:
        parts = parts[:lines]
        parts[-1] = parts[-1][:max(1, width - 1)].rstrip() + "…"
    return parts or [""]


def compose_header(playing: bool, song: str, station: str, service: str, bitrate=0, codec: str = "") -> tuple[str, str, str]:
    """(title, subtitle, info) for the top block of the tray menu."""
    if not playing:
        return "Not playing", "Pick a station to start", ""
    quality = " ".join(part for part in (f"{int(bitrate)}k" if bitrate else "", str(codec or "").upper()) if part)
    if song:
        return song, station, " · ".join(part for part in (quality, service) if part)
    return station or "Playing", service, quality


class _HeaderBlock(QWidget):
    """The title / station / info block where the platform lets Qt draw the menu (Windows): the title wraps."""

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedWidth(HEADER_WIDTH)
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 2, 10, 8)             # the same left edge as the item text
        box.setSpacing(0)
        self.title, self.subtitle, self.info = QLabel(), QLabel(), QLabel()
        for label, top in ((self.title, 0), (self.subtitle, 5), (self.info, 3)):
            label.setWordWrap(True)
            label.setTextFormat(Qt.PlainText)
            label.setContentsMargins(0, top, 0, 0)
            box.addWidget(label)
        font = self.title.font()
        font.setPixelSize(14)
        font.setWeight(QFont.DemiBold)
        self.title.setFont(font)
        for label in (self.subtitle, self.info):
            small = label.font()
            small.setPixelSize(13)
            label.setFont(small)

    def set_text(self, title: str, subtitle: str, info: str) -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.subtitle.setVisible(bool(subtitle))
        self.info.setText(info)
        self.info.setVisible(bool(info))
        self.adjustSize()

    def set_colors(self, strong: str, muted: str) -> None:
        self.title.setStyleSheet(f"background: transparent; color: {strong};")
        for label in (self.subtitle, self.info):
            label.setStyleSheet(f"background: transparent; color: {muted};")


def build_tray(window, host, player, rich: bool | None = None):
    """The tray icon and its menu. ``rich`` draws the header as a wrapping block (the default only on Windows;
    Linux and macOS show tray menus through the desktop, which takes plain items only, so the title is wrapped
    by hand into disabled lines there)."""
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    import sys
    from .. import APP_NAME
    rich = sys.platform == "win32" if rich is None else rich

    tray = QSystemTrayIcon(tray_icon(window.config), window)
    tray.setToolTip("StreamTuner-ng")
    menu = QMenu()
    if rich:                                           # a rounded card: the window behind the corners is see-through
        menu.setWindowFlag(Qt.FramelessWindowHint, True)
        menu.setWindowFlag(Qt.NoDropShadowWindowHint, True)
        menu.setAttribute(Qt.WA_TranslucentBackground, True)

    header_lines: list = []
    header_block = None
    if rich:
        header_block = _HeaderBlock()
        header_action = QWidgetAction(menu)
        header_action.setDefaultWidget(header_block)
        menu.addAction(header_action)
    else:
        for _ in range(TITLE_LINES + 2):               # the wrapped title, the station, the info line
            line = menu.addAction("")
            line.setEnabled(False)
            line.setVisible(False)
            header_lines.append(line)
    menu.addSeparator()

    play_action = menu.addAction("Play", lambda: window._toggle_play())   # no "checked" argument: it would read as Stop
    mute_action = menu.addAction("Mute", lambda: window._toggle_mute())
    menu.addSeparator()
    fav_now_action = menu.addAction("Favorite this station", lambda: window._toggle_playing_favourite())
    fav_menu = menu.addMenu("Favorites")
    bm = host.channels.get("bookmarks")

    def rebuild_favorites() -> None:
        fav_menu.clear()
        rows = list(getattr(bm, "favourite", ()) or ())[:25] if bm else []
        if not rows:
            fav_menu.addAction("(none yet)").setEnabled(False)
            return
        for row in rows:
            snapshot = dict(row)
            fav_menu.addAction(snapshot.get("title", "?"),
                               lambda _=False, r=snapshot: window._play_row(r))

    # The menu is a view of mutable Favorites data. Refresh immediately before it opens so
    # additions/removals made after startup are reflected without rebuilding the tray icon.
    fav_menu.aboutToShow.connect(rebuild_favorites)
    rebuild_favorites()

    menu.addSeparator()
    show_action = menu.addAction(f"Show {APP_NAME}", window._toggle_window)
    if rich:
        menu.setDefaultAction(show_action)             # the style sheet colors the default item: the blue line, same layout as the rest
    menu.addSeparator()
    quit_action = menu.addAction("Quit", window._quit)

    colors: dict = {}
    state: dict = {"playing": False, "can_resume": False, "is_fav": False, "visible": True, "muted": False}

    def sync_mute(muted: bool) -> None:
        state["muted"] = bool(muted)
        mute_action.setText("Unmute" if muted else "Mute")
        ratio = menu.devicePixelRatioF()
        mute_action.setIcon(_menu_icon("volume-off", colors.get("red", "#ef7771"), ratio) if muted
                            else _menu_icon("volume", colors.get("fg", "#dddddd"), ratio))

    def apply_state(new: dict | None = None) -> None:
        """Show the window's current facts in the menu: the header, Play or Stop, the favourite entry, Show or Hide."""
        if new:
            state.update(new)
        title, subtitle, info = state.get("header", ("Not playing", "Pick a station to start", ""))
        if header_block is not None:
            header_block.set_text(title, subtitle, info)
        else:
            lines = [*wrap_title(title)[:TITLE_LINES], subtitle, info]
            lines = [line for line in lines if line]
            for slot, action in enumerate(header_lines):
                text = lines[slot] if slot < len(lines) else ""
                action.setText(text)
                action.setVisible(bool(text))
        playing = state["playing"]
        play_action.setText("Stop" if playing else ("Play last station" if state["can_resume"] else "Play"))
        play_action.setIcon(_menu_icon("stop" if playing else "play", colors.get("red" if playing else "green", "#888888"),
                                       menu.devicePixelRatioF()))
        fav_now_action.setVisible(playing)
        fav_now_action.setText("Remove favorite" if state["is_fav"] else "Favorite this station")
        show_action.setText(f"{'Hide' if state['visible'] else 'Show'} {APP_NAME}")
        sync_mute(state["muted"])

    def refresh_colors() -> None:
        # Use the active application palette, including temporary theme previews.
        # QSS does not reliably update a previously polished menu's own palette.
        palette = QApplication.palette()
        foreground = palette.color(QPalette.WindowText)
        background = palette.color(QPalette.Base)
        ratio = menu.devicePixelRatioF()
        key = (foreground.rgba(), background.rgba(), ratio)
        if getattr(menu, "_color_key", None) == key:
            return
        menu._color_key = key
        dark = background.lightness() < 128
        green, red, gold, blue = (("#65dc9b", "#ef7771", "#edc064", "#81baff") if dark else
                                 ("#177448", "#b6312c", "#896009", "#2166a4"))
        colors.update(green=green, red=red, blue=blue, fg=foreground.name())
        accent_bg = palette.color(QPalette.Highlight)
        edge = QColor(background)
        edge.setRed((background.red() * 3 + foreground.red()) // 4 if dark else (background.red() * 4 + foreground.red()) // 5)
        edge.setGreen((background.green() * 3 + foreground.green()) // 4 if dark else (background.green() * 4 + foreground.green()) // 5)
        edge.setBlue((background.blue() * 3 + foreground.blue()) // 4 if dark else (background.blue() * 4 + foreground.blue()) // 5)
        quiet = QColor(foreground)
        quiet.setAlpha(150)
        menu.setStyleSheet(
            f"QMenu {{ background: {background.name()}; border: 1px solid {edge.name()}; border-radius: 10px; "
            f"padding: 8px 6px; font-size: 13px; }}"
            f"QMenu::item {{ padding: 5px 14px 5px 8px; border-radius: 6px; margin: 0; background: transparent; }}"
            f"QMenu::item:selected {{ background: {accent_bg.name()}; color: {palette.color(QPalette.HighlightedText).name()}; }}"
            f"QMenu::item:default {{ color: {blue}; font-weight: normal; }}"
            f"QMenu::item:default:selected {{ color: {palette.color(QPalette.HighlightedText).name()}; }}"
            f"QMenu::item:disabled {{ color: rgba({quiet.red()},{quiet.green()},{quiet.blue()},{quiet.alpha()}); }}"
            f"QMenu::separator {{ height: 1px; background: {edge.name()}; margin: 6px 8px; }}"
            f"QMenu::icon {{ padding-left: 4px; }}"
            f"QMenu::right-arrow {{ margin-right: 6px; }}")
        if header_block is not None:
            muted = QColor(foreground)
            muted.setAlpha(150)
            header_block.set_colors(foreground.name(), f"rgba({muted.red()},{muted.green()},{muted.blue()},{muted.alpha()})")
        for action, name, color in (
                (fav_now_action, "favorite", gold), (fav_menu.menuAction(), "favorites", gold),
                (quit_action, "quit", foreground.name())):
            action.setIcon(_menu_icon(name, color, ratio))
        show_action.setIcon(_menu_icon("window", blue, ratio))
        apply_state()

    # No timers, layout overrides or background changes: recolor only on theme
    # changes or when the native menu opens on another screen. The window refreshes the facts as it opens.
    menu.refresh = refresh_colors
    menu.sync_mute = sync_mute
    menu.apply_state = apply_state
    menu.header_lines = header_lines
    menu.header_block = header_block
    menu.aboutToShow.connect(refresh_colors)
    menu.aboutToShow.connect(lambda: window._refresh_tray_menu())
    refresh_colors()
    tray.setContextMenu(menu)

    last = [0.0]

    def _activated(reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            now = time.monotonic()
            if now - last[0] < QApplication.doubleClickInterval() / 1000:
                return                               # a double-click reports twice: show it once, do not show-and-hide
            last[0] = now
            window._toggle_window()

    tray.activated.connect(_activated)
    if window.config.get("tray_enabled", True):
        tray.show()
    return tray
