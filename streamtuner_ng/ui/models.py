"""Station table model — a proper QAbstractTableModel so the view virtualizes
big lists (Qt's strength; DECISIONS D2). Sorting/filtering via a proxy in the view.
"""

from __future__ import annotations

from ..security import redact_url, station_rows

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

# custom role the sort-proxy uses so numbers sort as numbers (not as strings)
SORT_ROLE = Qt.UserRole + 1
# custom role the filter uses so search matches name + genre + country + now-playing
FILTER_ROLE = Qt.UserRole + 2
_NUMERIC = {"bitrate", "votes", "listeners"}

COLUMNS = [
    ("Station", "title"),
    ("Genre", "genre"),
    ("Bitrate", "bitrate"),
    ("Codec", "format"),
    ("Listeners", "listeners"),
    ("Votes", "votes"),
    ("Country", "country"),
    ("Description", "description"),
    ("Now Playing", "playing"),
    ("URL", "url"),
]


class StationModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self._rows: list[dict] = []
        self._positions = {}
        self._search = []
        self._populated = set()
        self.icon_provider = None    # callable(row) -> QIcon | None  (station favicon)
        self.fav_provider = None     # callable(url) -> bool          (is favourited)

    def refresh_icons(self) -> None:
        if self._rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1, 0),
                                  [Qt.DecorationRole, Qt.DisplayRole])

    @staticmethod
    def _search_text(row):
        return " ".join(str(row.get(key, "")) for key in
                        ("title", "genre", "country", "description", "playing")).casefold()

    def _index_rows(self, rows, start):
        for i, row in enumerate(rows, start):
            self._positions[row["url"]] = i
            self._search.append(self._search_text(row))
            self._populated.update(key for _label, key in COLUMNS
                                   if row.get(key) not in (None, "", 0))

    def has_field(self, field):
        return field == "title" or field in self._populated

    def contains_url(self, url):
        return url in self._positions

    def set_rows(self, rows: list[dict], *, validated=False) -> None:
        rows = list(rows) if validated else station_rows(rows)
        self.beginResetModel()
        self._rows = rows
        self._positions.clear()
        self._search.clear()
        self._populated.clear()
        self._index_rows(rows, 0)
        self.endResetModel()

    def append_rows(self, rows: list[dict], *, validated=False) -> None:
        """Insert only unseen rows; indexes grow with the new page, not the whole catalog."""
        rows = rows if validated else station_rows(rows)
        new, seen = [], set()
        for row in rows:
            url = row["url"]
            if url not in self._positions and url not in seen:
                new.append(row)
                seen.add(url)
        if not new:
            return
        start = len(self._rows)
        self.beginInsertRows(QModelIndex(), start, start + len(new) - 1)
        self._rows.extend(new)
        self._index_rows(new, start)
        self.endInsertRows()

    def rows(self) -> list[dict]:
        return list(self._rows)

    def set_playing(self, url: str, text: str) -> None:
        """Update the live 'Now Playing' for the row currently streaming, so the table
        matches the player bar's ICY metadata."""
        if not url:
            return
        i = self._positions.get(url)
        if i is None:
            return
        row = dict(self._rows[i])
        row["playing"] = text
        self._rows[i] = row
        self._search[i] = self._search_text(row)
        self._populated.add("playing")
        col = next(i for i, (_lbl, key) in enumerate(COLUMNS) if key == "playing")
        self.dataChanged.emit(self.index(i, 0), self.index(i, col),
                              [Qt.DisplayRole, FILTER_ROLE])

    def row_at(self, r: int) -> dict | None:
        return self._rows[r] if 0 <= r < len(self._rows) else None

    # ---- Qt model API ----
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section][0]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        key = COLUMNS[index.column()][1]
        if role == Qt.DisplayRole:
            val = row.get(key, "")
            if key in ("url", "homepage"):
                return redact_url(val)
            if key == "title":
                title = str(val)
                if self.fav_provider and self.fav_provider(row.get("url", "")):
                    return "★ " + title       # mark favourites (visible in History too)
                return title
            if key == "bitrate":
                return f"{val}k" if val else ""
            if key in ("votes", "listeners"):
                return f"{val:,}" if val else ""
            if key == "format":
                return (val or "").replace("audio/", "")
            return str(val)
        if role == Qt.DecorationRole and key == "title" and self.icon_provider is not None:
            return self.icon_provider(row)
        if role == SORT_ROLE:
            # raw, comparable value so the proxy sorts numbers numerically
            val = row.get(key, "")
            if key in _NUMERIC:
                return int(val or 0)
            return str(val).lower()
        if role == FILTER_ROLE:
            return self._search[index.row()]
        if role == Qt.TextAlignmentRole and key in _NUMERIC:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None
