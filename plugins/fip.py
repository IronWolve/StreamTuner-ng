"""FIP (Radio France) — the legendary eclectic French public music station plus its themed
webradios, as direct Icecast MP3 streams. Hand-curated, no talk; superb music discovery."""

from __future__ import annotations

from ..base import Channel, make_row

STREAM = "https://icecast.radiofrance.fr/{}-midfi.mp3"

# (icecast mount, display name)
CHANNELS = [
    ("fip", "FIP"),
    ("fiprock", "FIP Rock"),
    ("fipjazz", "FIP Jazz"),
    ("fipgroove", "FIP Groove"),
    ("fipworld", "FIP Monde"),
    ("fipnouveautes", "FIP Nouveautés"),
    ("fipreggae", "FIP Reggae"),
    ("fipelectro", "FIP Electro"),
    ("fipmetal", "FIP Metal"),
    ("fippop", "FIP Pop"),
    ("fiphiphop", "FIP Hip-Hop"),
]


class Fip(Channel):
    id = "fip"
    title = "FIP"
    description = "FIP (Radio France) — eclectic French public radio + themed webradios."
    homepage = "https://www.fip.fr/"
    priority = "standard"
    icon_emoji = "🎶"

    def update_categories(self) -> list[str]:
        return ["Channels"]

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        rows = []
        for mount, name in CHANNELS:
            if search and search.lower() not in name.lower():
                continue
            rows.append(make_row(title=name, url=STREAM.format(mount),
                                 genre="FIP", country="France",
                                 format="audio/mpeg", listformat="url"))
        return rows
