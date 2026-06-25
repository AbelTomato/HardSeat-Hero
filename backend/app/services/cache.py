from datetime import datetime, timedelta, timezone
from typing import Generic, TypeVar


T = TypeVar("T")


class TtlCache(Generic[T]):
    def __init__(self, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than 0")
        self.ttl = timedelta(seconds=ttl_seconds)
        self._items: dict[str, tuple[datetime, T]] = {}

    def get(self, key: str) -> T | None:
        item = self._items.get(key)
        if item is None:
            return None

        expires_at, value = item
        if datetime.now(timezone.utc) >= expires_at:
            self._items.pop(key, None)
            return None

        return value

    def set(self, key: str, value: T) -> None:
        self._items[key] = (datetime.now(timezone.utc) + self.ttl, value)

    def clear(self) -> None:
        self._items.clear()
