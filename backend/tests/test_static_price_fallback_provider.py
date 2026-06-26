from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from app.adapters.base import TrainDataProvider, TrainDataProviderError
from app.adapters.static_price_fallback_provider import StaticPriceFallbackProvider
from app.adapters.static_price_provider import LocalStaticPriceProvider
from app.domain.models import RouteQuery, SeatPrice, TrainSegment
from app.services.static_price_repository import SQLiteStaticPriceRepository


class FakeFallbackProvider(TrainDataProvider):
    name = "fake-remote"

    def __init__(self, segments: list[TrainSegment] | None = None, error: Exception | None = None) -> None:
        self.segments = segments or []
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def search_segments(self, from_station: str, to_station: str, query: RouteQuery) -> list[TrainSegment]:
        self.calls.append((from_station, to_station))
        if self.error is not None:
            raise self.error
        return self.segments

    def candidate_transfer_stations(self, query: RouteQuery) -> list[str]:
        return ["南京南"]


def make_segment(train_no: str, price: str, *, source: str = "fixture") -> TrainSegment:
    depart_at = datetime.combine(date(2026, 7, 1), time(8, 0), tzinfo=timezone.utc)
    arrive_at = datetime.combine(date(2026, 7, 1), time(10, 0), tzinfo=timezone.utc)
    return TrainSegment(
        train_no=train_no,
        from_station="北京",
        to_station="上海",
        depart_at=depart_at,
        arrive_at=arrive_at,
        duration_minutes=120,
        prices=[SeatPrice(seat_type="二等座", price=Decimal(price))],
        source=source,
        updated_at=datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc),
    )


def make_provider(
    repository: SQLiteStaticPriceRepository,
    fallback: TrainDataProvider,
    *,
    max_age: timedelta | None = None,
) -> StaticPriceFallbackProvider:
    local = LocalStaticPriceProvider(repository=repository, max_age=max_age)
    return StaticPriceFallbackProvider(local_provider=local, fallback_provider=fallback, repository=repository)


@pytest.mark.asyncio
async def test_static_price_fallback_provider_returns_local_hit_without_remote_call(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    repository.upsert_segments("static-price", date(2026, 7, 1), "北京", "上海", [make_segment("D1", "309.0")])
    fallback = FakeFallbackProvider([make_segment("G1", "553.0", source="remote")])
    provider = make_provider(repository, fallback)

    segments = await provider.search_segments("北京", "上海", RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1)))

    assert [segment.train_no for segment in segments] == ["D1"]
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_static_price_fallback_provider_fetches_remote_and_writes_static_cache(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    fallback = FakeFallbackProvider([make_segment("G1", "553.0", source="remote")])
    provider = make_provider(repository, fallback)

    segments = await provider.search_segments("北京", "上海", RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1)))

    assert [segment.train_no for segment in segments] == ["G1"]
    assert fallback.calls == [("北京", "上海")]
    persisted = repository.query_segments("static-price", date(2026, 7, 1), "北京", "上海")
    assert [segment.train_no for segment in persisted] == ["G1"]
    assert persisted[0].lowest_price == Decimal("553.0")


@pytest.mark.asyncio
async def test_static_price_fallback_provider_refreshes_stale_local_data(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    repository.upsert_segments(
        "static-price",
        date(2026, 7, 1),
        "北京",
        "上海",
        [make_segment("D1", "309.0")],
        fetched_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    fallback = FakeFallbackProvider([make_segment("G1", "553.0", source="remote")])
    provider = make_provider(repository, fallback, max_age=timedelta(days=1))

    segments = await provider.search_segments("北京", "上海", RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1)))

    assert [segment.train_no for segment in segments] == ["G1"]
    assert fallback.calls == [("北京", "上海")]
    persisted = repository.query_segments("static-price", date(2026, 7, 1), "北京", "上海")
    assert {segment.train_no for segment in persisted} == {"D1", "G1"}


@pytest.mark.asyncio
async def test_static_price_fallback_provider_does_not_write_empty_remote_result(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    fallback = FakeFallbackProvider([])
    provider = make_provider(repository, fallback)

    segments = await provider.search_segments("北京", "上海", RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1)))

    assert segments == []
    assert repository.query_segments("static-price", date(2026, 7, 1), "北京", "上海") == []


@pytest.mark.asyncio
async def test_static_price_fallback_provider_propagates_remote_error(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    fallback = FakeFallbackProvider(error=TrainDataProviderError("remote failed"))
    provider = make_provider(repository, fallback)

    with pytest.raises(TrainDataProviderError, match="remote failed"):
        await provider.search_segments("北京", "上海", RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1)))


def test_static_price_fallback_provider_delegates_transfer_candidates(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    fallback = FakeFallbackProvider([])
    provider = make_provider(repository, fallback)

    assert provider.candidate_transfer_stations(RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1))) == ["南京南"]