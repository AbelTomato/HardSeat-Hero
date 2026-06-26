from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from app.adapters.base import TrainDataProvider
from app.domain.models import RouteQuery, TrainSegment
from app.services.static_price_repository import SQLiteStaticPriceRepository
from app.services.transfer_candidates import CandidateTransferStationGenerator


class LocalStaticPriceProvider(TrainDataProvider):
    name = "static-price"

    def __init__(
        self,
        repository: SQLiteStaticPriceRepository | None = None,
        *,
        db_path: str | Path | None = None,
        max_age: timedelta | None = None,
        transfer_generator: CandidateTransferStationGenerator | None = None,
    ) -> None:
        if repository is None and db_path is None:
            raise ValueError("repository or db_path is required")
        self.repository = repository or SQLiteStaticPriceRepository(db_path or "")
        self.max_age = max_age
        self.transfer_generator = transfer_generator or CandidateTransferStationGenerator()

    async def search_segments(
        self,
        from_station: str,
        to_station: str,
        query: RouteQuery,
    ) -> list[TrainSegment]:
        return self.repository.query_segments(
            self.name,
            query.date,
            from_station,
            to_station,
            max_age=self.max_age,
        )

    def candidate_transfer_stations(self, query: RouteQuery) -> list[str]:
        return self.transfer_generator.generate(query)