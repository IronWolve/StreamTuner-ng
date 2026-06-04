"""RadioBrowser — the anchor channel. Free, ~50k stations, open API, no key.

Uses the modern rotation API (`https://<srv>.api.radio-browser.info/json/`) — the
2018 `www.radio-browser.info/webservice` endpoint is dead. We send a descriptive
User-Agent (etiquette) via net.http.
"""

from __future__ import annotations

import random

from ...net import http
from ..base import Channel, make_row

# Mirrors — the project load-balances across these; we shuffle + fail over so a single
# bad/overloaded mirror (a 502 / timeout) just rolls to the next instead of erroring out.
_SERVERS = ["de1", "nl1", "fr1", "at1"]


class RadioBrowser(Channel):
    id = "radiobrowser"
    title = "RadioBrowser"
    description = "Community directory, ~50k stations, open API, no key."
    homepage = "https://www.radio-browser.info/"
    priority = "core"          # on by default
    has_search = True

    def __init__(self, config=None):
        super().__init__(config)
        order = list(_SERVERS)
        random.shuffle(order)                  # spread load across mirrors, per session
        pref = config.get("radiobrowser_srv") if config else None
        if pref:
            order = [pref] + [s for s in order if s != pref]
        self._order = order
        self._good: str | None = None          # last mirror that answered -> try it first next time

    def _get(self, endpoint: str, params: dict | None = None, timeout: float | None = None):
        """GET <mirror>/json/<endpoint> with failover: the last-good mirror first, then the rest,
        until one answers. Raises the last error only if EVERY mirror fails."""
        timeout = timeout or http.DEFAULT_TIMEOUT
        order = ([self._good] + [s for s in self._order if s != self._good]) if self._good else self._order
        last: Exception | None = None
        for srv in order:
            try:
                data = http.get_json(f"https://{srv}.api.radio-browser.info/json/{endpoint}",
                                     params, timeout=timeout)
                self._good = srv
                return data
            except Exception as exc:  # noqa: BLE001 — dead/overloaded mirror -> try the next one
                last = exc
        raise last if last else RuntimeError("no radio-browser mirror reachable")

    TOP = "★ Top 1000"
    ALL = "★ All Stations"
    category_pins = (TOP, ALL)   # pinned on top; the genre tags sort A–Z
    all_category = ALL           # "All Stations" = union of the sweep + every browsed tag
    listeners_category = "★ With Listeners"   # injected under All: only stations with clicks (active)
    page_size = 500           # UI shows the first 500 instantly, streams the rest in
    # big genre lists can be multi-MB; give the fetch room (airlock guards the UI)
    _FETCH_TIMEOUT = 35.0
    # the API silently caps at 1000 if no limit is sent — ask for everything
    _ALL = 100000

    def update_categories(self) -> list[str]:
        tags = self._get(
            "tags",
            {"order": "stationcount", "reverse": "true", "hidebroken": "true", "limit": 80},
        )
        # "Top" + "All" pinned first, then the tags A–Z.
        names = sorted({t["name"].title() for t in tags if t.get("name")})
        return [self.TOP, self.ALL] + names

    @staticmethod
    def _map(data) -> list[dict]:
        rows = []
        for s in data:
            codec = (s.get("codec") or "").lower()
            rows.append(make_row(
                title=s.get("name", ""),
                url=s.get("url_resolved") or s.get("url", ""),
                homepage=s.get("homepage", ""),
                favicon=s.get("favicon", ""),
                genre=s.get("tags", ""),
                country=s.get("country", ""),
                votes=int(s.get("votes") or 0),
                listeners=int(s.get("clickcount") or 0),   # 24h clicks ~ popularity
                bitrate=int(s.get("bitrate") or 0),
                format=f"audio/{codec}" if codec else "",
                listformat="srv",   # RadioBrowser gives direct/resolved stream URLs
            ))
        return rows

    def update_streams_page(self, category, offset=0, limit=None, search=None) -> list[dict]:
        limit = limit or self._ALL
        params = {"hidebroken": "true", "order": "clickcount", "reverse": "true",
                  "limit": limit, "offset": offset}
        if search:                           # a search overrides the category — searches the whole directory
            params["name"] = search
            return self._map(self._get("stations/search", params, self._FETCH_TIMEOUT))
        if category == self.TOP:
            if offset:                       # Top is a fixed popular list, no paging
                return []
            return self._map(self._get("stations/topclick", {"hidebroken": "true", "limit": 1000},
                                       self._FETCH_TIMEOUT))
        if category == self.ALL:
            endpoint = "stations"                    # the entire directory (~57k)
        else:
            endpoint = "stations/bytag/" + category.lower()
        return self._map(self._get(endpoint, params, self._FETCH_TIMEOUT))

    def update_streams(self, category, search=None) -> list[dict]:
        return self.update_streams_page(category, 0, self._ALL, search)
