"""Saved song history: every song a station announces, with the time it was heard.

The list lives in ``song_history.json`` in the data folder (so it is portable with the rest of
the settings), grows until the user clears it, is written at every new song and again on quit.
Entries are plain dicts so the file stays readable and easy to export.
"""

from __future__ import annotations

import threading
import time

from .security import station_row

NAME = "song_history"
MAX_TEXT = 300
MAX_ENTRIES = 1_000_000          # a safety stop far above any real listening; the user clears long before


def split_song(song: str) -> tuple[str, str]:
    """'Artist - Title' -> (artist, title); a name with no separator is all title."""
    for sep in (" - ", " – ", " — "):
        if sep in song:
            artist, title = song.split(sep, 1)
            return artist.strip(), title.strip()
    return "", song.strip()


def _text(value: object) -> str:
    return " ".join(str(value or "").split())[:MAX_TEXT]


def _clean(raw: object) -> dict | None:
    """One saved entry, checked; anything odd is dropped rather than shown."""
    if not isinstance(raw, dict):
        return None
    song = _text(raw.get("song"))
    when = raw.get("t")
    if not song or isinstance(when, bool) or not isinstance(when, (int, float)) or not 0 < when < 4e9:
        return None
    artist, title = split_song(song)
    entry = {"t": float(when), "song": song, "artist": artist, "title": title,
             "station": _text(raw.get("station")), "service": _text(raw.get("service"))}
    row = raw.get("row")
    if isinstance(row, dict):
        checked = station_row({"title": row.get("title") or entry["station"] or "Station",
                               "url": row.get("url"), "_source": row.get("_source")})
        if checked is not None:
            entry["row"] = {"title": checked["title"], "url": checked["url"]}
            if checked.get("_source"):
                entry["row"]["_source"] = checked["_source"]
    return entry


class SongHistory:
    """The saved list, newest last. Thread-safe; ``save`` hands a snapshot to the config writer."""

    def __init__(self, config) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._load_lock = threading.Lock()
        self._entries: list[dict] = []
        self._loaded = False
        self._dirty = False

    # The saved file is read the first time the list is needed, not when the window opens: with a long
    # history that read took most of a second before the window appeared. The app asks for it on a
    # background thread right after the window shows (``ensure_loaded``).
    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            data = self._config.load(NAME)
            items = data.get("entries") if isinstance(data, dict) else None
            loaded: list[dict] = []
            if isinstance(items, list):
                loaded = [e for e in map(_clean, items[:MAX_ENTRIES]) if e is not None]
                loaded.sort(key=lambda e: e["t"])
            self._entries = loaded
            self._loaded = True

    @property
    def entries(self) -> list[dict]:
        self.ensure_loaded()
        return self._entries

    @entries.setter
    def entries(self, value: list[dict]) -> None:
        self._entries = value
        self._loaded = True

    def __len__(self) -> int:
        return len(self.entries)

    def add(self, song: str, station: str, service: str, row: dict | None = None,
            when: float | None = None) -> dict | None:
        """Record a song; the same song from the same station twice in a row is one entry."""
        song = _text(song)
        if not song:
            return None
        station, service = _text(station), _text(service)
        self.ensure_loaded()
        with self._lock:
            last = self.entries[-1] if self.entries else None
            if last and last["song"] == song and last["station"] == station and last["service"] == service:
                return None
            raw = {"t": time.time() if when is None else when, "song": song, "station": station,
                   "service": service, "row": row}
            entry = _clean(raw)
            if entry is None or len(self.entries) >= MAX_ENTRIES:
                return None
            self.entries.append(entry)
            self._dirty = True
        self.save()
        return entry

    def remove(self, victims: list[dict]) -> int:
        self.ensure_loaded()
        ids = {id(v) for v in victims}
        with self._lock:
            before = len(self.entries)
            self.entries = [e for e in self.entries if id(e) not in ids]
            removed = before - len(self.entries)
            if removed:
                self._dirty = True
        if removed:
            self.save()
        return removed

    def clear(self) -> int:
        self.ensure_loaded()
        with self._lock:
            count = len(self.entries)
            self.entries = []
            self._dirty = True
        self.save()
        return count

    def snapshot(self) -> list[dict]:
        self.ensure_loaded()
        with self._lock:
            return list(self.entries)

    def save(self) -> bool:
        """Queue the whole list for writing. The config writer keeps only the newest queued copy of
        a file, so a late older write can never replace a newer one. Entries are never changed after they
        are added, so the write shares them instead of copying every one."""
        if not self._loaded:
            return True                       # never read, so nothing here to write: the file on disk stays as it is
        with self._lock:
            payload = {"version": 1, "entries": list(self._entries)}
            self._dirty = False
        try:
            self._config.save_shared(NAME, payload)
        except (OSError, ValueError):
            with self._lock:
                self._dirty = True
            return False
        return True

    def flush_if_dirty(self) -> None:
        if self._dirty:
            self.save()
