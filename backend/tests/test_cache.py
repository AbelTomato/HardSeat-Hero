import time

import pytest

from app.services.cache import TtlCache


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
