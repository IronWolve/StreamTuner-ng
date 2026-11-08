"""A small movable window to choose and order the table's fields for the feed being viewed.

Tick a field to show it, drag a row to change where its column sits. It stays open, follows
the feed you switch to, and changes the table (and what is remembered for that feed) live.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)


class ColumnsPanel(QWidget):
    def __init__(self, owner):
        super().__init__(owner, Qt.Tool | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self._owner = owner
        self._busy = False
        self.setObjectName("columnsPanel")
        self.setMinimumSize(250, 320)
        self.resize(290, 470)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self._heading = QLabel()
        self._heading.setObjectName("paneHeader")
        layout.addWidget(self._heading)
        hint = QLabel("Tick to show a field  ·  drag a row to move its column")
        hint.setWordWrap(True)
        hint.setEnabled(False)                      # muted by the theme
        layout.addWidget(hint)

        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setDropIndicatorShown(True)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list.itemChanged.connect(self._ticked)
        self.list.model().rowsMoved.connect(self._moved)
        layout.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        fit = QPushButton("Fit to contents")
        fit.clicked.connect(owner._fit_columns)
        reset = QPushButton("Reset")
        reset.setToolTip("Back to this feed's default fields, widths and order")
        reset.clicked.connect(owner._reset_columns)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        for button in (fit, reset):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the list from the feed being viewed (called whenever something relevant changes)."""
        if self._busy:
            return
        self._busy = True
        try:
            self._heading.setText(f"Fields — {self._owner._feed_title()}")
            self.setWindowTitle("Fields")
            scroll = self.list.verticalScrollBar().value()
            self.list.clear()
            for label, field, wanted, has_data in self._owner._panel_fields():
                item = QListWidgetItem(label if has_data or field == "title" else f"{label}   · none here")
                item.setData(Qt.UserRole, field)
                flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled
                if field != "title":
                    flags |= Qt.ItemIsUserCheckable
                item.setFlags(flags)
                item.setCheckState(Qt.Checked if wanted else Qt.Unchecked)
                if not has_data and field != "title":
                    item.setToolTip("This list has no values for this field yet")
                self.list.addItem(item)
            self.list.verticalScrollBar().setValue(scroll)
        finally:
            self._busy = False

    def _ticked(self, item) -> None:
        if self._busy:
            return
        self._busy = True                          # the list already shows the new state; don't rebuild it mid-click
        try:
            self._owner._set_column_visible(item.data(Qt.UserRole), item.checkState() == Qt.Checked)
        finally:
            self._busy = False

    def _moved(self, *_args) -> None:
        if self._busy:
            return
        order = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        self._busy = True
        try:
            self._owner._set_column_order(order)
        finally:
            self._busy = False
