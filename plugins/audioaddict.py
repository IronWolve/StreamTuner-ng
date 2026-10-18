"""AudioAddict (the DI.FM family) — one keyless API, six networks as the CATEGORIES of a single
channel: DI.FM, RadioTunes, JazzRadio, RockRadio, ClassicalRadio, ZenRadio.

Subscriber playback needs a LISTEN KEY. Channel lists and premium playlists come
from api.audioaddict.com without sending that key. Rows carry a tokenized
`audioaddict://<domain>/<channel>` URL so the key is NEVER written to disk caches, favourites/history,
CSV exports, or the status bar. The provider's playlist supplies the network's
actual stream address; the key is added only to a validated premium host at play time.
The plugin is off by default. Subscriber playback uses the provider-required HTTP transport.
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

from ...net import http
from ...security import valid_url
from ..base import Channel, make_row

API = "https://api.audioaddict.com/v1/{net}/channels"
PLAYLIST_API = "https://api.audioaddict.com/v1/{net}/listen/{quality}/{channel}.pls"
_SCHEME = "audioaddict://"
# Keep saved option values compatible; these are choices, not stream suffixes.
_QUALITY_SETS = {"_hi": ("premium_high", "audio/mpeg", 320),
                 "": ("premium", "audio/aac", 128),
                 "_aac": ("premium_medium", "audio/aac", 64)}

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
_BY_DOMAIN = {domain: net for _label, net, domain in NETWORKS}


class AudioAddict(Channel):
    id = "audioaddict"
    title = "AudioAddict"
    description = "AudioAddict family — DI.FM, RadioTunes, JazzRadio, RockRadio, ClassicalRadio, ZenRadio."
    homepage = "https://www.audioaddict.com/"
    priority = "optional"        # subscriber listen key required; off until set
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
        """Resolve a provider playlist without credentials, then add the key in memory.

        Mount names and quality suffixes differ between networks (including Zen's
        `zr` prefix). Only premium hosts for the selected network receive the key.
        """
        u = row.get("url", "")
        if not u.startswith(_SCHEME):
            return row
        if not valid_url(u, token=True):
            raise ValueError("Invalid AudioAddict station")
        parsed = urlsplit(u)
        domain, ch = parsed.hostname, parsed.path.lstrip("/")
        listen_key = ((self.config.get("audioaddict_listen_key", "") if self.config else "") or "").strip()
        if not listen_key:
            raise ValueError("An AudioAddict subscriber listen key is required")
        q = ((self.config.get("audioaddict_quality", "_hi") if self.config else "_hi") or "")
        if not isinstance(q, str) or q not in _QUALITY_SETS:
            q = "_hi"
        quality, audio_format, bitrate = _QUALITY_SETS[q]
        playlist = http.get_text(
            PLAYLIST_API.format(net=_BY_DOMAIN[domain], quality=quality, channel=quote(ch, safe="")),
            timeout=8, max_bytes=64 << 10,
        )
        for line in playlist.splitlines():
            field, separator, value = line.partition("=")
            if not separator or not re.fullmatch(r"File[1-9][0-9]*", field.strip(), re.IGNORECASE):
                continue
            stream = value.strip()
            if not valid_url(stream):
                continue
            parsed_stream = urlsplit(stream)
            if (parsed_stream.scheme not in ("http", "https")
                    or not re.fullmatch(r"prem[1-9][0-9]*\." + re.escape(domain), parsed_stream.hostname or "")
                    or parsed_stream.port not in (None, 80 if parsed_stream.scheme == "http" else 443)
                    or not re.fullmatch(r"/[A-Za-z0-9_-]+", parsed_stream.path)
                    or parsed_stream.query or parsed_stream.fragment):
                continue
            return {**row, "url": f"{stream}?{quote(listen_key, safe='')}",
                    "format": audio_format, "bitrate": bitrate}
        raise ValueError("AudioAddict returned no valid premium stream for this station and quality")
