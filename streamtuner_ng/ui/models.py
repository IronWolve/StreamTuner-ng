"""Station table model — a proper QAbstractTableModel so the view virtualizes
big lists (Qt's strength; DECISIONS D2). Sorting/filtering via a proxy in the view.
"""

from __future__ import annotations

from ..security import display_url, station_rows
from ..timefmt import local_stamp

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
    ("Website", "homepage"),
    ("Playlist", "listformat"),
    ("Source", "source"),
]
# Fields that stay off until the user turns them on (right-click the table or View > Columns):
# the stream URL is long, the playlist type is technical, and the source service only helps in
# lists that mix services (Favorites, Local, search results).
DEFAULT_HIDDEN = frozenset({"url", "listformat", "source"})
DEFAULT_WIDTH = 110
DEFAULT_WIDTHS = {"title": 230, "genre": 100, "bitrate": 60, "format": 60, "listeners": 80, "votes": 65,
                  "country": 95, "description": 200, "playing": 200, "url": 320, "homepage": 220,
                  "listformat": 70, "source": 120}


class StationModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self._rows: list[dict] = []
        self._positions = {}
        self._search = []
        self._populated = set()
        self._extra: list[tuple[str, str, str]] = []     # declared (label, key, type) of the viewed channel
        self._cols = list(COLUMNS)                       # built-in columns + "x:<key>" extras
        self.live_songs: dict[str, str] = {}
        self._types: dict[str, str] = {}                 # "x:<key>" -> text|int|float|date
        self.icon_provider = None    # callable(row) -> QIcon | None  (station favicon)
        self.fav_provider = None     # callable(url) -> bool          (is favourited)
        self.source_provider = None  # callable(row) -> str           (service the row belongs to)

    def refresh_icons(self) -> None:
        if self._rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1, 0),
                                  [Qt.DecorationRole, Qt.DisplayRole])

    @staticmethod
    def _search_text(row):
        extra = row.get("extra")
        tail = " " + " ".join(str(v) for v in extra.values() if isinstance(v, str)) if extra else ""
        return (" ".join(str(row.get(key, "")) for key in
                         ("title", "genre", "country", "description", "playing")) + tail).casefold()

    def columns(self) -> list[tuple[str, str]]:
        """[(label, field)] for the viewed channel: built-ins, then its declared extras (`x:<key>`)."""
        return list(self._cols)

    def column_type(self, field: str) -> str | None:
        return self._types.get(field)

    def set_extra_columns(self, spec) -> bool:
        """Show a channel's declared extra columns. Returns True when the column set changed."""
        spec = [tuple(item) for item in spec or ()]
        if spec == self._extra:
            return False
        self.beginResetModel()
        self._extra = spec
        self._cols = list(COLUMNS) + [(label, "x:" + key) for label, key, _kind in spec]
        self._types = {"x:" + key: kind for _label, key, kind in spec}
        self._populated.clear()
        for row in self._rows:
            self._mark_populated(row)
        self.endResetModel()
        return True

    def _mark_populated(self, row):
        if self.live_songs and row.get("url") in self.live_songs:
            self._populated.add("playing")
        self._populated.update(key for _label, key in COLUMNS if row.get(key) not in (None, "", 0))
        extra = row.get("extra")
        if extra and self._types:
            self._populated.update("x:" + k for k in extra if "x:" + k in self._types and extra[k] not in ("", 0))

    def filter_value(self, row, field):
        """The raw value a per-field filter compares (the favourite star and thousands commas never count)."""
        return self._value(row, field)

    def set_live_songs(self, songs: dict) -> None:
        """Songs stations announce right now (asked from their streams); shown in the Playing column while
        the app runs and never saved with the rows."""
        self.live_songs = dict(list(songs.items())[-2000:])
        if self.live_songs:
            self._populated.add("playing")
        if self._rows:
            col = next(i for i, (_lbl, key) in enumerate(COLUMNS) if key == "playing")
            self.dataChanged.emit(self.index(0, col), self.index(len(self._rows) - 1, col), [Qt.DisplayRole])

    def _value(self, row, key):
        if key == "playing" and self.live_songs:
            return self.live_songs.get(row.get("url", "")) or row.get("playing", "")
        if key.startswith("x:"):
            extra = row.get("extra")
            return extra.get(key[2:], "") if isinstance(extra, dict) else ""
        if key == "source":
            return (self.source_provider(row) if self.source_provider else row.get("_source", "")) or ""
        return row.get(key, "")

    def _display(self, row, key) -> str:
        """The text a cell shows (also what type-ahead and the clipped-cell tooltip use)."""
        val = self._value(row, key)
        kind = self._types.get(key)
        if kind:
            if kind == "int":
                return f"{val:,}" if isinstance(val, int) and not isinstance(val, bool) else str(val)
            if kind == "float":
                return f"{val:g}" if isinstance(val, (int, float)) else str(val)
            if kind == "date":
                return local_stamp(val)              # stored as UTC text; drawn in the computer's own time
            return str(val)
        if key in ("url", "homepage"):
            return display_url(val)
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
        if key == "listformat":
            return str(val or "").upper()
        return str(val)

    def find_text(self, src_row: int, field: str) -> str:
        """Lower-cased text of one cell for type-ahead: no favourite star, no thousands commas."""
        row = self._rows[src_row]
        text = self._display(row, field)
        if field == "title":
            text = text.removeprefix("★ ")
        elif field in _NUMERIC or self._types.get(field) in ("int", "float"):
            text = text.replace(",", "")
        return text.casefold()

    def _index_rows(self, rows, start):
        for i, row in enumerate(rows, start):
            self._positions[row["url"]] = i
            self._search.append(self._search_text(row))
            self._mark_populated(row)

    def has_field(self, field):
        return field in ("title", "source") or field in self._populated

    def contains_url(self, url):
        return url in self._positions

    def set_rows(self, rows: list[dict], *, validated=False) -> None:
        rows = list(rows) if validated else station_rows(rows)
        seen: set[str] = set()                     # a station listed again (under another genre) shows once, at its first place
        rows = [row for row in rows if not (row["url"] in seen or seen.add(row["url"]))]
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
        return len(self._cols)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and 0 <= section < len(self._cols):
            if role == Qt.DisplayRole:
                return self._cols[section][0]
            if role == Qt.ToolTipRole:
                return (f"{self._cols[section][0]}\nClick to sort · then type to jump to a row\n"
                        "Right-click to choose fields · drag to reorder")
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        key = self._cols[index.column()][1]
        kind = self._types.get(key)
        if role == Qt.DisplayRole:
            return self._display(row, key)
        if role == Qt.DecorationRole and key == "title" and self.icon_provider is not None:
            return self.icon_provider(row)
        if role == SORT_ROLE:
            # raw, comparable value so the proxy sorts numbers numerically
            val = self._value(row, key)
            if kind in ("int", "float"):
                try:
                    return float(val or 0)
                except (TypeError, ValueError):
                    return 0.0
            if key in _NUMERIC:
                return int(val or 0)
            return str(val).lower()
        if role == FILTER_ROLE:
            return self._search[index.row()]
        if role == Qt.TextAlignmentRole and (key in _NUMERIC or kind in ("int", "float")):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None
