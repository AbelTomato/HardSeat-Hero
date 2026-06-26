from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.domain.models import SeatPrice, TrainSegment
from app.services.static_price_repository import SQLiteStaticPriceRepository


def make_segment(
    train_no: str,
    price: str,
    *,
    seat_type: str = "二等座",
    source: str = "fixture",
    updated_at: datetime | None = None,
) -> TrainSegment:
    depart_at = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)
    arrive_at = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
    return TrainSegment(
        train_no=train_no,
        from_station="北京",
        to_station="上海",
        depart_at=depart_at,
        arrive_at=arrive_at,
        duration_minutes=120,
        prices=[SeatPrice(seat_type=seat_type, price=Decimal(price))],
        source=source,
        updated_at=updated_at or datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc),
    )


def test_static_price_repository_returns_empty_segments_for_empty_database(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")

    assert repository.query_segments("provider", date(2026, 7, 1), "北京", "上海") == []
    assert repository.get_min_price("provider", date(2026, 7, 1), "北京", "上海") is None


def test_static_price_repository_upserts_and_queries_segments(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    fetched_at = datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc)

    repository.upsert_segments(
        "12306-public-price",
        date(2026, 7, 1),
        "北京",
        "上海",
        [make_segment("G1", "553.0"), make_segment("D1", "309.0")],
        fetched_at=fetched_at,
    )

    segments = repository.query_segments("12306-public-price", date(2026, 7, 1), "北京", "上海")

    assert [segment.train_no for segment in segments] == ["D1", "G1"]
    assert segments[0].lowest_price == Decimal("309.0")
    assert segments[0].source == "fixture"
    assert segments[0].updated_at == fetched_at


def test_static_price_repository_keeps_multiple_seat_prices_for_same_train(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    segment = make_segment("G1", "553.0")
    segment.prices.append(SeatPrice(seat_type="一等座", price=Decimal("933.0")))

    repository.upsert_segments("provider", date(2026, 7, 1), "北京", "上海", [segment])

    segments = repository.query_segments("provider", date(2026, 7, 1), "北京", "上海")

    assert len(segments) == 1
    assert {price.seat_type: price.price for price in segments[0].prices} == {
        "一等座": Decimal("933.0"),
        "二等座": Decimal("553.0"),
    }


def test_static_price_repository_updates_same_train_seat_price(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    travel_date = date(2026, 7, 1)

    repository.upsert_segments("provider", travel_date, "北京", "上海", [make_segment("G1", "553.0")])
    repository.upsert_segments("provider", travel_date, "北京", "上海", [make_segment("G1", "500.0")])

    segments = repository.query_segments("provider", travel_date, "北京", "上海")


    assert len(segments) == 1
    assert segments[0].lowest_price == Decimal("500.0")


def test_static_price_repository_returns_min_price_snapshot(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    fetched_at = datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc)

    repository.upsert_segments(
        "provider",
        date(2026, 7, 1),
        "北京",
        "上海",
        [make_segment("G1", "553.0"), make_segment("D1", "309.0")],
        fetched_at=fetched_at,
    )

    min_price = repository.get_min_price("provider", date(2026, 7, 1), "北京", "上海")

    assert min_price is not None
    assert min_price.min_price == Decimal("309.0")
    assert min_price.train_code == "D1"
    assert min_price.seat_type == "二等座"
    assert min_price.fetched_at == fetched_at


def test_static_price_repository_filters_stale_segments_and_min_price(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    fetched_at = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)
    now = datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc)

    repository.upsert_segments(
        "provider",
        date(2026, 7, 1),
        "北京",
        "上海",
        [make_segment("G1", "553.0")],
        fetched_at=fetched_at,
    )

    assert repository.query_segments(
        "provider",
        date(2026, 7, 1),
        "北京",
        "上海",
        max_age=timedelta(days=7),
        now=now,
    ) == []
    assert repository.get_min_price(
        "provider",
        date(2026, 7, 1),
        "北京",
        "上海",
        max_age=timedelta(days=7),
        now=now,
    ) is None
    assert repository.is_stale(
        "provider",
        date(2026, 7, 1),
        "北京",
        "上海",
        timedelta(days=7),
        now=now,
    )