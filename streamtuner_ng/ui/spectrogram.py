"""A scrolling spectrum visualizer for the player bar — an alternative to the VU meter.

Driven by the live audio levels (the same astats RMS/peak the VU reads): louder → brighter,
transients flash the highs, silence scrolls to black. The embedded mpv engine doesn't expose
raw FFT bins, so the per-frequency detail is *modelled* from loudness + crest factor rather
than a true transform — a music visualizer, honest about what it is.

Resizable: drag the LEFT edge to stretch it wider (as far as there's room); it snaps back to
its normal width past the minimum, or whenever the window is resized.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QWidget


def _colormap() -> list[int]:
    """256-entry LUT: black → blue → green → yellow → red (classic spectrogram heat)."""
    stops = [(0.0, (0, 0, 0)), (0.22, (24, 28, 120)), (0.5, (24, 160, 96)),
             (0.76, (232, 200, 48)), (1.0, (236, 60, 45))]
    lut: list[int] = []
    for i in range(256):
        t = i / 255.0
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t0 <= t <= t1:
                f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                lut.append(QColor(int(c0[0] + (c1[0] - c0[0]) * f),
                                  int(c0[1] + (c1[1] - c0[1]) * f),
                                  int(c0[2] + (c1[2] - c0[2]) * f)).rgb())
                break
    return lut


class Spectrogram(QWidget):
    BINS = 40                 # vertical frequency bins
    HISTORY = 256             # columns kept (the time axis)
    DEFAULT_W = 150           # normal width; drag the left edge to grow
    _HANDLE = 6               # px near the left edge that begins a resize drag
    _FLOOR_DB = -48.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lut = _colormap()
        self._img = QImage(self.HISTORY, self.BINS, QImage.Format_RGB32)
        self._img.fill(0)
        self._t = 0
        self._dragging = False
        self.setFixedWidth(self.DEFAULT_W)
        self.setMinimumHeight(22)
        self.setMouseTracking(True)
        self.setToolTip("Spectrum — drag the left edge to widen it")

    # ---- data feed (called ~30 fps by the main window) ----
    def push(self, rms_db: float, crest_db: float) -> None:
        """Add one column from the live level: rms_db ≈ loudness, crest_db = peak − rms."""
        rms = self._norm(rms_db)
        crest = max(0.0, min(1.0, crest_db / 24.0))      # transient-ness, 0..1
        self._t += 1
        self._img = self._img.copy(1, 0, self.HISTORY, self.BINS)   # scroll left, black new col
        x = self.HISTORY - 1
        for i in range(self.BINS):
            f = i / (self.BINS - 1)                       # 0 low … 1 high
            base = rms * (1.0 - f) ** 1.4 + crest * rms * (f ** 0.8)  # lows=sustain, highs=transient
            shimmer = 0.12 * rms * (0.5 + 0.5 * math.sin(self._t * 0.3 + i * 0.7))
            v = max(0.0, min(1.0, base + shimmer))
            self._img.setPixel(x, self.BINS - 1 - i, self._lut[int(v * 255)])  # low freq at bottom
        self.update()

    def idle(self) -> None:
        """No audio: scroll in a black column so it fades out."""
        self._t += 1
        self._img = self._img.copy(1, 0, self.HISTORY, self.BINS)
        self.update()

    def _norm(self, db: float) -> float:
        if db != db or db <= self._FLOOR_DB:   # NaN (bad astats value) or below the floor -> silence
            return 0.0
        return max(0.0, min(1.0, (db - self._FLOOR_DB) / (-self._FLOOR_DB)))

    # ---- paint ----
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.drawImage(self.rect(), self._img)              # stretch HISTORY×BINS to the widget
        p.end()

    # ---- left-edge resize ----
    def reset_width(self) -> None:
        if self.width() != self.DEFAULT_W:
            self.setFixedWidth(self.DEFAULT_W)

    def _max_width(self) -> int:
        win = self.window().width() if self.window() else 800
        return max(self.DEFAULT_W, min(int(win * 0.45), 640))

    def mouseMoveEvent(self, e) -> None:
        if self._dragging:
            right = self.mapToGlobal(self.rect().topRight()).x()
            self.setFixedWidth(max(self.DEFAULT_W,
                                   min(right - e.globalPosition().toPoint().x(), self._max_width())))
        else:
            self.setCursor(Qt.SizeHorCursor if e.position().x() <= self._HANDLE else Qt.ArrowCursor)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and e.position().x() <= self._HANDLE:
            self._dragging = True

    def mouseReleaseEvent(self, _e) -> None:
        self._dragging = False
