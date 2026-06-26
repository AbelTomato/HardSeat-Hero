import asyncio
import heapq
import json
import os
from bisect import bisect_left
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app.adapters.base import TrainDataProvider, TrainDataProviderError
from app.domain.models import RouteQuery, RouteSearchResponse, TrainSegment, TransferPlan
from app.services.cache import SqliteOdCache, TtlCache


class BestPriceTracker:
    def __init__(self) -> None:
        self._value: Decimal | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> Decimal | None:
        async with self._lock:
            return self._value

    async def update(self, price: Decimal) -> None:
        async with self._lock:
            if self._value is None or price < self._value:
                self._value = price


@dataclass(frozen=True)
class PathSearchState:
    current_station: str
    segments: tuple[TrainSegment, ...]
    accumulated_price: Decimal
    arrive_at: datetime | None
    visited_stations: frozenset[str]

    @property
    def transfer_count(self) -> int:
        return max(0, len(self.segments) - 1)


@dataclass(frozen=True)
class FrontierEntry:
    price: Decimal
    arrive_at: datetime


class ParetoFrontier:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, int], list[FrontierEntry]] = {}

    def add_if_not_dominated(self, state: PathSearchState) -> bool:
        if state.arrive_at is None:
            return True

        key = (state.current_station, state.transfer_count)
        entries = self._entries.setdefault(key, [])
        for entry in entries:
            if entry.price <= state.accumulated_price and entry.arrive_at <= state.arrive_at:
                return False

        entries[:] = [
            entry
            for entry in entries
            if not (state.accumulated_price <= entry.price and state.arrive_at <= entry.arrive_at)
        ]
        entries.append(FrontierEntry(price=state.accumulated_price, arrive_at=state.arrive_at))
        return True


class CheapestPathSearcher:
    def __init__(self, route_search: "RouteSearchService") -> None:
        self.route_search = route_search

    async def search(self, query: RouteQuery) -> list[TransferPlan]:
        candidates = [
            station
            for station in self.route_search.provider.candidate_transfer_stations(query)
            if station not in {query.from_station, query.to_station}
        ]
        best_price: Decimal | None = None
        plans: list[TransferPlan] = []
        queue: list[tuple[Decimal, int, PathSearchState]] = []
        counter = 0
        initial_state = PathSearchState(
            current_station=query.from_station,
            segments=(),
            accumulated_price=Decimal("0"),
            arrive_at=None,
            visited_stations=frozenset({query.from_station}),
        )
        heapq.heappush(queue, (Decimal("0"), counter, initial_state))
        frontier = ParetoFrontier()
        while queue and len(plans) < 20:
            priority, _, state = heapq.heappop(queue)
            if best_price is not None and priority >= best_price:
                break

            for next_station in self._next_stations(state, query, candidates):
                try:
                    segments = await self._candidate_segments(state, next_station, query)
                except TrainDataProviderError:
                    continue

                for segment in segments:
                    next_price = state.accumulated_price + (segment.lowest_price or Decimal("0"))
                    if best_price is not None and next_price >= best_price:
                        continue
                    next_segments = (*state.segments, segment)
                    if next_station == query.to_station:
                        plan = self.route_search._build_plan(list(next_segments))
                        if plan.total_duration_minutes <= query.max_total_duration_minutes:
                            best_price = plan.total_price if best_price is None else min(best_price, plan.total_price)
                            plans.append(plan)
                        continue

                    next_state = PathSearchState(
                        current_station=next_station,
                        segments=next_segments,
                        accumulated_price=next_price,
                        arrive_at=segment.arrive_at,
                        visited_stations=state.visited_stations | {next_station},
                    )
                    if next_state.transfer_count > query.max_transfers:
                        continue
                    if not frontier.add_if_not_dominated(next_state):
                        continue
                    counter += 1
                    heapq.heappush(queue, (next_price + self._heuristic_lower_bound(next_station, query.to_station), counter, next_state))

        plans.sort(key=lambda plan: (plan.total_price, plan.total_duration_minutes, len(plan.transfer_stations)))
        return plans[:20]

    def _next_stations(self, state: PathSearchState, query: RouteQuery, candidates: list[str]) -> list[str]:
        stations = [query.to_station]
        if state.transfer_count < query.max_transfers:
            stations.extend(station for station in candidates if station not in state.visited_stations)
        return stations

    async def _candidate_segments(
        self,
        state: PathSearchState,
        next_station: str,
        query: RouteQuery,
    ) -> list[TrainSegment]:
        day_offsets = (0,) if not state.segments else (0, 1)
        segments = await self.route_search._search_transfer_window_segments(
            state.current_station,
            next_station,
            query,
            day_offsets=day_offsets,
        )
        segments = self.route_search._priced_segments_sorted_by_departure(segments)
        if state.arrive_at is None:
            return segments
        earliest_departure = state.arrive_at + timedelta(minutes=query.min_transfer_minutes)
        start_index = bisect_left([segment.depart_at for segment in segments], earliest_departure)
        return segments[start_index:]

    def _heuristic_lower_bound(self, from_station: str, to_station: str) -> Decimal:
        return Decimal("0")


class RouteSearchService:
    def __init__(
        self,
        provider: TrainDataProvider,
        cache_ttl_seconds: int = 600,
        max_remote_queries: int = 120,
        max_concurrent_remote_queries: int = 5,
        remote_query_interval_seconds: float | None = None,
        persistent_cache: SqliteOdCache[list[TrainSegment]] | None = None,
    ) -> None:
        self.provider = provider
        self.segment_cache: TtlCache[list[TrainSegment]] = TtlCache(cache_ttl_seconds)
        self.persistent_segment_cache = persistent_cache or self._default_persistent_cache(cache_ttl_seconds)
        self.max_remote_queries = max_remote_queries
        self.max_concurrent_remote_queries = max_concurrent_remote_queries
        self._remote_query_semaphore = asyncio.Semaphore(max_concurrent_remote_queries)
        self._remote_query_lock = asyncio.Lock()
        self.remote_query_interval_seconds = (
            remote_query_interval_seconds if remote_query_interval_seconds is not None else self._default_remote_query_interval()
        )
        self.cheapest_path_searcher = CheapestPathSearcher(self)
        self.remote_query_count = 0

    async def search(self, query: RouteQuery) -> RouteSearchResponse:
        self.remote_query_count = 0
        plans = [plan async for plan in self._iter_plans(query)]
        plans.sort(key=lambda plan: (plan.total_price, plan.total_duration_minutes, len(plan.transfer_stations)))

        return RouteSearchResponse(
            query_id=f"{query.date.isoformat()}:{query.from_station}:{query.to_station}",
            source=self.provider.name,
            updated_at=datetime.now(timezone.utc),
            plans=plans[:20],
        )

    async def stream(self, query: RouteQuery) -> AsyncIterator[TransferPlan]:
        self.remote_query_count = 0
        emitted: list[TransferPlan] = []
        async for plan in self._iter_plans(query):
            emitted.append(plan)
            emitted.sort(key=lambda item: (item.total_price, item.total_duration_minutes, len(item.transfer_stations)))
            if plan in emitted[:20]:
                yield plan

    async def _iter_plans(self, query: RouteQuery) -> AsyncIterator[TransferPlan]:
        if query.max_transfers >= 2:
            for plan in await self.cheapest_path_searcher.search(query):
                yield plan
            return

        best_price = BestPriceTracker()
        direct_segments = await self._search_segments(query.from_station, query.to_station, query)
        for segment in direct_segments:
            if segment.lowest_price is not None:
                plan = self._build_plan([segment])
                if plan.total_duration_minutes <= query.max_total_duration_minutes:
                    await best_price.update(plan.total_price)
                    yield plan

        if query.max_transfers >= 1:
            async for plan in self._iter_one_transfer(query, best_price):
                await best_price.update(plan.total_price)
                yield plan

    async def _search_segments(
        self,
        from_station: str,
        to_station: str,
        query: RouteQuery,
    ) -> list[TrainSegment]:
        cache_key = self._segment_cache_key(from_station, to_station, query)
        cached_segments = self.segment_cache.get(cache_key)
        if cached_segments is not None:
            return cached_segments

        if self.persistent_segment_cache is not None:
            persisted_segments = self.persistent_segment_cache.get(cache_key)
            if persisted_segments is not None:
                self.segment_cache.set(cache_key, persisted_segments)
                return persisted_segments

        async with self._remote_query_lock:
            if self.remote_query_count >= self.max_remote_queries:
                return []

            should_wait = self.remote_query_interval_seconds > 0 and self.remote_query_count > 0
            self.remote_query_count += 1

        if should_wait:
            await asyncio.sleep(self.remote_query_interval_seconds)

        async with self._remote_query_semaphore:
            segments = await self.provider.search_segments(from_station, to_station, query)
        self.segment_cache.set(cache_key, segments)
        if self.persistent_segment_cache is not None:
            self.persistent_segment_cache.set(cache_key, segments)
        return segments

    async def _iter_one_transfer(self, query: RouteQuery, best_price: BestPriceTracker) -> AsyncIterator[TransferPlan]:
        tasks = [
            asyncio.create_task(self._one_transfer_plans_for_station(query, transfer_station, best_price))
            for transfer_station in self.provider.candidate_transfer_stations(query)
        ]
        for task in asyncio.as_completed(tasks):
            try:
                plans = await task
            except TrainDataProviderError:
                continue
            for plan in plans:
                yield plan

    async def _one_transfer_plans_for_station(
        self,
        query: RouteQuery,
        transfer_station: str,
        best_price: BestPriceTracker,
    ) -> list[TransferPlan]:
        first_legs = await self._search_transfer_window_segments(
            query.from_station,
            transfer_station,
            query,
            day_offsets=(-1, 0),
        )
        first_legs = self._priced_segments_sorted_by_arrival(first_legs)
        if not first_legs:
            return []

        current_best_price = await best_price.get()
        first_legs = self._segments_that_can_improve_best_price(first_legs, current_best_price)
        if not first_legs:
            return []

        second_legs = await self._search_transfer_window_segments(
            transfer_station,
            query.to_station,
            query,
            day_offsets=(0, 1),
        )
        second_legs = self._priced_segments_sorted_by_departure(second_legs)
        if not second_legs:
            return []

        plans: list[TransferPlan] = []
        second_departures = [segment.depart_at for segment in second_legs]
        for first in first_legs:
            current_best_price = await best_price.get()
            if first.lowest_price is not None and current_best_price is not None and first.lowest_price >= current_best_price:
                continue
            earliest_departure = first.arrive_at + timedelta(minutes=query.min_transfer_minutes)
            start_index = bisect_left(second_departures, earliest_departure)
            for second in second_legs[start_index:]:
                plan = self._build_plan([first, second])
                if plan.total_duration_minutes <= query.max_total_duration_minutes:
                    current_best_price = await best_price.get()
                    if current_best_price is not None and plan.total_price >= current_best_price:
                        continue
                    await best_price.update(plan.total_price)
                    plans.append(plan)
        return plans

    async def _search_transfer_window_segments(
        self,
        from_station: str,
        to_station: str,
        query: RouteQuery,
        day_offsets: tuple[int, ...],
    ) -> list[TrainSegment]:
        segments: list[TrainSegment] = []
        for offset in day_offsets:
            window_query = query.model_copy(update={"date": query.date + timedelta(days=offset)})
            segments.extend(await self._search_segments(from_station, to_station, window_query))
        return segments

    def _priced_segments_sorted_by_arrival(self, segments: list[TrainSegment]) -> list[TrainSegment]:
        return sorted(
            [segment for segment in segments if segment.lowest_price is not None],
            key=lambda segment: segment.arrive_at,
        )

    def _priced_segments_sorted_by_departure(self, segments: list[TrainSegment]) -> list[TrainSegment]:
        return sorted(
            [segment for segment in segments if segment.lowest_price is not None],
            key=lambda segment: segment.depart_at,
        )

    def _segments_that_can_improve_best_price(
        self,
        segments: list[TrainSegment],
        best_price: Decimal | None,
    ) -> list[TrainSegment]:
        if best_price is None:
            return segments
        return [segment for segment in segments if segment.lowest_price is not None and segment.lowest_price < best_price]

    def _build_plan(self, segments: list[TrainSegment]) -> TransferPlan:
        total_price = sum([segment.lowest_price or Decimal("0") for segment in segments], Decimal("0"))
        total_duration = max(0, int((segments[-1].arrive_at - segments[0].depart_at).total_seconds() // 60))
        ride_minutes = sum(segment.duration_minutes for segment in segments)
        transfer_stations = [segment.to_station for segment in segments[:-1]]
        return TransferPlan(
            total_price=total_price,
            total_duration_minutes=total_duration,
            transfer_minutes=max(0, total_duration - ride_minutes),
            transfer_stations=transfer_stations,
            segments=segments,
        )

    def _segment_cache_key(self, from_station: str, to_station: str, query: RouteQuery) -> str:
        return ":".join(
            [
                self.provider.name,
                query.date.isoformat(),
                from_station,
                to_station,
            ]
        )

    def _default_remote_query_interval(self) -> float:
        if self.provider.name == "mock":
            return 0
        return 0.05

    def _default_persistent_cache(self, ttl_seconds: int) -> SqliteOdCache[list[TrainSegment]] | None:
        cache_path = os.getenv("ROUTE_SEGMENT_CACHE_DB", "").strip()
        if not cache_path:
            return None
        cache_ttl_seconds = int(os.getenv("ROUTE_SEGMENT_CACHE_TTL_SECONDS", str(ttl_seconds)))
        return SqliteOdCache[list[TrainSegment]](
            Path(cache_path),
            ttl_seconds=cache_ttl_seconds,
            serializer=serialize_train_segments,
            deserializer=deserialize_train_segments,
        )


def serialize_train_segments(segments: list[TrainSegment]) -> str:
    return json.dumps([segment.model_dump(mode="json") for segment in segments], ensure_ascii=False)


def deserialize_train_segments(value: str) -> list[TrainSegment]:
    payload = json.loads(value)
    if not isinstance(payload, list):
        raise ValueError("cached train segments must be a list")
    return [TrainSegment.model_validate(item) for item in payload]
