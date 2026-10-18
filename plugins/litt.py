"""LITT Live (formerly Dash Radio) — celebrity & DJ-curated live stations, via Dash's keyless web
API. Each station ships a free stream + logo; categorised by genre. (The premium_stream_url is the
subscriber-only HQ feed — we use the free `stream_url`.)"""

from __future__ import annotations

from ...net import http
from ..base import Channel, make_row

API = "https://web-api.dash-api.com/v1/stations"


class Litt(Channel):
    id = "litt"
    title = "LITT Live"
    description = "LITT Live (Dash Radio) — celebrity & DJ-curated live stations."
    homepage = "https://littlive.com/"
    priority = "standard"
    icon_emoji = "🔥"
    ALL = "★ All Stations"
    category_pins = (ALL,)
    all_category = ALL

    def __init__(self, config=None):
        super().__init__(config)
        self._stations: list[dict] | None = None

    def _load(self) -> list[dict]:
        generation, cached, current = self._snapshot_read("stations")
        if current:
            self._stations = cached
            return cached
        try:
            d = http.get_json(API, timeout=20)
            stations = ((d or {}).get("data") or {}).get("stations") or []
        except Exception:
            # Keep last-good data available for diagnostics/retry, but do not
            # report a transport/API error as a successful empty snapshot.
            has_newer, newer = self._snapshot_after_error("stations", generation)
            if has_newer:
                return newer
            raise
        rows = []
        for s in stations:
            if s.get("enabled") is False:
                continue
            url = s.get("stream_url") or s.get("recovery_stream_url") or ""
            if not url:
                continue
            rows.append(make_row(
                title=s.get("name", "") or s.get("short_name", ""),
                url=url,
                homepage="https://littlive.com/",
                genre=s.get("genre", "") or "Other",
                description=s.get("description", "") or "",
                favicon=s.get("medium_logo_url") or s.get("square_logo_url") or "",
                format="audio/mpeg",
                listformat="url",
            ))
        rows = self._snapshot_publish("stations", generation, rows)
        self._stations = rows
        return rows

    def invalidate_cache(self) -> None:
        super().invalidate_cache()

    def update_categories(self) -> list[str]:
        genres = sorted({r["genre"] for r in self._load() if r.get("genre")})
        return [self.ALL] + genres

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        rows = self._load()
        if category != self.ALL:
            rows = [r for r in rows if r.get("genre") == category]
        if search:
            q = search.lower()
            rows = [r for r in rows if q in r.get("title", "").lower() or q in r.get("genre", "").lower()]
        return rows
