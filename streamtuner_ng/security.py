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
# Plugin-declared extra table columns (Channel.extra_columns). Declared per channel, stored on a
# row under "extra", and bounded here so rows stay small and safe for caches, exports and backups.
EXTRA_TYPES = ('text', 'int', 'float', 'date')
MAX_EXTRA_COLUMNS = 8
MAX_EXTRA_TEXT = 256
RESERVED_ROW_KEYS = frozenset((
    'title', 'url', 'homepage', 'genre', 'playing', 'description', 'listeners', 'votes', 'country',
    'bitrate', 'format', 'favicon', 'listformat', 'favourite', 'deleted', 'extra'))
_EXTRA_KEY = re.compile(r'[a-z][a-z0-9_]{0,23}\Z')
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')
_IDENTIFIER = re.compile(r'[a-zA-Z0-9_][a-zA-Z0-9_-]{0,63}\Z')
_SECRET = re.compile(r'(?:password|passwd|secret|token|authorization|credential|listen.?key|api.?key)', re.I)


def valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value))


def is_secret_key(value: object) -> bool:
    return isinstance(value, str) and bool(_SECRET.search(value))


def valid_columns(value: object, problems: list | None = None) -> list[tuple[str, str, str]]:
    """Validate a channel's `extra_columns` declaration -> [(label, key, type)].

    Accepts `(label, key)`, `(label, key, type)` or `{"label","key","type"}` entries. Bad entries
    are skipped; `problems` (if given) receives a short reason for each."""
    def note(reason):
        if problems is not None:
            problems.append(reason)
    if not value:
        return []
    if not isinstance(value, (list, tuple)):
        note('extra_columns must be a list')
        return []
    out, seen = [], set()
    for item in value:
        if len(out) >= MAX_EXTRA_COLUMNS:
            note(f'more than {MAX_EXTRA_COLUMNS} extra columns; the rest are ignored')
            break
        if isinstance(item, dict):
            label, key, kind = item.get('label'), item.get('key'), item.get('type', 'text')
        elif isinstance(item, (list, tuple)) and 2 <= len(item) <= 3:
            label, key, kind = item[0], item[1], (item[2] if len(item) == 3 else 'text')
        else:
            note('column entry must be (label, key[, type])')
            continue
        label = _CONTROL.sub(' ', label).strip() if isinstance(label, str) else ''
        if not label or len(label) > 24:
            note('column label must be 1-24 characters')
        elif not isinstance(key, str) or not _EXTRA_KEY.fullmatch(key):
            note(f'column key {str(key)[:30]!r} must be lowercase letters, digits or _ (max 24)')
        elif key in RESERVED_ROW_KEYS:
            note(f"column key '{key}' is a built-in field name")
        elif is_secret_key(key):
            note(f"column key '{key}' looks like a credential name")
        elif key in seen:
            note(f"column key '{key}' is declared twice")
        elif kind not in EXTRA_TYPES:
            note(f"column type {str(kind)[:20]!r} must be one of {', '.join(EXTRA_TYPES)}")
        else:
            seen.add(key)
            out.append((label, key, kind))
    return out


def _coerce_extra(value: object, kind: str | None):
    if isinstance(value, bool):
        value = int(value)
    if kind == 'int':
        try:
            return max(-2**53, min(2**53, int(value)))
        except (TypeError, ValueError, OverflowError):
            return None
    if kind == 'float':
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if number == number and abs(number) != float('inf') else None
    if kind in ('text', 'date'):
        if not isinstance(value, (str, int, float)):
            return None
        return _CONTROL.sub(' ', str(value))[:32 if kind == 'date' else MAX_EXTRA_TEXT].strip()
    # Undeclared shape (stores, caches, backups): keep plain, bounded scalars only.
    if isinstance(value, str):
        return _CONTROL.sub(' ', value)[:MAX_EXTRA_TEXT].strip()
    if isinstance(value, int):
        return max(-2**53, min(2**53, value))
    if isinstance(value, float):
        return value if value == value and abs(value) != float('inf') else None
    return None


def clean_extra(value: object, columns: list | None = None) -> dict | None:
    """Bound a row's `extra` dict. With `columns` (a plugin's declared set) only declared keys
    survive, coerced to their type; without it (stores, caches, backups) any well-formed key does."""
    if not isinstance(value, dict) or not value:
        return None
    types = {key: kind for _label, key, kind in columns} if columns is not None else None
    out = {}
    for key, raw in value.items():
        if len(out) >= MAX_EXTRA_COLUMNS:
            break
        if (not isinstance(key, str) or not _EXTRA_KEY.fullmatch(key) or key in RESERVED_ROW_KEYS
                or is_secret_key(key) or (types is not None and key not in types)):
            continue
        cleaned = _coerce_extra(raw, types.get(key) if types is not None else None)
        if cleaned is not None and cleaned != '':
            out[key] = cleaned
    return out or None


def valid_url(value: object, *, token: bool = False, web: bool = False, local: bool = False) -> bool:
    """`local` admits file:// station URLs. It is granted only to a plugin that sets
    `local_files = True`; stores, imports and web links never pass it."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL:
        return False
    if any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value:
        return False
    try:
        url = urlsplit(value)
        if url.scheme == 'file':
            return (local and not web and url.netloc in ('', 'localhost')
                    and url.path.startswith('/') and not url.query and not url.fragment)
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


def station_row(value: object, *, local: bool = False, columns: list | None = None) -> dict | None:
    """Normalize data without executing plugin code or importing Qt."""
    if not isinstance(value, dict):
        return None
    url = value.get('url')
    if not isinstance(url, str):
        return None
    url = tokenized_url(url.strip())
    if not valid_url(url, token=True, local=local):
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
    extra = clean_extra(value.get('extra'), columns)
    if extra:
        out['extra'] = extra
    if valid_id(value.get('_source')):
        out['_source'] = value['_source']
    if url.startswith('audioaddict://'):
        out['_source'] = 'audioaddict'
    return out


def reject_reason(candidate: object) -> str:
    """Short, credential-free reason a plugin row was refused (for plugin authors)."""
    if not isinstance(candidate, dict):
        return 'row is not a dict'
    url = candidate.get('url')
    if not isinstance(url, str) or not url.strip():
        return 'missing url'
    try:
        scheme = urlsplit(url.strip()).scheme.lower()
    except ValueError:
        scheme = ''
    if scheme and scheme not in NETWORK_SCHEMES:
        return f"unsupported URL scheme '{scheme[:20]}'" + (
            " (set local_files = True on the plugin to allow file://)" if scheme == 'file' else '')
    return 'invalid url'


def station_rows(value: object, *, local: bool = False, rejected: list | None = None,
                 columns: list | None = None, ignored: set | None = None) -> list[dict]:
    """Normalize rows. `rejected`, if given, receives one reason string per refused row."""
    if not isinstance(value, list):
        return []
    out, seen = [], set()
    for candidate in value[:MAX_ROWS]:
        row = station_row(candidate, local=local, columns=columns)
        if (ignored is not None and columns is not None and isinstance(candidate, dict)
                and isinstance(candidate.get('extra'), dict)):
            declared = {key for _label, key, _kind in columns}
            ignored.update(str(k)[:24] for k in candidate['extra'] if k not in declared)
        if row is None and rejected is not None:
            rejected.append(reject_reason(candidate))
        if row and row['url'] not in seen:
            seen.add(row['url'])
            out.append(row)
    return out
