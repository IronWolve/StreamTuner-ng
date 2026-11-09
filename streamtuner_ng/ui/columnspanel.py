"""A small movable window to choose and order the table's fields for the feed being viewed.

Tick a field to show it, drag a row to change where its column sits. Select a field to filter by it
(text contains or picked values, number from-to, date since). It stays open, follows the feed you
switch to, and changes the table (and what is remembered for that feed) live.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout, QWidget,
)

from .fieldfilter import clean_spec, field_kind, parse_number, value_counts


class ColumnsPanel(QWidget):
    def __init__(self, owner):
        super().__init__(owner, Qt.Tool | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self._owner = owner
        self._busy = False
        self.setObjectName("columnsPanel")
        self.setMinimumSize(280, 420)
        self.resize(330, 680)
        self._filter_busy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self._heading = QLabel()
        self._heading.setObjectName("paneHeader")
        layout.addWidget(self._heading)
        hint = QLabel("Tick to show a field  ·  drag a row to move its column  ·  select a field to filter by it")
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
        layout.addWidget(self.list, 2)
        self.list.currentItemChanged.connect(lambda *_: self._show_filter())
        layout.addWidget(self._build_filter_box(), 3)

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

    # ---- the filter of the selected field ----
    def _build_filter_box(self) -> QWidget:
        box = QFrame()
        box.setObjectName("filterBox")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 4, 0, 0)
        v.setSpacing(6)
        self._filter_title = QLabel()
        self._filter_title.setObjectName("paneHeader")
        v.addWidget(self._filter_title)
        self._contains = QLineEdit()
        self._contains.setPlaceholderText("Contains…")
        self._contains.setClearButtonEnabled(True)
        self._contains.textChanged.connect(self._filter_edited)
        v.addWidget(self._contains)
        self._picks = QListWidget()
        self._picks.setSelectionMode(QAbstractItemView.NoSelection)
        self._picks.itemChanged.connect(self._filter_edited)
        v.addWidget(self._picks, 1)
        self._range = QWidget()
        r = QHBoxLayout(self._range)
        r.setContentsMargins(0, 0, 0, 0)
        self._min, self._max = QLineEdit(), QLineEdit()
        self._min.setPlaceholderText("From")
        self._max.setPlaceholderText("To")
        for edit in (self._min, self._max):
            edit.setClearButtonEnabled(True)
            edit.textChanged.connect(self._filter_edited)
            r.addWidget(edit)
        v.addWidget(self._range)
        self._since = QLineEdit()
        self._since.setPlaceholderText("Since (YYYY-MM-DD)")
        self._since.setClearButtonEnabled(True)
        self._since.textChanged.connect(self._filter_edited)
        v.addWidget(self._since)
        self._filter_note = QLabel()
        self._filter_note.setEnabled(False)
        self._filter_note.setWordWrap(True)
        v.addWidget(self._filter_note)
        row = QHBoxLayout()
        self._clear_one = QPushButton("Clear this filter")
        self._clear_one.clicked.connect(self._clear_this)
        self._clear_every = QPushButton("Clear all filters")
        self._clear_every.clicked.connect(self._clear_all)
        row.addWidget(self._clear_one)
        row.addWidget(self._clear_every)
        v.addLayout(row)
        box.setEnabled(False)
        self._filter_box = box
        return box

    def _field(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _show_filter(self) -> None:
        field = self._field()
        owner = self._owner
        self._filter_busy = True
        try:
            self._filter_box.setEnabled(field is not None)
            for widget in (self._contains, self._picks, self._range, self._since):
                widget.hide()
            if field is None:
                self._filter_title.setText("Filter")
                return
            kind = field_kind(owner.model, field)
            spec = owner._field_filter(field) or {}
            label = next((owner.model.headerData(i, Qt.Horizontal) for i, (_l, f) in enumerate(owner.model.columns())
                          if f == field), field)
            self._filter_title.setText(f"Filter — {label}")
            if kind == "text":
                self._contains.setText(spec.get("contains", ""))
                self._picks.clear()
                chosen = {v.casefold() for v in spec.get("values", ())}
                for value, count in value_counts(owner.model, field):
                    item = QListWidgetItem(f"{value}   ({count:,})")
                    item.setData(Qt.UserRole, value)
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Checked if value.casefold() in chosen else Qt.Unchecked)
                    self._picks.addItem(item)
                self._contains.show()
                self._picks.show()
                self._filter_note.setText("" if self._picks.count() else "No values loaded yet for this field.")
            elif kind == "number":
                self._min.setText(_fmt(spec.get("min")))
                self._max.setText(_fmt(spec.get("max")))
                self._range.show()
                self._filter_note.setText("Shows stations whose value is in range; stations with no value are hidden.")
            else:
                self._since.setText(spec.get("since", ""))
                self._since.show()
                self._filter_note.setText("Shows stations on or after this day (your computer's time).")
            self._clear_one.setEnabled(bool(spec))
            self._clear_every.setEnabled(owner._any_field_filter())
        finally:
            self._filter_busy = False

    def _filter_edited(self, *_args) -> None:
        if self._filter_busy:
            return
        field = self._field()
        if field is None:
            return
        owner = self._owner
        kind = field_kind(owner.model, field)
        spec: dict = {}
        if kind == "text":
            spec = {"contains": self._contains.text(),
                    "values": [self._picks.item(i).data(Qt.UserRole) for i in range(self._picks.count())
                               if self._picks.item(i).checkState() == Qt.Checked]}
        elif kind == "number":
            spec = {k: parse_number(e.text()) for k, e in (("min", self._min), ("max", self._max))
                    if parse_number(e.text()) is not None}
        else:
            spec = {"since": self._since.text()}
        owner._set_field_filter(field, clean_spec(kind, spec))
        self._busy = True
        try:
            self._mark_filtered()
        finally:
            self._busy = False
        self._clear_one.setEnabled(owner._field_filter(field) is not None)
        self._clear_every.setEnabled(owner._any_field_filter())

    def _clear_this(self) -> None:
        field = self._field()
        if field is not None:
            self._owner._set_field_filter(field, None)
            self._mark_filtered()
            self._show_filter()

    def _clear_all(self) -> None:
        self._owner._clear_field_filters()
        self._mark_filtered()
        self._show_filter()

    def _mark_filtered(self) -> None:
        """'· filtered' beside each field that has a filter on."""
        for i in range(self.list.count()):
            item = self.list.item(i)
            field = item.data(Qt.UserRole)
            base = item.data(Qt.UserRole + 1) or item.text()
            item.setData(Qt.UserRole + 1, base)
            item.setText(f"{base}   · filtered" if self._owner._field_filter(field) else base)

    def refresh(self) -> None:
        """Rebuild the list from the feed being viewed (called whenever something relevant changes)."""
        if self._busy:
            return
        self._busy = True
        try:
            self._heading.setText(f"Fields — {self._owner._feed_title()}")
            self.setWindowTitle("Fields")
            scroll = self.list.verticalScrollBar().value()
            keep = self._field()
            self.list.blockSignals(True)
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
            self._mark_filtered()
            self.list.verticalScrollBar().setValue(scroll)
            self.list.blockSignals(False)
            row = next((i for i in range(self.list.count()) if self.list.item(i).data(Qt.UserRole) == keep), -1)
            self.list.setCurrentRow(row)                  # the field being filtered stays selected
            self._show_filter()
        finally:
            self.list.blockSignals(False)
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


def _fmt(value) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"
