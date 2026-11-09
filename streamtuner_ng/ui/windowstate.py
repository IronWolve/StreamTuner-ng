"""Remember where a window of its own was left: its size and place, per kind of window.

Saved in the ``dialog_bounds`` setting as ``{name: [x, y, width, height]}``; restored only when it
still lands on a connected screen, otherwise the window opens centred over its owner.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect, QTimer
from PySide6.QtGui import QGuiApplication

SETTING = "dialog_bounds"
MAX_KINDS = 64


def _valid(value) -> QRect | None:
    if not (isinstance(value, (list, tuple)) and len(value) == 4):
        return None
    if any(isinstance(v, bool) or not isinstance(v, int) for v in value):
        return None
    x, y, w, h = value
    if not (120 <= w <= 20000 and 80 <= h <= 20000 and abs(x) < 100000 and abs(y) < 100000):
        return None
    return QRect(x, y, w, h)


def _on_screen(rect: QRect) -> bool:
    """At least a usable corner of the window is on some connected screen."""
    for screen in QGuiApplication.screens():
        overlap = screen.availableGeometry().intersected(rect)
        if overlap.width() >= 120 and overlap.height() >= 60:
            return True
    return False


class _Keeper(QObject):
    def __init__(self, widget, config, name: str):
        super().__init__(widget)
        self._widget, self._config, self._name = widget, config, name
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self.save)
        widget.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._widget and event.type() in (QEvent.Type.Move, QEvent.Type.Resize) and self._widget.isVisible():
            self._timer.start()
        elif obj is self._widget and event.type() in (QEvent.Type.Close, QEvent.Type.Hide) and not event.spontaneous():
            self.save()
        return False

    def save(self) -> None:
        frame = self._widget.geometry()
        saved = self._config.get(SETTING, {})
        saved = dict(saved) if isinstance(saved, dict) else {}
        value = [frame.x(), frame.y(), frame.width(), frame.height()]
        if saved.get(self._name) == value:
            return
        saved[self._name] = value
        while len(saved) > MAX_KINDS:
            saved.pop(next(iter(saved)))
        self._config.set(SETTING, saved)


def remember_bounds(widget, config, name: str) -> None:
    """Restore the saved size and place of ``widget`` (if still on a screen) and keep saving changes."""
    saved = config.get(SETTING, {})
    rect = _valid(saved.get(name)) if isinstance(saved, dict) else None
    if rect is not None and _on_screen(rect):
        widget.setGeometry(rect)
    else:
        owner = widget.parentWidget()
        if owner is not None:
            center = owner.frameGeometry().center()
            widget.move(center.x() - widget.width() // 2, center.y() - widget.height() // 2)
    _Keeper(widget, config, name)
