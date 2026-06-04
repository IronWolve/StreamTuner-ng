"""Fetch + cache a streaming service's favicon from its homepage, for the channel
tab icons. Cached on disk (~/.config/streamtuner-ng/icons/) so we don't re-fetch
every launch (DECISIONS D13 — the service's OWN favicon, no third-party service).
"""

from __future__ import annotations

import hashlib
import re
import struct
from urllib.parse import urljoin, urlparse

from .net import http

_LINK = re.compile(r'<link\b[^>]*rel=["\'][^"\']*icon[^"\']*["\'][^>]*>', re.I)
_HREF = re.compile(r'href=["\']([^"\']+)', re.I)
_MAGIC = [(b"\x89PNG", "png"), (b"\x00\x00\x01\x00", "ico"), (b"GIF8", "gif"),
          (b"\xff\xd8\xff", "jpg"), (b"<svg", "svg"), (b"<?xml", "svg")]


def _ext(data: bytes) -> str:
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    return "ico"


def _fetch(homepage: str) -> bytes | None:
    """Try the homepage's <link rel=icon>, else fall back to /favicon.ico."""
    href = None
    try:
        html = http.get_text(homepage, timeout=_FAV_TIMEOUT)
        m = _LINK.search(html)
        if m:
            h = _HREF.search(m.group(0))
            if h:
                href = urljoin(homepage, h.group(1))
    except Exception:  # noqa: BLE001 — homepage may be unfetchable; fall through
        pass
    if not href:
        p = urlparse(homepage)
        href = f"{p.scheme}://{p.netloc}/favicon.ico"
    try:
        data = http.get_bytes(href, timeout=_FAV_TIMEOUT)
        if data and len(data) > 70:        # skip empty / 1x1 stub responses
            return data
    except Exception:  # noqa: BLE001
        return None
    return None


def ensure_favicon(config, channel) -> str | None:
    """Return a cached favicon path for the channel (fetching once if needed), or None."""
    home = getattr(channel, "homepage", "")
    if not home:
        return None
    for existing in config.icon_dir.glob(f"chan_{channel.id}.*"):
        return str(existing)               # already cached -> no network
    data = _fetch(home)
    if not data:
        return None
    path = config.icon_dir / f"chan_{channel.id}.{_ext(data)}"
    try:
        path.write_bytes(data)
        return str(path)
    except Exception:  # noqa: BLE001
        return None


_BROWSER_UA = {"User-Agent": http.BROWSER_UA}
_FAV_TIMEOUT = 4   # short per-fetch timeout: favicon network is best-effort, laggy hosts must fail fast


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:16]


def _img_size(data: bytes) -> int:
    """Best-effort max(width, height) in px from raw image bytes (0 = unknown). SVG is
    scalable -> reported large so it always counts as 'big enough'."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", data[16:24]); return max(w, h)
        if data[:3] == b"GIF":
            w, h = struct.unpack("<HH", data[6:10]); return max(w, h)
        if data[:4] == b"\x00\x00\x01\x00":                  # ICO: largest directory entry
            best = 0
            for i in range(struct.unpack("<H", data[4:6])[0]):
                off = 6 + i * 16
                best = max(best, data[off] or 256, data[off + 1] or 256)
            return best
        if data[:2] == b"\xff\xd8":                           # JPEG: scan for a SOF marker
            i, n = 2, len(data)
            while i + 9 < n:
                if data[i] != 0xFF:
                    i += 1; continue
                mk = data[i + 1]
                if 0xC0 <= mk <= 0xCF and mk not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9]); return max(w, h)
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
            return 0
        if data[:5] == b"<?xml" or b"<svg" in data[:300].lower():
            return 9999
    except Exception:  # noqa: BLE001
        return 0
    return 0


def _rank_linked_icons(homepage: str, html: str) -> list[str]:
    """Icon URLs declared in the page <link>s, best resolution first: apple-touch-icon
    (~180px) > rel=icon with the largest declared `sizes` > the rest."""
    apple, sized = [], []
    for m in re.finditer(r"<link\b[^>]*>", html, re.I):
        tag = m.group(0)
        if not re.search(r'rel=["\'][^"\']*icon', tag, re.I):
            continue
        href = _HREF.search(tag)
        if not href:
            continue
        u = urljoin(homepage, href.group(1))
        if re.search(r"apple-touch-icon", tag, re.I):
            apple.append(u)
        else:
            sm = re.search(r'sizes=["\']?(\d+)', tag, re.I)
            sized.append((int(sm.group(1)) if sm else 0, u))
    sized.sort(key=lambda t: t[0], reverse=True)
    return apple + [u for _, u in sized]


def _best_icon_bytes(homepage: str) -> bytes | None:
    """The best-resolution icon for a homepage. Tries the declared icons (apple-touch-icon
    first), then the usual /apple-touch-icon.png and /favicon.ico paths; returns the first that
    decodes to >=96px, else the largest small one found (so we never settle for a 16px stub when
    something bigger exists)."""
    try:
        html = http.get_text(homepage, headers=_BROWSER_UA, timeout=_FAV_TIMEOUT)
    except Exception:  # noqa: BLE001
        html = ""
    cands = _rank_linked_icons(homepage, html)
    p = urlparse(homepage)
    if p.scheme and p.netloc:
        base = f"{p.scheme}://{p.netloc}"
        cands += [f"{base}/apple-touch-icon.png",
                  f"{base}/apple-touch-icon-precomposed.png", f"{base}/favicon.ico"]
    seen: set[str] = set()
    best, best_sz = None, 0
    tries = 0
    for u in cands:
        if u in seen:
            continue
        seen.add(u)
        if tries >= 4:           # cap attempts: a dead/laggy host can't run up a string of timeouts
            break
        tries += 1
        try:
            data = http.get_bytes(u, timeout=_FAV_TIMEOUT)
        except Exception:  # noqa: BLE001
            continue
        if not data or len(data) <= 70:
            continue
        sz = _img_size(data)
        if sz >= 96:
            return data
        if sz > best_sz:
            best, best_sz = data, sz
    return best


def favicon_url_for_home(homepage: str) -> str | None:
    """The best icon URL for a homepage: its largest declared <link rel=icon>
    (apple-touch-icon preferred), else /favicon.ico."""
    try:
        html = http.get_text(homepage, headers=_BROWSER_UA, timeout=_FAV_TIMEOUT)
    except Exception:  # noqa: BLE001
        html = ""
    linked = _rank_linked_icons(homepage, html)
    if linked:
        return linked[0]
    p = urlparse(homepage)
    return f"{p.scheme}://{p.netloc}/favicon.ico" if p.scheme and p.netloc else None


def _icy_homepage(url: str) -> str:
    """The station homepage announced in a stream's ICY `icy-url` header (resolving a
    .pls/.m3u playlist to the server first). '' if none."""
    if not url:
        return ""
    server = url
    if any(x in url for x in (".pls", ".m3u", "tunein-station")):
        try:
            txt = http.get_text(url, headers=_BROWSER_UA, timeout=_FAV_TIMEOUT)
            m = re.search(r"File\d+=(\S+)", txt) or re.search(r"https?://\S+", txt)
            if m:
                server = (m.group(1) if m.lastindex else m.group(0)).strip()
        except Exception:  # noqa: BLE001
            return ""
    try:
        r = http.session.get(server, headers={**_BROWSER_UA, "Icy-MetaData": "1"}, timeout=_FAV_TIMEOUT, stream=True)
        home = r.headers.get("icy-url", "") or ""
        r.close()
    except Exception:  # noqa: BLE001
        return ""
    return home if home.startswith("http") else ""


def derive_station_favicon_url(row: dict) -> str | None:
    """For an icon-less station, find a favicon URL via its broadcaster homepage (see
    `_derived_home`: the row `homepage`, else the stream's ICY `icy-url`). Used lazily
    when a station is favourited."""
    home = _derived_home(row)
    return favicon_url_for_home(home) if home else None


def station_favicon(config, url: str) -> str | None:
    """Download + cache a station's favicon (the row's `favicon` URL), keyed by URL
    hash. Returns the cached path, or None. Cached on disk -> no re-fetch next time."""
    if not url or not url.startswith("http"):
        return None
    h = _hash(url)
    for existing in config.icon_dir.glob(f"st_{h}.*"):
        return None if existing.suffix == ".miss" else str(existing)
    try:
        data = http.get_bytes(url, timeout=_FAV_TIMEOUT)
        if data and len(data) > 70:
            path = config.icon_dir / f"st_{h}.{_ext(data)}"
            path.write_bytes(data)
            return str(path)
    except Exception:  # noqa: BLE001
        return None
    return None


def _derived_home(row: dict) -> str:
    """The broadcaster homepage for a station: the row `homepage`, else (or for a Live365
    station page, which only yields Live365's own logo) the stream's ICY `icy-url`."""
    home = row.get("homepage", "")
    if not home or "live365.com" in home:
        home = _icy_homepage(row.get("url", ""))
    return home or ""


def derived_station_favicon(config, row: dict) -> str | None:
    """For a station the directory gives no favicon for (Shoutcast, TuneIn, …), derive the
    broadcaster's REAL logo from its own site (row `homepage`, else the stream's ICY `icy-url`),
    preferring a big apple-touch-icon over the tiny /favicon.ico. Cached keyed by the STATION
    url, plus a `.miss` marker for logo-less stations, so the slow round-trip runs once each."""
    surl = row.get("url", "")
    if not surl or not surl.startswith("http"):
        return None
    h = _hash("derived:" + surl)
    for existing in config.icon_dir.glob(f"st_{h}.*"):
        return None if existing.suffix == ".miss" else str(existing)
    home = _derived_home(row)
    data = _best_icon_bytes(home) if home else None
    if data:
        path = config.icon_dir / f"st_{h}.{_ext(data)}"
        try:
            path.write_bytes(data)
            return str(path)
        except Exception:  # noqa: BLE001
            return None
    try:
        (config.icon_dir / f"st_{h}.miss").write_bytes(b"")   # remember the miss; don't re-derive
    except Exception:  # noqa: BLE001
        pass
    return None


def best_station_icon(config, row: dict, *, refetch: bool = False) -> str | None:
    """The best now-playing art-box icon for a station. Normal: a cached derived (broadcaster)
    icon if present > the directory favicon > derive one. `refetch=True` (Recache) clears every
    cached file for this station and re-fetches, preferring the big derived broadcaster logo."""
    surl = row.get("url", "") or ""
    fav = row.get("favicon", "") or ""
    hd = _hash("derived:" + surl) if surl else ""
    if refetch:
        for h in filter(None, (hd, _hash(fav) if fav else "")):
            for p in config.icon_dir.glob(f"st_{h}.*"):
                try:
                    p.unlink()
                except OSError:
                    pass
        return derived_station_favicon(config, row) or (station_favicon(config, fav) if fav else None)
    derived_missed = False
    if hd:
        for existing in config.icon_dir.glob(f"st_{hd}.*"):
            if existing.suffix == ".miss":
                derived_missed = True
            else:
                return str(existing)                  # a recached/cached broadcaster logo wins
    if fav:
        p = station_favicon(config, fav)
        if p:
            return p
    if hd and not derived_missed:
        return derived_station_favicon(config, row)
    return None


