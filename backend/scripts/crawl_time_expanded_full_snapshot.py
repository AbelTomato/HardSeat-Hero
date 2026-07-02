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
from typing import Any, Callable, Iterable

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
    train_failed: int
    train_od_count: int
    unique_station_od_count: int
    station_od_compression_ratio: float
    price_total: int
    price_success: int
    price_skipped: int
    price_failed: int
    edge_count: int
    unmapped_station_count: int
    price_request_count: int = 0
    reused_price_task_count: int = 0


def generate_price_tasks(
    train: SnapshotTrainName,
    stops: list[SnapshotTrainStop],
    *,
    price_task_mode: str = "all",
    min_station_gap: int = 1,
    max_od_per_train: int = 0,
) -> list[PriceTask]:
    if price_task_mode not in {"all", "adjacent"}:
        raise ValueError(f"unsupported price task mode: {price_task_mode}")

    tasks: list[PriceTask] = []
    if price_task_mode == "adjacent":
        for i in range(len(stops) - 1):
            tasks.append(PriceTask(train.train_no, train.train_code, stops[i], stops[i + 1]))
            if max_od_per_train and len(tasks) >= max_od_per_train:
                return tasks
        return tasks

    for i, from_stop in enumerate(stops):
        for j in range(i + min_station_gap, len(stops)):
            tasks.append(PriceTask(train.train_no, train.train_code, from_stop, stops[j]))
            if max_od_per_train and len(tasks) >= max_od_per_train:
                return tasks
    return tasks


def filter_price_tasks_by_station_codes(
    tasks: Iterable[PriceTask],
    station_codes: dict[str, str],
) -> tuple[list[PriceTask], set[str]]:
    filtered: list[PriceTask] = []
    unmapped_stations: set[str] = set()
    for task in tasks:
        missing = [station for station in (task.from_stop.station_name, task.to_stop.station_name) if station not in station_codes]
        if missing:
            unmapped_stations.update(missing)
            continue
        filtered.append(task)
    return filtered, unmapped_stations


def summarize_station_od_compression(tasks: Iterable[PriceTask]) -> tuple[int, int, float]:
    task_list = list(tasks)
    train_od_count = len(task_list)
    unique_station_od_count = len({(task.from_stop.station_name, task.to_stop.station_name) for task in task_list})
    compression_ratio = unique_station_od_count / train_od_count if train_od_count else 0.0
    return train_od_count, unique_station_od_count, compression_ratio


def group_price_tasks_by_station_od(tasks: Iterable[PriceTask]) -> dict[tuple[str, str], list[PriceTask]]:
    grouped: dict[tuple[str, str], list[PriceTask]] = {}
    for task in tasks:
        key = (task.from_stop.station_name, task.to_stop.station_name)
        grouped.setdefault(key, []).append(task)
    return grouped


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


def upsert_train_schedule_snapshot(
    db_path: str | Path,
    provider: str,
    crawl_date: date,
    train: SnapshotTrainName,
    stops: list[SnapshotTrainStop],
    *,
    fetched_at: datetime | None = None,
) -> None:
    if not stops:
        return

    fetched_at = fetched_at or datetime.now(timezone.utc)
    fetched_at_text = fetched_at.isoformat()
    service_raw_hash = _raw_hash(
        {
            "train_no": train.train_no,
            "train_code": train.train_code,
            "stops": [_stop_raw(stop) for stop in stops],
        }
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO train_service_snapshot(
                provider, service_date, train_no, train_code,
                start_station, end_station, fetched_at, raw_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, service_date, train_no) DO UPDATE SET
                train_code = excluded.train_code,
                start_station = excluded.start_station,
                end_station = excluded.end_station,
                fetched_at = excluded.fetched_at,
                raw_hash = excluded.raw_hash
            """,
            (
                provider,
                crawl_date.isoformat(),
                train.train_no,
                train.train_code,
                stops[0].station_name,
                stops[-1].station_name,
                fetched_at_text,
                service_raw_hash,
            ),
        )
        connection.executemany(
            """
            INSERT INTO train_stop_snapshot(
                provider, service_date, train_no, train_code,
                station_name, station_no, arrive_at, depart_at,
                arrive_day_diff, running_time, fetched_at, raw_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, service_date, train_no, station_no) DO UPDATE SET
                train_code = excluded.train_code,
                station_name = excluded.station_name,
                arrive_at = excluded.arrive_at,
                depart_at = excluded.depart_at,
                arrive_day_diff = excluded.arrive_day_diff,
                running_time = excluded.running_time,
                fetched_at = excluded.fetched_at,
                raw_hash = excluded.raw_hash
            """,
            [
                (
                    provider,
                    crawl_date.isoformat(),
                    train.train_no,
                    train.train_code,
                    stop.station_name,
                    stop.station_no,
                    stop.arrive_time,
                    stop.start_time,
                    stop.arrive_day_diff,
                    stop.running_time,
                    fetched_at_text,
                    _raw_hash(_stop_raw(stop)),
                )
                for stop in stops
            ],
        )


def _stop_raw(stop: SnapshotTrainStop) -> dict[str, Any]:
    return {
        "station_name": stop.station_name,
        "station_train_code": stop.station_train_code,
        "station_no": stop.station_no,
        "arrive_time": stop.arrive_time,
        "start_time": stop.start_time,
        "arrive_day_diff": stop.arrive_day_diff,
        "running_time": stop.running_time,
        "start_station_name": stop.start_station_name,
        "end_station_name": stop.end_station_name,
    }


def _raw_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


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


def succeeded_train_task_keys(db_path: str | Path, provider: str, crawl_date: date) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT train_no FROM crawl_full_snapshot_train_task
            WHERE provider = ? AND crawl_date = ? AND status = 'succeeded'
            """,
            (provider, crawl_date.isoformat()),
        ).fetchall()
    return {str(row[0]) for row in rows}


def mark_train_task_running(db_path: str | Path, provider: str, crawl_date: date, train: SnapshotTrainName) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE crawl_full_snapshot_train_task
            SET status = 'running', attempts = attempts + 1, last_error = NULL, updated_at = ?
            WHERE provider = ? AND crawl_date = ? AND train_no = ?
            """,
            (now, provider, crawl_date.isoformat(), train.train_no),
        )


def mark_train_task_succeeded(
    db_path: str | Path,
    provider: str,
    crawl_date: date,
    train: SnapshotTrainName,
    *,
    stop_count: int,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE crawl_full_snapshot_train_task
            SET status = 'succeeded', stop_count = ?, last_error = NULL, updated_at = ?
            WHERE provider = ? AND crawl_date = ? AND train_no = ?
            """,
            (stop_count, now, provider, crawl_date.isoformat(), train.train_no),
        )


def mark_train_task_failed(
    db_path: str | Path,
    provider: str,
    crawl_date: date,
    train: SnapshotTrainName,
    error: Exception,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE crawl_full_snapshot_train_task
            SET status = 'failed', last_error = ?, updated_at = ?
            WHERE provider = ? AND crawl_date = ? AND train_no = ?
            """,
            (f"{type(error).__name__}: {error}", now, provider, crawl_date.isoformat(), train.train_no),
        )


def is_non_retryable_railway_error(exc: Exception) -> bool:
    return isinstance(exc, Railway12306Error) and "站名无法映射电报码" in str(exc)


async def run_with_retries(
    coro_factory,
    *,
    max_retries: int,
    should_retry: Callable[[Exception], bool] | None = None,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except Exception as exc:  # noqa: BLE001 - crawler isolates each task and records final error.
            last_error = exc
            if should_retry is not None and not should_retry(exc):
                break
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
    skipped_succeeded_train_nos = succeeded_train_task_keys(repository.db_path, args.provider, crawl_date) if args.resume else set()
    trains_to_fetch = [train for train in trains if train.train_no not in skipped_succeeded_train_nos]
    station_codes = (
        {}
        if args.train_info_only
        else await run_with_retries(lambda: client.station_codes.get_station_codes(), max_retries=args.max_retries)
    )

    train_sem = asyncio.Semaphore(args.train_info_concurrency)
    price_sem = asyncio.Semaphore(args.price_concurrency)
    price_tasks: list[PriceTask] = []
    train_success = 0
    train_failed = 0
    price_success = 0
    price_skipped = 0
    price_failed = 0
    edge_count = 0
    unmapped_stations: set[str] = set()
    include_seat_types = set(args.include_seat_types.split(",")) if args.include_seat_types else None

    async def fetch_train(train: SnapshotTrainName) -> None:
        nonlocal train_success, train_failed, price_tasks, unmapped_stations
        try:
            mark_train_task_running(repository.db_path, args.provider, crawl_date, train)
            async with train_sem:
                await asyncio.sleep(args.train_info_delay)
                stops = await run_with_retries(lambda: client.fetch_train_stops(crawl_date, train.train_no), max_retries=args.max_retries)
            if args.max_stops_per_train:
                stops = stops[: args.max_stops_per_train]
            upsert_train_schedule_snapshot(repository.db_path, args.provider, crawl_date, train, stops)
            if len(stops) >= 2:
                train_success += 1
                if not args.train_info_only:
                    generated_tasks = generate_price_tasks(
                        train,
                        stops,
                        price_task_mode=args.price_task_mode,
                        min_station_gap=args.min_station_gap,
                        max_od_per_train=args.max_od_per_train,
                    )
                    filtered_tasks, train_unmapped_stations = filter_price_tasks_by_station_codes(generated_tasks, station_codes)
                    price_tasks.extend(filtered_tasks)
                    unmapped_stations.update(train_unmapped_stations)
                if args.progress_interval and train_success % args.progress_interval == 0:
                    print(
                        f"train progress: success={train_success}, failed={train_failed}, "
                        f"price_tasks={len(price_tasks)}, unmapped_stations={len(unmapped_stations)}"
                    )
                mark_train_task_succeeded(repository.db_path, args.provider, crawl_date, train, stop_count=len(stops))
            else:
                raise Railway12306Error(f"经停站数量不足：{len(stops)}")
        except Exception as exc:  # noqa: BLE001 - failed train must not stop the whole crawl.
            train_failed += 1
            mark_train_task_failed(repository.db_path, args.provider, crawl_date, train, exc)
            print(f"train failed: {train.train_code} {train.train_no}: {type(exc).__name__}: {exc}", file=sys.stderr)

    await asyncio.gather(*(fetch_train(train) for train in trains_to_fetch))
    train_od_count, unique_station_od_count, compression_ratio = summarize_station_od_compression(price_tasks)
    grouped_price_tasks = group_price_tasks_by_station_od(price_tasks)
    price_request_count = len(grouped_price_tasks)
    reused_price_task_count = train_od_count - price_request_count

    if args.train_info_only:
        print(
            f"train-info-only: trains={len(trains)}, skipped_succeeded={len(skipped_succeeded_train_nos)}, "
            f"train_success={train_success}, train_failed={train_failed}"
        )
        return CrawlSummary(len(trains), train_success, train_failed, 0, 0, 0.0, 0, 0, 0, 0, 0, 0)

    if args.dry_run:
        print(
            f"dry-run: trains={len(trains)}, train_success={train_success}, train_failed={train_failed}, "
            f"price_task_mode={args.price_task_mode}, train_od_count={train_od_count}, "
            f"unique_station_od_count={unique_station_od_count}, compression_ratio={compression_ratio:.4f}, "
            f"price_tasks={len(price_tasks)}, price_requests={price_request_count}, "
            f"reused_price_tasks={reused_price_task_count}, unmapped_stations={len(unmapped_stations)}"
        )
        if unmapped_stations:
            print(f"unmapped station samples: {', '.join(sorted(unmapped_stations)[:20])}")
        return CrawlSummary(
            len(trains),
            train_success,
            train_failed,
            train_od_count,
            unique_station_od_count,
            compression_ratio,
            len(price_tasks),
            0,
            0,
            0,
            0,
            len(unmapped_stations),
            price_request_count,
            reused_price_task_count,
        )

    async def fetch_price_group(station_od: tuple[str, str], tasks: list[PriceTask]) -> None:
        nonlocal price_success, price_skipped, price_failed, edge_count
        from_station, to_station = station_od
        try:
            async with price_sem:
                await asyncio.sleep(args.price_delay)
                rows = await run_with_retries(
                    lambda: client.query_public_prices(crawl_date, from_station, to_station),
                    max_retries=args.max_retries,
                    should_retry=lambda exc: not is_non_retryable_railway_error(exc),
                )
            for task in tasks:
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
            if is_non_retryable_railway_error(exc):
                price_skipped += len(tasks)
                print(
                    f"price skipped: station_od={from_station}->{to_station}, tasks={len(tasks)}: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
            else:
                price_failed += len(tasks)
                print(
                    f"price failed: station_od={from_station}->{to_station}, tasks={len(tasks)}: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
        finally:
            done = price_success + price_skipped + price_failed
            if args.progress_interval and done % args.progress_interval == 0:
                print(
                    f"price progress: done={done}/{len(price_tasks)}, requests={price_request_count}, "
                    f"reused_tasks={reused_price_task_count}, success={price_success}, skipped={price_skipped}, "
                    f"failed={price_failed}, edges={edge_count}"
                )

    await asyncio.gather(*(fetch_price_group(station_od, tasks) for station_od, tasks in grouped_price_tasks.items()))
    return CrawlSummary(
        len(trains),
        train_success,
        train_failed,
        train_od_count,
        unique_station_od_count,
        compression_ratio,
        len(price_tasks),
        price_success,
        price_skipped,
        price_failed,
        edge_count,
        len(unmapped_stations),
        price_request_count,
        reused_price_task_count,
    )


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
    parser.add_argument("--price-task-mode", choices=("adjacent", "all"), default="all")
    parser.add_argument("--include-seat-types", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--train-info-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--progress-interval", type=int, default=100)
    return parser.parse_args(argv)


async def async_main(argv: list[str] | None = None) -> int:
    summary = await crawl_full_snapshot(parse_args(argv))
    print(
        "crawl finished: "
        f"trains={summary.train_success}/{summary.train_total}, train_failed={summary.train_failed}, "
        f"train_od_count={summary.train_od_count}, unique_station_od_count={summary.unique_station_od_count}, "
        f"compression_ratio={summary.station_od_compression_ratio:.4f}, "
        f"price_requests={summary.price_request_count}, reused_price_tasks={summary.reused_price_task_count}, "
        f"prices={summary.price_success}/{summary.price_total}, "
        f"skipped={summary.price_skipped}, failed={summary.price_failed}, edges={summary.edge_count}, "
        f"unmapped_stations={summary.unmapped_station_count}"
    )
    return 0 if summary.train_failed == 0 and summary.price_failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())