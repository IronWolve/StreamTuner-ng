"""The channel plugin contract.

A channel subclasses `Channel`, declares a little metadata (class attributes —
our modern, py3 replacement for st2's YAML-in-comments header), and implements:

    update_categories() -> list[str]
    update_streams(category, search=None) -> list[row dict]

A "row" is the universal station dict (see MECHANICS.md §1); `make_row` enforces
the schema and the two required keys (title, url).
"""

from __future__ import annotations

from typing import Any

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
    row["title"] = (title or "").strip()
    row["url"] = (url or "").strip()
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
    test_only: bool = False      # hidden helper plugins (e.g. selftest fakes)

    def __init__(self, config: Any = None):
        self.config = config
        if not self.id:
            self.id = type(self).__name__.lower().lstrip("_")

    # --- the contract (override these) ---
    def update_categories(self) -> list[str]:
        """Return the list of category/genre names for this channel."""
        return []

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        """Return station rows for a category (or a search query)."""
        return []

    def update_streams_page(self, category: str, offset: int = 0,
                            limit: int | None = None, search: str | None = None) -> list[dict]:
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
