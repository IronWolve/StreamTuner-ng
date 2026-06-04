"""Nightride.FM — synthwave / darksynth / retrowave net-radio. A handful of curated, ad-free
channels as direct streams (like SomaFM)."""

from __future__ import annotations

from ..base import Channel, make_row

STREAM = "https://stream.nightride.fm/{}.m4a"

# (mount, display name, genre)
CHANNELS = [
    ("nightride", "Nightride", "Synthwave"),
    ("chillsynth", "ChillSynth", "Chillwave"),
    ("datawave", "DataWave", "Synthwave"),
    ("spacesynth", "SpaceSynth", "Spacesynth"),
    ("darksynth", "Darksynth", "Darksynth"),
    ("horrorsynth", "Horror Synth", "Horrorsynth"),
    ("ebsm", "EBSM", "EBM / Industrial"),
    ("rekt", "REKT", "Hard / Aggrotech"),
]


class Nightride(Channel):
    id = "nightride"
    title = "Nightride.FM"
    description = "Nightride.FM — synthwave / darksynth / retrowave radio (ad-free)."
    homepage = "https://nightride.fm/"
    priority = "standard"
    icon_emoji = "🌃"

    def update_categories(self) -> list[str]:
        return ["Channels"]

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        rows = []
        for mount, name, genre in CHANNELS:
            if search and search.lower() not in name.lower():
                continue
            rows.append(make_row(title=name, url=STREAM.format(mount),
                                 genre=genre, format="audio/mpeg", listformat="url"))
        return rows
