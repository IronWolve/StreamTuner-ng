"""iHeartRadio — ~3,700 live US broadcast stations (the official AM/FM streams) via iHeart's
keyless web-player API. Each station carries a real logo and an HTTPS AAC stream mpv plays
directly. The big US brands (Z100, Hot 97, KIIS, Power 105.1, K-LOVE…) our internet-only
directories don't carry.

The directory is fetched once (paginated) and cached on the instance; categories are the
stations' genres, and "★ All Stations" is everything — filtered locally, no per-genre calls.
"""

from __future__ import annotations

from ...net import http
from ..base import Channel, make_row

API = "https://us.api.iheart.com/api/v2/content/liveStations"
PAGE = 500


class IHeart(Channel):
    id = "iheart"
    title = "iHeartRadio"
    description = "iHeartRadio — official US AM/FM broadcast stations."
    homepage = "https://www.iheart.com/"
    priority = "standard"
    has_search = False
    row_fields = ('genre', 'format', 'country', 'description', 'homepage')
    icon_emoji = "❤"
    ALL = "★ All Stations"
    category_pins = (ALL,)
    all_category = ALL
    extra_columns = [("Call letters", "call_letters"), ("Band", "band"), ("Frequency", "frequency", "float"),
                     ("City", "city"), ("State", "state"), ("Audience", "audience", "int"),
                     ("Station format", "station_format"), ("Owner", "owner"), ("Genres", "genres"),
                     ("Station site", "site")]
    extra_columns_hidden = ("audience", "station_format", "owner", "genres", "site")

    def __init__(self, config=None):
        super().__init__(config)
        self._stations: list[dict] | None = None   # whole directory, cached after first load

    def _load(self) -> list[dict]:
        generation, cached, current = self._snapshot_read("stations")
        if current:
            self._stations = cached
            return cached
        rows: list[dict] = []
        seen: set[str] = set()

        def page(offset: int) -> tuple[list, int]:
            http.check_request_context()
            d = http.get_json(f"{API}?limit={PAGE}&offset={offset}", timeout=20)
            if not isinstance(d, dict):
                raise ValueError("iHeart returned an invalid station page")
            hits = d.get("hits")
            if not isinstance(hits, list) or "total" not in d:
                raise ValueError("iHeart station page is missing required fields")
            return hits, int(d.get("total") or 0)

        def add(hits: list) -> None:
            for r in self._map(hits):
                if r["url"] not in seen:
                    seen.add(r["url"])
                    rows.append(r)

        hits, total = page(0)
        offset = len(hits)
        add(hits)
        if hits and len(hits) == PAGE and offset < total:
            # Full pages: the rest of the directory is asked for side by side instead of one page after another
            # (eight round trips in a row on a first open). The answers are put back in page order.
            offsets = list(range(PAGE, min(total, 20000), PAGE))
            for later in http.map_with_context(lambda o: page(o)[0], offsets, 4):
                if not later:
                    raise ValueError("iHeart station directory ended before its reported total")
                offset += len(later)
                add(later)
            if offset < min(total, 20000):                  # a page came back short in the middle
                raise ValueError("iHeart station directory ended before its reported total")
        else:
            while offset < total and offset < 20000:        # a short first page: follow the pages as they come
                hits, _total = page(offset)
                if not hits:
                    raise ValueError("iHeart station directory ended before its reported total")
                offset += len(hits)
                add(hits)
        if total and offset < total:
            raise ValueError("iHeart directory exceeds its supported safety limit")
        rows = self._snapshot_publish("stations", generation, rows)
        self._stations = rows
        return rows

    @staticmethod
    def _genre(station: dict) -> str:
        genres = station.get("genres") or []
        for g in genres:
            if isinstance(g, dict) and g.get("primary"):
                return g.get("name", "") or "Other"
        if genres and isinstance(genres[0], dict):
            return genres[0].get("name", "") or "Other"
        return "Other"

    @classmethod
    def _map(cls, hits) -> list[dict]:
        out = []
        for s in hits:
            st = s.get("streams") or {}
            url = (st.get("secure_shoutcast_stream") or st.get("secure_hls_stream")
                   or st.get("shoutcast_stream") or st.get("hls_stream") or "")
            if not url:
                continue
            mk = (s.get("markets") or [{}])[0] if s.get("markets") else {}
            loc = ", ".join(p for p in (mk.get("city", ""), mk.get("stateAbbreviation", "")) if p)
            tag = f"{s.get('band', '')} {s.get('callLetters', '')}".strip()
            names = [g.get("name") for g in (s.get("genres") or []) if isinstance(g, dict) and g.get("name")]
            out.append(make_row(
                title=s.get("name", "") or s.get("callLetters", ""),
                url=url,
                homepage=f"https://www.iheart.com/live/{s.get('id', '')}/",
                genre=cls._genre(s),
                country=(mk.get("country", "") or "US"),
                description=" · ".join(p for p in (loc, tag) if p),
                favicon=s.get("logo", "") or "",
                format="audio/aac",
                listformat="url",
                extra={"call_letters": s.get("callLetters"), "band": s.get("band"), "frequency": s.get("freq") or None,
                       "city": mk.get("city"), "state": mk.get("stateAbbreviation"), "audience": s.get("cume"),
                       "station_format": s.get("format"), "owner": s.get("provider"),
                       "genres": ", ".join(names), "site": s.get("website")},
            ))
        return out

    def invalidate_cache(self) -> None:
        super().invalidate_cache()

    def update_categories(self) -> list[str]:
        from collections import Counter
        counts = Counter(r["genre"] for r in self._load() if r.get("genre"))
        # keep only genres with enough stations to be worth a tab; the rest (iHeart's one-off
        # curated tags like "Christmas") still appear under ★ All Stations.
        genres = sorted(g for g, n in counts.items() if n >= 8)
        return [self.ALL] + genres

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        rows = self._load()
        if category != self.ALL:
            rows = [r for r in rows if r.get("genre") == category]
        if search:
            q = search.lower()
            rows = [r for r in rows if q in r.get("title", "").lower() or q in r.get("genre", "").lower()]
        return rows
