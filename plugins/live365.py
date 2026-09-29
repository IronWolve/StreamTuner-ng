"""Live365 — the full station directory, keyless, via the public sitemap.

Live365's station *API* is token-gated, but its **sitemap** (`sitemap-main.xml`) publicly lists
every station page, and each URL embeds the mount id:  `/station/<Name-Slug>-a<id>`.  So we read
the sitemap once (~5.4k stations), turn each into a row — name from the slug, stream =
`streaming.live365.com/<id>` — and the streams play directly (ICY).  The homepage also
server-renders a small *featured* set with richer ICY metadata, kept as a pinned category.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from ...net import http
from ..base import Channel, make_row

LISTEN = "https://live365.com/listen"
SITEMAP = "https://live365.com/sitemap-main.xml"
STREAM = "https://streaming.live365.com/"
_ID_RE = re.compile(r"streaming\.live365\.com/([a-z0-9]+)")
_STATION_RE = re.compile(r"/station/(.+)-(a\d+)/?$")        # /station/<Name-Slug>-a<id>
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_BROWSER = {"User-Agent": http.BROWSER_UA}


class Live365(Channel):
    id = "live365"
    title = "Live365"
    description = "Live365 — independent stations (full directory via the public sitemap)."
    homepage = "https://live365.com/"
    priority = "standard"
    icon_emoji = "📻"
    TOP = "★ Featured"
    ALL = "All Stations"
    category_pins = (TOP, ALL)
    all_category = ALL

    def update_categories(self) -> list[str]:
        return [self.TOP, self.ALL]

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        if category == self.TOP and not search:
            return self._featured()
        return self._all_stations()

    def _all_stations(self) -> list[dict]:
        """Every station from the sitemap — name from the slug, stream from the embedded id."""
        xml = http.get_text(SITEMAP, headers=_BROWSER, timeout=25)
        seen: set[str] = set()
        rows: list[dict] = []
        for loc in _LOC_RE.findall(xml):
            m = _STATION_RE.search(loc)
            if not m:
                continue
            url = STREAM + m.group(2)
            if url in seen:
                continue
            seen.add(url)
            name = re.sub(r"\s+", " ", m.group(1).replace("-", " ")).strip()
            rows.append(make_row(
                title=name or m.group(2),
                url=url,                         # a direct stream — mpv plays it as-is
                homepage=loc,                    # the Live365 station page (Open Website / Info)
                listformat="url",
            ))
        rows.sort(key=lambda r: r["title"].lower())
        return rows

    def _featured(self) -> list[dict]:
        """The homepage's curated set, enriched with each stream's ICY headers."""
        html = http.get_text(LISTEN, headers=_BROWSER, timeout=15)
        ids = sorted(set(_ID_RE.findall(html)))
        with ThreadPoolExecutor(max_workers=12) as ex:
            rows = [r for r in ex.map(self._probe, ids) if r]
        rows.sort(key=lambda r: r["title"].lower())
        return rows

    @staticmethod
    def _probe(sid: str) -> dict | None:
        """Read a stream's ICY headers -> a richer row (name / genre / bitrate / codec)."""
        url = STREAM + sid
        try:
            r = http.session.get(url, headers={**_BROWSER, "Icy-MetaData": "1"}, timeout=8, stream=True)
            h = r.headers
            r.close()
        except Exception:  # noqa: BLE001 — a dead / blocked stream is just skipped
            return None
        br = (h.get("icy-br") or "").split(",")[0].strip()
        return make_row(
            title=h.get("icy-name") or sid,
            url=url,
            genre=h.get("icy-genre", "") or "",
            homepage=h.get("icy-url", "") or "",
            bitrate=int(br) if br.isdigit() else 0,
            format=h.get("content-type", "audio/mpeg") or "audio/mpeg",
            listformat="url",
        )
