"""Ask a station's stream which song it is announcing right now (the ICY title blocks).

Only streams that announce titles can answer: a direct stream whose server sends `icy-metaint`.
Playlist addresses (.pls, .m3u, .asx, TuneIn) are followed to their first stream; HLS and servers
without title blocks answer with an empty string. Reads at most 400 kB per station.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

from .net import http
from .player.mpv_player import clean_icy_title
from .security import valid_url

MAX_READ = 400 * 1024                 # bytes of stream read at most
MAX_METAINT = 256 * 1024              # a larger block interval is not a title stream
PLAYLIST_BYTES = 64 * 1024
_TITLE = re.compile(r"StreamTitle='(.*?)'\s*;|StreamTitle='(.*)'", re.S)
_PLAYLIST_HINTS = (".pls", ".m3u", ".asx", "tunein-station")


def first_stream(text: str) -> str:
    """The first stream address in a .pls / .m3u / .asx playlist text ('' if none)."""
    for pattern in (r"(?im)^\s*File\d+\s*=\s*(\S+)", r"(?is)<ref[^>]*\bhref\s*=\s*[\"']([^\"']+)", r"(?m)^\s*(https?://\S+)"):
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def parse_title(block: bytes) -> str:
    """'StreamTitle' out of one metadata block (UTF-8, else Latin-1); '' if there is none."""
    try:
        text = block.rstrip(b"\x00").decode("utf-8")
    except UnicodeDecodeError:
        text = block.rstrip(b"\x00").decode("latin-1")
    match = _TITLE.search(text)
    if not match:
        return ""
    return clean_icy_title((match.group(1) if match.group(1) is not None else match.group(2) or "").strip())


def _read_exact(raw, count: int, deadline: float) -> bytes:
    data = b""
    while len(data) < count:
        if time.monotonic() > deadline:
            raise TimeoutError("stream too slow")
        chunk = raw.read(count - len(data))
        if not chunk:
            raise EOFError("stream ended")
        data += chunk
    return data


def _is_playlist(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path.endswith((".pls", ".m3u", ".asx")) or "tunein-station" in url


def current_song(url: str, timeout: float = 8.0) -> str:
    """The song `url` announces now, or '' (never raises)."""
    try:
        return _current_song(url, timeout)
    except Exception:  # noqa: BLE001 - one dead station must not stop the others
        return ""


def _current_song(url: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout * 2
    if not valid_url(url, web=True):
        return ""
    if _is_playlist(url) or any(h in url for h in _PLAYLIST_HINTS):
        text = http.get_text(url, headers={"User-Agent": http.BROWSER_UA}, timeout=timeout, max_bytes=PLAYLIST_BYTES)
        url = first_stream(text)
        if not valid_url(url, web=True):
            return ""
    if urlsplit(url).path.lower().endswith((".m3u8",)):
        return ""                                                   # HLS carries no ICY title blocks
    response = http.session.get(url, headers={"User-Agent": http.BROWSER_UA, "Icy-MetaData": "1",
                                              "Accept-Encoding": "identity"}, timeout=timeout, stream=True)
    try:
        interval = response.headers.get("icy-metaint", "")
        if response.status_code >= 400 or not interval.isdigit() or not 0 < int(interval) <= MAX_METAINT:
            return ""
        step, raw, used = int(interval), response.raw, 0
        while used + step + 1 <= MAX_READ:
            _read_exact(raw, step, deadline)
            size = _read_exact(raw, 1, deadline)[0] * 16
            used += step + 1 + size
            if size:
                title = parse_title(_read_exact(raw, size, deadline))
                if title:
                    return title
        return ""
    finally:
        response.close()


__all__ = ["current_song", "first_stream", "parse_title"]
