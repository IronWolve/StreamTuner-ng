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
        if self._stations is not None:
            return self._stations
        try:
            d = http.get_json(API, timeout=20)
            stations = ((d or {}).get("data") or {}).get("stations") or []
        except Exception:  # noqa: BLE001 — airlock-safe; channel just shows empty/red
            stations = []
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
        self._stations = rows
        return rows

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
