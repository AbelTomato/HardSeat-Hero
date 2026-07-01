from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.domain.models import RouteQuery
from app.services.static_price_repository import SQLiteStaticPriceRepository
from app.services.time_expanded_route_search import TimeExpandedRouteSearchEngine


def insert_od_row(
    repository: SQLiteStaticPriceRepository,
    *,
    service_date: date,
    train_no: str,
    from_station: str,
    to_station: str,
    depart_at: datetime,
    arrive_at: datetime,
    price: str,
    seat_type: str = "二等座",
) -> None:
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute(
            """
            INSERT INTO train_od_price_snapshot(
                provider, service_date, train_no, train_code,
                from_station, to_station, from_station_no, to_station_no,
                depart_at, arrive_at, duration_minutes, seat_type,
                price, currency, source, fetched_at, raw_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CNY', ?, ?, NULL)
            """,
            (
                "12306-public-price",
                service_date.isoformat(),
                train_no,
                train_no,
                from_station,
                to_station,
                1,
                2,
                depart_at.isoformat(),
                arrive_at.isoformat(),
                int((arrive_at - depart_at).total_seconds() // 60),
                seat_type,
                price,
                "fixture",
                datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc).isoformat(),
            ),
        )


@pytest.mark.asyncio
async def test_time_expanded_search_returns_direct_plan(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    insert_od_row(
        repository,
        service_date=date(2026, 7, 1),
        train_no="D1",
        from_station="北京",
        to_station="上海",
        depart_at=datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc),
        arrive_at=datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc),
        price="309.0",
    )

    engine = TimeExpandedRouteSearchEngine(repository)
    response = await engine.search(RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1), max_transfers=0))

    assert response.source == "time-expanded"
    assert len(response.plans) == 1
    assert response.plans[0].total_price == Decimal("309.0")
    assert len(response.plans[0].segments) == 1
    assert response.plans[0].segments[0].train_no == "D1"


@pytest.mark.asyncio
async def test_time_expanded_search_returns_one_transfer_plan(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    insert_od_row(
        repository,
        service_date=date(2026, 7, 1),
        train_no="D1",
        from_station="北京",
        to_station="中转站",
        depart_at=datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc),
        arrive_at=datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
        price="50",
    )
    insert_od_row(
        repository,
        service_date=date(2026, 7, 1),
        train_no="D2",
        from_station="中转站",
        to_station="上海",
        depart_at=datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc),
        arrive_at=datetime(2026, 7, 1, 11, 0, tzinfo=timezone.utc),
        price="60",
    )

    engine = TimeExpandedRouteSearchEngine(repository)
    response = await engine.search(
        RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1), max_transfers=1, min_transfer_minutes=30)
    )

    assert len(response.plans) == 1
    plan = response.plans[0]
    assert plan.total_price == Decimal("110")
    assert plan.transfer_stations == ["中转站"]
    assert plan.transfer_minutes == 60
    assert [segment.train_no for segment in plan.segments] == ["D1", "D2"]


@pytest.mark.asyncio
async def test_time_expanded_search_rejects_insufficient_transfer_time(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    insert_od_row(
        repository,
        service_date=date(2026, 7, 1),
        train_no="D1",
        from_station="北京",
        to_station="中转站",
        depart_at=datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc),
        arrive_at=datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
        price="50",
    )
    insert_od_row(
        repository,
        service_date=date(2026, 7, 1),
        train_no="D2",
        from_station="中转站",
        to_station="上海",
        depart_at=datetime(2026, 7, 1, 9, 15, tzinfo=timezone.utc),
        arrive_at=datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc),
        price="60",
    )

    engine = TimeExpandedRouteSearchEngine(repository)
    response = await engine.search(
        RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1), max_transfers=1, min_transfer_minutes=30)
    )

    assert response.plans == []


@pytest.mark.asyncio
async def test_time_expanded_search_does_not_require_provider_argument(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    insert_od_row(
        repository,
        service_date=date(2026, 7, 1),
        train_no="D1",
        from_station="北京",
        to_station="上海",
        depart_at=datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc),
        arrive_at=datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc),
        price="309.0",
    )

    engine = TimeExpandedRouteSearchEngine(repository)
    response = await engine.search(RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1)))

    assert response.plans[0].total_price == Decimal("309.0")