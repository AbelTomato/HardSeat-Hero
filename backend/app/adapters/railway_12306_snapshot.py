from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from app.adapters.railway_12306_public_price import (
    BASE_URL,
    PublicPriceClient,
    Railway12306Error,
    StationCodeRepository,
    describe_httpx_error,
    request_headers,
)


TRAIN_NAME_URL = f"{BASE_URL}/otn/queryTrainInfo/getTrainName"
TRAIN_STOPS_URL = f"{BASE_URL}/otn/queryTrainInfo/query"


@dataclass(frozen=True)
class SnapshotTrainName:
    train_no: str
    train_code: str


@dataclass(frozen=True)
class SnapshotTrainStop:
    station_name: str
    station_train_code: str
    station_no: int
    arrive_time: str | None
    start_time: str | None
    arrive_day_diff: int
    running_time: str | None
    start_station_name: str
    end_station_name: str


class Railway12306SnapshotClient:
    def __init__(
        self,
        *,
        station_codes: StationCodeRepository | None = None,
        timeout_seconds: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.station_codes = station_codes or StationCodeRepository(timeout_seconds=timeout_seconds)
        self.timeout_seconds = timeout_seconds
        self.http_client = http_client
        self.public_price_client = PublicPriceClient(self.station_codes, timeout_seconds=timeout_seconds)

    async def fetch_train_names(self, service_date: date) -> list[SnapshotTrainName]:
        payload = await self._get_json(TRAIN_NAME_URL, params={"date": service_date.isoformat()})
        rows = _extract_data_rows(payload)
        by_train_no: dict[str, SnapshotTrainName] = {}
        for row in rows:
            train_no = str(row.get("train_no") or "").strip()
            train_code = str(row.get("station_train_code") or row.get("train_code") or "").strip()
            if train_no and train_code and train_no not in by_train_no:
                by_train_no[train_no] = SnapshotTrainName(train_no=train_no, train_code=_normalize_train_code(train_code))
        return list(by_train_no.values())

    async def fetch_train_stops(self, service_date: date, train_no: str) -> list[SnapshotTrainStop]:
        payload = await self._get_json(
            TRAIN_STOPS_URL,
            params={
                "leftTicketDTO.train_no": train_no,
                "leftTicketDTO.train_date": service_date.isoformat(),
                "rand_code": "",
            },
        )
        rows = _extract_data_rows(payload)
        stops = [_parse_stop(row) for row in rows]
        return sorted(stops, key=lambda stop: stop.station_no)

    async def query_public_prices(self, service_date: date, from_station: str, to_station: str) -> list[dict[str, Any]]:
        from app.domain.models import RouteQuery

        query = RouteQuery(from_station=from_station, to_station=to_station, date=service_date, max_transfers=0)
        return await self.public_price_client.query(from_station, to_station, query)

    async def _get_json(self, url: str, *, params: dict[str, str]) -> dict[str, Any]:
        owns_client = self.http_client is None
        client = self.http_client or httpx.AsyncClient(timeout=self.timeout_seconds, headers=request_headers())
        try:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise Railway12306Error(f"全量快照接口请求失败：{describe_httpx_error(exc)}") from exc
            try:
                payload = response.json()
            except ValueError as exc:
                raise Railway12306Error("全量快照接口返回非 JSON") from exc
            if not isinstance(payload, dict):
                raise Railway12306Error("全量快照接口 JSON 根节点不是对象")
            if payload.get("status") is False:
                raise Railway12306Error(f"全量快照接口返回失败：{payload}")
            return payload
        finally:
            if owns_client:
                await client.aclose()


def _extract_data_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", [])
    if isinstance(data, dict):
        data = data.get("data", [])
    if not isinstance(data, list):
        raise Railway12306Error("全量快照接口 data 不是列表")
    return [row for row in data if isinstance(row, dict)]


def _parse_stop(row: dict[str, Any]) -> SnapshotTrainStop:
    return SnapshotTrainStop(
        station_name=str(row.get("station_name") or ""),
        station_train_code=_normalize_train_code(str(row.get("station_train_code") or "")),
        station_no=int(str(row.get("station_no") or "0")),
        arrive_time=_normalize_time_text(row.get("arrive_time")),
        start_time=_normalize_time_text(row.get("start_time")),
        arrive_day_diff=int(str(row.get("arrive_day_diff") or "0")),
        running_time=_normalize_time_text(row.get("running_time")),
        start_station_name=str(row.get("start_station_name") or ""),
        end_station_name=str(row.get("end_station_name") or ""),
    )


def _normalize_train_code(value: str) -> str:
    return value.split("(", 1)[0].strip()


def _normalize_time_text(value: Any) -> str | None:
    if value in (None, "", "----"):
        return None
    return str(value)