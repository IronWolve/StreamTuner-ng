"""Bounded, thread-local HTTP for public directories and automatic artwork."""
from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.util.connection import create_connection

from .. import APP_NAME, __version__
from ..security import redact_url, valid_url

USER_AGENT = f'{APP_NAME}/{__version__} (+https://github.com/IronWolve/StreamTuner-ng)'
BROWSER_UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36'
DEFAULT_TIMEOUT = 8.0
MAX_BYTES = 32 << 20
MAX_TEXT_BYTES = 4 << 20


def public_addresses(host: str, port: int) -> list[str]:
    """Resolve once and connect to the checked IP, preventing DNS rebinding."""
    addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise OSError('Automatic directory/artwork requests require a public network address')
    return addresses


class _PublicConnection:
    def _new_conn(self):
        last = None
        for address in public_addresses(self.host, self.port):
            try:
                return create_connection((address, self.port), self.timeout,
                                         self.source_address, self.socket_options)
            except OSError as exc:
                last = exc
        raise OSError('Unable to connect to the public host') from last


class _HTTP(_PublicConnection, HTTPConnection):
    pass


class _HTTPS(_PublicConnection, HTTPSConnection):
    pass


class _HTTPPool(HTTPConnectionPool):
    ConnectionCls = _HTTP


class _HTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _HTTPS


class _PublicAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {'http': _HTTPPool, 'https': _HTTPSPool}


class _Session(requests.Session):
    def __init__(self):
        super().__init__()
        # Do not attach personal .netrc or ambient proxy credentials to directory URLs.
        self.trust_env = False
        self.headers.update({'User-Agent': USER_AGENT, 'Accept': '*/*', 'Accept-Encoding': 'gzip, deflate'})
        self.mount('http://', _PublicAdapter())
        self.mount('https://', _PublicAdapter())

    def request(self, method, url, **kwargs):
        follow = kwargs.pop('allow_redirects', True)
        kwargs.setdefault('timeout', DEFAULT_TIMEOUT)
        kwargs['stream'] = True
        if kwargs.get('proxies') or kwargs.get('auth') or kwargs.get('verify') is False:
            raise ValueError('Automatic fetches do not accept proxy/auth/TLS overrides')
        for attempt in range(6):
            if not valid_url(url, web=True):
                raise ValueError('Invalid HTTP URL')
            try:
                response = super().request(method, url, allow_redirects=False, **kwargs)
            except (requests.RequestException, OSError) as exc:
                raise requests.RequestException(f'{type(exc).__name__} fetching {redact_url(url)}') from None
            if not follow or not response.is_redirect:
                return response
            target = urljoin(response.url, response.headers.get('Location', ''))
            status = response.status_code
            response.close()
            if attempt == 5:
                raise requests.TooManyRedirects('Too many redirects')
            if urlsplit(url).scheme == 'https' and urlsplit(target).scheme != 'https':
                raise ValueError('HTTPS redirect downgrade blocked')
            if urlsplit(url).netloc != urlsplit(target).netloc:
                kwargs['headers'] = {k: v for k, v in (kwargs.get('headers') or {}).items()
                                     if k.lower() not in ('authorization', 'cookie', 'proxy-authorization')}
            kwargs.pop('params', None)
            if status == 303 or (status in (301, 302) and method.upper() == 'POST'):
                method = 'GET'
                kwargs.pop('data', None)
                kwargs.pop('json', None)
            url = target


class _ThreadSession:
    def __init__(self):
        self.local = threading.local()

    def __getattr__(self, name):
        if not hasattr(self.local, 'session'):
            self.local.session = _Session()
        return getattr(self.local.session, name)


session = _ThreadSession()


def _read(method, url, *, timeout=DEFAULT_TIMEOUT, max_bytes=MAX_BYTES, **kwargs):
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError('A positive response-size limit is required')
    started = time.monotonic()
    response = session.request(method, url, timeout=timeout, **kwargs)
    try:
        response.raise_for_status()
        declared = response.headers.get('Content-Length', '')
        if declared.isdigit() and int(declared) > max_bytes:
            raise ValueError('Response exceeds the size limit')
        chunks, total = [], 0
        for chunk in response.iter_content(8192):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError('Response exceeds the size limit')
            if time.monotonic() - started > max(10, timeout * 2):
                raise requests.Timeout('Response exceeded the download time limit')
            chunks.append(chunk)
        encoding = response.encoding if 'charset=' in response.headers.get('Content-Type', '').lower() else None
        return b''.join(chunks), encoding
    except requests.RequestException as exc:
        raise requests.RequestException(f'{type(exc).__name__} fetching {redact_url(url)}') from None
    finally:
        response.close()


def get_json(url, params=None, timeout=DEFAULT_TIMEOUT):
    data, _encoding = _read('GET', url, params=params or {}, timeout=timeout)
    return json.loads(data)


def get_text(url, params=None, headers=None, timeout=DEFAULT_TIMEOUT, max_bytes=MAX_TEXT_BYTES):
    data, encoding = _read('GET', url, params=params or {}, headers=headers or {}, timeout=timeout, max_bytes=max_bytes)
    if not encoding:
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            from charset_normalizer import from_bytes
            detected = from_bytes(data).best()
            return str(detected) if detected is not None else data.decode('utf-8', errors='replace')
    try:
        return data.decode(encoding, errors='replace')
    except LookupError:
        return data.decode('utf-8', errors='replace')


def get_bytes(url, timeout=DEFAULT_TIMEOUT, max_bytes=MAX_BYTES):
    return _read('GET', url, timeout=timeout, max_bytes=max_bytes)[0]


def post_json(url, data=None, headers=None, timeout=DEFAULT_TIMEOUT):
    body, _encoding = _read('POST', url, data=data or {}, headers=headers or {}, timeout=timeout)
    return json.loads(body)
