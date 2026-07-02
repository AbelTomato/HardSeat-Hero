from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.adapters.railway_12306_public_price import PRICE_FIELDS, Railway12306Error, parse_duration_minutes, parse_price  # noqa: E402
from app.adapters.railway_12306_snapshot import Railway12306SnapshotClient, SnapshotTrainName, SnapshotTrainStop  # noqa: E402
from app.services.static_price_repository import SQLiteStaticPriceRepository, TrainOdFareEdge  # noqa: E402


SOURCE = "12306-full-snapshot"


@dataclass(frozen=True)
class PriceTask:
    train_no: str
    train_code: str
    from_stop: SnapshotTrainStop
    to_stop: SnapshotTrainStop


@dataclass(frozen=True)
class CrawlSummary:
    train_total: int
    train_success: int
    price_total: int
    price_success: int
    price_skipped: int
    price_failed: int
    edge_count: int


def generate_price_tasks(
    train: SnapshotTrainName,
    stops: list[SnapshotTrainStop],
    *,
    min_station_gap: int = 1,
    max_od_per_train: int = 0,
) -> list[PriceTask]:
    tasks: list[PriceTask] = []
    for i, from_stop in enumerate(stops):
        for j in range(i + min_station_gap, len(stops)):
            tasks.append(PriceTask(train.train_no, train.train_code, from_stop, stops[j]))
            if max_od_per_train and len(tasks) >= max_od_per_train:
                return tasks
    return tasks


def build_fare_edges_from_public_price_rows(
    *,
    provider: str,
    task: PriceTask,
    rows: list[dict[str, Any]],
    include_seat_types: set[str] | None = None,
    fetched_at: datetime | None = None,
) -> list[TrainOdFareEdge]:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    edges: list[TrainOdFareEdge] = []
    for row in rows:
        dto = row.get("queryLeftNewDTO")
        if not isinstance(dto, dict):
            continue
        row_train_no = str(dto.get("train_no") or "")
        row_train_code = normalize_train_code(str(dto.get("station_train_code") or ""))
        if row_train_no != task.train_no and row_train_code != task.train_code:
            continue
        raw_hash = hashlib.sha256(json.dumps(dto, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        for field, seat_type in PRICE_FIELDS.items():
            if include_seat_types and seat_type not in include_seat_types:
                continue
            price = parse_price(dto.get(field))
            if price is None:
                continue
            duration = _duration_minutes(task, dto)
            edges.append(
                TrainOdFareEdge(
                    provider=provider,
                    train_no=task.train_no,
                    train_code=task.train_code,
                    from_station=task.from_stop.station_name,
                    to_station=task.to_stop.station_name,
                    from_station_no=task.from_stop.station_no,
                    to_station_no=task.to_stop.station_no,
                    depart_time=parse_time(task.from_stop.start_time),
                    arrive_time=parse_time(task.to_stop.arrive_time or task.to_stop.start_time),
                    depart_day_offset=0,
                    arrive_day_offset=task.to_stop.arrive_day_diff,
                    duration_minutes=duration,
                    seat_type=seat_type,
                    price=price,
                    source=SOURCE,
                    fetched_at=fetched_at,
                    raw_hash=raw_hash,
                )
            )
    return edges


def parse_time(value: str | None) -> time:
    if value is None:
        raise ValueError("缺少到发时刻")
    return time.fromisoformat(value)


def normalize_train_code(value: str) -> str:
    return value.split("(", 1)[0].strip()


def init_full_snapshot_task_tables(db_path: str | Path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS crawl_full_snapshot_train_task (
                provider TEXT NOT NULL,
                crawl_date TEXT NOT NULL,
                train_no TEXT NOT NULL,
                train_code TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                stop_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(provider, crawl_date, train_no)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS crawl_full_snapshot_price_task (
                provider TEXT NOT NULL,
                crawl_date TEXT NOT NULL,
                train_no TEXT NOT NULL,
                train_code TEXT NOT NULL,
                from_station TEXT NOT NULL,
                to_station TEXT NOT NULL,
                from_station_no INTEGER NOT NULL,
                to_station_no INTEGER NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                edge_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(provider, crawl_date, train_no, from_station_no, to_station_no)
            )
            """
        )


def reset_running_tasks(db_path: str | Path, provider: str, crawl_date: date) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        for table in ("crawl_full_snapshot_train_task", "crawl_full_snapshot_price_task"):
            connection.execute(
                f"UPDATE {table} SET status = 'pending', updated_at = ? WHERE provider = ? AND crawl_date = ? AND status = 'running'",
                (now, provider, crawl_date.isoformat()),
            )


def reset_failed_tasks(db_path: str | Path, provider: str, crawl_date: date) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        for table in ("crawl_full_snapshot_train_task", "crawl_full_snapshot_price_task"):
            connection.execute(
                f"UPDATE {table} SET status = 'pending', last_error = NULL, updated_at = ? WHERE provider = ? AND crawl_date = ? AND status = 'failed'",
                (now, provider, crawl_date.isoformat()),
            )


def upsert_train_tasks(db_path: str | Path, provider: str, crawl_date: date, trains: Iterable[SnapshotTrainName]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            """
            INSERT INTO crawl_full_snapshot_train_task(
                provider, crawl_date, train_no, train_code, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT(provider, crawl_date, train_no) DO UPDATE SET
                train_code = excluded.train_code,
                updated_at = excluded.updated_at
            """,
            [(provider, crawl_date.isoformat(), train.train_no, train.train_code, now, now) for train in trains],
        )


async def run_with_retries(coro_factory, *, max_retries: int) -> Any:
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except Exception as exc:  # noqa: BLE001 - crawler isolates each task and records final error.
            last_error = exc
            if attempt >= max_retries:
                break
            await asyncio.sleep((2 ** (attempt + 1)) + random.random())
    raise last_error or RuntimeError("unknown retry error")


async def crawl_full_snapshot(args: argparse.Namespace) -> CrawlSummary:
    crawl_date = date.fromisoformat(args.date)
    repository = SQLiteStaticPriceRepository(args.db)
    init_full_snapshot_task_tables(repository.db_path)
    if args.resume:
        reset_running_tasks(repository.db_path, args.provider, crawl_date)
    if args.retry_failed:
        reset_failed_tasks(repository.db_path, args.provider, crawl_date)

    client = Railway12306SnapshotClient(timeout_seconds=args.timeout_seconds)
    trains = await run_with_retries(lambda: client.fetch_train_names(crawl_date), max_retries=args.max_retries)
    if args.limit_trains:
        trains = trains[: args.limit_trains]
    upsert_train_tasks(repository.db_path, args.provider, crawl_date, trains)

    train_sem = asyncio.Semaphore(args.train_info_concurrency)
    price_sem = asyncio.Semaphore(args.price_concurrency)
    price_tasks: list[PriceTask] = []
    train_success = 0
    price_success = 0
    price_skipped = 0
    price_failed = 0
    edge_count = 0
    include_seat_types = set(args.include_seat_types.split(",")) if args.include_seat_types else None

    async def fetch_train(train: SnapshotTrainName) -> None:
        nonlocal train_success, price_tasks
        async with train_sem:
            await asyncio.sleep(args.train_info_delay)
            stops = await run_with_retries(lambda: client.fetch_train_stops(crawl_date, train.train_no), max_retries=args.max_retries)
        if args.max_stops_per_train:
            stops = stops[: args.max_stops_per_train]
        if len(stops) >= 2:
            train_success += 1
            price_tasks.extend(
                generate_price_tasks(
                    train,
                    stops,
                    min_station_gap=args.min_station_gap,
                    max_od_per_train=args.max_od_per_train,
                )
            )

    await asyncio.gather(*(fetch_train(train) for train in trains))

    if args.dry_run:
        print(f"dry-run: trains={len(trains)}, train_success={train_success}, price_tasks={len(price_tasks)}")
        return CrawlSummary(len(trains), train_success, len(price_tasks), 0, 0, 0, 0)

    async def fetch_price(task: PriceTask) -> None:
        nonlocal price_success, price_skipped, price_failed, edge_count
        try:
            async with price_sem:
                await asyncio.sleep(args.price_delay)
                rows = await run_with_retries(
                    lambda: client.query_public_prices(crawl_date, task.from_stop.station_name, task.to_stop.station_name),
                    max_retries=args.max_retries,
                )
            edges = build_fare_edges_from_public_price_rows(
                provider=args.provider,
                task=task,
                rows=rows,
                include_seat_types=include_seat_types,
            )
            if edges:
                repository.upsert_train_od_fare_edges(args.provider, edges)
                edge_count += len(edges)
                price_success += 1
            else:
                price_skipped += 1
        except Exception as exc:  # noqa: BLE001 - failed OD must not stop the whole crawl.
            price_failed += 1
            print(f"price failed: {task.train_code} {task.from_stop.station_name}->{task.to_stop.station_name}: {type(exc).__name__}: {exc}", file=sys.stderr)

    await asyncio.gather(*(fetch_price(task) for task in price_tasks))
    return CrawlSummary(len(trains), train_success, len(price_tasks), price_success, price_skipped, price_failed, edge_count)


def _duration_minutes(task: PriceTask, dto: dict[str, Any]) -> int:
    lishi = dto.get("lishi")
    if isinstance(lishi, str) and lishi:
        try:
            return parse_duration_minutes(lishi)
        except Railway12306Error:
            pass
    from_minutes = _running_minutes(task.from_stop.running_time)
    to_minutes = _running_minutes(task.to_stop.running_time)
    if from_minutes is not None and to_minutes is not None and to_minutes >= from_minutes:
        return to_minutes - from_minutes
    return 0


def _running_minutes(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return parse_duration_minutes(value)
    except Railway12306Error:
        return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="全量爬取指定日期车次经停和同车 OD 公布票价，写入 train_od_fare_edge。")
    parser.add_argument("--db", required=True)
    parser.add_argument("--provider", default="full-snapshot")
    parser.add_argument("--date", required=True)
    parser.add_argument("--train-info-concurrency", type=int, default=4)
    parser.add_argument("--price-concurrency", type=int, default=2)
    parser.add_argument("--train-info-delay", type=float, default=0.3)
    parser.add_argument("--price-delay", type=float, default=0.8)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--limit-trains", type=int, default=0)
    parser.add_argument("--max-stops-per-train", type=int, default=0)
    parser.add_argument("--max-od-per-train", type=int, default=0)
    parser.add_argument("--min-station-gap", type=int, default=1)
    parser.add_argument("--include-seat-types", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    return parser.parse_args(argv)


async def async_main(argv: list[str] | None = None) -> int:
    summary = await crawl_full_snapshot(parse_args(argv))
    print(
        "crawl finished: "
        f"trains={summary.train_success}/{summary.train_total}, "
        f"prices={summary.price_success}/{summary.price_total}, "
        f"skipped={summary.price_skipped}, failed={summary.price_failed}, edges={summary.edge_count}"
    )
    return 0 if summary.price_failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())