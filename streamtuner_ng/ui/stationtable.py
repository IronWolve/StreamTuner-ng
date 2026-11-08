"""The station table: type-ahead jump, tooltips for clipped cells, smooth sideways scrolling.

Click a column header (or any cell) and start typing: the table jumps to the first row whose
text in that column starts with what you typed, and a small pill shows what is being matched.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QAbstractItemView, QLabel, QTableView, QToolTip

FIND_TIMEOUT_MS = 1400        # the typed text is forgotten after this much idle time
_MODIFIER_KEYS = (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr,
                  Qt.Key_CapsLock, Qt.Key_NumLock, Qt.Key_ScrollLock)


class StationTable(QTableView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffer = ""
        self._find_col = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(FIND_TIMEOUT_MS)
        self._timer.timeout.connect(self.end_find)
        self._pill = QLabel(self.viewport())
        self._pill.setObjectName("findPill")
        self._pill.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._pill.hide()

        # Long and wide lists both scroll: bars appear as needed, sideways in pixels (not a
        # whole column at a time), and clipped text is never wrapped.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setTextElideMode(Qt.ElideRight)
        self.setWordWrap(False)
        self.setCornerButtonEnabled(False)
        self.horizontalHeader().sectionClicked.connect(self.set_find_column)
        self.clicked.connect(lambda index: self.set_find_column(index.column()))

    # ---- which column type-ahead searches ----
    def set_find_column(self, column: int) -> None:
        if column >= 0 and column != self._find_col:
            self._find_col = column
            self.end_find()

    def reset_find_column(self) -> None:
        self._find_col = 0
        self.end_find()

    def find_column(self) -> int:
        col = self._find_col
        if not 0 <= col < self.model().columnCount() or self.isColumnHidden(col):
            return 0
        return col

    # ---- keyboard ----
    def event(self, ev):
        # Space is the window's play/pause key and Esc clears the search box; once a word is being
        # typed here, both belong to the table (space joins the word, Esc forgets it).
        if (ev.type() == QEvent.ShortcutOverride and self._buffer
                and (self._typed_text(ev) or ev.key() == Qt.Key_Escape)):
            ev.accept()
            return True
        return super().event(ev)

    @staticmethod
    def _typed_text(ev) -> str:
        text = ev.text()
        if (text and text.isprintable()
                and not ev.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
            return text
        return ""

    def keyPressEvent(self, ev):
        key = ev.key()
        if key in _MODIFIER_KEYS:
            return super().keyPressEvent(ev)
        text = self._typed_text(ev)
        if text and (self._buffer or not text.isspace()):
            self._type(text)
            ev.accept()
            return
        if self._buffer and key == Qt.Key_Backspace:
            self._buffer = self._buffer[:-1]
            if self._buffer:
                self._timer.start()
                self._jump()
            else:
                self.end_find()
            ev.accept()
            return
        if self._buffer and key == Qt.Key_Escape:
            self.end_find()
            ev.accept()
            return
        self.end_find()
        super().keyPressEvent(ev)

    def _type(self, text: str) -> None:
        first = not self._buffer
        self._buffer += text
        self._timer.start()
        self._jump(first=first)

    def _jump(self, *, first: bool = False) -> None:
        proxy = self.model()
        count = proxy.rowCount()
        if not count:
            self._show_pill(False)
            return
        source = proxy.sourceModel()
        col = self.find_column()
        field = source.columns()[col][1]
        needle = self._buffer.casefold()
        cycle = len(needle) > 1 and needle == needle[0] * len(needle)    # "bbb" steps through the b's
        if cycle:
            needle = needle[0]
        current = self.currentIndex().row() if self.currentIndex().isValid() else -1

        def matches(row: int) -> bool:
            return source.find_text(proxy.mapToSource(proxy.index(row, 0)).row(), field).startswith(needle)

        if cycle or (first and current >= 0 and matches(current)):
            start = current + 1                  # already on a match: move on to the next one
        elif first or current < 0:
            start = 0                            # a fresh word starts at the top of the list
        else:
            start = current                      # typing on (or backspacing): stay if still matching
        for step in range(count):
            row = (start + step) % count
            if matches(row):
                self._go_to(row, col)
                self._show_pill(True)
                return
        self._show_pill(False)

    def _go_to(self, row: int, col: int) -> None:
        proxy = self.model()
        index = proxy.index(row, col)
        seen = self.viewport().rect().contains(self.visualRect(index))
        self.setCurrentIndex(index)
        self.scrollTo(index, QAbstractItemView.EnsureVisible)
        if not seen:                             # a long jump lands mid-screen, not on the edge
            page = max(1, self.viewport().height() // max(1, self.rowHeight(row)))
            self.verticalScrollBar().setValue(max(0, row - page // 2))

    def end_find(self) -> None:
        self._buffer = ""
        self._timer.stop()
        self._pill.hide()

    # ---- the small "Find in Genre: ro" pill ----
    def _show_pill(self, found: bool) -> None:
        label = self.model().headerData(self.find_column(), Qt.Horizontal) or ""
        self._pill.setText(f"Find in {label}:  {self._buffer}" + ("" if found else "   ·   no match"))
        self._pill.setProperty("nomatch", not found)
        self._pill.style().unpolish(self._pill)
        self._pill.style().polish(self._pill)
        self._place_pill()
        self._pill.show()
        self._pill.raise_()

    def _place_pill(self) -> None:
        self._pill.adjustSize()
        view = self.viewport()
        self._pill.move(12, max(0, view.height() - self._pill.height() - 12))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._pill.isVisible():
            self._place_pill()

    # ---- tooltips only where the text is clipped ----
    def viewportEvent(self, ev):
        if ev.type() == QEvent.ToolTip:
            index = self.indexAt(ev.pos())
            text = str(index.data(Qt.DisplayRole) or "") if index.isValid() else ""
            room = self.columnWidth(index.column()) - 16 if index.isValid() else 0
            if index.isValid() and index.column() == 0:
                room -= self.iconSize().width() + 4
            if text and QFontMetrics(self.font()).horizontalAdvance(text) > room:
                QToolTip.showText(ev.globalPos(), f"<p>{escape(text)}</p>", self.viewport(),
                                  self.visualRect(index))
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(ev)
