from collections.abc import AsyncIterator
from typing import Protocol

from app.domain.models import RouteQuery, RouteSearchResponse
from app.services.route_search import StreamSnapshot


class RouteSearchEngine(Protocol):
    @property
    def source(self) -> str:
        ...

    @property
    def status(self) -> dict[str, object]:
        ...

    async def search(self, query: RouteQuery) -> RouteSearchResponse:
        ...

    def stream_snapshots(self, query: RouteQuery) -> AsyncIterator[StreamSnapshot]:
        ...