from datetime import date

import pytest

from app.adapters.mock_provider import MockTrainDataProvider
from app.domain.models import RouteQuery
from app.services.route_search import RouteSearchService


class CountingMockTrainDataProvider(MockTrainDataProvider):
    def __init__(self) -> None:
        super().__init__()
        self.search_count = 0

    async def search_segments(self, from_station, to_station, query):
        self.search_count += 1
        return await super().search_segments(from_station, to_station, query)


@pytest.mark.asyncio
async def test_search_returns_lowest_price_first() -> None:
    service = RouteSearchService(MockTrainDataProvider())
    query = RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1))

    response = await service.search(query)

    assert response.plans
    assert response.plans[0].total_price <= response.plans[1].total_price
    assert response.plans[0].transfer_stations == ["南京南"]


@pytest.mark.asyncio
async def test_min_transfer_filter_removes_tight_transfer() -> None:
    service = RouteSearchService(MockTrainDataProvider())
    query = RouteQuery(
        from_station="北京",
        to_station="上海",
        date=date(2026, 7, 1),
        min_transfer_minutes=40,
    )

    response = await service.search(query)

    assert all(plan.transfer_minutes >= 40 for plan in response.plans if plan.transfer_stations)
    assert ["天津南"] not in [plan.transfer_stations for plan in response.plans]


@pytest.mark.asyncio
async def test_no_route_returns_empty_list() -> None:
    service = RouteSearchService(MockTrainDataProvider())
    query = RouteQuery(from_station="杭州东", to_station="北京", date=date(2026, 7, 1))

    response = await service.search(query)

    assert response.plans == []


@pytest.mark.asyncio
async def test_search_uses_segment_cache_for_repeated_query() -> None:
    provider = CountingMockTrainDataProvider()
    service = RouteSearchService(provider)
    query = RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1))

    await service.search(query)
    first_search_count = provider.search_count
    await service.search(query)

    assert first_search_count > 0
    assert provider.search_count == first_search_count
