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
from ..result import Page

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
    _GENRE_BATCH = 10            # cap requests in one provider page
    _TAIL_CHUNK = _GENRE_BATCH

    def __init__(self, config=None):
        super().__init__(config)
        self._genres: list[str] | None = None    # full genre list (scraped once, then cached)
        self._genre_errors: tuple[str, ...] = ()

    def _genre_state(self) -> dict:
        """The directory's full ~313-genre list (scraped from /Genre); falls back to the
        seeded primaries if the page can't be read. Cached on the instance."""
        generation, cached, current = self._snapshot_read("genre_state")
        if current:
            self._genres = cached["genres"]
            self._genre_errors = cached["errors"]
            return cached
        try:
            page = http.get_text(GENRE_PAGE, headers=_HEADERS, timeout=15)
            names = sorted({unquote(g) for g in _GENRE_RE.findall(page)})
            state = {"genres": names or list(GENRES),
                     "errors": () if names else ("Shoutcast genre list was empty; using primary genres",)}
        except (http.RequestCancelled, http.RequestDeadlineExceeded):
            raise
        except Exception:  # a primary list remains useful, with a degraded marker
            state = {"genres": list(GENRES), "errors": ("Shoutcast genre list request failed",)}
        state = self._snapshot_publish("genre_state", generation, state)
        self._genres = state["genres"]
        self._genre_errors = state["errors"]
        return state

    def _genre_names(self) -> list[str]:
        return list(self._genre_state()["genres"])

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

    def invalidate_cache(self) -> None:
        super().invalidate_cache()

    def update_categories(self) -> list[str]:
        return [self.TOP, self.ALL] + self._genre_names()

    def update_streams(self, category: str, search: str | None = None) -> list[dict] | Page:
        if category == self.ALL and not search:
            state = self._genre_state()
            page = self._sweep(state["genres"])
            return Page(page.rows, page.has_more, page.next_offset,
                        page.complete and not state["errors"],
                        tuple(state["errors"]) + tuple(page.errors))
        if search:
            data = http.post_json(SEARCH, {"query": search}, headers=_HEADERS, timeout=20)
        elif category == self.TOP:
            data = http.post_json(TOP_URL, {}, headers=_HEADERS, timeout=20)
        else:
            data = http.post_json(BROWSE, {"genrename": category}, headers=_HEADERS, timeout=20)
        return self._map(data)

    def _sweep(self, genres) -> Page:
        """Fetch one bounded genre group and retain per-genre failures."""
        def fetch(g: str) -> tuple[list[dict], str | None]:
            http.check_request_context()
            try:
                return self._map(http.post_json(BROWSE, {"genrename": g}, headers=_HEADERS, timeout=15)), None
            except (http.RequestCancelled, http.RequestDeadlineExceeded):
                raise
            except Exception:  # keep successful genres and surface the missing subset
                return [], f"Shoutcast genre request failed: {g}"

        seen: set[str] = set()
        rows: list[dict] = []
        errors: list[str] = []
        results = http.map_with_context(fetch, list(genres), max_workers=10)
        for result, error in results:
            if error:
                errors.append(error)
            for r in result:
                if r["url"] not in seen:
                    seen.add(r["url"])
                    rows.append(r)
        return Page(rows, has_more=False, complete=not errors, errors=tuple(errors))

    def update_streams_page(self, category: str, offset: int = 0,
                            limit: int | None = None, search: str | None = None) -> Page | list[dict]:
        # All Stations pages by small genre groups, primaries first, then the scraped tail.
        if category == self.ALL and not search:
            state = self._genre_state()
            seeded = set(GENRES)
            genres = list(GENRES) + [g for g in state["genres"] if g not in seeded]
            start = max(0, offset)
            stop = min(len(genres), start + self._GENRE_BATCH)
            result = self._sweep(genres[start:stop])
            has_more = stop < len(genres)
            errors = tuple(state["errors"]) + tuple(result.errors)
            return Page(result.rows, has_more=has_more,
                        next_offset=stop if has_more else None,
                        complete=result.complete and not state["errors"], errors=errors)
        if offset:
            return Page([], has_more=False)
        result = self.update_streams(category, search)
        return result if isinstance(result, Page) else Page(result, has_more=False)
