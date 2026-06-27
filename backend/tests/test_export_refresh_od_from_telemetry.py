from datetime import date, datetime, timezone
from decimal import Decimal

from app.domain.models import RouteQuery
from app.services.search_telemetry import SqliteSearchTelemetryRecorder
from app.domain.models import SeatPrice, TrainSegment, TransferPlan
from scripts.export_refresh_od_from_telemetry import (
    RefreshOdCandidate,
    collect_popular_search_od,
    collect_transfer_hit_od,
    merge_candidates,
    main,
    parse_args,
    write_refresh_od_csv,
)


def test_export_refresh_od_parse_args_defaults() -> None:
    args = parse_args(["--telemetry-db", "telemetry.sqlite3", "--output", "out.csv"])

    assert args.telemetry_db == "telemetry.sqlite3"
    assert args.output == "out.csv"
    assert args.provider == "static-price"
    assert args.popular_limit == 100
    assert args.transfer_limit == 200


def test_collect_popular_search_od_orders_by_search_count(tmp_path) -> None:
    db_path = tmp_path / "telemetry.sqlite3"
    recorder = SqliteSearchTelemetryRecorder(db_path)
    query_a = RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1))
    query_b = RouteQuery(from_station="天津", to_station="上海", date=date(2026, 7, 1))
    recorder.record_search(query_a, "static-price", [], 0)
    recorder.record_search(query_a, "static-price", [], 0)
    recorder.record_search(query_b, "static-price", [], 0)

    candidates = collect_popular_search_od(db_path, provider="static-price", limit=10)

    assert candidates[0].origin == "北京"
    assert candidates[0].destination == "上海"
    assert candidates[0].priority == 20
    assert candidates[0].reason == "popular_search"
    assert candidates[1].origin == "天津"


def make_transfer_plan() -> TransferPlan:
    first_depart = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)
    first_arrive = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
    second_depart = datetime(2026, 7, 1, 11, 0, tzinfo=timezone.utc)
    second_arrive = datetime(2026, 7, 1, 13, 0, tzinfo=timezone.utc)
    first = TrainSegment(
        train_no="D1",
        from_station="北京",
        to_station="南京南",
        depart_at=first_depart,
        arrive_at=first_arrive,
        duration_minutes=120,
        prices=[SeatPrice(seat_type="二等座", price=Decimal("100.0"))],
        source="fixture",
        updated_at=first_depart,
    )
    second = TrainSegment(
        train_no="D2",
        from_station="南京南",
        to_station="上海",
        depart_at=second_depart,
        arrive_at=second_arrive,
        duration_minutes=120,
        prices=[SeatPrice(seat_type="二等座", price=Decimal("200.0"))],
        source="fixture",
        updated_at=second_depart,
    )
    return TransferPlan(
        total_price=Decimal("300.0"),
        total_duration_minutes=300,
        transfer_minutes=60,
        transfer_stations=["南京南"],
        segments=[first, second],
    )


def test_collect_transfer_hit_od_expands_transfer_station(tmp_path) -> None:
    db_path = tmp_path / "telemetry.sqlite3"
    recorder = SqliteSearchTelemetryRecorder(db_path)
    query = RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1))
    recorder.record_search(query, "static-price", [make_transfer_plan()], 0)

    candidates = collect_transfer_hit_od(db_path, provider="static-price", limit=10)

    assert candidates == [
        type(candidates[0])(origin="北京", destination="南京南", priority=10, reason="transfer_hit"),
        type(candidates[0])(origin="南京南", destination="上海", priority=10, reason="transfer_hit"),
    ]


def test_merge_candidates_keeps_highest_priority_and_sorts() -> None:
    candidates = merge_candidates(
        [
            RefreshOdCandidate("北京", "上海", 20, "popular_search"),
            RefreshOdCandidate("北京", "上海", 10, "transfer_hit"),
            RefreshOdCandidate("天津", "上海", 20, "popular_search"),
        ]
    )

    assert candidates == [
        RefreshOdCandidate("北京", "上海", 10, "transfer_hit"),
        RefreshOdCandidate("天津", "上海", 20, "popular_search"),
    ]


def test_write_refresh_od_csv_writes_expected_columns(tmp_path) -> None:
    output = tmp_path / "refresh_od.csv"

    write_refresh_od_csv(output, [RefreshOdCandidate("北京", "上海", 20, "popular_search")])

    assert output.read_text(encoding="utf-8").splitlines() == [
        "origin,destination,priority,reason",
        "北京,上海,20,popular_search",
    ]


def test_export_main_writes_csv(tmp_path, capsys) -> None:
    db_path = tmp_path / "telemetry.sqlite3"
    output = tmp_path / "refresh_od.csv"
    recorder = SqliteSearchTelemetryRecorder(db_path)
    query = RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1))
    recorder.record_search(query, "static-price", [make_transfer_plan()], 0)

    exit_code = main(["--telemetry-db", str(db_path), "--output", str(output)])

    stdout = capsys.readouterr().out
    assert exit_code == 0
    assert "导出完成" in stdout
    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "origin,destination,priority,reason"
    assert "北京,南京南,10,transfer_hit" in lines
    assert "南京南,上海,10,transfer_hit" in lines