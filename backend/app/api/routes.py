import json
from datetime import datetime, timezone
from time import perf_counter

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.adapters.base import (
    TrainDataBadRequestError,
    TrainDataConfigurationError,
    TrainDataProviderError,
)
from app.adapters.provider_factory import create_train_data_provider
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

    try:        # 防止search(q)抛异常直接冒泡到500 Interval Server Error
        stations = search(q)
        if hasattr(stations, "__await__"):
            stations = await stations
        return StationSearchResponse(stations=stations)
    except TrainDataProviderError as exc:
        raise HTTPException(status_code=provider_error_status_code(exc), detail=str(exc)) from exc


@router.get("/providers/status")
async def provider_status() -> dict[str, object]:
    return {
        "provider": provider.name,
        "status": "ok",
        "transfer_candidate_enabled": provider.name != "mock",
        "max_remote_queries": route_search_service.max_remote_queries,
        "max_concurrent_remote_queries": route_search_service.max_concurrent_remote_queries,
        "last_remote_query_count": route_search_service.remote_query_count,
        "last_diagnostics": route_search_service.last_diagnostics.as_dict(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/routes/search", response_model=RouteSearchResponse)
async def search_routes(query: RouteQuery) -> RouteSearchResponse:
    try:
        return await route_search_service.search(query)
    except TrainDataProviderError as exc:
        raise HTTPException(status_code=provider_error_status_code(exc), detail=str(exc)) from exc


@router.post("/routes/search/stream")
async def stream_routes(query: RouteQuery) -> StreamingResponse:
    async def generate():
        started_at = perf_counter()
        try:
            async for snapshot in route_search_service.stream_snapshots(query):
                payload = {
                    **snapshot,
                    "elapsed_ms": max(0, round((perf_counter() - started_at) * 1000)),
                    "plans": [plan.model_dump(mode="json") for plan in snapshot["plans"]],
                    "updated_at": snapshot["updated_at"].isoformat(),
                }
                yield json.dumps(payload, ensure_ascii=False) + "\n"
            yield json.dumps(
                {
                    "type": "metadata",
                    "query_id": f"{query.date.isoformat()}:{query.from_station}:{query.to_station}",
                    "source": route_search_service.provider.name,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "elapsed_ms": max(0, round((perf_counter() - started_at) * 1000)),
                },
                ensure_ascii=False,
            ) + "\n"
        except TrainDataProviderError as exc:
            yield json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


def provider_error_status_code(exc: TrainDataProviderError) -> int:
    if isinstance(exc, TrainDataBadRequestError):
        return 400
    if isinstance(exc, TrainDataConfigurationError):
        return 503
    return 502
