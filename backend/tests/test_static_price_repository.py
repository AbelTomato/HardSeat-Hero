from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import sqlite3

from app.domain.models import SeatPrice, TrainSegment
from app.services.static_price_repository import SQLiteStaticPriceRepository, TrainOdFareEdge, TrainOdPriceSnapshot


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


def test_time_expanded_tables_are_created(tmp_path) -> None:
    db = tmp_path / "static.sqlite3"
    SQLiteStaticPriceRepository(db)

    with sqlite3.connect(db) as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert "train_service_snapshot" in names
    assert "train_stop_snapshot" in names
    assert "train_od_price_snapshot" in names
    assert "train_od_fare_edge" in names


def test_static_price_repository_queries_train_od_prices(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    fetched_at = datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc)

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
                date(2026, 7, 1).isoformat(),
                "G1",
                "G1",
                "北京",
                "上海",
                1,
                16,
                datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc).isoformat(),
                datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc).isoformat(),
                120,
                "二等座",
                "553.0",
                "fixture",
                fetched_at.isoformat(),
            ),
        )
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
                date(2026, 7, 2).isoformat(),
                "D1",
                "D1",
                "北京",
                "上海",
                1,
                16,
                datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc).isoformat(),
                datetime(2026, 7, 2, 11, 0, tzinfo=timezone.utc).isoformat(),
                120,
                "二等座",
                "309.0",
                "fixture",
                fetched_at.isoformat(),
            ),
        )

    snapshots = repository.query_train_od_prices(
        "12306-public-price",
        [date(2026, 7, 1), date(2026, 7, 2)],
        from_station="北京",
        to_station="上海",
    )

    assert [snapshot.train_no for snapshot in snapshots] == ["D1", "G1"]
    assert all(isinstance(snapshot, TrainOdPriceSnapshot) for snapshot in snapshots)
    assert snapshots[0].price == Decimal("309.0")
    assert isinstance(snapshots[0].depart_at, datetime)
    assert isinstance(snapshots[0].arrive_at, datetime)
    assert snapshots[0].from_station_no == 1
    assert snapshots[0].to_station_no == 16
    assert snapshots[0].fetched_at == fetched_at


def test_static_price_repository_query_train_od_prices_returns_empty_for_empty_dates(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")

    assert repository.query_train_od_prices("provider", []) == []


def test_static_price_repository_upserts_and_queries_train_od_fare_edges(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    fetched_at = datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc)

    edge = TrainOdFareEdge(
        provider="full-snapshot",
        train_no="240000G1010A",
        train_code="G1",
        from_station="北京",
        to_station="上海",
        from_station_no=1,
        to_station_no=16,
        depart_time=time(8, 0),
        arrive_time=time(13, 0),
        depart_day_offset=0,
        arrive_day_offset=0,
        duration_minutes=300,
        seat_type="二等座",
        price=Decimal("553.0"),
        source="12306-full-snapshot",
        fetched_at=fetched_at,
        raw_hash="abc",
    )

    repository.upsert_train_od_fare_edges("full-snapshot", [edge])

    edges = repository.query_train_od_fare_edges("full-snapshot", from_station="北京", to_station="上海")

    assert len(edges) == 1
    assert isinstance(edges[0], TrainOdFareEdge)
    assert edges[0].price == Decimal("553.0")
    assert edges[0].depart_time == time(8, 0)
    assert edges[0].arrive_time == time(13, 0)
    assert edges[0].depart_day_offset == 0
    assert edges[0].arrive_day_offset == 0
    assert edges[0].fetched_at == fetched_at
    assert edges[0].raw_hash == "abc"


def test_static_price_repository_upsert_train_od_fare_edges_updates_same_key(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    fetched_at = datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc)
    base = TrainOdFareEdge(
        provider="full-snapshot",
        train_no="G1NO",
        train_code="G1",
        from_station="北京",
        to_station="上海",
        from_station_no=1,
        to_station_no=16,
        depart_time=time(8, 0),
        arrive_time=time(13, 0),
        depart_day_offset=0,
        arrive_day_offset=0,
        duration_minutes=300,
        seat_type="二等座",
        price=Decimal("553.0"),
        source="fixture",
        fetched_at=fetched_at,
    )
    updated = TrainOdFareEdge(**{**base.__dict__, "price": Decimal("500.0")})

    repository.upsert_train_od_fare_edges("full-snapshot", [base])
    repository.upsert_train_od_fare_edges("full-snapshot", [updated])

    edges = repository.query_train_od_fare_edges("full-snapshot")
    assert len(edges) == 1
    assert edges[0].price == Decimal("500.0")


def test_static_price_repository_query_train_od_fare_edges_filters_station(tmp_path) -> None:
    repository = SQLiteStaticPriceRepository(tmp_path / "static_prices.sqlite3")
    fetched_at = datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc)
    edges = [
        TrainOdFareEdge(
            provider="full-snapshot",
            train_no="D1NO",
            train_code="D1",
            from_station="北京",
            to_station="南京",
            from_station_no=1,
            to_station_no=2,
            depart_time=time(8, 0),
            arrive_time=time(10, 0),
            depart_day_offset=0,
            arrive_day_offset=0,
            duration_minutes=120,
            seat_type="二等座",
            price=Decimal("100"),
            source="fixture",
            fetched_at=fetched_at,
        ),
        TrainOdFareEdge(
            provider="full-snapshot",
            train_no="D2NO",
            train_code="D2",
            from_station="南京",
            to_station="上海",
            from_station_no=1,
            to_station_no=2,
            depart_time=time(11, 0),
            arrive_time=time(12, 0),
            depart_day_offset=0,
            arrive_day_offset=0,
            duration_minutes=60,
            seat_type="二等座",
            price=Decimal("80"),
            source="fixture",
            fetched_at=fetched_at,
        ),
    ]
    repository.upsert_train_od_fare_edges("full-snapshot", edges)

    filtered = repository.query_train_od_fare_edges("full-snapshot", from_station="南京")

    assert [edge.train_code for edge in filtered] == ["D2"]