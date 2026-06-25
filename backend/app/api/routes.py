from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.adapters.provider_factory import create_train_data_provider
from app.adapters.railway_12306_public_price import Railway12306Error
from app.domain.models import RouteQuery, RouteSearchResponse, StationSearchResponse
from app.services.route_search import RouteSearchService


router = APIRouter()
provider = create_train_data_provider()
route_search_service = RouteSearchService(provider)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "hardseat-hero-api"}


@router.get("/stations/search", response_model=StationSearchResponse)
async def search_stations(q: str = "") -> StationSearchResponse:
    search = getattr(provider, "search_stations", None)
    if search is None:
        return StationSearchResponse(stations=[])
    stations = search(q)
    if hasattr(stations, "__await__"):
        stations = await stations
    return StationSearchResponse(stations=stations)


@router.get("/providers/status")
async def provider_status() -> dict[str, str]:
    return {
        "provider": provider.name,
        "status": "ok",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/routes/search", response_model=RouteSearchResponse)
async def search_routes(query: RouteQuery) -> RouteSearchResponse:
    try:
        return await route_search_service.search(query)
    except Railway12306Error as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
