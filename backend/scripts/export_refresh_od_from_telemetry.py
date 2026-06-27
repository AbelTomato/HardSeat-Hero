from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@dataclass(frozen=True)
class RefreshOdCandidate:
    origin: str
    destination: str
    priority: int
    reason: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从搜索遥测库导出静态票价刷新 OD CSV。")
    parser.add_argument("--telemetry-db", required=True, help="搜索遥测 SQLite 路径")
    parser.add_argument("--output", required=True, help="输出 CSV 路径")
    parser.add_argument("--provider", default="static-price", help="provider 名称，默认 static-price")
    parser.add_argument("--popular-limit", type=int, default=100, help="最多导出多少个高频查询 OD")
    parser.add_argument("--transfer-limit", type=int, default=200, help="最多导出多少个中转命中派生 OD")
    return parser.parse_args(argv)


def collect_popular_search_od(
    telemetry_db: str | Path,
    *,
    provider: str,
    limit: int,
) -> list[RefreshOdCandidate]:
    with sqlite3.connect(telemetry_db) as connection:
        rows = connection.execute(
            """
            SELECT origin, destination, COUNT(*) AS search_count
            FROM search_runs
            WHERE provider = ?
            GROUP BY origin, destination
            ORDER BY search_count DESC, origin ASC, destination ASC
            LIMIT ?
            """,
            (provider, limit),
        ).fetchall()
    return [
        RefreshOdCandidate(origin=row[0], destination=row[1], priority=20, reason="popular_search")
        for row in rows
    ]


def collect_transfer_hit_od(
    telemetry_db: str | Path,
    *,
    provider: str,
    limit: int,
) -> list[RefreshOdCandidate]:
    with sqlite3.connect(telemetry_db) as connection:
        rows = connection.execute(
            """
            SELECT origin, destination, transfer_station, hit_count, best_price
            FROM transfer_station_hits
            WHERE provider = ?
            ORDER BY hit_count DESC, CAST(best_price AS REAL) ASC, origin ASC, destination ASC, transfer_station ASC
            LIMIT ?
            """,
            (provider, limit),
        ).fetchall()
    candidates: list[RefreshOdCandidate] = []
    for origin, destination, transfer_station, _hit_count, _best_price in rows:
        candidates.append(RefreshOdCandidate(origin=origin, destination=transfer_station, priority=10, reason="transfer_hit"))
        candidates.append(RefreshOdCandidate(origin=transfer_station, destination=destination, priority=10, reason="transfer_hit"))
    return candidates


def merge_candidates(candidates: list[RefreshOdCandidate]) -> list[RefreshOdCandidate]:
    by_od: dict[tuple[str, str], RefreshOdCandidate] = {}
    for candidate in candidates:
        key = (candidate.origin, candidate.destination)
        existing = by_od.get(key)
        if existing is None or candidate.priority < existing.priority:
            by_od[key] = candidate
        elif existing.priority == candidate.priority and candidate.reason not in existing.reason.split("+"):
            by_od[key] = RefreshOdCandidate(
                origin=existing.origin,
                destination=existing.destination,
                priority=existing.priority,
                reason=f"{existing.reason}+{candidate.reason}",
            )
    return sorted(by_od.values(), key=lambda item: (item.priority, item.origin, item.destination, item.reason))


def write_refresh_od_csv(path: str | Path, candidates: list[RefreshOdCandidate]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["origin", "destination", "priority", "reason"])
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(
                {
                    "origin": candidate.origin,
                    "destination": candidate.destination,
                    "priority": candidate.priority,
                    "reason": candidate.reason,
                }
            )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    popular = collect_popular_search_od(args.telemetry_db, provider=args.provider, limit=args.popular_limit)
    transfer = collect_transfer_hit_od(args.telemetry_db, provider=args.provider, limit=args.transfer_limit)
    candidates = merge_candidates([*popular, *transfer])
    write_refresh_od_csv(args.output, candidates)
    print(f"导出完成：output={args.output}, count={len(candidates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())