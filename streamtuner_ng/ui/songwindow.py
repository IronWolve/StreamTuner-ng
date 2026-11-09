"""Song History window: every song heard, newest first, under day headings.

A window of its own (system title bar, movable and resizable anywhere, remembers its size and place).
Filter box with a count; Time | Artist | Title | Station | Source columns; always-visible scroll bar;
Enter or a double-click plays the station again; Delete removes; Ctrl+C copies; typing goes to the filter.
"""

from __future__ import annotations

import csv
import io

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import timefmt
from ..security import csv_cell
from .windowstate import remember_bounds

HEADINGS = ("Time", "Artist", "Title", "Station", "Source")
ENTRY = Qt.UserRole


class SongHistoryWindow(QWidget):
    def __init__(self, owner, history):
        super().__init__(owner, Qt.Window)
        self._owner = owner
        self._history = history
        self._sure = False                                   # Clear needs a second click
        self._shown: dict[int, dict] = {}                     # row token -> entry (Qt copies dicts stored in items)
        self.setObjectName("songHistory")
        self.setWindowTitle("Song History")
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setMinimumSize(560, 340)
        self.resize(860, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        top = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by song, artist, title or station…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._filter_changed)
        self.count = QLabel()
        self.count.setEnabled(False)                         # muted by the theme
        top.addWidget(self.filter, 1)
        top.addWidget(self.count)
        layout.addLayout(top)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(HEADINGS))
        self.tree.setHeaderLabels(HEADINGS)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemDoubleClicked.connect(lambda item, _c: self._play(item))
        head = self.tree.header()
        head.setStretchLastSection(False)
        head.setSectionResizeMode(QHeaderView.Interactive)
        for column, width in enumerate((92, 190, 260, 190, 130)):
            self.tree.setColumnWidth(column, width)
        head.setSectionResizeMode(2, QHeaderView.Stretch)
        self.tree.installEventFilter(self)
        layout.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        self.copy_button = QPushButton("Copy list")
        self.copy_button.setToolTip("Copy the songs shown, as text you can paste")
        self.copy_button.clicked.connect(self._copy_list)
        self.export_button = QPushButton("Export CSV…")
        self.export_button.clicked.connect(self._export)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self._clear)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        for button in (self.copy_button, self.export_button, self.clear_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        self.status = QLabel()
        self.status.setEnabled(False)
        buttons.addWidget(self.status)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.refresh)
        remember_bounds(self, owner.config, "song_history")
        self.refresh()

    # ---- content ----
    def _visible_entries(self) -> list[dict]:
        words = self.filter.text().lower().split()
        entries = reversed(self._history.snapshot())
        if not words:
            return list(entries)
        return [e for e in entries
                if all(w in f"{e['song']} {e['station']} {e['service']}".lower() for w in words)]

    def refresh(self) -> None:
        """Rebuild the list (keeps the scroll place and the selection where it can)."""
        bar = self.tree.verticalScrollBar()
        keep, place = {i.data(0, ENTRY) for i in self.tree.selectedItems() if i.data(0, ENTRY)}, bar.value()
        self._shown = {}
        entries = self._visible_entries()
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        group = None
        group_name = None
        for entry in entries:
            moment = timefmt.from_epoch(entry["t"])
            heading = timefmt.day_heading(moment)
            if heading != group_name:
                group_name = heading
                group = QTreeWidgetItem(self.tree, [heading])
                group.setFlags(Qt.ItemIsEnabled)
                font = group.font(0)
                font.setBold(True)
                group.setFont(0, font)
                group.setFirstColumnSpanned(True)
                group.setExpanded(True)
            item = QTreeWidgetItem(group, [timefmt.clock(moment), entry["artist"], entry["title"] or entry["song"],
                                           entry["station"], entry["service"]])
            token = id(entry)
            self._shown[token] = entry
            item.setData(0, ENTRY, token)
            item.setToolTip(2, entry["song"])
            if token in keep:
                item.setSelected(True)
        self.tree.setUpdatesEnabled(True)
        bar.setValue(place)
        total = len(self._history)
        self.count.setText(f"{len(entries):,} of {total:,} songs" if len(entries) != total else f"{total:,} songs")
        has = bool(entries)
        for button in (self.copy_button, self.export_button):
            button.setEnabled(has)
        self.clear_button.setEnabled(total > 0)
        self._sure = False
        self.clear_button.setText("Clear")
        if not entries:
            self.status.setText("Nothing heard yet — songs a station announces are saved here."
                                if not total else "No song matches the filter.")
        else:
            self.status.setText("")

    def changed(self) -> None:
        """A new song was recorded (called by the main window); redraw shortly, once per burst."""
        if self.isVisible():
            self._timer.start()

    def _filter_changed(self, _text: str) -> None:
        self._timer.start()

    def _selected(self) -> list[dict]:
        return [e for e in map(self._entry_of, self.tree.selectedItems()) if e]

    def _entry_of(self, item) -> dict | None:
        token = item.data(0, ENTRY) if item is not None else None
        return self._shown.get(token) if token else None

    # ---- keyboard ----
    def eventFilter(self, obj, event):
        if obj is self.tree and event.type() == event.Type.KeyPress:
            key, text = event.key(), event.text()
            if key in (Qt.Key_Return, Qt.Key_Enter):
                items = self.tree.selectedItems()
                if items:
                    self._play(items[0])
                    return True
            elif key == Qt.Key_Delete:
                self._remove()
                return True
            elif event.matches(QKeySequence.Copy):
                self._copy_selected("song")
                return True
            elif key == Qt.Key_Home and not self.tree.selectedItems():
                self._select_edge(first=True)
                return True
            elif key == Qt.Key_End and not self.tree.selectedItems():
                self._select_edge(first=False)
                return True
            elif text and text.isprintable() and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
                self.filter.setFocus()
                self.filter.setText(self.filter.text() + text)
                return True
        return super().eventFilter(obj, event)

    def _select_edge(self, first: bool) -> None:
        groups = self.tree.topLevelItemCount()
        if not groups:
            return
        group = self.tree.topLevelItem(0 if first else groups - 1)
        item = group.child(0 if first else group.childCount() - 1)
        if item is not None:
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)

    # ---- actions ----
    def _play(self, item) -> None:
        entry = self._entry_of(item)
        if not entry:
            return
        row = entry.get("row")
        if not row:
            self.status.setText("This song was heard before stations were saved with it; it cannot be played again.")
            return
        self._owner._play_row(dict(row))

    def _remove(self) -> None:
        picked = self._selected()
        if picked:
            self._history.remove(picked)
            self.refresh()

    def _text_of(self, entry: dict, part: str) -> str:
        return {"song": entry["song"], "artist": entry["artist"], "title": entry["title"],
                "station": entry["station"]}.get(part, "")

    def _copy_selected(self, part: str) -> None:
        text = "\n".join(self._text_of(e, part) for e in self._selected() if self._text_of(e, part))
        if text:
            QGuiApplication.clipboard().setText(text)
            self.status.setText("Copied.")

    def _menu(self, pos: QPoint) -> None:
        item = self.tree.itemAt(pos)
        entry = self._entry_of(item)
        if entry is None:
            return
        if not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
        self.tree.setToolTip("")
        menu = QMenu(self)
        play = menu.addAction("Play this station")
        play.setEnabled(bool(entry.get("row")))
        play.triggered.connect(lambda: self._play(item))
        from ..websearch import open_search
        search = menu.addAction("Search the web for this song")
        search.setEnabled(bool(entry.get("song")))
        search.triggered.connect(lambda: open_search(entry["song"]))
        menu.addSeparator()
        for label, part in (("Copy song", "song"), ("Copy artist", "artist"), ("Copy title", "title"),
                            ("Copy station", "station")):
            action = QAction(label, menu)
            action.triggered.connect(lambda _=False, p=part: self._copy_selected(p))
            menu.addAction(action)
        menu.addSeparator()
        remove = menu.addAction("Remove from history")
        remove.triggered.connect(self._remove)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _rows(self) -> list[list[str]]:
        return [[timefmt.stamp(timefmt.from_epoch(e["t"])), e["artist"], e["title"] or e["song"], e["station"],
                 e["service"]] for e in self._visible_entries()]

    def _copy_list(self) -> None:
        lines = ["\t".join(row) for row in self._rows()]
        QGuiApplication.clipboard().setText("\n".join(["\t".join(HEADINGS)] + lines))
        self.status.setText(f"Copied {len(lines):,} songs.")

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export song history", "song-history.csv", "CSV files (*.csv)")
        if not path:
            return
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(HEADINGS)
        for row in self._rows():
            writer.writerow([csv_cell(cell) for cell in row])
        try:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(out.getvalue())
        except OSError as exc:
            self.status.setText(f"Could not save: {exc.strerror or exc}")
            return
        self.status.setText(f"Exported {len(self._rows()):,} songs.")

    def _clear(self) -> None:
        if not self._sure:
            self._sure = True
            self.clear_button.setText("Click again to clear all")
            self.status.setText("This removes every saved song.")
            return
        removed = self._history.clear()
        self.refresh()
        self.status.setText(f"Cleared {removed:,} songs.")

    def closeEvent(self, event) -> None:
        self._owner._song_window = None
        super().closeEvent(event)

