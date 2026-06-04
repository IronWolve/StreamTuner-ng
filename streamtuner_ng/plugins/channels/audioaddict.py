"""AudioAddict (the DI.FM family) — one keyless API, six networks as the CATEGORIES of a single
channel: DI.FM, RadioTunes, JazzRadio, RockRadio, ClassicalRadio, ZenRadio.

Channel lists come from api.audioaddict.com (no key). Playback needs YOUR AudioAddict listen key
(Options → General): url = http://prem1.<net>/<channel>?<key>  (128k AAC; "<channel>_hi" = 320k,
premium). OFF by default until the key is set.
"""

from __future__ import annotations

from ...net import http
from ..base import Channel, make_row

API = "https://api.audioaddict.com/v1/{net}/channels"

# (category label, network slug, premium host)
NETWORKS = [
    ("DI.FM",          "di",             "prem1.di.fm"),
    ("RadioTunes",     "radiotunes",     "prem1.radiotunes.com"),
    ("JazzRadio",      "jazzradio",      "prem1.jazzradio.com"),
    ("RockRadio",      "rockradio",      "prem1.rockradio.com"),
    ("ClassicalRadio", "classicalradio", "prem1.classicalradio.com"),
    ("ZenRadio",       "zenradio",       "prem1.zenradio.com"),
]
_BY_LABEL = {label: (net, host) for label, net, host in NETWORKS}


class AudioAddict(Channel):
    id = "audioaddict"
    title = "AudioAddict"
    description = "AudioAddict family — DI.FM, RadioTunes, JazzRadio, RockRadio, ClassicalRadio, ZenRadio."
    homepage = "https://www.audioaddict.com/"
    priority = "optional"        # needs the listen key; off until it's set in Options → General
    has_search = True
    category_sort = False        # keep the networks in their listed order, not A–Z
    icon_emoji = "🎚"

    def update_categories(self) -> list[str]:
        return [label for label, _net, _host in NETWORKS]

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        if category not in _BY_LABEL:
            return []
        net, prem_host = _BY_LABEL[category]
        listen_key = ((self.config.get("audioaddict_listen_key", "") if self.config else "") or "").strip()
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
                url=f"http://{prem_host}/{key}?{listen_key}",
                genre=category,
                description=(c.get("description") or "").strip(),
                favicon=fav,
                format="audio/aac",
                listformat="url",
            ))
        return rows
