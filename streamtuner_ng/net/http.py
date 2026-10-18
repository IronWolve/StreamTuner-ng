"""Bounded, thread-local HTTP for public directories and automatic artwork."""
from __future__ import annotations

import ipaddress
import json
import queue
import socket
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from functools import wraps
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
MAX_THREAD_SESSIONS = 32
MAX_REDIRECTS = 5
MAX_RESOLVER_WORKERS = 4
MAX_RESOLVER_PENDING = 32
DNS_CACHE_SIZE = 256
DNS_CACHE_TTL = 30.0


class RequestCancelled(requests.RequestException):
    """Raised when a superseded provider request observes cooperative cancel."""


class RequestDeadlineExceeded(requests.Timeout):
    """Raised when a provider call's shared wall-clock deadline expires."""


_context_local = threading.local()


def _current_context():
    return getattr(_context_local, "request", None), getattr(_context_local, "session_key", None)


@contextmanager
def request_context(deadline: float | None, cancel_event=None):
    """Apply a shared provider-call deadline and cancellation event in this thread."""
    previous, _key = _current_context()
    if previous is not None and previous[0] is not None:
        deadline = min(deadline, previous[0]) if deadline is not None else previous[0]
    if previous is not None and previous[1] is not None:
        cancel_event = previous[1] if cancel_event is None else (previous[1], cancel_event)
    _context_local.request = (deadline, cancel_event)
    try:
        yield
    finally:
        _context_local.request = previous


@contextmanager
def session_scope(key):
    """Keep cookies and connection pools isolated by provider identity."""
    _previous = getattr(_context_local, "session_key", None)
    _context_local.session_key = key
    try:
        yield
    finally:
        _context_local.session_key = _previous


def bind_request_context(fn):
    """Capture the current request/session context for a provider fanout task."""
    policy, key = _current_context()

    @wraps(fn)
    def wrapped(*args, **kwargs):
        old_policy, old_key = _current_context()
        _context_local.request = policy
        _context_local.session_key = key
        try:
            return fn(*args, **kwargs)
        finally:
            _context_local.request = old_policy
            _context_local.session_key = old_key

    return wrapped


def map_with_context(fn, values, max_workers: int):
    """Bounded ordered fanout on persistent, provider-scoped daemon workers."""
    items = list(values)
    if not items:
        return []
    count = max(1, min(int(max_workers), 32, len(items)))
    results = queue.Queue()
    fanout_cancel = threading.Event()
    policy, key = _current_context()
    deadline, external_cancel = policy if policy is not None else (None, None)
    output = [None] * len(items)
    with request_context(deadline, (external_cancel, fanout_cancel)):
        bound = bind_request_context(fn)
    scope = key if key is not None else ("fanout", threading.get_ident())
    _fanout_pool.submit(scope, bound, items, count, results, fanout_cancel)
    remaining = len(items)
    try:
        while remaining:
            check_request_context()
            try:
                index, result = results.get(timeout=0.05)
            except queue.Empty:
                continue
            if isinstance(result, BaseException):
                fanout_cancel.set()
                raise result
            output[index] = result
            remaining -= 1
        return output
    except BaseException:
        fanout_cancel.set()
        raise


class _ContextFanoutPool:
    """Persistent bounded daemon lanes preserve per-provider HTTP pools across calls."""

    def __init__(self, workers: int = 32, max_pending: int = 2048):
        self.limit = workers
        self.max_pending = max_pending
        self.lock = threading.Lock()
        self.queues: list[queue.Queue] = []
        self.threads: list[threading.Thread] = []
        self.loads: list[int] = []
        self.scopes: dict[object, list[int]] = {}
        self.pending = 0

    def submit(self, scope, fn, items, workers, results, cancel_event) -> None:
        with self.lock:
            if self.pending + len(items) > self.max_pending:
                raise RuntimeError("Provider fanout queue is full")
            lanes = self.scopes.setdefault(scope, [])
            desired = min(workers, self.limit)
            while len(lanes) < desired and len(self.threads) < self.limit:
                lanes.append(self._new_lane())
            if not lanes:
                lanes.append(min(range(len(self.threads)), key=lambda lane: self.loads[lane]))
            for index, value in enumerate(items):
                lane = lanes[index % len(lanes)]
                self.queues[lane].put_nowait((fn, value, index, results, cancel_event))
                self.loads[lane] += 1
                self.pending += 1

    def _new_lane(self) -> int:
        lane = len(self.threads)
        jobs = queue.Queue(maxsize=self.max_pending)
        self.queues.append(jobs)
        self.loads.append(0)
        thread = threading.Thread(target=self._run, args=(lane,), daemon=True,
                                  name=f"provider-fanout-{lane + 1}")
        self.threads.append(thread)
        thread.start()
        return lane

    def _run(self, lane: int) -> None:
        jobs = self.queues[lane]
        while True:
            fn, value, index, results, cancel_event = jobs.get()
            try:
                if cancel_event.is_set():
                    result = RequestCancelled("Provider fanout cancelled")
                else:
                    try:
                        result = fn(value)
                    except BaseException as exc:
                        result = exc
                results.put((index, result))
            finally:
                with self.lock:
                    self.loads[lane] -= 1
                    self.pending -= 1
                jobs.task_done()


_fanout_pool = _ContextFanoutPool()


def check_request_context() -> None:
    policy, _key = _current_context()
    if policy is None:
        return
    deadline, cancel = policy
    events = []
    pending = [cancel]
    while pending:
        event = pending.pop()
        if isinstance(event, tuple):
            pending.extend(event)
        elif event is not None:
            events.append(event)
    if any(event is not None and event.is_set() for event in events):
        raise RequestCancelled("Provider request cancelled")
    if deadline is not None and time.monotonic() >= deadline:
        raise RequestDeadlineExceeded("Provider request deadline exceeded")


def _bounded_timeout(timeout):
    check_request_context()
    policy, _key = _current_context()
    if policy is None or policy[0] is None:
        return timeout
    remaining = max(0.001, policy[0] - time.monotonic())
    if timeout is None:
        return remaining
    if isinstance(timeout, tuple):
        return tuple(remaining if value is None else min(float(value), remaining) for value in timeout)
    return min(float(timeout), remaining)


class _ResolveJob:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.done = threading.Event()
        self.result: list[str] | None = None
        self.error: BaseException | None = None


class _PublicResolver:
    """Bounded daemon DNS workers; callers retain their own deadline/cancel."""

    def __init__(self, workers: int = MAX_RESOLVER_WORKERS,
                 max_pending: int = MAX_RESOLVER_PENDING):
        self.jobs: queue.Queue[_ResolveJob] = queue.Queue(maxsize=max_pending)
        self.lock = threading.Lock()
        self.inflight: dict[tuple[str, int], _ResolveJob] = {}
        self.cache: OrderedDict[tuple[str, int], tuple[float, list[str]]] = OrderedDict()
        self.threads = []
        for index in range(workers):
            thread = threading.Thread(target=self._run, daemon=True,
                                      name=f"public-dns-{index + 1}")
            self.threads.append(thread)
            thread.start()

    @staticmethod
    def _resolve(host: str, port: int) -> list[str]:
        addresses = list(dict.fromkeys(
            item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ))
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise OSError('Automatic directory/artwork requests require a public network address')
        return addresses

    def _run(self):
        while True:
            job = self.jobs.get()
            key = (job.host, job.port)
            try:
                addresses = self._resolve(*key)
                job.result = addresses
                with self.lock:
                    self.cache[key] = (time.monotonic() + DNS_CACHE_TTL, addresses)
                    self.cache.move_to_end(key)
                    while len(self.cache) > DNS_CACHE_SIZE:
                        self.cache.popitem(last=False)
            except BaseException as exc:
                job.error = exc
            finally:
                with self.lock:
                    if self.inflight.get(key) is job:
                        del self.inflight[key]
                job.done.set()
                self.jobs.task_done()

    def addresses(self, host: str, port: int) -> list[str]:
        check_request_context()
        policy, _key = _current_context()
        deadline = policy[0] if policy is not None else None
        if deadline is None:
            deadline = time.monotonic() + DEFAULT_TIMEOUT
        key = (host, port)
        now = time.monotonic()
        with self.lock:
            cached = self.cache.get(key)
            if cached is not None:
                expires, addresses = cached
                if expires > now:
                    self.cache.move_to_end(key)
                    return list(addresses)
                del self.cache[key]
            job = self.inflight.get(key)
            if job is None:
                job = _ResolveJob(host, port)
                self.inflight[key] = job
                try:
                    self.jobs.put_nowait(job)
                except queue.Full:
                    del self.inflight[key]
                    raise requests.Timeout("Public DNS resolver queue is full")
        while not job.done.is_set():
            check_request_context()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RequestDeadlineExceeded("Public DNS resolution exceeded request deadline")
            job.done.wait(min(0.05, remaining))
        check_request_context()
        if job.error is not None:
            if isinstance(job.error, (KeyboardInterrupt, SystemExit)):
                raise OSError("Public DNS resolution failed") from job.error
            raise job.error
        return list(job.result or ())


_public_resolver = _PublicResolver()


def public_addresses(host: str, port: int) -> list[str]:
    """Resolve once and connect to checked IPs, preventing DNS rebinding."""
    return _public_resolver.addresses(host, port)


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
        if kwargs['timeout'] is None:
            kwargs['timeout'] = DEFAULT_TIMEOUT
        kwargs['stream'] = True
        if kwargs.get('proxies') or kwargs.get('auth') or kwargs.get('verify') is False:
            raise ValueError('Automatic fetches do not accept proxy/auth/TLS overrides')
        for attempt in range(MAX_REDIRECTS + 1):
            check_request_context()
            if not valid_url(url, web=True):
                raise ValueError('Invalid HTTP URL')
            try:
                request_kwargs = dict(kwargs)
                request_kwargs['timeout'] = _bounded_timeout(request_kwargs['timeout'])
                response = super().request(method, url, allow_redirects=False, **request_kwargs)
            except (RequestCancelled, RequestDeadlineExceeded):
                raise
            except (requests.RequestException, OSError) as exc:
                raise requests.RequestException(f'{type(exc).__name__} fetching {redact_url(url)}') from None
            if not follow or not response.is_redirect:
                return response
            target = urljoin(response.url, response.headers.get('Location', ''))
            status = response.status_code
            response.close()
            if attempt == MAX_REDIRECTS:
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

    def current_session(self):
        """Return this thread's provider-scoped Session (also useful to adapters)."""
        sessions = getattr(self.local, 'sessions', None)
        if sessions is None:
            sessions = self.local.sessions = OrderedDict()
        key = getattr(_context_local, 'session_key', None)
        if key is None:
            key = ('thread', threading.get_ident())
        if key not in sessions:
            sessions[key] = _Session()
        sessions.move_to_end(key)
        while len(sessions) > MAX_THREAD_SESSIONS:
            _old_key, old_session = sessions.popitem(last=False)
            old_session.close()
        return sessions[key]

    def __getattr__(self, name):
        return getattr(self.current_session(), name)


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
            check_request_context()
            total += len(chunk)
            if total > max_bytes:
                raise ValueError('Response exceeds the size limit')
            elapsed_limit = max(10, timeout * 2)
            policy, _key = _current_context()
            if policy is not None and policy[0] is not None:
                elapsed_limit = min(elapsed_limit, max(0, policy[0] - started))
            if time.monotonic() - started > elapsed_limit:
                raise requests.Timeout('Response exceeded the download time limit')
            chunks.append(chunk)
        encoding = response.encoding if 'charset=' in response.headers.get('Content-Type', '').lower() else None
        return b''.join(chunks), encoding
    except (RequestCancelled, RequestDeadlineExceeded):
        raise
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
