"""System tray (radiotray-ng-style, DECISIONS D18). Now-playing, Play/Stop,
Favorites quick-play, show/hide, quit. The icon is a chosen emoji (a music note by
default — a logo doesn't read at tray size). Returns None when no tray host is available
(e.g. GNOME without an AppIndicator extension) so the app stays a normal window.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

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


def _menu_icon(name: str, color: str, ratio: float) -> QIcon:
    """Draw small colored controls sharply at the menu's screen pixel density."""
    pixmap = QPixmap(round(16 * ratio), round(16 * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    ink = QColor(color)
    painter.setPen(QPen(ink, 1.5))
    if name == "play":
        painter.setBrush(ink)
        painter.drawPolygon(QPolygonF([QPointF(5, 3), QPointF(13, 8), QPointF(5, 13)]))
    elif name == "stop":
        painter.setBrush(ink)
        painter.drawRoundedRect(4, 4, 8, 8, 1, 1)
    elif name == "favorite":
        painter.setBrush(ink)
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in (
            (8, 2), (10, 6), (14, 6.5), (11, 9.5), (12, 14),
            (8, 12), (4, 14), (5, 9.5), (2, 6.5), (6, 6))]))
    elif name == "window":
        painter.drawRoundedRect(2, 3, 12, 10, 1, 1)
        painter.drawLine(2, 6, 14, 6)
    elif name == "note":
        painter.drawLine(10, 3, 10, 11)
        painter.drawLine(10, 3, 14, 4)
        painter.setBrush(ink)
        painter.drawEllipse(5, 9, 5, 4)
    else:
        painter.drawLine(4, 4, 12, 12)
        painter.drawLine(12, 4, 4, 12)
    painter.end()
    icon = QIcon(pixmap)
    icon.addPixmap(pixmap, QIcon.Disabled)
    return icon


def build_tray(window, host, player):
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None

    tray = QSystemTrayIcon(tray_icon(window.config), window)
    tray.setToolTip("StreamTuner-ng")
    menu = QMenu()

    np = menu.addAction("Stopped")           # now-playing line (kept current by the window)
    np.setEnabled(False)
    tray._np_action = np                     # window updates this via _update_tray_np()
    menu.addSeparator()
    play_action = menu.addAction("Play / Stop", window._toggle_play)
    stop_action = menu.addAction("Stop", window._stop)
    menu.addSeparator()

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
    show_action = menu.addAction("Show / Hide", window._toggle_window)
    quit_action = menu.addAction("Quit", window._quit)

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
        for action, name, color in (
                (np, "note", blue), (play_action, "play", green),
                (stop_action, "stop", red), (fav_menu.menuAction(), "favorite", gold),
                (show_action, "window", blue), (quit_action, "quit", foreground.name())):
            action.setIcon(_menu_icon(name, color, ratio))

    # No timers, layout overrides or background changes: recolor only on theme
    # changes or when the native menu opens on another screen.
    menu.refresh = refresh_colors
    menu.aboutToShow.connect(refresh_colors)
    refresh_colors()
    tray.setContextMenu(menu)

    def _activated(reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            window._toggle_window()

    tray.activated.connect(_activated)
    if window.config.get("tray_enabled", True):
        tray.show()
    return tray
