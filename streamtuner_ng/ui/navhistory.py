"""Back and forward through the lists the user has opened (a feed plus one of its categories).

A plain history like a browser's: opening a new list drops whatever was ahead of the current one,
the same list twice in a row is one stop, and the history is capped. Going back or forward only
moves the position and names the list to show; the window then selects it, and the ``visit`` that
the landing reports is recognised and not recorded as a new stop.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QWidget

LIMIT = 60


class NavHistory:
    def __init__(self, limit: int = LIMIT) -> None:
        self.limit = limit
        self.stops: list[tuple[str, str]] = []
        self.pos = -1
        self.target: tuple[str, str] | None = None     # a list we are moving to and have not reached yet

    def can_back(self) -> bool:
        return self.pos > 0

    def can_forward(self) -> bool:
        return 0 <= self.pos < len(self.stops) - 1

    def visit(self, cid: str, cat: str) -> None:
        """The user is now looking at (cid, cat)."""
        key = (cid, cat)
        if self.target is not None:
            reached = self.target == key
            self.target = None
            if reached:
                return                                  # arrived by going back or forward: no new stop
        if 0 <= self.pos < len(self.stops) and self.stops[self.pos] == key:
            return
        del self.stops[self.pos + 1:]
        self.stops.append(key)
        if len(self.stops) > self.limit:
            del self.stops[:len(self.stops) - self.limit]
        self.pos = len(self.stops) - 1

    def step(self, delta: int, exists=lambda key: True) -> tuple[str, str] | None:
        """Move one stop back (-1) or forward (+1), skipping lists that no longer exist; returns the
        list to show, or None when there is nowhere to go (the position then stays where it was)."""
        pos = self.pos + delta
        while 0 <= pos < len(self.stops):
            if exists(self.stops[pos]):
                self.pos = pos
                self.target = self.stops[pos]
                return self.target
            pos += delta
        return None

    def forget(self, key: tuple[str, str]) -> None:
        """A list that could not be reached: it stays in the history but is no longer expected."""
        if self.target == key:
            self.target = None


class SideButtons(QObject):
    """Mouse Back / Forward buttons: ``go(-1)`` / ``go(+1)`` while ``window`` is the active window.

    Watches the whole application, so a click on any pane of the window counts, but nothing for a
    dialog or while a menu is open. The buttons are consumed (a list never sees them).
    """

    PRESS = (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)

    def __init__(self, window, go) -> None:
        super().__init__(window)
        self._window, self._go = window, go
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, ev):
        kind = ev.type()
        if kind != QEvent.Type.MouseButtonRelease and kind not in self.PRESS:
            return False
        if ev.button() not in (Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton):
            return False
        if not (isinstance(obj, QWidget) and obj.window() is self._window
                and QApplication.activeWindow() is self._window and QApplication.activePopupWidget() is None):
            return False
        if kind in self.PRESS:
            self._go(-1 if ev.button() == Qt.MouseButton.BackButton else 1)
        return True
