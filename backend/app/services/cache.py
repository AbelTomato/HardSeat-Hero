from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Generic, TypeVar


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


class SqliteOdCache(Generic[T]):
    def __init__(
        self,
        db_path: str | Path,
        ttl_seconds: int,
        serializer: Callable[[T], str] | None = None,
        deserializer: Callable[[str], T] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than 0")
        self.db_path = Path(db_path)
        self.ttl = timedelta(seconds=ttl_seconds)
        self.serializer = serializer or (lambda value: json.dumps(value, ensure_ascii=False))
        self.deserializer = deserializer or json.loads
        self._ensure_schema()

    def get(self, key: str) -> T | None:
        now = datetime.now(timezone.utc)
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT expires_at, response_json FROM query_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            expires_at = datetime.fromisoformat(row[0])
            if now >= expires_at:
                connection.execute("DELETE FROM query_cache WHERE cache_key = ?", (key,))
                return None
            try:
                return self.deserializer(row[1])
            except Exception:
                connection.execute("DELETE FROM query_cache WHERE cache_key = ?", (key,))
                return None

    def set(self, key: str, value: T) -> None:
        now = datetime.now(timezone.utc)
        expires_at = now + self.ttl
        provider, travel_date, origin, destination = self._split_key(key)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO query_cache(
                    cache_key, provider, travel_date, origin, destination,
                    fetched_at, expires_at, response_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    provider,
                    travel_date,
                    origin,
                    destination,
                    now.isoformat(),
                    expires_at.isoformat(),
                    self.serializer(value),
                ),
            )

    def clear(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("DELETE FROM query_cache")

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS query_cache(
                    cache_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    travel_date TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    response_json TEXT NOT NULL
                )
                """
            )

    def _split_key(self, key: str) -> tuple[str, str, str, str]:
        parts = key.split(":", 3)
        if len(parts) != 4:
            raise ValueError("cache key must be provider:date:from_station:to_station")
        return parts[0], parts[1], parts[2], parts[3]
