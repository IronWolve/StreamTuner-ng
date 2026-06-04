"""Shoutcast — via directory.shoutcast.com's own internal API (keyless).

The directory's website calls these endpoints; they answer JSON if you send a
browser User-Agent + Referer (a plain UA gets a 405). No Dev-ID key needed. This
is the one source with TRUE live listener counts.

Stations play through the public tune-in .pls (yp.shoutcast.com), which mpv loads
directly. If that endpoint ever changes, the airlock just shows the channel red.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from ...net import http
from ..base import Channel, make_row

BROWSE = "https://directory.shoutcast.com/Home/BrowseByGenre"
TOP_URL = "https://directory.shoutcast.com/Home/Top"        # top 500 across all genres
GENRE_PAGE = "https://directory.shoutcast.com/Genre"        # lists ALL ~313 genres as links
SEARCH = "https://directory.shoutcast.com/Search/UpdateSearch"
TUNEIN = "https://yp.shoutcast.com/sbin/tunein-station.pls?id="
_GENRE_RE = re.compile(r'href="/Genre\?name=([^"]+)"')

# browser-ish headers — the internal API 405s a non-browser UA
_HEADERS = {
    "User-Agent": http.BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://directory.shoutcast.com/",
    "X-Requested-With": "XMLHttpRequest",
}

# Shoutcast has no genre-list endpoint exposed; seed the primary genres.
GENRES = [
    "Pop", "Rock", "Alternative", "Electronic", "Dance", "Jazz", "Classical",
    "Country", "Hip Hop", "Metal", "Reggae", "Blues", "Folk", "Latin", "Oldies",
    "R&B", "Soul", "Funk", "Punk", "Ambient", "Lounge", "Soundtrack", "Talk",
    "News", "Sports", "Religious", "World", "Top 40", "80s", "90s",
]


class Shoutcast(Channel):
    id = "shoutcast"
    title = "Shoutcast"
    description = "Shoutcast directory — with live listener counts."
    homepage = "https://directory.shoutcast.com/"
    priority = "standard"
    has_search = True
    TOP = "★ Top 500"
    ALL = "★ All Stations"
    category_pins = (TOP, ALL)   # Top 500 + All pinned; genres sort A–Z
    all_category = ALL           # "All Stations" = union of the sweep + every browsed genre
    listeners_category = "★ With Listeners"   # injected under All: only stations with live listeners
    default_sort = "listeners"   # open every category sorted by live listeners (most active first)
    all_chunked = True           # ★ All Stations pages in by genre-chunk (count climbs as it loads)
    page_size = 600              # first-page threshold (capped 500-genres won't trigger a 2nd page)
    _TAIL_CHUNK = 60             # genres swept per tail page

    def __init__(self, config=None):
        super().__init__(config)
        self._genres: list[str] | None = None    # full genre list (scraped once, then cached)

    def _genre_names(self) -> list[str]:
        """The directory's full ~313-genre list (scraped from /Genre); falls back to the
        seeded primaries if the page can't be read. Cached on the instance."""
        if self._genres is None:
            try:
                html = http.get_text(GENRE_PAGE, headers=_HEADERS, timeout=15)
                names = sorted({unquote(g) for g in _GENRE_RE.findall(html)})
                self._genres = names or list(GENRES)
            except Exception:  # noqa: BLE001 — airlock-safe; just use the primaries
                self._genres = list(GENRES)
        return self._genres

    @staticmethod
    def _map(data) -> list[dict]:
        rows = []
        for s in data:
            sid = s.get("ID")
            if sid is None:
                continue
            rows.append(make_row(
                title=s.get("Name", ""),
                url=TUNEIN + str(sid),       # public tune-in .pls; mpv loads it directly
                genre=s.get("Genre", ""),
                playing=s.get("CurrentTrack", ""),
                listeners=int(s.get("Listeners") or 0),   # REAL live listeners
                bitrate=int(s.get("Bitrate") or 0),
                format=s.get("Format", "audio/mpeg"),
                listformat="pls",
            ))
        return rows

    def update_categories(self) -> list[str]:
        return [self.TOP, self.ALL] + self._genre_names()

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        if category == self.ALL and not search:
            return self._sweep(self._genre_names())
        if search:
            data = http.post_json(SEARCH, {"query": search}, headers=_HEADERS, timeout=20)
        elif category == self.TOP:
            data = http.post_json(TOP_URL, {}, headers=_HEADERS, timeout=20)
        else:
            data = http.post_json(BROWSE, {"genrename": category}, headers=_HEADERS, timeout=20)
        return self._map(data)

    def _sweep(self, genres) -> list[dict]:
        """Concurrent BrowseByGenre over a genre list, deduped by URL (the directory has no
        'all' endpoint). ★ All Stations streams it in via update_streams_page — primaries
        first, then the long tail — and the UI caches the deduped union."""
        from concurrent.futures import ThreadPoolExecutor

        def fetch(g: str) -> list[dict]:
            try:
                return self._map(http.post_json(BROWSE, {"genrename": g}, headers=_HEADERS, timeout=15))
            except Exception:  # noqa: BLE001 — one slow/blocked genre just contributes nothing
                return []

        seen: set[str] = set()
        rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=10) as ex:
            for result in ex.map(fetch, list(genres)):
                for r in result:
                    if r["url"] not in seen:
                        seen.add(r["url"])
                        rows.append(r)
        return rows

    def update_streams_page(self, category: str, offset: int = 0,
                            limit: int | None = None, search: str | None = None) -> list[dict]:
        # ★ All Stations streams in: page 0 = primaries (~9k), then the long tail of genres in
        # chunks (offset 1, 2, …) so the count climbs visibly instead of one long stall.
        if category == self.ALL and not search:
            if offset == 0:
                return self._sweep(GENRES)
            rest = [g for g in self._genre_names() if g not in set(GENRES)]
            chunk = rest[(offset - 1) * self._TAIL_CHUNK: offset * self._TAIL_CHUNK]
            return self._sweep(chunk)            # [] once the tail is exhausted -> the UI stops
        if offset:
            return []
        return self.update_streams(category, search)
