import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

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
async def provider_status() -> dict[str, object]:
    return {
        "provider": provider.name,
        "status": "ok",
        "transfer_candidate_enabled": provider.name != "mock",
        "max_remote_queries": route_search_service.max_remote_queries,
        "max_concurrent_remote_queries": route_search_service.max_concurrent_remote_queries,
        "last_remote_query_count": route_search_service.remote_query_count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/routes/search", response_model=RouteSearchResponse)
async def search_routes(query: RouteQuery) -> RouteSearchResponse:
    try:
        return await route_search_service.search(query)
    except Railway12306Error as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/routes/search/stream")
async def stream_routes(query: RouteQuery) -> StreamingResponse:
    async def generate():
        try:
            async for plan in route_search_service.stream(query):
                yield plan.model_dump_json() + "\n"
        except Railway12306Error as exc:
            yield json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")
