from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@dataclass(frozen=True)
class StaticPriceOverview:
    provider: str
    od_count: int
    row_count: int
    train_count: int
    latest_fetched_at: str | None


@dataclass(frozen=True)
class StaticPriceOdDetail:
    provider: str
    travel_date: date
    origin: str
    destination: str
    segment_count: int
    seat_types: tuple[str, ...]
    min_price: str | None
    min_price_train_code: str | None
    min_price_seat_type: str | None
    latest_fetched_at: str | None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查 SQLite 静态票价库覆盖和指定 OD 明细。")
    parser.add_argument("--db", required=True, help="SQLite 静态票价库路径")
    parser.add_argument("--provider", default="static-price", help="provider 名称，默认 static-price")
    parser.add_argument("--date", help="出行日期，格式 YYYY-MM-DD。提供 OD 查询时必填")
    parser.add_argument("--from", dest="origin", help="出发站")
    parser.add_argument("--to", dest="destination", help="到达站")
    return parser.parse_args(argv)


def inspect_overview(db_path: str | Path, provider: str = "static-price") -> StaticPriceOverview:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(DISTINCT travel_date || ':' || origin || ':' || destination) AS od_count,
                COUNT(*) AS row_count,
                COUNT(DISTINCT train_code) AS train_count,
                MAX(fetched_at) AS latest_fetched_at
            FROM od_public_price_snapshot
            WHERE provider = ?
            """,
            (provider,),
        ).fetchone()
    return StaticPriceOverview(
        provider=provider,
        od_count=int(row[0] or 0),
        row_count=int(row[1] or 0),
        train_count=int(row[2] or 0),
        latest_fetched_at=row[3],
    )


def inspect_od_detail(
    db_path: str | Path,
    *,
    provider: str,
    travel_date: date,
    origin: str,
    destination: str,
) -> StaticPriceOdDetail:
    with sqlite3.connect(db_path) as connection:
        summary = connection.execute(
            """
            SELECT
                COUNT(DISTINCT train_code),
                MAX(fetched_at)
            FROM od_public_price_snapshot
            WHERE provider = ? AND travel_date = ? AND origin = ? AND destination = ?
            """,
            (provider, travel_date.isoformat(), origin, destination),
        ).fetchone()
        seat_rows = connection.execute(
            """
            SELECT DISTINCT seat_type
            FROM od_public_price_snapshot
            WHERE provider = ? AND travel_date = ? AND origin = ? AND destination = ?
            ORDER BY seat_type ASC
            """,
            (provider, travel_date.isoformat(), origin, destination),
        ).fetchall()
        min_row = connection.execute(
            """
            SELECT min_price, train_code, seat_type
            FROM od_min_price_snapshot
            WHERE provider = ? AND travel_date = ? AND origin = ? AND destination = ?
            """,
            (provider, travel_date.isoformat(), origin, destination),
        ).fetchone()
    return StaticPriceOdDetail(
        provider=provider,
        travel_date=travel_date,
        origin=origin,
        destination=destination,
        segment_count=int(summary[0] or 0),
        seat_types=tuple(row[0] for row in seat_rows),
        min_price=min_row[0] if min_row is not None else None,
        min_price_train_code=min_row[1] if min_row is not None else None,
        min_price_seat_type=min_row[2] if min_row is not None else None,
        latest_fetched_at=summary[1],
    )


def print_overview(overview: StaticPriceOverview) -> None:
    print(f"provider={overview.provider}")
    print(f"od_count={overview.od_count}")
    print(f"row_count={overview.row_count}")
    print(f"train_count={overview.train_count}")
    print(f"latest_fetched_at={overview.latest_fetched_at or '-'}")


def print_od_detail(detail: StaticPriceOdDetail) -> None:
    print(f"provider={detail.provider}")
    print(f"date={detail.travel_date.isoformat()}")
    print(f"origin={detail.origin}")
    print(f"destination={detail.destination}")
    print(f"segment_count={detail.segment_count}")
    print(f"seat_types={','.join(detail.seat_types) if detail.seat_types else '-'}")
    print(f"min_price={detail.min_price or '-'}")
    print(f"min_price_train_code={detail.min_price_train_code or '-'}")
    print(f"min_price_seat_type={detail.min_price_seat_type or '-'}")
    print(f"latest_fetched_at={detail.latest_fetched_at or '-'}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    od_args = [args.date, args.origin, args.destination]
    if any(od_args) and not all(od_args):
        print("--date、--from、--to 必须同时提供", file=sys.stderr)
        return 2
    if all(od_args):
        detail = inspect_od_detail(
            args.db,
            provider=args.provider,
            travel_date=date.fromisoformat(args.date),
            origin=args.origin,
            destination=args.destination,
        )
        print_od_detail(detail)
        return 0
    overview = inspect_overview(args.db, args.provider)
    print_overview(overview)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())