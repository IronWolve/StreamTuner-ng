"""Radio Paradise — every channel × every quality (you pick the bitrate / format).

RP runs a handful of DJ-curated mixes, each offered at several qualities. `list_chan` lists the
channels (Radio 2050 is a real channel it omits, so we add it). Stream names:
  Main Mix:   aac-32 / aac-64 / aac-128 / mp3-192 / aac-320 / flacm
  others:     <slug>-32 / -64 / -128 / -192 / -320 / -flacm    (Serenity uses -flac, 64k+FLAC only)
FLAC is lossless (heavy); pick a lighter row for less bandwidth.
"""

from __future__ import annotations

from ...net import http
from ..base import Channel, make_row

LIST = "https://api.radioparadise.com/api/list_chan?list_type=json"
STREAM = "https://stream.radioparadise.com/"
EXTRA = [{"chan": "2050", "slug": "radio2050", "title": "Radio 2050"}]   # not in list_chan

# (label, mime, quality key)
QUALITIES = [
    ("32k AAC+", "audio/aac",  "32"),
    ("64k AAC+", "audio/aac",  "64"),
    ("128k AAC", "audio/aac",  "128"),
    ("192k MP3", "audio/mpeg", "192"),
    ("320k AAC", "audio/aac",  "320"),
    ("FLAC",     "audio/flac", "flac"),
]
MAIN_PATH = {"32": "aac-32", "64": "aac-64", "128": "aac-128",
             "192": "mp3-192", "320": "aac-320", "flac": "flacm"}
SERENITY_ONLY = {"64", "flac"}            # Serenity offers just 64k AAC+ and FLAC


class RadioParadise(Channel):
    id = "radioparadise"
    title = "Radio Paradise"
    description = "Hand-curated, listener-supported radio — every channel & quality."
    homepage = "https://radioparadise.com/"
    priority = "standard"
    icon_emoji = "🎵"

    def update_categories(self) -> list[str]:
        return ["Channels"]

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        chans = http.get_json(LIST, timeout=15)
        chans = (chans if isinstance(chans, list) else []) + EXTRA
        rows = []
        for c in chans:
            slug = (c.get("slug") or "").strip()
            ctitle = c.get("title") or slug
            if not slug:
                continue
            is_main = str(c.get("chan")) == "0"
            listeners = int(c.get("current_listeners") or 0)
            for label, mime, q in QUALITIES:
                if slug == "serenity" and q not in SERENITY_ONLY:
                    continue
                if is_main:
                    path = MAIN_PATH[q]
                elif slug == "serenity":
                    path = "serenity" if q == "64" else "serenity-flac"   # 64k = bare slug; FLAC = -flac
                elif q == "flac":
                    path = f"{slug}-flacm"
                else:
                    path = f"{slug}-{q}"
                title = f"{ctitle} — {label}"
                if search and search.lower() not in title.lower():
                    continue
                rows.append(make_row(
                    title=title,
                    url=STREAM + path,
                    genre="Radio Paradise",
                    listeners=listeners,
                    format=mime,
                    listformat="url",
                ))
        rows.sort(key=lambda r: r["title"].lower())
        return rows
