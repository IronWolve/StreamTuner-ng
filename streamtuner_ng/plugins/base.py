"""The channel plugin contract.

A channel subclasses `Channel`, declares a little metadata (class attributes —
our modern, py3 replacement for st2's YAML-in-comments header), and implements:

    update_categories() -> list[str]
    update_streams(category, search=None) -> list[row dict]

A "row" is the universal station dict (see MECHANICS.md §1); `make_row` enforces
the schema and the two required keys (title, url).
"""

from __future__ import annotations

import threading
from typing import Any

from .result import Page

_SNAPSHOT_INIT_LOCK = threading.Lock()

# The station row schema. title + url are required; the rest have sane defaults.
ROW_DEFAULTS: dict[str, Any] = {
    "title": "",
    "url": "",
    "homepage": "",
    "genre": "",
    "playing": "",      # the live current track (ICY / Shoutcast CurrentTrack)
    "description": "",  # static station tagline/slogan (not a song)
    "listeners": 0,     # live listeners (Shoutcast/Icecast servers; 0 if unknown)
    "votes": 0,         # community votes / popularity (RadioBrowser)
    "country": "",
    "bitrate": 0,
    "format": "",       # MIME, e.g. audio/mpeg
    "favicon": "",      # artwork URL (fetched+cached, never bundled)
    "listformat": "pls",
}


def make_row(title: str, url: str, **kw: Any) -> dict[str, Any]:
    """Build a station row with defaults; drops unknown keys to keep it clean."""
    row = dict(ROW_DEFAULTS)
    row["title"] = title.strip() if isinstance(title, str) else ""
    row["url"] = url.strip() if isinstance(url, str) else ""
    for k, v in kw.items():
        if k in ROW_DEFAULTS:
            row[k] = v
    return row


class Channel:
    """Base class for a channel (a radio directory source)."""

    # --- metadata (a plugin overrides these) ---
    id: str = ""                 # defaults to the class name, lowercased
    title: str = "Channel"
    description: str = ""
    homepage: str = ""
    # priority decides default-on: core/standard/default -> enabled by default
    priority: str = "optional"
    listformat: str = "pls"
    audioformat: str = "audio/mpeg"
    has_search: bool = False
    needs_resolve: bool = False  # row.url must be resolved (resolve_url) before play
    page_size: int | None = None # if set, the UI loads this first then streams the rest
    group: str | None = None     # sidebar grouping label (e.g. "AudioAddict")
    category_sort: bool = True    # show categories A–Z by default (pins stay on top)
    category_pins: tuple = ()     # categories kept at the top, in this order
    all_category: str | None = None  # the "everything" category; its view = union of all loaded cats
    listeners_category: str = ""  # if set, the UI injects this category right under all_category; it
                                  # shows the all-pool filtered to stations with listeners > 0 (active)
    default_sort: str = ""        # if set, the table opens sorted DESC by this row field (e.g.
                                  # "listeners") — surfaces the active/popular stations first
    icon_emoji: str = ""          # sidebar icon fallback when there's no favicon
    options_spec: list = []      # right-click options the UI renders + stores in config (the plugin
                                 # just reads its config key back) — see PLUGINS.md. Supports a "choice"
                                 # submenu (e.g. bitrate) and a "secret" login/key dialog. Empty = none.
    test_only: bool = False      # hidden helper plugins (e.g. selftest fakes)

    def __init__(self, config: Any = None):
        self.config = config
        self._ensure_snapshot_state()
        if not self.id:
            self.id = type(self).__name__.lower().lstrip("_")

    # --- the contract (override these) ---
    def _ensure_snapshot_state(self) -> None:
        # Older drop-in plugins sometimes define __init__ without calling super.
        if hasattr(self, "_snapshot_lock"):
            return
        with _SNAPSHOT_INIT_LOCK:
            if not hasattr(self, "_snapshot_lock"):
                self._snapshot_lock = threading.RLock()
                self._snapshot_generation = 0
                self._snapshot_values = {}
                self._snapshot_versions = {}

    def invalidate_cache(self) -> None:
        """Advance the snapshot generation without discarding last-good values."""
        self._ensure_snapshot_state()
        with self._snapshot_lock:
            self._snapshot_generation += 1

    def _snapshot_read(self, key: str) -> tuple[int, Any, bool]:
        """Return the active generation, its value if present, and whether it is current."""
        self._ensure_snapshot_state()
        with self._snapshot_lock:
            generation = self._snapshot_generation
            current = self._snapshot_versions.get(key) == generation and key in self._snapshot_values
            return generation, self._snapshot_values.get(key), current

    def _snapshot_publish(self, key: str, generation: int, value: Any) -> Any:
        """Publish only into the generation that initiated the load.

        A late pre-refresh request may still return its own result to its caller,
        but it cannot replace the newer provider snapshot.
        """
        self._ensure_snapshot_state()
        with self._snapshot_lock:
            if generation == self._snapshot_generation:
                self._snapshot_values[key] = value
                self._snapshot_versions[key] = generation
                return value
            if self._snapshot_versions.get(key) == self._snapshot_generation:
                return self._snapshot_values[key]
            return value

    def _snapshot_after_error(self, key: str, generation: int) -> tuple[bool, Any]:
        """A stale failed request may use a newer result; current failures still raise."""
        self._ensure_snapshot_state()
        with self._snapshot_lock:
            if (generation != self._snapshot_generation
                    and self._snapshot_versions.get(key) == self._snapshot_generation):
                return True, self._snapshot_values[key]
            return False, None

    def refresh_categories(self) -> list[str]:
        self.invalidate_cache()
        return self.update_categories()

    def refresh_streams(self, category: str, search: str | None = None) -> list[dict]:
        self.invalidate_cache()
        self.update_categories()  # rebuild provider routing maps before fetching stations
        return self.update_streams(category, search)

    def update_categories(self) -> list[str]:
        """Return the list of category/genre names for this channel."""
        return []

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        """Return station rows for a category (or a search query)."""
        return []

    def update_streams_page(self, category: str, offset: int = 0,
                            limit: int | None = None, search: str | None = None) -> list[dict] | Page:
        """Optional pagination hook. Default: page 0 returns everything (via
        update_streams), no further pages — so non-paginated channels work
        unchanged. Channels that set `page_size` override this to stream big
        lists in progressively."""
        if offset:
            return []
        return self.update_streams(category, search)

    # --- optional: a channel may resolve a urn:/indirection URL before play ---
    def resolve_url(self, row: dict) -> dict:
        return row

    def default_enabled(self) -> bool:
        return self.priority in ("core", "builtin", "always", "default", "standard")
