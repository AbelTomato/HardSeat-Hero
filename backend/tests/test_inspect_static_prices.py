from datetime import date, datetime, timezone
from decimal import Decimal

from app.domain.models import SeatPrice, TrainSegment
from app.services.static_price_repository import SQLiteStaticPriceRepository
from scripts.inspect_static_prices import inspect_od_detail, inspect_overview, main, parse_args


def test_inspect_static_prices_parse_args_defaults_provider() -> None:
    args = parse_args(["--db", "test.sqlite3"])

    assert args.db == "test.sqlite3"
    assert args.provider == "static-price"


def test_inspect_overview_returns_zero_for_empty_database(tmp_path) -> None:
    db_path = tmp_path / "static_prices.sqlite3"
    SQLiteStaticPriceRepository(db_path)

    overview = inspect_overview(db_path)

    assert overview.provider == "static-price"
    assert overview.od_count == 0
    assert overview.row_count == 0
    assert overview.train_count == 0
    assert overview.latest_fetched_at is None


def make_segment(train_no: str, seat_type: str, price: str) -> TrainSegment:
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
        source="fixture",
        updated_at=datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc),
    )


def test_inspect_od_detail_returns_min_price_and_seat_types(tmp_path) -> None:
    db_path = tmp_path / "static_prices.sqlite3"
    repository = SQLiteStaticPriceRepository(db_path)
    repository.upsert_segments(
        "static-price",
        date(2026, 7, 1),
        "北京",
        "上海",
        [make_segment("G1", "二等座", "553.0"), make_segment("D1", "硬座", "309.0")],
        fetched_at=datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc),
    )

    detail = inspect_od_detail(
        db_path,
        provider="static-price",
        travel_date=date(2026, 7, 1),
        origin="北京",
        destination="上海",
    )

    assert detail.segment_count == 2
    assert detail.seat_types == ("二等座", "硬座")
    assert detail.min_price == "309.0"
    assert detail.min_price_train_code == "D1"
    assert detail.min_price_seat_type == "硬座"
    assert detail.latest_fetched_at == "2026-06-26T08:00:00+00:00"


def test_inspect_main_prints_overview_for_empty_database(tmp_path, capsys) -> None:
    db_path = tmp_path / "static_prices.sqlite3"
    SQLiteStaticPriceRepository(db_path)

    exit_code = main(["--db", str(db_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "provider=static-price" in output
    assert "od_count=0" in output
    assert "row_count=0" in output


def test_inspect_main_prints_od_detail(tmp_path, capsys) -> None:
    db_path = tmp_path / "static_prices.sqlite3"
    repository = SQLiteStaticPriceRepository(db_path)
    repository.upsert_segments(
        "static-price",
        date(2026, 7, 1),
        "北京",
        "上海",
        [make_segment("D1", "硬座", "309.0")],
        fetched_at=datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc),
    )

    exit_code = main(["--db", str(db_path), "--date", "2026-07-01", "--from", "北京", "--to", "上海"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "origin=北京" in output
    assert "destination=上海" in output
    assert "segment_count=1" in output
    assert "min_price=309.0" in output


def test_inspect_main_rejects_partial_od_args(tmp_path, capsys) -> None:
    db_path = tmp_path / "static_prices.sqlite3"
    SQLiteStaticPriceRepository(db_path)

    exit_code = main(["--db", str(db_path), "--date", "2026-07-01"])

    error = capsys.readouterr().err
    assert exit_code == 2
    assert "必须同时提供" in error