"""Open the default browser on a web search for a song or a station name.

The owner chose a plain search address, with no other engine offered. Nothing is fetched here: the
text only goes to the browser the user already has.
"""

from __future__ import annotations

from urllib.parse import quote_plus

SEARCH = "https://duckduckgo.com/?q="
MAX_TEXT = 200


def search_url(text: object) -> str:
    """The search address for ``text`` ('' when there is nothing to search for)."""
    words = " ".join(str(text or "").split())[:MAX_TEXT]
    return SEARCH + quote_plus(words) if words else ""


def open_search(text: object) -> bool:
    """Open the browser on a search for ``text``; False when there is nothing to search or it cannot open."""
    url = search_url(text)
    if not url:
        return False
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    return bool(QDesktopServices.openUrl(QUrl(url)))


def song_of(row: dict) -> str:
    """The song a station announces now, cleaned for a search ('' when none)."""
    from .player.mpv_player import clean_icy_title
    return clean_icy_title(str((row or {}).get("playing") or ""))


def station_of(row: dict) -> str:
    return " ".join(str((row or {}).get("title") or "").split())
