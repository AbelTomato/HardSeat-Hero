from __future__ import annotations

from app.adapters.base import TrainDataProvider
from app.adapters.static_price_provider import LocalStaticPriceProvider
from app.domain.models import RouteQuery, TrainSegment
from app.services.static_price_repository import SQLiteStaticPriceRepository


class StaticPriceFallbackProvider(TrainDataProvider):
    name = "static-price"

    def __init__(
        self,
        local_provider: LocalStaticPriceProvider,
        fallback_provider: TrainDataProvider,
        repository: SQLiteStaticPriceRepository,
    ) -> None:
        self.local_provider = local_provider
        self.fallback_provider = fallback_provider
        self.repository = repository

    async def search_segments(
        self,
        from_station: str,
        to_station: str,
        query: RouteQuery,
    ) -> list[TrainSegment]:
        local_segments = await self.local_provider.search_segments(from_station, to_station, query)
        if local_segments:
            return local_segments

        remote_segments = await self.fallback_provider.search_segments(from_station, to_station, query)
        if remote_segments:
            self.repository.upsert_segments(
                self.name,
                query.date,
                from_station,
                to_station,
                remote_segments,
            )
        return remote_segments

    def candidate_transfer_stations(self, query: RouteQuery) -> list[str]:
        return self.fallback_provider.candidate_transfer_stations(query)