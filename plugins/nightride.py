"""Nightride.FM — synthwave / darksynth / retrowave net-radio. A handful of curated, ad-free
channels as direct streams (like SomaFM)."""

from __future__ import annotations

from ..base import Channel, make_row

STREAM = "https://stream.nightride.fm/{}.mp3"

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
    needs_resolve = True

    def resolve_url(self, row: dict) -> dict:
        # Existing favorites and disk caches can still contain the retired AAC mounts.
        old = {f"https://stream.nightride.fm/{mount}.m4a": STREAM.format(mount)
               for mount, _name, _genre in CHANNELS}
        return {**row, "url": old.get(row.get("url"), row.get("url", ""))}

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
