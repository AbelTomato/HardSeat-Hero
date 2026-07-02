import argparse
import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
import sqlite3

from scripts.crawl_time_expanded_full_snapshot import (
    PriceTask,
    build_fare_edges_from_public_price_rows,
    crawl_full_snapshot,
    filter_price_tasks_by_station_codes,
    generate_price_tasks,
    group_price_tasks_by_station_od,
    init_full_snapshot_task_tables,
    is_non_retryable_railway_error,
    mark_train_task_failed,
    mark_train_task_running,
    mark_train_task_succeeded,
    reset_running_tasks,
    run_with_retries,
    succeeded_train_task_keys,
    summarize_station_od_compression,
    upsert_train_schedule_snapshot,
    upsert_train_tasks,
)
from app.adapters.railway_12306_public_price import Railway12306Error
from app.adapters.railway_12306_snapshot import SnapshotTrainName, SnapshotTrainStop


def make_stop(name: str, no: int, start: str = "08:00", arrive: str = "08:00") -> SnapshotTrainStop:
    return SnapshotTrainStop(
        station_name=name,
        station_train_code="G1",
        station_no=no,
        arrive_time=arrive,
        start_time=start,
        arrive_day_diff=0,
        running_time=f"0{no}:00",
        start_station_name="A",
        end_station_name="D",
    )


def test_generate_price_tasks_all_mode_generates_all_od_for_four_stops() -> None:
    train = SnapshotTrainName(train_no="G1NO", train_code="G1")
    stops = [make_stop("A", 1), make_stop("B", 2), make_stop("C", 3), make_stop("D", 4)]

    tasks = generate_price_tasks(train, stops, price_task_mode="all")

    assert [(task.from_stop.station_name, task.to_stop.station_name) for task in tasks] == [
        ("A", "B"),
        ("A", "C"),
        ("A", "D"),
        ("B", "C"),
        ("B", "D"),
        ("C", "D"),
    ]


def test_generate_price_tasks_adjacent_mode_generates_adjacent_od_for_four_stops() -> None:
    train = SnapshotTrainName(train_no="G1NO", train_code="G1")
    stops = [make_stop("A", 1), make_stop("B", 2), make_stop("C", 3), make_stop("D", 4)]

    tasks = generate_price_tasks(train, stops, price_task_mode="adjacent")

    assert [(task.from_stop.station_name, task.to_stop.station_name) for task in tasks] == [("A", "B"), ("B", "C"), ("C", "D")]


def test_generate_price_tasks_all_mode_respects_station_gap_and_limit() -> None:
    train = SnapshotTrainName(train_no="G1NO", train_code="G1")
    stops = [make_stop("A", 1), make_stop("B", 2), make_stop("C", 3), make_stop("D", 4)]

    tasks = generate_price_tasks(train, stops, min_station_gap=2, max_od_per_train=2)

    assert [(task.from_stop.station_name, task.to_stop.station_name) for task in tasks] == [("A", "C"), ("A", "D")]


def test_summarize_station_od_compression_counts_train_and_unique_station_od() -> None:
    train = SnapshotTrainName(train_no="G1NO", train_code="G1")
    tasks = [
        PriceTask(train.train_no, train.train_code, make_stop("A", 1), make_stop("B", 2)),
        PriceTask("G2NO", "G2", make_stop("A", 1), make_stop("B", 2)),
        PriceTask(train.train_no, train.train_code, make_stop("B", 2), make_stop("C", 3)),
    ]

    assert summarize_station_od_compression(tasks) == (3, 2, 2 / 3)


def test_group_price_tasks_by_station_od_groups_shared_station_od() -> None:
    tasks = [
        PriceTask("G1NO", "G1", make_stop("A", 1), make_stop("B", 2)),
        PriceTask("G2NO", "G2", make_stop("A", 1), make_stop("B", 2)),
        PriceTask("G1NO", "G1", make_stop("B", 2), make_stop("C", 3)),
    ]

    grouped = group_price_tasks_by_station_od(tasks)

    assert set(grouped) == {("A", "B"), ("B", "C")}
    assert [task.train_code for task in grouped[("A", "B")]] == ["G1", "G2"]


def test_filter_price_tasks_by_station_codes_skips_unmapped_stations() -> None:
    train = SnapshotTrainName(train_no="G1NO", train_code="G1")
    tasks = [
        PriceTask(train.train_no, train.train_code, make_stop("北京", 1), make_stop("上海", 2)),
        PriceTask(train.train_no, train.train_code, make_stop("北京", 1), make_stop("长发屯", 3)),
    ]

    filtered, unmapped = filter_price_tasks_by_station_codes(tasks, {"北京": "BJP", "上海": "SHH"})

    assert [(task.from_stop.station_name, task.to_stop.station_name) for task in filtered] == [("北京", "上海")]
    assert unmapped == {"长发屯"}


def test_station_code_mapping_error_is_non_retryable() -> None:
    assert is_non_retryable_railway_error(Railway12306Error("站名无法映射电报码：'长发屯'"))
    assert not is_non_retryable_railway_error(Railway12306Error("票价接口返回非 JSON"))


async def _always_unmapped_station() -> None:
    raise Railway12306Error("站名无法映射电报码：'长发屯'")


def test_run_with_retries_stops_on_non_retryable_error() -> None:
    attempts = 0

    async def factory() -> None:
        nonlocal attempts
        attempts += 1
        await _always_unmapped_station()

    import pytest

    with pytest.raises(Railway12306Error):
        import asyncio

        asyncio.run(run_with_retries(factory, max_retries=5, should_retry=lambda exc: not is_non_retryable_railway_error(exc)))

    assert attempts == 1


def test_build_fare_edges_filters_target_train_and_seat_types() -> None:
    task = PriceTask(
        train_no="G1NO",
        train_code="G1",
        from_stop=make_stop("北京", 1, start="08:00"),
        to_stop=make_stop("上海", 2, arrive="10:00"),
    )
    rows = [
        {
            "queryLeftNewDTO": {
                "train_no": "G1NO",
                "station_train_code": "G1",
                "lishi": "02:00",
                "ze_price": "03090",
                "zy_price": "05530",
            }
        },
        {"queryLeftNewDTO": {"train_no": "D1NO", "station_train_code": "D1", "ze_price": "01000"}},
    ]

    edges = build_fare_edges_from_public_price_rows(
        provider="full-snapshot",
        task=task,
        rows=rows,
        include_seat_types={"二等座"},
        fetched_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )

    assert len(edges) == 1
    assert edges[0].seat_type == "二等座"
    assert edges[0].price == Decimal("309")
    assert edges[0].duration_minutes == 120
    assert edges[0].raw_hash


def test_init_and_reset_full_snapshot_task_tables(tmp_path) -> None:
    db = tmp_path / "static.sqlite3"
    init_full_snapshot_task_tables(db)
    with sqlite3.connect(db) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "crawl_full_snapshot_train_task" in names
        assert "crawl_full_snapshot_price_task" in names
        connection.execute(
            """
            INSERT INTO crawl_full_snapshot_train_task(
                provider, crawl_date, train_no, train_code, status, created_at, updated_at
            ) VALUES ('p', '2026-07-12', 'G1NO', 'G1', 'running', 'now', 'now')
            """
        )

    reset_running_tasks(db, "p", datetime(2026, 7, 12).date())

    with sqlite3.connect(db) as connection:
        status = connection.execute("SELECT status FROM crawl_full_snapshot_train_task").fetchone()[0]
    assert status == "pending"


def test_upsert_train_schedule_snapshot_writes_service_and_stops(tmp_path) -> None:
    from app.services.static_price_repository import SQLiteStaticPriceRepository

    db = tmp_path / "static.sqlite3"
    SQLiteStaticPriceRepository(db)
    train = SnapshotTrainName(train_no="G1NO", train_code="G1")
    stops = [make_stop("A", 1, start="08:00"), make_stop("B", 2, arrive="09:00", start="09:05")]

    upsert_train_schedule_snapshot(
        db,
        "full-snapshot-train-info",
        date(2026, 7, 12),
        train,
        stops,
        fetched_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )

    with sqlite3.connect(db) as connection:
        service = connection.execute("SELECT train_code, start_station, end_station, raw_hash FROM train_service_snapshot").fetchone()
        stop_rows = connection.execute(
            "SELECT station_name, station_no, arrive_at, depart_at, raw_hash FROM train_stop_snapshot ORDER BY station_no"
        ).fetchall()

    assert service == ("G1", "A", "B", service[3])
    assert service[3]
    assert [(row[0], row[1], row[2], row[3]) for row in stop_rows] == [
        ("A", 1, "08:00", "08:00"),
        ("B", 2, "09:00", "09:05"),
    ]
    assert all(row[4] for row in stop_rows)


def test_train_task_status_helpers_support_resume_keys(tmp_path) -> None:
    db = tmp_path / "static.sqlite3"
    init_full_snapshot_task_tables(db)
    crawl_date = date(2026, 7, 12)
    train = SnapshotTrainName(train_no="G1NO", train_code="G1")
    upsert_train_tasks(db, "p", crawl_date, [train])

    mark_train_task_running(db, "p", crawl_date, train)
    mark_train_task_succeeded(db, "p", crawl_date, train, stop_count=2)

    assert succeeded_train_task_keys(db, "p", crawl_date) == {"G1NO"}
    with sqlite3.connect(db) as connection:
        status, attempts, stop_count = connection.execute(
            "SELECT status, attempts, stop_count FROM crawl_full_snapshot_train_task"
        ).fetchone()
    assert (status, attempts, stop_count) == ("succeeded", 1, 2)

    mark_train_task_failed(db, "p", crawl_date, train, RuntimeError("boom"))
    with sqlite3.connect(db) as connection:
        status, last_error = connection.execute("SELECT status, last_error FROM crawl_full_snapshot_train_task").fetchone()
    assert status == "failed"
    assert "RuntimeError: boom" in last_error


def test_crawl_train_info_only_writes_schedule_and_skips_price_flow(monkeypatch, tmp_path) -> None:
    calls = {"station_codes": 0, "prices": 0, "stops": 0}

    class FakeStationCodes:
        async def get_station_codes(self):
            calls["station_codes"] += 1
            return {"A": "AAA", "B": "BBB"}

    class FakeClient:
        def __init__(self, *, timeout_seconds: float) -> None:
            self.station_codes = FakeStationCodes()

        async def fetch_train_names(self, service_date: date) -> list[SnapshotTrainName]:
            return [SnapshotTrainName(train_no="G1NO", train_code="G1")]

        async def fetch_train_stops(self, service_date: date, train_no: str) -> list[SnapshotTrainStop]:
            calls["stops"] += 1
            return [make_stop("A", 1), make_stop("B", 2)]

        async def query_public_prices(self, service_date: date, from_station: str, to_station: str):
            calls["prices"] += 1
            return []

    monkeypatch.setattr("scripts.crawl_time_expanded_full_snapshot.Railway12306SnapshotClient", FakeClient)
    db = tmp_path / "static.sqlite3"
    args = argparse.Namespace(
        db=str(db),
        provider="full-snapshot-train-info",
        date="2026-07-12",
        train_info_concurrency=1,
        price_concurrency=1,
        train_info_delay=0,
        price_delay=0,
        max_retries=0,
        timeout_seconds=1,
        limit_trains=0,
        max_stops_per_train=0,
        max_od_per_train=0,
        min_station_gap=1,
        price_task_mode="adjacent",
        include_seat_types="",
        dry_run=False,
        train_info_only=True,
        resume=False,
        retry_failed=False,
        progress_interval=0,
    )

    summary = asyncio.run(crawl_full_snapshot(args))

    assert summary.train_total == 1
    assert summary.train_success == 1
    assert summary.price_total == 0
    assert calls == {"station_codes": 0, "prices": 0, "stops": 1}
    with sqlite3.connect(db) as connection:
        task_status = connection.execute("SELECT status FROM crawl_full_snapshot_train_task").fetchone()[0]
        service_count = connection.execute("SELECT COUNT(*) FROM train_service_snapshot").fetchone()[0]
        stop_count = connection.execute("SELECT COUNT(*) FROM train_stop_snapshot").fetchone()[0]
    assert task_status == "succeeded"
    assert service_count == 1
    assert stop_count == 2

    args.resume = True
    resumed_summary = asyncio.run(crawl_full_snapshot(args))

    assert resumed_summary.train_total == 1
    assert resumed_summary.train_success == 0
    assert calls["stops"] == 1


def test_crawl_dry_run_reports_adjacent_train_and_unique_station_od(monkeypatch, tmp_path, capsys) -> None:
    class FakeStationCodes:
        async def get_station_codes(self):
            return {"A": "AAA", "B": "BBB", "C": "CCC", "D": "DDD"}

    class FakeClient:
        def __init__(self, *, timeout_seconds: float) -> None:
            self.station_codes = FakeStationCodes()

        async def fetch_train_names(self, service_date: date) -> list[SnapshotTrainName]:
            return [SnapshotTrainName(train_no="G1NO", train_code="G1"), SnapshotTrainName(train_no="G2NO", train_code="G2")]

        async def fetch_train_stops(self, service_date: date, train_no: str) -> list[SnapshotTrainStop]:
            return [make_stop("A", 1), make_stop("B", 2), make_stop("C", 3), make_stop("D", 4)]

        async def query_public_prices(self, service_date: date, from_station: str, to_station: str):
            raise AssertionError("dry-run must not query prices")

    monkeypatch.setattr("scripts.crawl_time_expanded_full_snapshot.Railway12306SnapshotClient", FakeClient)
    args = argparse.Namespace(
        db=str(tmp_path / "static.sqlite3"),
        provider="full-snapshot-dry-run",
        date="2026-07-12",
        train_info_concurrency=1,
        price_concurrency=1,
        train_info_delay=0,
        price_delay=0,
        max_retries=0,
        timeout_seconds=1,
        limit_trains=0,
        max_stops_per_train=0,
        max_od_per_train=0,
        min_station_gap=1,
        price_task_mode="adjacent",
        include_seat_types="",
        dry_run=True,
        train_info_only=False,
        resume=False,
        retry_failed=False,
        progress_interval=0,
    )

    summary = asyncio.run(crawl_full_snapshot(args))
    output = capsys.readouterr().out

    assert summary.train_od_count == 6
    assert summary.unique_station_od_count == 3
    assert summary.station_od_compression_ratio == 0.5
    assert summary.price_request_count == 3
    assert summary.reused_price_task_count == 3
    assert "train_od_count=6" in output
    assert "unique_station_od_count=3" in output
    assert "price_requests=3" in output
    assert "reused_price_tasks=3" in output


def test_crawl_reuses_single_public_price_query_for_shared_station_od(monkeypatch, tmp_path) -> None:
    price_queries: list[tuple[str, str]] = []

    class FakeStationCodes:
        async def get_station_codes(self):
            return {"A": "AAA", "B": "BBB"}

    class FakeClient:
        def __init__(self, *, timeout_seconds: float) -> None:
            self.station_codes = FakeStationCodes()

        async def fetch_train_names(self, service_date: date) -> list[SnapshotTrainName]:
            return [SnapshotTrainName(train_no="G1NO", train_code="G1"), SnapshotTrainName(train_no="G2NO", train_code="G2")]

        async def fetch_train_stops(self, service_date: date, train_no: str) -> list[SnapshotTrainStop]:
            train_code = "G1" if train_no == "G1NO" else "G2"
            return [
                SnapshotTrainStop(
                    station_name="A",
                    station_train_code=train_code,
                    station_no=1,
                    arrive_time="08:00",
                    start_time="08:00",
                    arrive_day_diff=0,
                    running_time="00:00",
                    start_station_name="A",
                    end_station_name="B",
                ),
                SnapshotTrainStop(
                    station_name="B",
                    station_train_code=train_code,
                    station_no=2,
                    arrive_time="09:00",
                    start_time="09:00",
                    arrive_day_diff=0,
                    running_time="01:00",
                    start_station_name="A",
                    end_station_name="B",
                ),
            ]

        async def query_public_prices(self, service_date: date, from_station: str, to_station: str):
            price_queries.append((from_station, to_station))
            return [
                {"queryLeftNewDTO": {"train_no": "G1NO", "station_train_code": "G1", "lishi": "01:00", "ze_price": "01000"}},
                {"queryLeftNewDTO": {"train_no": "G2NO", "station_train_code": "G2", "lishi": "01:00", "ze_price": "01200"}},
            ]

    monkeypatch.setattr("scripts.crawl_time_expanded_full_snapshot.Railway12306SnapshotClient", FakeClient)
    args = argparse.Namespace(
        db=str(tmp_path / "static.sqlite3"),
        provider="full-snapshot-shared-od",
        date="2026-07-12",
        train_info_concurrency=1,
        price_concurrency=1,
        train_info_delay=0,
        price_delay=0,
        max_retries=0,
        timeout_seconds=1,
        limit_trains=0,
        max_stops_per_train=0,
        max_od_per_train=0,
        min_station_gap=1,
        price_task_mode="adjacent",
        include_seat_types="二等座",
        dry_run=False,
        train_info_only=False,
        resume=False,
        retry_failed=False,
        progress_interval=0,
    )

    summary = asyncio.run(crawl_full_snapshot(args))

    assert price_queries == [("A", "B")]
    assert summary.train_od_count == 2
    assert summary.unique_station_od_count == 1
    assert summary.price_total == 2
    assert summary.price_request_count == 1
    assert summary.reused_price_task_count == 1
    assert summary.price_success == 2
    assert summary.edge_count == 2
