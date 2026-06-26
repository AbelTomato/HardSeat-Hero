from abc import ABC, abstractmethod

from app.domain.models import RouteQuery, TrainSegment


class TrainDataProviderError(RuntimeError):
    pass


class TrainDataProvider(ABC):
    name: str

    @abstractmethod
    async def search_segments(
        self,
        from_station: str,
        to_station: str,
        query: RouteQuery,
    ) -> list[TrainSegment]:
        raise NotImplementedError

    @abstractmethod
    def candidate_transfer_stations(self, query: RouteQuery) -> list[str]:
        raise NotImplementedError
