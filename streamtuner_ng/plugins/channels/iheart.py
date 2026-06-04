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
    icon_emoji = "❤"
    ALL = "★ All Stations"
    category_pins = (ALL,)
    all_category = ALL

    def __init__(self, config=None):
        super().__init__(config)
        self._stations: list[dict] | None = None   # whole directory, cached after first load

    def _load(self) -> list[dict]:
        if self._stations is not None:
            return self._stations
        rows: list[dict] = []
        seen: set[str] = set()
        offset, total = 0, None
        while (total is None or offset < total) and offset < 20000:   # hard safety cap
            d = http.get_json(f"{API}?limit={PAGE}&offset={offset}", timeout=20)
            if not isinstance(d, dict):
                break
            hits = d.get("hits") or []
            if total is None:
                total = d.get("total") or 0
            if not hits:
                break
            offset += len(hits)
            for r in self._map(hits):
                if r["url"] not in seen:
                    seen.add(r["url"])
                    rows.append(r)
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
            ))
        return out

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
