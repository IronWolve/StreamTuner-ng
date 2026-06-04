"""System tray (radiotray-ng-style, DECISIONS D18). Now-playing, Play/Pause/Stop,
Favourites quick-play, show/hide, quit. The icon is a chosen emoji (a music note by
default — a logo doesn't read at tray size). Returns None when no tray host is available
(e.g. GNOME without an AppIndicator extension) so the app stays a normal window.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

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
    menu.addAction("Play / Pause", window._toggle_play)
    menu.addAction("Stop", window._stop)
    menu.addSeparator()

    fav_menu = menu.addMenu("★ Favourites")
    bm = host.channels.get("bookmarks")
    if bm and bm.favourite:
        for row in bm.favourite[:25]:
            fav_menu.addAction(row.get("title", "?"),
                               lambda _=False, r=row: player.play(r["url"]))
    else:
        fav_menu.addAction("(none yet)").setEnabled(False)

    menu.addSeparator()
    menu.addAction("Show / Hide", window._toggle_window)
    menu.addAction("Quit", window._quit)
    tray.setContextMenu(menu)

    def _activated(reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            window._toggle_window()

    tray.activated.connect(_activated)
    if window.config.get("tray_enabled", True):
        tray.show()
    return tray
