from datetime import datetime, timezone

from fastapi import APIRouter

from app.adapters.mock_provider import MockTrainDataProvider
from app.domain.models import RouteQuery, RouteSearchResponse, StationSearchResponse
from app.services.route_search import RouteSearchService


router = APIRouter()
provider = MockTrainDataProvider()
route_search_service = RouteSearchService(provider)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "hardseat-hero-api"}


@router.get("/stations/search", response_model=StationSearchResponse)
async def search_stations(q: str = "") -> StationSearchResponse:
    return StationSearchResponse(stations=provider.search_stations(q))


@router.get("/providers/status")
async def provider_status() -> dict[str, str]:
    return {
        "provider": provider.name,
        "status": "ok",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/routes/search", response_model=RouteSearchResponse)
async def search_routes(query: RouteQuery) -> RouteSearchResponse:
    return await route_search_service.search(query)
