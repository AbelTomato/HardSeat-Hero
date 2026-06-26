from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.adapters.static_price_provider import LocalStaticPriceProvider
from app.domain.models import RouteQuery, SeatPrice, StationMetadata, TrainSegment
from app.services.static_price_repository import SQLiteStaticPriceRepository
from app.services.transfer_candidates import CandidateTransferStationGenerator, StationMetadataRepository


def make_segment(train_no: str, price: str, *, fetched_at: datetime | None = None) -> TrainSegment:
    depart_at = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)
    arrive_at = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
    return TrainSegment(
        train_no=train_no,
        from_station="北京",
        to_station="上海",
        depart_at=depart_at,
        arrive_at=arrive_at,
        duration_minutes=120,
        prices=[SeatPrice(seat_type="二等座", price=Decimal(price))],
        source="fixture",
        updated_at=fetched_at or datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_static_price_provider_returns_segments_from_repository(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    repository.upsert_segments(
        "static-price",
        date(2026, 7, 1),
        "北京",
        "上海",
        [make_segment("D1", "309.0")],
    )
    provider = LocalStaticPriceProvider(repository)

    segments = await provider.search_segments("北京", "上海", RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1)))

    assert provider.name == "static-price"
    assert len(segments) == 1
    assert segments[0].train_no == "D1"
    assert segments[0].lowest_price == Decimal("309.0")


@pytest.mark.asyncio
async def test_static_price_provider_uses_static_price_provider_namespace(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    repository.upsert_segments(
        "other-provider",
        date(2026, 7, 1),
        "北京",
        "上海",
        [make_segment("G1", "553.0")],
    )
    provider = LocalStaticPriceProvider(repository)

    segments = await provider.search_segments("北京", "上海", RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1)))

    assert segments == []


@pytest.mark.asyncio
async def test_static_price_provider_filters_stale_segments(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    repository.upsert_segments(
        "static-price",
        date(2026, 7, 1),
        "北京",
        "上海",
        [make_segment("G1", "553.0")],
        fetched_at=datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc),
    )
    provider = LocalStaticPriceProvider(repository, max_age=timedelta(days=7))

    segments = await provider.search_segments("北京", "上海", RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1)))

    assert segments == []


def test_static_price_provider_requires_repository_or_db_path() -> None:
    with pytest.raises(ValueError):
        LocalStaticPriceProvider()


def test_static_price_provider_can_initialize_repository_from_db_path(tmp_path) -> None:
    provider = LocalStaticPriceProvider(db_path=tmp_path / "static_prices.sqlite3")

    assert provider.name == "static-price"


def test_static_price_provider_delegates_transfer_candidates() -> None:
    repository = SQLiteStaticPriceRepository(":memory:")
    transfer_generator = CandidateTransferStationGenerator(
        repository=StationMetadataRepository(
            [
                StationMetadata(name="北京", telecode="BJP", latitude=39.9, longitude=116.4),
                StationMetadata(name="上海", telecode="SHH", latitude=31.2, longitude=121.5),
                StationMetadata(name="南京", telecode="NJH", latitude=32.0, longitude=118.8, centrality_score=20),
            ]
        )
    )
    provider = LocalStaticPriceProvider(repository, transfer_generator=transfer_generator)

    candidates = provider.candidate_transfer_stations(RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1)))

    assert "南京" in candidates