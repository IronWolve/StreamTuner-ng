"""Validation and redaction at the untrusted station/data boundaries (Qt-free)."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

NETWORK_SCHEMES = frozenset(('http', 'https', 'icy', 'mmsh'))
AUDIOADDICT_DOMAINS = frozenset(('di.fm', 'radiotunes.com', 'jazzradio.com',
                               'rockradio.com', 'classicalradio.com', 'zenradio.com'))
MAX_URL = 8192
MAX_ROWS = 100_000
MAX_IMPORT_BYTES = 32 << 20
# Runtime stores have their own compatible read/write limits. Imports stay
# smaller because they are parsed synchronously by existing UI entry points.
MAX_STORE_BYTES = 128 << 20
MAX_CACHE_BYTES = 64 << 20
MAX_CACHE_DISK_BYTES = 256 << 20
MAX_CACHE_FILES = 256
_IDENTIFIER = re.compile(r'[a-zA-Z0-9_][a-zA-Z0-9_-]{0,63}\Z')
_SECRET = re.compile(r'(?:password|passwd|secret|token|authorization|credential|listen.?key|api.?key)', re.I)


def valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value))


def is_secret_key(value: object) -> bool:
    return isinstance(value, str) and bool(_SECRET.search(value))


def valid_url(value: object, *, token: bool = False, web: bool = False) -> bool:
    if not isinstance(value, str) or not value or len(value) > MAX_URL:
        return False
    if any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value:
        return False
    try:
        url = urlsplit(value)
        if not url.hostname or url.username is not None or url.password is not None:
            return False
        if url.port is not None and not 1 <= url.port <= 65535:
            return False
        if token and url.scheme == 'audioaddict':
            return (url.hostname in AUDIOADDICT_DOMAINS and url.port is None
                    and bool(re.fullmatch(r'/[a-zA-Z0-9_-]+', url.path))
                    and not url.query and not url.fragment)
        return url.scheme in ({'http', 'https'} if web else NETWORK_SCHEMES)
    except (ValueError, UnicodeError):
        return False


def tokenized_url(value: str) -> str:
    """Remove legacy AudioAddict keys before persistence or display."""
    try:
        url = urlsplit(value)
        host = url.hostname or ''
        match = re.fullmatch(r'prem\d+\.(.+)', host)
        if match and match.group(1) in AUDIOADDICT_DOMAINS:
            channel = re.sub(r'_(?:hi|aac)\Z', '', url.path)
            result = 'audioaddict://' + match.group(1) + channel
            if valid_url(result, token=True):
                return result
    except (ValueError, UnicodeError):
        pass
    return value


def redact_url(value: object) -> str:
    if not isinstance(value, str):
        return ''
    value = tokenized_url(value)
    try:
        url = urlsplit(value)
        if not url.scheme or not url.netloc:
            return value
        host = url.netloc.rsplit('@', 1)[-1]
        # Query strings may contain positional keys, signed URLs or service-specific
        # secret names. Do not guess which query components are safe to publish.
        return urlunsplit((url.scheme, host, url.path, 'REDACTED' if url.query else '', ''))
    except (ValueError, UnicodeError):
        return '[invalid URL]'


def redact_text(value: object, secrets=()) -> str:
    text = str(value)
    for secret in secrets:
        if isinstance(secret, str) and secret:
            text = text.replace(secret, '[redacted]').replace(quote(secret, safe=''), '[redacted]')
    return re.sub(r'(?:https?|icy|mmsh|audioaddict)://[^\s<>"\x27]+',
                  lambda m: redact_url(m.group()), text, flags=re.I)


def export_url(value: str) -> str:
    value = tokenized_url(value)
    try:
        url = urlsplit(value)
        if not url.scheme or not url.netloc:
            return value
        if url.query and any("=" not in part for part in url.query.split("&")):
            return urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], url.path, "REDACTED", ""))
        query = []
        for key, val in parse_qsl(url.query, keep_blank_values=True):
            private = is_secret_key(key) or key.lower() in ("key", "auth", "sig", "signature", "jwt", "session", "ticket")
            query.append((key, "[redacted]" if private or not val else val))
        return urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], url.path, urlencode(query), ""))
    except (ValueError, UnicodeError):
        return "[invalid URL]"


def export_text(value: object, secrets=()) -> str:
    text = str(value)
    for secret in secrets:
        if isinstance(secret, str) and secret:
            text = text.replace(secret, "[redacted]").replace(quote(secret, safe=""), "[redacted]")
    return re.sub(r'(?:https?|icy|mmsh|audioaddict)://[^\s<>"\x27]+',
                  lambda m: export_url(m.group()), text, flags=re.I)


def csv_cell(value: object, secrets=()) -> str:
    text = export_text(value, secrets)
    suspicious = text.lstrip(' \t\r\n\ufeff\v\f\0').startswith(('=', '+', '-', '@'))
    return "'" + text if suspicious or text.startswith(('\t', '\r', '\n')) else text


def scrub_data(value, *, depth: int = 0, secrets=()):
    if depth > 20:
        return None
    if isinstance(value, dict):
        return {str(k): scrub_data(v, depth=depth + 1, secrets=secrets) for k, v in value.items() if not is_secret_key(k)}
    if isinstance(value, (list, tuple)):
        return [scrub_data(v, depth=depth + 1, secrets=secrets) for v in value[:MAX_ROWS]]
    if isinstance(value, str):
        return export_text(value, secrets)
    return value if value is None or isinstance(value, (bool, int, float)) else None


def backup_bundle(value: object) -> dict:
    if not isinstance(value, dict) or value.get("app") != "streamtuner-ng":
        raise ValueError("This is not a StreamTuner-ng backup")
    settings = value.get("settings", {})
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise ValueError("Backup settings must be an object")
    bookmarks, local = value.get("bookmarks", {}), value.get("local", {})
    bookmarks = {} if bookmarks is None else bookmarks
    local = {} if local is None else local
    if not isinstance(bookmarks, dict) or not isinstance(local, dict):
        raise ValueError("Backup station containers must be objects")
    output = {"settings": settings}
    for key, rows in (("favourite", bookmarks.get("favourite", [])),
                      ("history", bookmarks.get("history", [])),
                      ("stations", local.get("stations", []))):
        if not isinstance(rows, list) or len(rows) > MAX_ROWS:
            raise ValueError("Backup station list is invalid or too large")
        output[key] = station_rows(rows)
    return output


def station_row(value: object) -> dict | None:
    """Normalize data without executing plugin code or importing Qt."""
    if not isinstance(value, dict):
        return None
    url = value.get('url')
    if not isinstance(url, str):
        return None
    url = tokenized_url(url.strip())
    if not valid_url(url, token=True):
        return None
    out = {'url': url}
    for key in ('title', 'genre', 'country', 'format', 'description', 'playing', 'listformat'):
        v = value.get(key, '')
        out[key] = v[:4096] if isinstance(v, str) else ''
    out['title'] = out['title'].strip() or redact_url(url)
    for key in ('homepage', 'favicon'):
        v = value.get(key, '')
        out[key] = v if valid_url(v, web=True) else ''
    for key in ('bitrate', 'votes', 'listeners'):
        try:
            out[key] = max(0, min(2**31 - 1, int(value.get(key, 0))))
        except (TypeError, ValueError, OverflowError):
            out[key] = 0
    if valid_id(value.get('_source')):
        out['_source'] = value['_source']
    if url.startswith('audioaddict://'):
        out['_source'] = 'audioaddict'
    return out


def station_rows(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    out, seen = [], set()
    for candidate in value[:MAX_ROWS]:
        row = station_row(candidate)
        if row and row['url'] not in seen:
            seen.add(row['url'])
            out.append(row)
    return out
