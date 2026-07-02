from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.adapters.railway_12306_public_price import PRICE_FIELDS, parse_price  # noqa: E402
from app.adapters.railway_12306_snapshot import Railway12306SnapshotClient  # noqa: E402
from app.services.static_price_repository import SQLiteStaticPriceRepository  # noqa: E402


@dataclass(frozen=True)
class ScheduleStop:
    station_name: str
    station_no: int


@dataclass(frozen=True)
class SampleOd:
    train_no: str
    train_code: str
    from_station: str
    to_station: str
    from_station_no: int
    to_station_no: int
    adjacent_pairs: tuple[tuple[str, str], ...]
    sample_kind: str


@dataclass(frozen=True)
class PriceComparison:
    train_no: str
    train_code: str
    from_station: str
    to_station: str
    seat_type: str
    direct_price: Decimal
    adjacent_sum_price: Decimal
    diff: Decimal
    abs_diff: Decimal
    sample_kind: str


@dataclass
class SeatTypeDiffStats:
    sample_count: int = 0
    exact_match_count: int = 0
    diff_count: int = 0
    total_abs_diff: Decimal = Decimal("0")
    max_abs_diff: Decimal = Decimal("0")

    @property
    def avg_abs_diff(self) -> Decimal:
        if self.sample_count == 0:
            return Decimal("0")
        return self.total_abs_diff / Decimal(self.sample_count)


@dataclass(frozen=True)
class ErrorReport:
    sample_count: int
    exact_match_count: int
    diff_count: int
    avg_abs_diff: Decimal
    max_abs_diff: Decimal
    by_seat_type_diff_stats: dict[str, SeatTypeDiffStats] = field(default_factory=dict)


def load_sample_ods(
    db_path: str | Path,
    *,
    provider: str,
    service_date: date,
    sample_trains: int,
    min_stop_count: int = 4,
) -> list[SampleOd]:
    SQLiteStaticPriceRepository(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        train_rows = connection.execute(
            """
            SELECT train_no, train_code, COUNT(*) AS stop_count
            FROM train_stop_snapshot
            WHERE provider = ? AND service_date = ?
            GROUP BY train_no, train_code
            HAVING stop_count >= ?
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (provider, service_date.isoformat(), min_stop_count, sample_trains),
        ).fetchall()

        samples: list[SampleOd] = []
        for train_row in train_rows:
            stop_rows = connection.execute(
                """
                SELECT station_name, station_no
                FROM train_stop_snapshot
                WHERE provider = ? AND service_date = ? AND train_no = ?
                ORDER BY station_no
                """,
                (provider, service_date.isoformat(), train_row["train_no"]),
            ).fetchall()
            stops = [ScheduleStop(station_name=row["station_name"], station_no=row["station_no"]) for row in stop_rows]
            samples.extend(build_sample_ods_for_train(str(train_row["train_no"]), str(train_row["train_code"]), stops))
    return samples


def build_sample_ods_for_train(train_no: str, train_code: str, stops: list[ScheduleStop]) -> list[SampleOd]:
    if len(stops) < 2:
        return []

    candidates: list[tuple[int, int, str]] = [(0, 1, "adjacent")]
    if len(stops) >= 4:
        candidates.append((0, 3, "medium"))
    if len(stops) >= 3:
        candidates.append((0, len(stops) - 1, "long"))

    samples: list[SampleOd] = []
    seen: set[tuple[int, int]] = set()
    for from_index, to_index, sample_kind in candidates:
        if (from_index, to_index) in seen:
            continue
        seen.add((from_index, to_index))
        adjacent_pairs = tuple(
            (stops[index].station_name, stops[index + 1].station_name) for index in range(from_index, to_index)
        )
        samples.append(
            SampleOd(
                train_no=train_no,
                train_code=train_code,
                from_station=stops[from_index].station_name,
                to_station=stops[to_index].station_name,
                from_station_no=stops[from_index].station_no,
                to_station_no=stops[to_index].station_no,
                adjacent_pairs=adjacent_pairs,
                sample_kind=sample_kind,
            )
        )
    return samples


def extract_train_prices_by_seat(rows: list[dict[str, Any]], *, train_no: str, train_code: str) -> dict[str, Decimal]:
    prices: dict[str, Decimal] = {}
    normalized_train_code = normalize_train_code(train_code)
    for row in rows:
        dto = row.get("queryLeftNewDTO")
        if not isinstance(dto, dict):
            continue
        row_train_no = str(dto.get("train_no") or "")
        row_train_code = normalize_train_code(str(dto.get("station_train_code") or ""))
        if row_train_no != train_no and row_train_code != normalized_train_code:
            continue
        for field_name, seat_type in PRICE_FIELDS.items():
            price = parse_price(dto.get(field_name))
            if price is not None:
                prices[seat_type] = price
    return prices


def compare_sample_from_rows(
    sample: SampleOd,
    *,
    direct_rows: list[dict[str, Any]],
    adjacent_rows_by_pair: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[PriceComparison]:
    direct_prices = extract_train_prices_by_seat(direct_rows, train_no=sample.train_no, train_code=sample.train_code)
    adjacent_prices_by_pair = {
        pair: extract_train_prices_by_seat(rows, train_no=sample.train_no, train_code=sample.train_code)
        for pair, rows in adjacent_rows_by_pair.items()
    }

    comparisons: list[PriceComparison] = []
    for seat_type, direct_price in direct_prices.items():
        segment_prices: list[Decimal] = []
        for pair in sample.adjacent_pairs:
            segment_price = adjacent_prices_by_pair.get(pair, {}).get(seat_type)
            if segment_price is None:
                segment_prices = []
                break
            segment_prices.append(segment_price)
        if not segment_prices:
            continue
        adjacent_sum = sum(segment_prices, Decimal("0"))
        diff = adjacent_sum - direct_price
        comparisons.append(
            PriceComparison(
                train_no=sample.train_no,
                train_code=sample.train_code,
                from_station=sample.from_station,
                to_station=sample.to_station,
                seat_type=seat_type,
                direct_price=direct_price,
                adjacent_sum_price=adjacent_sum,
                diff=diff,
                abs_diff=abs(diff),
                sample_kind=sample.sample_kind,
            )
        )
    return comparisons


def summarize_comparisons(comparisons: Iterable[PriceComparison]) -> ErrorReport:
    comparison_list = list(comparisons)
    by_seat_type: dict[str, SeatTypeDiffStats] = {}
    total_abs_diff = Decimal("0")
    exact_match_count = 0
    max_abs_diff = Decimal("0")

    for comparison in comparison_list:
        stats = by_seat_type.setdefault(comparison.seat_type, SeatTypeDiffStats())
        stats.sample_count += 1
        stats.total_abs_diff += comparison.abs_diff
        stats.max_abs_diff = max(stats.max_abs_diff, comparison.abs_diff)
        if comparison.diff == 0:
            stats.exact_match_count += 1
            exact_match_count += 1
        else:
            stats.diff_count += 1
        total_abs_diff += comparison.abs_diff
        max_abs_diff = max(max_abs_diff, comparison.abs_diff)

    sample_count = len(comparison_list)
    return ErrorReport(
        sample_count=sample_count,
        exact_match_count=exact_match_count,
        diff_count=sample_count - exact_match_count,
        avg_abs_diff=Decimal("0") if sample_count == 0 else total_abs_diff / Decimal(sample_count),
        max_abs_diff=max_abs_diff,
        by_seat_type_diff_stats=by_seat_type,
    )


async def compare_adjacent_vs_direct(args: argparse.Namespace) -> tuple[ErrorReport, list[PriceComparison]]:
    service_date = date.fromisoformat(args.date)
    samples = load_sample_ods(
        args.db,
        provider=args.provider,
        service_date=service_date,
        sample_trains=args.sample_trains,
        min_stop_count=args.min_stop_count,
    )
    client = Railway12306SnapshotClient(timeout_seconds=args.timeout_seconds)
    semaphore = asyncio.Semaphore(args.price_concurrency)

    async def query_prices(from_station: str, to_station: str) -> list[dict[str, Any]]:
        async with semaphore:
            await asyncio.sleep(args.price_delay)
            return await client.query_public_prices(service_date, from_station, to_station)

    async def compare_one(sample: SampleOd) -> list[PriceComparison]:
        direct_rows = await query_prices(sample.from_station, sample.to_station)
        adjacent_rows_by_pair = {
            pair: rows
            for pair, rows in zip(
                sample.adjacent_pairs,
                await asyncio.gather(*(query_prices(from_station, to_station) for from_station, to_station in sample.adjacent_pairs)),
                strict=True,
            )
        }
        return compare_sample_from_rows(sample, direct_rows=direct_rows, adjacent_rows_by_pair=adjacent_rows_by_pair)

    nested = await asyncio.gather(*(compare_one(sample) for sample in samples))
    comparisons = [comparison for group in nested for comparison in group]
    return summarize_comparisons(comparisons), comparisons


def report_to_dict(report: ErrorReport) -> dict[str, Any]:
    return {
        "sample_count": report.sample_count,
        "exact_match_count": report.exact_match_count,
        "diff_count": report.diff_count,
        "avg_abs_diff": decimal_to_text(report.avg_abs_diff),
        "max_abs_diff": decimal_to_text(report.max_abs_diff),
        "by_seat_type_diff_stats": {
            seat_type: {
                "sample_count": stats.sample_count,
                "exact_match_count": stats.exact_match_count,
                "diff_count": stats.diff_count,
                "avg_abs_diff": decimal_to_text(stats.avg_abs_diff),
                "max_abs_diff": decimal_to_text(stats.max_abs_diff),
            }
            for seat_type, stats in sorted(report.by_seat_type_diff_stats.items())
        },
    }


def decimal_to_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def normalize_train_code(value: str) -> str:
    return value.split("(", 1)[0].strip()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抽样比较 direct OD 公布票价与相邻段累加票价误差。")
    parser.add_argument("--db", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--sample-trains", type=int, default=50)
    parser.add_argument("--min-stop-count", type=int, default=4)
    parser.add_argument("--price-concurrency", type=int, default=2)
    parser.add_argument("--price-delay", type=float, default=0.8)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    return parser.parse_args(argv)


async def async_main(argv: list[str] | None = None) -> int:
    report, comparisons = await compare_adjacent_vs_direct(parse_args(argv))
    print(json.dumps(report_to_dict(report), ensure_ascii=False, indent=2))
    if comparisons:
        print("comparison samples:")
        for comparison in comparisons[:20]:
            print(
                f"{comparison.train_code} {comparison.from_station}->{comparison.to_station} "
                f"{comparison.seat_type}: direct={comparison.direct_price}, "
                f"adjacent_sum={comparison.adjacent_sum_price}, diff={comparison.diff}"
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())