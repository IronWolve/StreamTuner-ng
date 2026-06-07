"""The HTTP layer — one shared session with an honest User-Agent.

RadioBrowser etiquette asks for a descriptive UA and reasonable rate; we oblige.
Plugins call get_json()/get_text(); the plugin host runs these off the UI thread
inside the airlock (with a timeout), so this module stays simple and synchronous.
"""

from __future__ import annotations

from typing import Any

import requests

from .. import APP_NAME, __version__

# Honest, descriptive UA (not masquerading as a browser) — good manners + lets
# directories identify us. Includes the version (single source).
USER_AGENT = f"{APP_NAME}/{__version__} (+https://github.com/IronWolve/StreamTuner-ng)"

# Browser-like UA, single-sourced — Shoutcast/Live365 + some favicon hosts reject a
# non-browser UA. Plugins and favicons import this instead of re-typing the literal.
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"

DEFAULT_TIMEOUT = 8.0

session = requests.Session()
session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
    }
)


def get_json(url: str, params: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> Any:
    r = session.get(url, params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_text(url: str, params: dict | None = None, headers: dict | None = None,
             timeout: float = DEFAULT_TIMEOUT) -> str:
    r = session.get(url, params=params or {}, headers=headers or {}, timeout=timeout)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        # requests defaults to Latin-1 when a server omits the charset header, which mangles
        # UTF-8 (e.g. TuneIn's "Rádio" -> "RÃ¡dio"). Detect the real encoding instead.
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def get_bytes(url: str, timeout: float = DEFAULT_TIMEOUT, max_bytes: int | None = None) -> bytes:
    """Fetch raw bytes. `max_bytes` caps the download (streamed): a resource that declares or
    streams past the cap raises ValueError, so a runaway 'favicon' can't fill the disk or RAM."""
    if not max_bytes:
        r = session.get(url, timeout=timeout)
        r.raise_for_status()
        return r.content
    r = session.get(url, timeout=timeout, stream=True)
    try:
        r.raise_for_status()
        declared = r.headers.get("Content-Length", "")
        if declared.isdigit() and int(declared) > max_bytes:
            raise ValueError(f"resource too large: {declared} bytes > {max_bytes}")
        chunks, total = [], 0
        for chunk in r.iter_content(65536):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"resource exceeded {max_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        r.close()


def post_json(url: str, data: dict | None = None, headers: dict | None = None,
              timeout: float = DEFAULT_TIMEOUT):
    """POST form data, return JSON. `headers` override the session defaults
    (e.g. a browser User-Agent for sites that gate their internal API)."""
    r = session.post(url, data=data or {}, headers=headers or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()
