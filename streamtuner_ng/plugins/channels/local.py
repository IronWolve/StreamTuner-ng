"""Local — your own hand-added streams (and, later, local media files).

Persists to ~/.config/streamtuner-ng/local.json. No network; never fails.
"""

from __future__ import annotations

from ..base import Channel, make_row
from ...security import station_row, station_rows


class Local(Channel):
    id = "local"
    title = "Local"
    description = "Your own added streams."
    priority = "core"          # always on
    icon_emoji = "📁"          # your own added streams

    def __init__(self, config=None):
        super().__init__(config)
        self._rows: list[dict] = []
        if config is not None:
            data = config.load("local")
            self._rows = station_rows(data.get("stations")) if isinstance(data, dict) else []

    def _save(self) -> None:
        if self.config is not None:
            self.config.save("local", {"stations": self._rows})

    def add(self, title: str, url: str, **kw) -> dict:
        row = station_row(make_row(title, url, **kw))
        if row is None:
            raise ValueError("A supported network stream URL is required")
        self._rows = [r for r in self._rows if r.get("url") != row["url"]]
        self._rows.append(row)
        self._save()
        return row

    def add_many(self, rows) -> int:
        """Merge rows in, skipping URLs already present (no clobber). Returns how many added."""
        have = {r.get("url") for r in self._rows}
        added = 0
        for r in station_rows(rows):
            u = (r.get("url") or "").strip()
            if not u or u in have:
                continue
            self._rows.append(dict(r))
            have.add(u)
            added += 1
        if added:
            self._save()
        return added

    def remove(self, url: str) -> None:
        self._rows = [r for r in self._rows if r.get("url") != url]
        self._save()

    def update_categories(self) -> list[str]:
        return ["My Stations"]

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        if search:
            s = search.lower()
            return [r for r in self._rows if s in r.get("title", "").lower()]
        return list(self._rows)
