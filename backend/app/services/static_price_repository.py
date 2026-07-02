from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app.domain.models import SeatPrice, TrainSegment


@dataclass(frozen=True)
class StaticOdMinPrice:
    provider: str
    travel_date: date
    origin: str
    destination: str
    min_price: Decimal
    train_code: str
    seat_type: str
    depart_at: datetime
    arrive_at: datetime
    duration_minutes: int
    fetched_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TrainOdPriceSnapshot:
    provider: str
    service_date: date
    train_no: str
    train_code: str
    from_station: str
    to_station: str
    from_station_no: int
    to_station_no: int
    depart_at: datetime
    arrive_at: datetime
    duration_minutes: int
    seat_type: str
    price: Decimal
    source: str
    fetched_at: datetime


@dataclass(frozen=True)
class TrainOdFareEdge:
    provider: str
    train_no: str
    train_code: str
    from_station: str
    to_station: str
    from_station_no: int
    to_station_no: int
    depart_time: time
    arrive_time: time
    depart_day_offset: int
    arrive_day_offset: int
    duration_minutes: int
    seat_type: str
    price: Decimal
    source: str
    fetched_at: datetime
    raw_hash: str | None = None


class SQLiteStaticPriceRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._ensure_schema()

    def upsert_segments(
        self,
        provider: str,
        travel_date: date,
        origin: str,
        destination: str,
        segments: list[TrainSegment],
        *,
        fetched_at: datetime | None = None,
    ) -> None:
        fetched_at = fetched_at or datetime.now(timezone.utc)
        fetched_at_text = fetched_at.isoformat()
        with sqlite3.connect(self.db_path) as connection:
            for segment in segments:
                train_code = segment.train_no
                for price in segment.prices:
                    connection.execute(
                        """
                        INSERT INTO od_public_price_snapshot(
                            provider, travel_date, origin, destination,
                            train_no, train_code, depart_at, arrive_at,
                            duration_minutes, seat_type, price, currency,
                            source, fetched_at, raw_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CNY', ?, ?, NULL)
                        ON CONFLICT(provider, travel_date, origin, destination, train_code, seat_type)
                        DO UPDATE SET
                            train_no = excluded.train_no,
                            depart_at = excluded.depart_at,
                            arrive_at = excluded.arrive_at,
                            duration_minutes = excluded.duration_minutes,
                            price = excluded.price,
                            currency = excluded.currency,
                            source = excluded.source,
                            fetched_at = excluded.fetched_at,
                            raw_hash = excluded.raw_hash
                        """,
                        (
                            provider,
                            travel_date.isoformat(),
                            origin,
                            destination,
                            segment.train_no,
                            train_code,
                            segment.depart_at.isoformat(),
                            segment.arrive_at.isoformat(),
                            segment.duration_minutes,
                            price.seat_type,
                            str(price.price),
                            segment.source,
                            fetched_at_text,
                        ),
                    )
            self._refresh_min_price(connection, provider, travel_date, origin, destination, fetched_at)

    def query_segments(
        self,
        provider: str,
        travel_date: date,
        origin: str,
        destination: str,
        *,
        max_age: timedelta | None = None,
        now: datetime | None = None,
    ) -> list[TrainSegment]:
        now = now or datetime.now(timezone.utc)
        rows = self._price_rows(provider, travel_date, origin, destination)
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            if max_age is not None and self._is_stale(row["fetched_at"], max_age, now):
                continue
            grouped.setdefault(row["train_code"], []).append(row)

        segments: list[TrainSegment] = []
        for train_rows in grouped.values():
            first = train_rows[0]
            segments.append(
                TrainSegment(
                    train_no=first["train_no"],
                    from_station=first["origin"],
                    to_station=first["destination"],
                    depart_at=datetime.fromisoformat(first["depart_at"]),
                    arrive_at=datetime.fromisoformat(first["arrive_at"]),
                    duration_minutes=first["duration_minutes"],
                    prices=[
                        SeatPrice(seat_type=row["seat_type"], price=Decimal(row["price"]))
                        for row in sorted(train_rows, key=lambda item: item["seat_type"])
                    ],
                    source=first["source"],
                    updated_at=datetime.fromisoformat(first["fetched_at"]),
                )
            )
        segments.sort(key=lambda segment: (segment.lowest_price or Decimal("Infinity"), segment.depart_at, segment.train_no))
        return segments

    def get_min_price(
        self,
        provider: str,
        travel_date: date,
        origin: str,
        destination: str,
        *,
        max_age: timedelta | None = None,
        now: datetime | None = None,
    ) -> StaticOdMinPrice | None:
        now = now or datetime.now(timezone.utc)
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT * FROM od_min_price_snapshot
                WHERE provider = ? AND travel_date = ? AND origin = ? AND destination = ?
                """,
                (provider, travel_date.isoformat(), origin, destination),
            ).fetchone()
        if row is None:
            return None
        if max_age is not None and self._is_stale(row["fetched_at"], max_age, now):
            return None
        return StaticOdMinPrice(
            provider=row["provider"],
            travel_date=date.fromisoformat(row["travel_date"]),
            origin=row["origin"],
            destination=row["destination"],
            min_price=Decimal(row["min_price"]),
            train_code=row["train_code"],
            seat_type=row["seat_type"],
            depart_at=datetime.fromisoformat(row["depart_at"]),
            arrive_at=datetime.fromisoformat(row["arrive_at"]),
            duration_minutes=row["duration_minutes"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def query_train_od_prices(
        self,
        provider: str,
        service_dates: list[date],
        *,
        from_station: str | None = None,
        to_station: str | None = None,
    ) -> list[TrainOdPriceSnapshot]:
        if not service_dates:
            return []

        placeholders = ",".join("?" for _ in service_dates)
        sql = f"""
            SELECT * FROM train_od_price_snapshot
            WHERE provider = ? AND service_date IN ({placeholders})
        """
        params: list[str] = [provider, *(service_date.isoformat() for service_date in service_dates)]
        if from_station is not None:
            sql += " AND from_station = ?"
            params.append(from_station)
        if to_station is not None:
            sql += " AND to_station = ?"
            params.append(to_station)
        sql += " ORDER BY CAST(price AS REAL) ASC, depart_at ASC, train_code ASC, seat_type ASC"

        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(sql, params).fetchall()

        return [
            TrainOdPriceSnapshot(
                provider=row["provider"],
                service_date=date.fromisoformat(row["service_date"]),
                train_no=row["train_no"],
                train_code=row["train_code"],
                from_station=row["from_station"],
                to_station=row["to_station"],
                from_station_no=row["from_station_no"],
                to_station_no=row["to_station_no"],
                depart_at=datetime.fromisoformat(row["depart_at"]),
                arrive_at=datetime.fromisoformat(row["arrive_at"]),
                duration_minutes=row["duration_minutes"],
                seat_type=row["seat_type"],
                price=Decimal(row["price"]),
                source=row["source"],
                fetched_at=datetime.fromisoformat(row["fetched_at"]),
            )
            for row in rows
        ]

    def upsert_train_od_fare_edges(
        self,
        provider: str,
        edges: list[TrainOdFareEdge],
    ) -> None:
        if not edges:
            return

        with sqlite3.connect(self.db_path) as connection:
            connection.executemany(
                """
                INSERT INTO train_od_fare_edge(
                    provider, train_no, train_code,
                    from_station, to_station, from_station_no, to_station_no,
                    depart_time, arrive_time, depart_day_offset, arrive_day_offset,
                    duration_minutes, seat_type, price, currency, source, fetched_at, raw_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CNY', ?, ?, ?)
                ON CONFLICT(provider, train_code, from_station, to_station, seat_type)
                DO UPDATE SET
                    train_no = excluded.train_no,
                    from_station_no = excluded.from_station_no,
                    to_station_no = excluded.to_station_no,
                    depart_time = excluded.depart_time,
                    arrive_time = excluded.arrive_time,
                    depart_day_offset = excluded.depart_day_offset,
                    arrive_day_offset = excluded.arrive_day_offset,
                    duration_minutes = excluded.duration_minutes,
                    price = excluded.price,
                    currency = excluded.currency,
                    source = excluded.source,
                    fetched_at = excluded.fetched_at,
                    raw_hash = excluded.raw_hash
                """,
                [
                    (
                        provider,
                        edge.train_no,
                        edge.train_code,
                        edge.from_station,
                        edge.to_station,
                        edge.from_station_no,
                        edge.to_station_no,
                        edge.depart_time.isoformat(timespec="minutes"),
                        edge.arrive_time.isoformat(timespec="minutes"),
                        edge.depart_day_offset,
                        edge.arrive_day_offset,
                        edge.duration_minutes,
                        edge.seat_type,
                        str(edge.price),
                        edge.source,
                        edge.fetched_at.isoformat(),
                        edge.raw_hash,
                    )
                    for edge in edges
                ],
            )

    def query_train_od_fare_edges(
        self,
        provider: str,
        *,
        from_station: str | None = None,
        to_station: str | None = None,
    ) -> list[TrainOdFareEdge]:
        sql = """
            SELECT * FROM train_od_fare_edge
            WHERE provider = ?
        """
        params: list[str] = [provider]
        if from_station is not None:
            sql += " AND from_station = ?"
            params.append(from_station)
        if to_station is not None:
            sql += " AND to_station = ?"
            params.append(to_station)
        sql += " ORDER BY CAST(price AS REAL) ASC, depart_time ASC, train_code ASC, seat_type ASC"

        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(sql, params).fetchall()

        return [
            TrainOdFareEdge(
                provider=row["provider"],
                train_no=row["train_no"],
                train_code=row["train_code"],
                from_station=row["from_station"],
                to_station=row["to_station"],
                from_station_no=row["from_station_no"],
                to_station_no=row["to_station_no"],
                depart_time=time.fromisoformat(row["depart_time"]),
                arrive_time=time.fromisoformat(row["arrive_time"]),
                depart_day_offset=row["depart_day_offset"],
                arrive_day_offset=row["arrive_day_offset"],
                duration_minutes=row["duration_minutes"],
                seat_type=row["seat_type"],
                price=Decimal(row["price"]),
                source=row["source"],
                fetched_at=datetime.fromisoformat(row["fetched_at"]),
                raw_hash=row["raw_hash"],
            )
            for row in rows
        ]

    def is_stale(
        self,
        provider: str,
        travel_date: date,
        origin: str,
        destination: str,
        max_age: timedelta,
        *,
        now: datetime | None = None,
    ) -> bool:
        min_price = self.get_min_price(provider, travel_date, origin, destination)
        if min_price is None:
            return True
        return self._is_stale(min_price.fetched_at.isoformat(), max_age, now or datetime.now(timezone.utc))

    def _price_rows(self, provider: str, travel_date: date, origin: str, destination: str) -> list[sqlite3.Row]:
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(
                """
                SELECT * FROM od_public_price_snapshot
                WHERE provider = ? AND travel_date = ? AND origin = ? AND destination = ?
                ORDER BY CAST(price AS REAL) ASC, depart_at ASC, train_code ASC, seat_type ASC
                """,
                (provider, travel_date.isoformat(), origin, destination),
            ).fetchall()

    def _refresh_min_price(
        self,
        connection: sqlite3.Connection,
        provider: str,
        travel_date: date,
        origin: str,
        destination: str,
        updated_at: datetime,
    ) -> None:
        row = connection.execute(
            """
            SELECT provider, travel_date, origin, destination, price, train_code, seat_type,
                   depart_at, arrive_at, duration_minutes, fetched_at
            FROM od_public_price_snapshot
            WHERE provider = ? AND travel_date = ? AND origin = ? AND destination = ?
            ORDER BY CAST(price AS REAL) ASC, depart_at ASC, train_code ASC, seat_type ASC
            LIMIT 1
            """,
            (provider, travel_date.isoformat(), origin, destination),
        ).fetchone()
        if row is None:
            return
        connection.execute(
            """
            INSERT INTO od_min_price_snapshot(
                provider, travel_date, origin, destination, min_price,
                train_code, seat_type, depart_at, arrive_at, duration_minutes,
                fetched_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, travel_date, origin, destination)
            DO UPDATE SET
                min_price = excluded.min_price,
                train_code = excluded.train_code,
                seat_type = excluded.seat_type,
                depart_at = excluded.depart_at,
                arrive_at = excluded.arrive_at,
                duration_minutes = excluded.duration_minutes,
                fetched_at = excluded.fetched_at,
                updated_at = excluded.updated_at
            """,
            (*row, updated_at.isoformat()),
        )

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS station_snapshot (
                    provider TEXT NOT NULL,
                    station_name TEXT NOT NULL,
                    station_code TEXT NOT NULL,
                    pinyin TEXT,
                    short_pinyin TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY(provider, station_name)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_station_snapshot_code
                ON station_snapshot(provider, station_code)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS od_public_price_snapshot (
                    provider TEXT NOT NULL,
                    travel_date TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    train_no TEXT NOT NULL,
                    train_code TEXT NOT NULL,
                    depart_at TEXT NOT NULL,
                    arrive_at TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    seat_type TEXT NOT NULL,
                    price TEXT NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'CNY',
                    source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    raw_hash TEXT,
                    PRIMARY KEY(provider, travel_date, origin, destination, train_code, seat_type)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_od_price_query
                ON od_public_price_snapshot(provider, travel_date, origin, destination, price)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_od_price_train
                ON od_public_price_snapshot(provider, travel_date, train_code)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS od_min_price_snapshot (
                    provider TEXT NOT NULL,
                    travel_date TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    min_price TEXT NOT NULL,
                    train_code TEXT NOT NULL,
                    seat_type TEXT NOT NULL,
                    depart_at TEXT NOT NULL,
                    arrive_at TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    fetched_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(provider, travel_date, origin, destination)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_od_min_price_origin_dest
                ON od_min_price_snapshot(provider, origin, destination, min_price)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS crawl_job (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    travel_date TEXT,
                    scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT,
                    finished_at TEXT,
                    error_message TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS crawl_task (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    travel_date TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 100,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    next_retry_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id, travel_date, origin, destination)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_crawl_task_pick
                ON crawl_task(status, priority, next_retry_at, id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS train_service_snapshot (
                    provider TEXT NOT NULL,
                    service_date TEXT NOT NULL,
                    train_no TEXT NOT NULL,
                    train_code TEXT NOT NULL,
                    start_station TEXT NOT NULL,
                    end_station TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    raw_hash TEXT,
                    PRIMARY KEY(provider, service_date, train_no)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_train_service_code
                ON train_service_snapshot(provider, service_date, train_code)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS train_stop_snapshot (
                    provider TEXT NOT NULL,
                    service_date TEXT NOT NULL,
                    train_no TEXT NOT NULL,
                    train_code TEXT NOT NULL,
                    station_name TEXT NOT NULL,
                    station_no INTEGER NOT NULL,
                    arrive_at TEXT,
                    depart_at TEXT,
                    arrive_day_diff INTEGER NOT NULL DEFAULT 0,
                    running_time TEXT,
                    fetched_at TEXT NOT NULL,
                    raw_hash TEXT,
                    PRIMARY KEY(provider, service_date, train_no, station_no)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_train_stop_station_time
                ON train_stop_snapshot(provider, service_date, station_name, depart_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_train_stop_train
                ON train_stop_snapshot(provider, service_date, train_no, station_no)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS train_od_price_snapshot (
                    provider TEXT NOT NULL,
                    service_date TEXT NOT NULL,
                    train_no TEXT NOT NULL,
                    train_code TEXT NOT NULL,
                    from_station TEXT NOT NULL,
                    to_station TEXT NOT NULL,
                    from_station_no INTEGER NOT NULL,
                    to_station_no INTEGER NOT NULL,
                    depart_at TEXT NOT NULL,
                    arrive_at TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    seat_type TEXT NOT NULL,
                    price TEXT NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'CNY',
                    source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    raw_hash TEXT,
                    PRIMARY KEY(
                        provider,
                        service_date,
                        train_code,
                        from_station,
                        to_station,
                        seat_type
                    )
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_train_od_price_query
                ON train_od_price_snapshot(provider, service_date, from_station, to_station, price)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_train_od_price_train
                ON train_od_price_snapshot(provider, service_date, train_no, from_station_no, to_station_no)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS train_od_fare_edge (
                    provider TEXT NOT NULL,
                    train_no TEXT NOT NULL,
                    train_code TEXT NOT NULL,
                    from_station TEXT NOT NULL,
                    to_station TEXT NOT NULL,
                    from_station_no INTEGER NOT NULL,
                    to_station_no INTEGER NOT NULL,
                    depart_time TEXT NOT NULL,
                    arrive_time TEXT NOT NULL,
                    depart_day_offset INTEGER NOT NULL DEFAULT 0,
                    arrive_day_offset INTEGER NOT NULL DEFAULT 0,
                    duration_minutes INTEGER NOT NULL,
                    seat_type TEXT NOT NULL,
                    price TEXT NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'CNY',
                    source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    raw_hash TEXT,
                    PRIMARY KEY(
                        provider,
                        train_code,
                        from_station,
                        to_station,
                        seat_type
                    )
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_train_od_fare_edge_from
                ON train_od_fare_edge(provider, from_station, price)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_train_od_fare_edge_train
                ON train_od_fare_edge(provider, train_no, from_station_no, to_station_no)
                """
            )

    def _is_stale(self, fetched_at: str, max_age: timedelta, now: datetime) -> bool:
        fetched = datetime.fromisoformat(fetched_at)
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now - fetched > max_age