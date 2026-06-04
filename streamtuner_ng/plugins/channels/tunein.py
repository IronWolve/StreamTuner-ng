"""TuneIn — via the open OPML directory (opml.radiotime.com, the "Radio Time" API
st2 used). No key. Stations come back as Tune.ashx links that must be resolved to a
real stream URL at play time (needs_resolve), so we don't fetch 100s of URLs upfront.

Note: this is TuneIn's undocumented OPML endpoint — handy and widely used, but if it
ever changes the plugin just shows red (the airlock keeps the app fine).
"""

from __future__ import annotations

import html
import re
from urllib.parse import quote

from ...net import http
from ..base import Channel, make_row

BASE = "http://opml.radiotime.com/"
_FMT = {"mp3": "audio/mpeg", "aac": "audio/aac", "ogg": "audio/ogg"}
_ATTR = re.compile(r'(\w+)="([^"]*)"')


class TuneIn(Channel):
    id = "tunein"
    title = "TuneIn"
    description = "TuneIn directory (OPML / Radio Time) — global stations & shows."
    homepage = "https://tunein.com/"
    priority = "standard"
    has_search = True
    needs_resolve = True
    category_pins = ("Local Radio", "All Music", "Talk", "Sports")  # top; genres sort A–Z
    all_category = "All Music"   # = union of the genre sweep + every browsed genre

    def __init__(self, config=None):
        super().__init__(config)
        self.catmap: dict[str, str] = {}

    def update_categories(self) -> list[str]:
        # broad top categories (each leads to stations) + the music genres.
        # TuneIn is hierarchical, so there's no single "All" feed; "Local Radio"
        # is the closest to a non-genre "everything near you" view.
        self.catmap = {
            "Local Radio": BASE + "Browse.ashx?c=local",
            "Talk": BASE + "Browse.ashx?c=talk",
            "Sports": BASE + "Browse.ashx?c=sports",
        }
        names = ["Local Radio", "All Music", "Talk", "Sports"]   # "All Music" is synthetic
        xml = http.get_text(BASE + "Browse.ashx?c=music")
        for m in re.findall(r'<outline\b[^>]*type="link"[^>]*>', xml):
            a = dict(_ATTR.findall(m))
            text, url = html.unescape(a.get("text", "")), html.unescape(a.get("URL", ""))
            if text and url:
                self.catmap[text] = url
                names.append(text)
        return names

    _SUBGENRE = re.compile(r'[?&]id=g\d')   # Browse.ashx?id=g62 = a sub-genre station list

    def _crawl(self, url, seen, pages, rows, budget, subgenres=True) -> None:
        """Collect live stations from a Browse page, then follow 'More Stations'
        pagination (key=nextStations) and one level of sub-genres. `budget` is a
        1-element list capping total HTTP fetches so a click stays bounded; `pages`
        guards against re-fetching the same URL (loops)."""
        if budget[0] <= 0 or url in pages:
            return
        pages.add(url)
        budget[0] -= 1
        try:
            xml = http.get_text(url)
        except Exception:  # noqa: BLE001 — airlock handles the channel; one bad page just stops here
            return
        for m in re.findall(r'<outline\b[^>]*type="audio"[^>]*>', xml):
            a = dict(_ATTR.findall(m))
            u = html.unescape(a.get("URL", ""))
            if "Tune.ashx" not in u or u in seen:   # audio + Tune.ashx = a real station (not a show)
                continue
            seen.add(u)
            fmt = (a.get("formats", "").split(",") or [""])[0]
            rows.append(make_row(
                title=html.unescape(a.get("text", "")),
                url=u,
                description=html.unescape(a.get("subtext", "")),   # subtext = slogan/show, not a song
                bitrate=int(a.get("bitrate") or 0),
                format=_FMT.get(fmt, "audio/mpeg"),
                favicon=html.unescape(a.get("image", "")),
                listformat="tunein",
            ))
        links = [dict(_ATTR.findall(m)) for m in re.findall(r'<outline\b[^>]*type="link"[^>]*>', xml)]
        # 1) pagination — follow the station-list "More Stations" (NOT "More Shows")
        for a in links:
            if a.get("key") == "nextStations":
                self._crawl(html.unescape(a.get("URL", "")), seen, pages, rows, budget, subgenres)
                break
        # 2) sub-genres — one level deep, station lists only (skip show pivots, filter=p)
        if subgenres:
            for a in links:
                if budget[0] <= 0:
                    break
                u = html.unescape(a.get("URL", ""))
                if "Browse.ashx" in u and self._SUBGENRE.search(u) and "filter=p" not in u:
                    self._crawl(u, seen, pages, rows, budget, subgenres=False)

    def _stations(self, url: str, budget: int = 12, subgenres: bool = True) -> list[dict]:
        seen: set[str] = set()
        pages: set[str] = set()
        rows: list[dict] = []
        self._crawl(url, seen, pages, rows, [budget], subgenres)
        return rows

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        if search:
            return self._stations(BASE + "Search.ashx?query=" + quote(search), budget=4, subgenres=False)
        if category == "All Music":
            return self._all_music()
        url = self.catmap.get(category) or (BASE + "Browse.ashx?c=music")
        return self._stations(url)

    def _all_music(self) -> list[dict]:
        """Aggregate the music genres (each crawled with a small budget) CONCURRENTLY,
        deduped by URL — the UI caches the result after the first load."""
        from concurrent.futures import ThreadPoolExecutor

        skip = {"Local Radio", "Talk", "Sports", "All Music"}
        genres = [u for n, u in self.catmap.items() if n not in skip]
        seen: set[str] = set()
        rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            for result in ex.map(lambda u: self._stations(u, budget=5, subgenres=True), genres):
                for r in result:
                    if r["url"] and r["url"] not in seen:
                        seen.add(r["url"])
                        rows.append(r)
        return rows

    def resolve_url(self, row: dict) -> dict:
        """Tune.ashx returns a text list of stream URLs; take the first http(s) one."""
        text = http.get_text(row["url"])
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("http"):
                resolved = dict(row)
                resolved["url"] = line
                return resolved
        return row
