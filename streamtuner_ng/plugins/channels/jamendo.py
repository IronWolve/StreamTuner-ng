"""Jamendo — Creative-Commons music radios via the public Jamendo API.

Jamendo hosts free, artist-friendly (CC-licensed) music and exposes a JSON API with a
long-standing public client id (no signup). The `radios/` endpoint lists ~15 genre radios;
each radio's continuous stream URL comes from `radios/stream/?id=<id>`. mpv plays them directly.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ...net import http
from ..base import Channel, make_row

CID = "49daa4f5"                          # Jamendo's public client id (keyless, long-standing)
API = "https://api.jamendo.com/v3.0"


class Jamendo(Channel):
    id = "jamendo"
    title = "Jamendo"
    description = "Creative-Commons music radios — free, artist-friendly."
    homepage = "https://www.jamendo.com/"
    priority = "standard"
    icon_emoji = "🎵"

    def update_categories(self) -> list[str]:
        return ["Radios"]

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        data = http.get_json(f"{API}/radios/",
                             {"client_id": CID, "format": "json", "limit": "200"}, timeout=20)
        radios = data.get("results", []) if isinstance(data, dict) else []

        def stream_of(rd):
            try:
                s = http.get_json(f"{API}/radios/stream/",
                                  {"client_id": CID, "id": rd.get("id"), "format": "json"}, timeout=12)
                return (s.get("results") or [{}])[0]
            except Exception:  # noqa: BLE001 — a missing stream just drops that radio
                return {}

        with ThreadPoolExecutor(max_workers=8) as ex:
            streams = list(ex.map(stream_of, radios))

        rows = []
        for rd, res in zip(radios, streams):
            url = res.get("stream") or ""
            name = rd.get("dispname") or rd.get("name") or ""
            if not url or not name:
                continue
            if search and search.lower() not in name.lower():
                continue
            rows.append(make_row(
                title=name,
                url=url,                              # streaming.jamendo.com/Jam<Name> — direct mp3
                genre="Jamendo",
                favicon=rd.get("image") or res.get("image") or "",
                format="audio/mpeg",
                listformat="url",
            ))
        rows.sort(key=lambda r: r["title"].lower())
        return rows
