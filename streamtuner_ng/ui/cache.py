"""Bounded session catalog snapshots; authoritative user lists are never stored here."""
from collections import OrderedDict
from collections.abc import MutableMapping
import time


def row_bytes(row):
    # Conservative accounting for Python dictionaries, references and Unicode strings.
    return 512 + sum(64 + len(value) * 4 for value in row.values() if isinstance(value, str))


class CatalogCache(MutableMapping):
    def __init__(self, max_rows=100_000, max_bytes=128 << 20, max_entries=24):
        self._entries = OrderedDict()
        self.max_rows, self.max_bytes, self.max_entries = max_rows, max_bytes, max_entries
        self.row_count = self.byte_count = 0

    def __getitem__(self, key):
        rows, stamp, cost = self._entries[key]
        self._entries.move_to_end(key)
        return rows

    def __setitem__(self, key, rows):
        self.put(key, rows)

    def put(self, key, rows, *, age=0, cost=None):
        rows = list(rows)
        cost = sum(map(row_bytes, rows)) if cost is None else cost
        if len(rows) > self.max_rows or cost > self.max_bytes:
            return False  # preserve the last good in-memory snapshot on rejection
        if key in self._entries:
            del self[key]
        self._entries[key] = (rows, time.monotonic() - max(0, age), cost)
        self.row_count += len(rows)
        self.byte_count += cost
        while (len(self._entries) > self.max_entries or self.row_count > self.max_rows
               or self.byte_count > self.max_bytes):
            del self[next(iter(self._entries))]
        return True

    def age(self, key):
        entry = self._entries.get(key)
        return max(0, time.monotonic() - entry[1]) if entry else float('inf')

    def timestamp(self, key):
        entry = self._entries.get(key)
        return entry[1] if entry else float("-inf")

    def mark_stale(self, source):
        for key, (rows, stamp, cost) in list(self._entries.items()):
            if key[0] == source:
                self._entries[key] = (rows, float('-inf'), cost)

    def __delitem__(self, key):
        rows, stamp, cost = self._entries.pop(key)
        self.row_count -= len(rows)
        self.byte_count -= cost

    def __iter__(self):
        return iter(tuple(self._entries))

    def __len__(self):
        return len(self._entries)
