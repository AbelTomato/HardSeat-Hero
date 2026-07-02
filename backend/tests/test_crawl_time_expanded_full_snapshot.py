from datetime import datetime, timezone
from decimal import Decimal
import sqlite3

from scripts.crawl_time_expanded_full_snapshot import (
    PriceTask,
    build_fare_edges_from_public_price_rows,
    generate_price_tasks,
    init_full_snapshot_task_tables,
    reset_running_tasks,
)
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


def test_generate_price_tasks_respects_station_gap_and_limit() -> None:
    train = SnapshotTrainName(train_no="G1NO", train_code="G1")
    stops = [make_stop("A", 1), make_stop("B", 2), make_stop("C", 3), make_stop("D", 4)]

    tasks = generate_price_tasks(train, stops, min_station_gap=2, max_od_per_train=2)

    assert [(task.from_stop.station_name, task.to_stop.station_name) for task in tasks] == [("A", "C"), ("A", "D")]


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