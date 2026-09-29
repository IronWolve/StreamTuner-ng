"""A small stereo VU / level meter for the player bar: two horizontal bars (L on top, R below)
that bounce with the live audio level, green → amber → red, each with a peak-hold tick. Fed from
mpv's `astats` audio filter (per-frame RMS + peak), polled by the main window ~30×/s."""

from __future__ import annotations

from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QWidget

_FLOOR_DB = -48.0          # bottom of the scale (silence); 0 dB = full bar


def _norm(db: float) -> float:
    """Map a dB level (≤0) onto 0..1 across the meter's floor..0 dB range."""
    if db != db:           # NaN guard
        return 0.0
    return max(0.0, min(1.0, (db - _FLOOR_DB) / -_FLOOR_DB))


class VuMeter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("vuMeter")
        self.setFixedSize(150, 24)
        self.setToolTip("Audio level  (L / R)")
        self._l = self._r = 0.0          # smoothed bar levels, 0..1
        self._lpk = self._rpk = 0.0      # peak-hold, 0..1

    # smoothing per update (~30 fps): ease UP moderately, fall SLOWLY. This low-passes the jittery
    # per-frame RMS so the bars glide like a real VU needle instead of flickering.
    _ATTACK = 0.25
    _DECAY = 0.08

    def _ease(self, cur: float, target: float) -> float:
        return cur + (target - cur) * (self._ATTACK if target > cur else self._DECAY)

    def set_db(self, l_db: float, r_db: float, lpk_db: float, rpk_db: float) -> None:
        self._l = self._ease(self._l, _norm(l_db))
        self._r = self._ease(self._r, _norm(r_db))
        self._lpk = max(_norm(lpk_db), self._lpk - 0.015)   # peak tick hangs, then drifts down
        self._rpk = max(_norm(rpk_db), self._rpk - 0.015)
        self.update()

    def idle(self) -> None:
        """Called when nothing's playing — decay smoothly to rest, then stop repainting."""
        if max(self._l, self._r, self._lpk, self._rpk) < 0.004:
            if self._l or self._r or self._lpk or self._rpk:
                self._l = self._r = self._lpk = self._rpk = 0.0
                self.update()
            return
        self._l *= 0.75
        self._r *= 0.75
        self._lpk = max(0.0, self._lpk - 0.03)
        self._rpk = max(0.0, self._rpk - 0.03)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        gap = 4
        bh = (h - gap) // 2
        grad = QLinearGradient(0, 0, w, 0)
        grad.setColorAt(0.0, QColor(80, 230, 150))    # green
        grad.setColorAt(0.65, QColor(225, 205, 90))   # amber
        grad.setColorAt(0.88, QColor(230, 90, 60))    # red
        track = QColor(22, 24, 27)
        for i, (lvl, pk) in enumerate(((self._l, self._lpk), (self._r, self._rpk))):
            y = i * (bh + gap)
            p.fillRect(0, y, w, bh, track)
            fw = round(w * lvl)
            if fw > 0:
                p.save()
                p.setClipRect(0, y, fw, bh)
                p.fillRect(0, y, w, bh, grad)          # gradient revealed up to the level
                p.restore()
            if pk > 0.01:
                px = min(w - 2, round(w * pk))
                p.fillRect(px, y, 2, bh, QColor(240, 240, 240))   # peak-hold tick
        p.end()
