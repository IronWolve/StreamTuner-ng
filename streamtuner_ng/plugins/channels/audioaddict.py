"""AudioAddict (the DI.FM family) — one keyless API, six networks as the CATEGORIES of a single
channel: DI.FM, RadioTunes, JazzRadio, RockRadio, ClassicalRadio, ZenRadio.

Playback needs a paid subscriber LISTEN KEY. AudioAddict decommissioned its FREE public cluster —
the `pubN.<domain>` hosts that the `public3` playlists hand out are now NXDOMAIN (dead), so there is
no working free stream. The premium hosts (`prem1.<domain>`) are alive and require the key.

Channel lists come from api.audioaddict.com (no key). Rows carry a tokenized
`audioaddict://<domain>/<channel>` URL so the key is NEVER written to disk caches, favourites/history,
CSV exports, or the status bar; the real stream URL is built only at play time in resolve_url():
    http://prem1.<domain>/<channel>[_hi|_aac]?<listen_key>   (320k MP3 / 128k AAC / 64k AAC)
OFF by default until the key is set (Options → General).
"""

from __future__ import annotations

from ...net import http
from ..base import Channel, make_row

API = "https://api.audioaddict.com/v1/{net}/channels"
_SCHEME = "audioaddict://"

# (category label, network slug, stream domain)
NETWORKS = [
    ("DI.FM",          "di",             "di.fm"),
    ("RadioTunes",     "radiotunes",     "radiotunes.com"),
    ("JazzRadio",      "jazzradio",      "jazzradio.com"),
    ("RockRadio",      "rockradio",      "rockradio.com"),
    ("ClassicalRadio", "classicalradio", "classicalradio.com"),
    ("ZenRadio",       "zenradio",       "zenradio.com"),
]
_BY_LABEL = {label: (net, domain) for label, net, domain in NETWORKS}


class AudioAddict(Channel):
    id = "audioaddict"
    title = "AudioAddict"
    description = "AudioAddict family — DI.FM, RadioTunes, JazzRadio, RockRadio, ClassicalRadio, ZenRadio."
    homepage = "https://www.audioaddict.com/"
    priority = "optional"        # needs a subscriber listen key (free cluster is dead); off until set
    has_search = True
    needs_resolve = True         # rows carry audioaddict://… ; the listen key is added only at play time
    category_sort = False        # keep the networks in their listed order, not A–Z
    icon_emoji = "🎚"
    options_spec = [
        {"type": "choice", "key": "audioaddict_quality", "label": "Bitrate", "default": "_hi",
         "choices": [("320 kbps MP3", "_hi"), ("128 kbps AAC", ""), ("64 kbps AAC", "_aac")]},
        {"type": "secret", "key": "audioaddict_listen_key", "label": "Listen Key",
         "placeholder": "your DI.FM / AudioAddict listen key"},
    ]

    def update_categories(self) -> list[str]:
        return [label for label, _net, _domain in NETWORKS]

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        if category not in _BY_LABEL:
            return []
        net, domain = _BY_LABEL[category]
        data = http.get_json(API.format(net=net), timeout=20)
        rows = []
        for c in data:
            key = c.get("key")
            if not key:
                continue
            if search and search.lower() not in (
                (c.get("name", "") + " " + (c.get("description") or "")).lower()
            ):
                continue
            fav = c.get("asset_url") or ""
            if fav.startswith("//"):
                fav = "https:" + fav
            fav = fav.replace("{?size}", "")
            rows.append(make_row(
                title=c.get("name", ""),
                url=f"{_SCHEME}{domain}/{key}",   # tokenized — the listen key is never stored here
                genre=category,
                description=(c.get("description") or "").strip(),
                favicon=fav,
                format="audio/aac",
                listformat="url",
            ))
        return rows

    def resolve_url(self, row: dict) -> dict:
        """Build the real premium stream URL at play time from `audioaddict://<domain>/<channel>`,
        using the user's listen key. The key is applied here only — never written into the row that
        gets cached/favourited/exported/displayed. With no key there is nothing playable (the free
        cluster is decommissioned), so the row is returned unchanged."""
        u = row.get("url", "")
        if not u.startswith(_SCHEME):
            return row
        domain, _, ch = u[len(_SCHEME):].partition("/")
        if not domain or not ch:
            return row
        listen_key = ((self.config.get("audioaddict_listen_key", "") if self.config else "") or "").strip()
        if not listen_key:
            return row
        # quality suffix: '' = 128k AAC, '_hi' = 320k MP3, '_aac' = 64k AAC (right-click Bitrate; default 320k)
        q = ((self.config.get("audioaddict_quality", "_hi") if self.config else "_hi") or "")
        return {**row, "url": f"http://prem1.{domain}/{ch}{q}?{listen_key}"}
