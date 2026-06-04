"""Bookmarks — favourites + recently-played history.

Persists to ~/.config/streamtuner-ng/bookmarks.json:
    {"favourite": [row, ...], "history": [row, ...]}
The star button calls add()/remove(); is_favourite() drives the lit-state.
"""

from __future__ import annotations

from ..base import Channel

HISTORY_MAX = 60


class Bookmarks(Channel):
    id = "bookmarks"
    title = "Favourites"
    description = "Your starred stations and play history."
    priority = "core"          # always on
    category_pins = ("Favourites", "History")
    icon_emoji = "⭐"          # Favourites — a star in the sidebar

    def __init__(self, config=None):
        super().__init__(config)
        data = (config.load("bookmarks") if config else None) or {}
        self.favourite: list[dict] = data.get("favourite", [])
        self.history: list[dict] = data.get("history", [])
        self._fav_urls = {r.get("url") for r in self.favourite}

    def _save(self) -> None:
        if self.config is not None:
            self.config.save("bookmarks", {"favourite": self.favourite, "history": self.history})

    # ---- favourites ----
    def is_favourite(self, url: str) -> bool:
        return url in self._fav_urls

    def add(self, row: dict) -> None:
        url = row.get("url")
        if not url or url in self._fav_urls:
            return
        self.favourite.append(dict(row))
        self._fav_urls.add(url)
        self._save()

    def add_many(self, rows: list[dict]) -> int:
        """Merge several rows in at once — skips any URL already saved (no clobber),
        saves once at the end. Returns how many were actually added."""
        added = 0
        for row in rows:
            url = (row.get("url") or "").strip()
            if not url or url in self._fav_urls:
                continue
            self.favourite.append(dict(row))
            self._fav_urls.add(url)
            added += 1
        if added:
            self._save()
        return added

    def remove(self, url: str) -> None:
        self.favourite = [r for r in self.favourite if r.get("url") != url]
        self._fav_urls.discard(url)
        self._save()

    def toggle(self, row: dict) -> bool:
        url = row.get("url", "")
        if self.is_favourite(url):
            self.remove(url)
            return False
        self.add(row)
        return True

    def set_favicon(self, url: str, favicon_url: str) -> None:
        """Stamp a derived favicon onto a saved favourite that had none (icon-less stations
        get one in the background when you add them)."""
        changed = False
        for r in self.favourite:
            if r.get("url") == url and not r.get("favicon"):
                r["favicon"] = favicon_url
                changed = True
        if changed:
            self._save()

    # ---- history ----
    def add_history(self, row: dict) -> None:
        url = row.get("url")
        self.history = [r for r in self.history if r.get("url") != url]
        self.history.insert(0, dict(row))
        del self.history[HISTORY_MAX:]
        self._save()

    # ---- channel contract ----
    def update_categories(self) -> list[str]:
        return ["Favourites", "History"]

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        rows = self.history if category == "History" else self.favourite
        if search:
            s = search.lower()
            rows = [r for r in rows if s in r.get("title", "").lower()]
        # "Now Playing" is a LIVE value — clear any stale stored snapshot for display
        out = []
        for r in rows:
            c = dict(r)
            c["playing"] = ""
            out.append(c)
        return out
