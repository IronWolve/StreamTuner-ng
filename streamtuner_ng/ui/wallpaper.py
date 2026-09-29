"""Wallpaper support: a background widget that paints an image behind the central content, plus a
generated "synthwave" background (sunset + neon perspective grid) so the Synthwave theme ships one
without bundling a binary asset.

The widget paints the image cover-scaled with a configurable dim overlay; the theme stylesheet makes
the panels translucent (see theme.stylesheet(translucent=True)) so the wallpaper shows through behind
the station list. With no wallpaper set it paints nothing — the normal theme background shows, so
non-wallpaper themes are completely unaffected.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

_BUILTIN_CACHE: dict = {}   # spec -> QPixmap: generate Synthwave / decode the bundled JPEGs only once

# Bundled wallpaper images (in streamtuner_ng/assets/wallpapers/), keyed by their short spec name.
_BUNDLED = {
    "vaporwave-1": "wallpapers/vaporwave-1.jpg",
    "vaporwave-2": "wallpapers/vaporwave-2.jpg",
}
# The selectable wallpaper list shown in Options → Themes (spec, label). '' = the active theme's own.
BUILTIN_WALLPAPERS = [
    ("", "Theme default"),
    ("synthwave", "Synthwave (neon grid)"),
    ("vaporwave-1", "Vaporwave 1"),
    ("vaporwave-2", "Vaporwave 2"),
]


class WallpaperWidget(QWidget):
    """A plain container that, when given a pixmap, paints it (cover-scaled, centered) plus a dim
    overlay behind its child widgets. Used as the window's central widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("wallpaperBack")
        self._pix: QPixmap | None = None
        self._scaled: QPixmap | None = None      # cover-scaled to _scaled_size, cached so paint never re-scales
        self._scaled_size = None
        self._dim = 35

    def set_wallpaper(self, pix: QPixmap | None, dim: int = 35) -> None:
        self._pix = pix if (pix is not None and not pix.isNull()) else None
        self._scaled = None                      # source changed -> drop the cached scale
        self._scaled_size = None
        self._dim = max(0, min(90, int(dim)))
        self.update()

    def has_wallpaper(self) -> bool:
        return self._pix is not None

    def paintEvent(self, e):
        if self._pix is None:
            super().paintEvent(e)               # no wallpaper -> normal (transparent) background
            return
        size = self.size()
        if self._scaled is None or self._scaled_size != size:
            # Smooth-scale once per size change (NOT every repaint) — cover-fit, then cache it.
            self._scaled = self._pix.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            self._scaled_size = size
        scaled = self._scaled
        p = QPainter(self)
        x = (self.width() - scaled.width()) // 2     # center the oversized (cover) pixmap
        y = (self.height() - scaled.height()) // 2
        p.drawPixmap(x, y, scaled)
        if self._dim:                                # darken so translucent panels + text stay readable
            p.fillRect(self.rect(), QColor(0, 0, 0, int(self._dim / 90 * 235)))
        p.end()


def synthwave_pixmap(w: int = 1600, h: int = 1000) -> QPixmap:
    """A generated outrun/synthwave background: purple→pink sunset sky, a glowing sun on the horizon,
    a dark ground, and a cyan perspective grid. Painted once, then cover-scaled by the widget."""
    pm = QPixmap(w, h)
    pm.fill(Qt.black)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    horizon = int(h * 0.62)

    sky = QLinearGradient(0, 0, 0, horizon)
    sky.setColorAt(0.0, QColor(13, 11, 30))
    sky.setColorAt(0.55, QColor(95, 10, 135))
    sky.setColorAt(1.0, QColor(255, 42, 109))
    p.fillRect(0, 0, w, horizon, sky)

    cx, r = w / 2, h * 0.26
    sun = QRadialGradient(QPointF(cx, horizon), r)
    sun.setColorAt(0.0, QColor(255, 204, 102))
    sun.setColorAt(0.6, QColor(255, 110, 199))
    sun.setColorAt(1.0, QColor(255, 42, 109, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(sun)
    p.drawEllipse(QPointF(cx, horizon), r, r)

    p.fillRect(0, horizon, w, h - horizon, QColor(5, 3, 10))     # ground

    grid = QPen(QColor(5, 217, 232, 170))
    grid.setWidthF(1.4)
    p.setPen(grid)
    n = 12
    for i in range(-n, n + 1):                                   # vertical lines converging at the horizon
        xb = cx + i * (w / (n * 1.1))
        p.drawLine(QPointF(cx, horizon), QPointF(xb, h))
    rows = 14
    for j in range(1, rows + 1):                                 # horizontal lines, denser toward the horizon
        t = j / rows
        yy = horizon + (h - horizon) * (t * t)
        p.drawLine(QPointF(0, yy), QPointF(w, yy))

    glow = QPen(QColor(255, 42, 109, 200))                       # hot-pink horizon line
    glow.setWidthF(2.5)
    p.setPen(glow)
    p.drawLine(QPointF(0, horizon), QPointF(w, horizon))
    p.end()
    return pm


def load_wallpaper(spec: str, base_dir=None) -> QPixmap | None:
    """Resolve a wallpaper spec to a pixmap: 'synthwave' -> the generated image; a bundled name
    (BUILTIN_WALLPAPERS, e.g. 'vaporwave-1') -> the shipped asset; an absolute file path, or a bare
    filename found in `base_dir` (the themes folder, so a shared theme can ship its own image) ->
    that image; anything else -> None."""
    if not spec:
        return None
    if spec in _BUILTIN_CACHE:                       # already generated/decoded -> reuse (no rework)
        return _BUILTIN_CACHE[spec]
    if spec == "synthwave":
        pm = synthwave_pixmap()
        _BUILTIN_CACHE[spec] = pm
        return pm
    if spec in _BUNDLED:                              # a bundled image (Vaporwave 1/2)
        from .. import asset_path
        pm = QPixmap(asset_path(_BUNDLED[spec]))
        pm = pm if not pm.isNull() else None
        _BUILTIN_CACHE[spec] = pm
        return pm
    from pathlib import Path
    candidates = [Path(spec)]
    if base_dir is not None:
        candidates.append(Path(base_dir) / spec)
    for c in candidates:
        try:
            if c.is_file():
                pm = QPixmap(str(c))
                if not pm.isNull():
                    return pm
        except OSError:
            pass
    return None
