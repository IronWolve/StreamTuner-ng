"""SomaFM — curated, commercial-free. st2 hardcoded the list; we use the live
`channels.json` feed (even st2 2.2.2 still hardcodes it, so this is an upgrade)."""

from __future__ import annotations

from ...net import http
from ..base import Channel, make_row

FEED = "https://somafm.com/channels.json"
_QUALITY_RANK = {"highest": 3, "high": 2, "low": 1}
_FMT_MIME = {"mp3": "audio/mpeg", "aac": "audio/aac", "aacp": "audio/aac"}


class SomaFM(Channel):
    id = "somafm"
    title = "SomaFM"
    description = "Listener-supported, commercial-free, underground/independent radio."
    homepage = "https://somafm.com/"
    priority = "standard"      # on by default
    category_pins = ("All",)   # "All" on top; genres sort A–Z
    all_category = "All"

    def __init__(self, config=None):
        super().__init__(config)
        self._channels: list[dict] = []

    def _fetch(self) -> list[dict]:
        self._channels = http.get_json(FEED).get("channels", [])
        return self._channels

    @staticmethod
    def _best_playlist(ch: dict) -> tuple[str, str]:
        """Pick the highest-quality playlist; return (url, mime)."""
        pls = ch.get("playlists", [])
        # highest quality first; prefer mp3 for broad player support on ties
        pls = sorted(
            pls,
            key=lambda p: (_QUALITY_RANK.get(p.get("quality", ""), 0),
                           1 if p.get("format") == "mp3" else 0),
            reverse=True,
        )
        if not pls:
            return "", ""
        top = pls[0]
        return top.get("url", ""), _FMT_MIME.get(top.get("format", ""), "audio/mpeg")

    def update_categories(self) -> list[str]:
        chans = self._fetch()
        genres = sorted({(c.get("genre") or "").split("|")[0] for c in chans if c.get("genre")})
        return ["All"] + genres

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        chans = self._channels or self._fetch()
        rows = []
        for c in chans:
            genre = c.get("genre", "")
            if category not in ("All", "", None) and not genre.startswith(category):
                continue
            if search and search.lower() not in (c.get("title", "") + c.get("description", "")).lower():
                continue
            url, mime = self._best_playlist(c)
            rows.append(make_row(
                title=c.get("title", ""),
                url=url,
                homepage="https://somafm.com/" + c.get("id", "") + "/",
                favicon=c.get("image", ""),
                genre=genre,
                description=c.get("description", ""),   # SomaFM tagline, not a song
                listeners=int(c.get("listeners") or 0),
                format=mime,
                listformat="pls",
            ))
        return rows
