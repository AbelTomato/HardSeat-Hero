import time

import pytest

from app.services.cache import SqliteOdCache, TtlCache


def test_ttl_cache_returns_stored_value_before_expiry() -> None:
    cache = TtlCache[list[str]](ttl_seconds=60)

    cache.set("stations", ["北京", "上海"])

    assert cache.get("stations") == ["北京", "上海"]


def test_ttl_cache_expires_value() -> None:
    cache = TtlCache[str](ttl_seconds=1)

    cache.set("key", "value")
    time.sleep(1.1)

    assert cache.get("key") is None


def test_ttl_cache_rejects_invalid_ttl() -> None:
    with pytest.raises(ValueError):
        TtlCache[str](ttl_seconds=0)


def test_sqlite_od_cache_returns_stored_value(tmp_path) -> None:
    cache = SqliteOdCache[list[dict[str, str]]](tmp_path / "od.sqlite", ttl_seconds=60)

    cache.set("provider:2026-07-01:北京:上海", [{"train_no": "G1"}])

    assert cache.get("provider:2026-07-01:北京:上海") == [{"train_no": "G1"}]


def test_sqlite_od_cache_expires_value(tmp_path) -> None:
    cache = SqliteOdCache[str](tmp_path / "od.sqlite", ttl_seconds=1)

    cache.set("provider:2026-07-01:北京:上海", "value")
    time.sleep(1.1)

    assert cache.get("provider:2026-07-01:北京:上海") is None


def test_sqlite_od_cache_drops_corrupted_value(tmp_path) -> None:
    cache = SqliteOdCache[list[str]](tmp_path / "od.sqlite", ttl_seconds=60)
    cache.set("provider:2026-07-01:北京:上海", ["G1"])
    with __import__("sqlite3").connect(tmp_path / "od.sqlite") as connection:
        connection.execute(
            "UPDATE query_cache SET response_json = ? WHERE cache_key = ?",
            ("not-json", "provider:2026-07-01:北京:上海"),
        )

    assert cache.get("provider:2026-07-01:北京:上海") is None
