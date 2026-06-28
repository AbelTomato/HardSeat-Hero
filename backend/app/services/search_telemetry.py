from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.domain.models import RouteQuery, TransferPlan


@dataclass(frozen=True)
class TransferStationHit:
    station: str
    hit_count: int
    best_price: Decimal | None


class SqliteSearchTelemetryRecorder:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._ensure_schema()

    def record_search(
        self,
        query: RouteQuery,
        provider: str,
        plans: list[TransferPlan],
        remote_query_count: int,
    ) -> None:
        best_plan = plans[0] if plans else None
        best_price = str(best_plan.total_price) if best_plan is not None else None
        transfer_stations = best_plan.transfer_stations if best_plan is not None else []
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO search_runs(
                    searched_at, provider, travel_date, origin, destination,
                    max_transfers, min_transfer_minutes, max_total_duration_minutes,
                    plan_count, best_price, best_transfer_stations_json, remote_query_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    provider,
                    query.date.isoformat(),
                    query.from_station,
                    query.to_station,
                    query.max_transfers,
                    query.min_transfer_minutes,
                    query.max_total_duration_minutes,
                    len(plans),
                    best_price,
                    json.dumps(transfer_stations, ensure_ascii=False),
                    remote_query_count,
                ),
            )
            for station in transfer_stations:
                connection.execute(
                    """
                    INSERT INTO transfer_station_hits(
                        provider, origin, destination, transfer_station,
                        hit_count, best_price, last_seen_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(provider, origin, destination, transfer_station)
                    DO UPDATE SET
                        hit_count = hit_count + 1,
                        best_price = CASE
                            WHEN best_price IS NULL THEN excluded.best_price
                            WHEN excluded.best_price IS NULL THEN best_price
                            WHEN CAST(excluded.best_price AS REAL) < CAST(best_price AS REAL) THEN excluded.best_price
                            ELSE best_price
                        END,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (
                        provider,
                        query.from_station,
                        query.to_station,
                        station,
                        best_price,
                        now,
                    ),
                )

    def best_transfer_stations(self, provider: str, origin: str, destination: str, limit: int = 20) -> list[str]:
        return [hit.station for hit in self.transfer_station_hits(provider, origin, destination, limit)]

    def transfer_station_hits(
        self,
        provider: str,
        origin: str,
        destination: str,
        limit: int = 20,
    ) -> list[TransferStationHit]:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT transfer_station, hit_count, best_price
                FROM transfer_station_hits
                WHERE provider = ? AND origin = ? AND destination = ?
                ORDER BY hit_count DESC, CAST(best_price AS REAL) ASC, transfer_station ASC
                LIMIT ?
                """,
                (provider, origin, destination, limit),
            ).fetchall()
        return [
            TransferStationHit(
                station=row[0],
                hit_count=row[1],
                best_price=Decimal(row[2]) if row[2] is not None else None,
            )
            for row in rows
        ]

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS search_runs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    searched_at TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    travel_date TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    max_transfers INTEGER NOT NULL,
                    min_transfer_minutes INTEGER NOT NULL,
                    max_total_duration_minutes INTEGER,
                    plan_count INTEGER NOT NULL,
                    best_price TEXT,
                    best_transfer_stations_json TEXT NOT NULL,
                    remote_query_count INTEGER NOT NULL
                )
                """
            )
            self._migrate_nullable_max_total_duration(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transfer_station_hits(
                    provider TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    transfer_station TEXT NOT NULL,
                    hit_count INTEGER NOT NULL,
                    best_price TEXT,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY(provider, origin, destination, transfer_station)
                )
                """
            )

    def _migrate_nullable_max_total_duration(self, connection: sqlite3.Connection) -> None:
        table_info = connection.execute("PRAGMA table_info(search_runs)").fetchall()
        max_duration_column = next((column for column in table_info if column[1] == "max_total_duration_minutes"), None)
        if max_duration_column is None or max_duration_column[3] == 0:
            return

        connection.execute("ALTER TABLE search_runs RENAME TO search_runs_legacy")
        connection.execute(
            """
            CREATE TABLE search_runs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                searched_at TEXT NOT NULL,
                provider TEXT NOT NULL,
                travel_date TEXT NOT NULL,
                origin TEXT NOT NULL,
                destination TEXT NOT NULL,
                max_transfers INTEGER NOT NULL,
                min_transfer_minutes INTEGER NOT NULL,
                max_total_duration_minutes INTEGER,
                plan_count INTEGER NOT NULL,
                best_price TEXT,
                best_transfer_stations_json TEXT NOT NULL,
                remote_query_count INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO search_runs(
                id, searched_at, provider, travel_date, origin, destination,
                max_transfers, min_transfer_minutes, max_total_duration_minutes,
                plan_count, best_price, best_transfer_stations_json, remote_query_count
            )
            SELECT
                id, searched_at, provider, travel_date, origin, destination,
                max_transfers, min_transfer_minutes, max_total_duration_minutes,
                plan_count, best_price, best_transfer_stations_json, remote_query_count
            FROM search_runs_legacy
            """
        )
        connection.execute("DROP TABLE search_runs_legacy")