"""RadioBrowser — the anchor channel. Free, ~50k stations, open API, no key.

Uses the modern rotation API (`https://<srv>.api.radio-browser.info/json/`) — the
2018 `www.radio-browser.info/webservice` endpoint is dead. We send a descriptive
User-Agent (etiquette) via net.http.
"""

from __future__ import annotations

import random
import re
from urllib.parse import quote

from ...net import http
from ..base import Channel, make_row
from ..result import Page

# Mirrors — the project load-balances across these; we shuffle + fail over so a single
# bad/overloaded mirror (a 502 / timeout) just rolls to the next instead of erroring out.
_SERVERS = ["de1"]  # fallback; discover the current list through the provider
_MAX_MIRRORS_PER_CALL = 3


class RadioBrowser(Channel):
    id = "radiobrowser"
    title = "RadioBrowser"
    description = "Community directory, ~50k stations, open API, no key."
    homepage = "https://www.radio-browser.info/"
    priority = "core"          # on by default
    has_search = True
    # Extra table columns (PLUGINS.md "Extra columns"): radio-browser reports these per station.
    extra_columns = [("Language", "language"), ("State", "state")]

    def __init__(self, config=None):
        super().__init__(config)
        order = list(_SERVERS)
        random.shuffle(order)                  # spread load across mirrors, per session
        pref = config.get("radiobrowser_srv") if config else None
        if pref:
            order = [pref] + [s for s in order if s != pref]
        self._order = order
        self._discovered = False
        self._good: str | None = None          # last mirror that answered -> try it first next time

    def _get(self, endpoint: str, params: dict | None = None, timeout: float | None = None):
        """GET <mirror>/json/<endpoint> with failover: the last-good mirror first, then the rest,
        until one answers. Raises the last error only if EVERY mirror fails."""
        timeout = timeout or http.DEFAULT_TIMEOUT
        if not self._discovered:
            self._discovered = True
            try:
                servers = http.get_json("https://all.api.radio-browser.info/json/servers", timeout=5)
                names = list(dict.fromkeys(record.get("name", "") for record in servers if isinstance(record, dict)))
                discovered = [name.removesuffix(".api.radio-browser.info") for name in names
                              if re.fullmatch(r"[a-z0-9-]+\.api\.radio-browser\.info", name)]
                if discovered:
                    random.shuffle(discovered)
                    self._order = discovered
            except (http.RequestCancelled, http.RequestDeadlineExceeded):
                raise
            except Exception:
                pass  # bounded fallback when discovery is unavailable
        order = ([self._good] + [s for s in self._order if s != self._good]) if self._good else self._order
        last: Exception | None = None
        for srv in order[:_MAX_MIRRORS_PER_CALL]:
            http.check_request_context()
            try:
                data = http.get_json(f"https://{srv}.api.radio-browser.info/json/{endpoint}",
                                     params, timeout=timeout)
                self._good = srv
                return data
            except (http.RequestCancelled, http.RequestDeadlineExceeded):
                raise
            except Exception as exc:  # dead/overloaded mirror -> try the next one
                last = exc
        raise last if last else RuntimeError("no radio-browser mirror reachable")

    TOP = "★ Top 1000"
    ALL = "★ All Stations"
    category_pins = (TOP, ALL)   # pinned on top; the genre tags sort A–Z
    all_category = ALL           # "All Stations" = union of the sweep + every browsed tag
    listeners_category = "★ With Listeners"   # injected under All: only stations with clicks (active)
    page_size = 250           # render a small first page promptly
    page_batch_size = 1000    # keep every subsequent network/model update bounded
    # big genre lists can be multi-MB; give the fetch room (airlock guards the UI)
    _FETCH_TIMEOUT = 8.0
    # the API silently caps at 1000 if no limit is sent — ask for everything
    _ALL = 100000
    COUNTRY = "Country: "          # "Country: Germany" -> stations/bycountrycodeexact/DE
    _MIN_COUNTRY_STATIONS = 10     # skip near-empty countries so the list stays browsable

    def _country_map(self, refresh: bool = False) -> dict[str, str]:
        """Category label -> ISO 3166-1 code. Rebuilt on demand: disk-cached category names do
        not restore this map, so a cached "Country: X" must be able to resolve itself."""
        if refresh or not getattr(self, "_countries", None):
            found: dict[str, str] = {}
            for c in self._get("countries", {"hidebroken": "true"}):
                if not isinstance(c, dict):
                    continue
                name, code = c.get("name"), c.get("iso_3166_1")
                count = c.get("stationcount")
                if (isinstance(name, str) and name.strip() and isinstance(code, str)
                        and re.fullmatch(r"[A-Za-z]{2}", code)
                        and isinstance(count, int) and count >= self._MIN_COUNTRY_STATIONS):
                    found[self.COUNTRY + name.strip()[:80]] = code.upper()
            self._countries = found
        return self._countries

    def update_categories(self) -> list[str]:
        tags = self._get(
            "tags",
            {"order": "stationcount", "reverse": "true", "hidebroken": "true", "limit": 80},
        )
        # "Top" + "All" pinned first, then the tags A–Z with the countries beside them.
        names = sorted({t["name"].title() for t in tags if t.get("name")})
        try:
            names += sorted(self._country_map(refresh=True))
        except (http.RequestCancelled, http.RequestDeadlineExceeded):
            raise
        except Exception:
            pass       # genre browsing still works if the country list is unavailable
        return [self.TOP, self.ALL] + names

    @staticmethod
    def _language(value) -> str:
        """radio-browser sends "german,english"; show "German, English"."""
        return ", ".join(p.strip().title() for p in value.split(",") if p.strip()) if isinstance(value, str) else ""

    @staticmethod
    def _map(data) -> list[dict]:
        rows = []
        for s in data:
            if not isinstance(s, dict):
                continue
            codec = s.get("codec")
            codec = codec.lower() if isinstance(codec, str) else ""
            rows.append(make_row(
                title=s.get("name", ""),
                url=s.get("url_resolved") or s.get("url", ""),
                homepage=s.get("homepage", ""),
                favicon=s.get("favicon", ""),
                genre=s.get("tags", ""),
                country=s.get("country", ""),
                votes=s.get("votes") or 0,
                listeners=s.get("clickcount") or 0,   # normalized by the host with the other row fields
                bitrate=s.get("bitrate") or 0,
                format=f"audio/{codec}" if codec else "",
                listformat="srv",   # RadioBrowser gives direct/resolved stream URLs
                extra={"language": RadioBrowser._language(s.get("language")), "state": s.get("state")},
            ))
        return rows

    def update_streams_page(self, category, offset=0, limit=None, search=None) -> Page:
        limit = limit or self._ALL
        params = {"hidebroken": "true", "order": "clickcount", "reverse": "true",
                  "limit": limit, "offset": offset}
        if search:                           # a search overrides the category — searches the whole directory
            params["name"] = search
            raw = self._get("stations/search", params, self._FETCH_TIMEOUT)
            return self._page(raw, offset, limit, self._ALL)
        if category == self.TOP:
            if offset >= 1000:
                return self._page([], offset, 0, 1000)
            params["limit"] = min(limit, 1000 - offset)
            raw = self._get("stations/topclick", params, self._FETCH_TIMEOUT)
            return self._page(raw, offset, params["limit"], 1000)
        if category == self.ALL:
            endpoint = "stations"                    # the entire directory (~57k)
        elif category.startswith(self.COUNTRY):
            code = self._country_map().get(category)
            if not code:
                raise ValueError("Unknown RadioBrowser country: " + category[:80])
            endpoint = "stations/bycountrycodeexact/" + code
        else:
            endpoint = "stations/bytag/" + quote(category.lower(), safe="")
        raw = self._get(endpoint, params, self._FETCH_TIMEOUT)
        return self._page(raw, offset, limit, self._ALL)

    def _page(self, raw, offset: int, requested: int, ceiling: int) -> Page:
        # Preserve the API row count before make_row/security sanitization. A full
        # raw page may sanitize to zero valid stations and still need continuation.
        if not isinstance(raw, list):
            raise ValueError("RadioBrowser returned an invalid station page")
        raw_count = len(raw)
        rows = self._map(raw)
        has_more = bool(raw_count and raw_count >= requested and offset + raw_count < ceiling)
        return Page(rows, has_more=has_more,
                    next_offset=offset + raw_count if has_more else None)

    def update_streams(self, category, search=None) -> list[dict]:
        page = self.update_streams_page(category, 0, self._ALL, search)
        return page.rows if isinstance(page, Page) else page
