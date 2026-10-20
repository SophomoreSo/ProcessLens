"""In-process LRU cache for derived analytics.

Keys always start with ``(dataset_id, dataset_version, settings_revision)``
followed by the analysis name and a normalized filter key, so a new import or
a settings change can never serve stale results. ``invalidate_dataset`` also
evicts entries eagerly to free memory.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any

from app.config import get_settings


class LRUCache:
    def __init__(self, max_entries: int):
        self.max_entries = max_entries
        self._data: OrderedDict[Hashable, Any] = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def get_or_compute(self, key: Hashable, fn: Callable[[], Any]) -> Any:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self.hits += 1
                return self._data[key]
            self.misses += 1
        value = fn()  # computed outside the lock; a duplicate computation is harmless
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)
        return value

    def invalidate_dataset(self, dataset_id: int) -> int:
        with self._lock:
            doomed = [k for k in self._data if isinstance(k, tuple) and k and k[0] == dataset_id]
            for k in doomed:
                del self._data[k]
            return len(doomed)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.hits = self.misses = 0

    def __len__(self) -> int:
        return len(self._data)


cache = LRUCache(get_settings().cache_max_entries)
