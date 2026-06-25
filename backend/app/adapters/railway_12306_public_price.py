from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol

import httpx

from app.adapters.base import TrainDataProvider
from app.domain.models import RouteQuery, SeatPrice, TrainSegment
from app.services.cache import TtlCache


BASE_URL = "https://kyfw.12306.cn"
PUBLIC_QUERY_PAGE_URL = f"{BASE_URL}/otn/view/queryPublicIndex.html"
PUBLIC_PRICE_URL = f"{BASE_URL}/otn/leftTicketPrice/queryAllPublicPrice"
STATION_URLS = (
    f"{BASE_URL}/otn/resources/js/framework/station_name.js",
    f"{BASE_URL}/otn/personalJS/core/common/station_name_new.js",
)

PRICE_FIELDS = {
    "swz_price": "商务座",
    "zy_price": "一等座",
    "ze_price": "二等座",
    "yz_price": "硬座",
    "yw_price": "硬卧",
    "rw_price": "软卧",
    "gr_price": "高级软卧",
}


class Railway12306Error(RuntimeError):
    pass


class PublicPriceQueryClient(Protocol):
    async def query(self, from_station: str, to_station: str, query: RouteQuery) -> list[dict[str, Any]]:
        pass


def parse_station_codes(text: str) -> dict[str, str]:
    match = re.search(r"'(?P<data>[^']+)'", text)
    if not match:
        raise Railway12306Error("车站列表响应格式不符合预期")

    stations: dict[str, str] = {}
    for row in match.group("data").split("@"):
        if not row:
            continue
        parts = row.split("|")
        if len(parts) >= 3:
            stations[parts[1]] = parts[2]
    return stations


def parse_price(value: Any) -> Decimal | None:
    if value in (None, "", "--"):
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    return Decimal(text) / Decimal("10")


def parse_duration_minutes(value: str) -> int:
    parts = value.split(":")
    if len(parts) != 2:
        raise Railway12306Error(f"历时格式不符合预期：{value}")
    hours, minutes = (int(part) for part in parts)
    return hours * 60 + minutes


def parse_day_difference(value: Any) -> int:
    if value in (None, ""):
        return 0
    return int(value)


def build_segment_from_public_price(row: dict[str, Any], query: RouteQuery, updated_at: datetime) -> TrainSegment | None:
    dto = row.get("queryLeftNewDTO")
    if not isinstance(dto, dict):
        raise Railway12306Error("缺少 queryLeftNewDTO")

    prices = [
        SeatPrice(seat_type=seat_type, price=price, remaining="unknown")
        for field, seat_type in PRICE_FIELDS.items()
        if (price := parse_price(dto.get(field))) is not None
    ]
    if not prices:
        return None

    depart_time = time.fromisoformat(str(dto.get("start_time", "")))
    arrive_time = time.fromisoformat(str(dto.get("arrive_time", "")))
    day_difference = parse_day_difference(dto.get("day_difference"))
    depart_at = datetime.combine(query.date, depart_time, tzinfo=timezone.utc)
    arrive_at = datetime.combine(query.date + timedelta(days=day_difference), arrive_time, tzinfo=timezone.utc)
    duration_minutes = parse_duration_minutes(str(dto.get("lishi", "")))

    return TrainSegment(
        train_no=str(dto.get("station_train_code") or dto.get("train_no") or ""),
        from_station=str(dto.get("from_station_name") or query.from_station),
        to_station=str(dto.get("to_station_name") or query.to_station),
        depart_at=depart_at,
        arrive_at=arrive_at,
        duration_minutes=duration_minutes,
        prices=prices,
        source="12306-public-price",
        updated_at=updated_at,
    )


class StationCodeRepository:
    def __init__(self, timeout_seconds: float = 10.0, cache_ttl_seconds: int = 86400) -> None:
        self.timeout_seconds = timeout_seconds
        self.cache: TtlCache[dict[str, str]] = TtlCache(cache_ttl_seconds)

    async def get_station_codes(self) -> dict[str, str]:
        cached = self.cache.get("station-codes")
        if cached is not None:
            return cached

        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=request_headers()) as client:
            for url in STATION_URLS:
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    station_codes = parse_station_codes(response.text)
                    self.cache.set("station-codes", station_codes)
                    return station_codes
                except (httpx.HTTPError, Railway12306Error) as exc:
                    last_error = exc
        raise Railway12306Error(f"无法获取车站电报码：{last_error}")

    async def search_stations(self, keyword: str) -> list[str]:
        stations = sorted((await self.get_station_codes()).keys())
        if not keyword:
            return stations[:20]
        return [station for station in stations if keyword in station][:20]


class PublicPriceClient:
    def __init__(self, station_codes: StationCodeRepository, timeout_seconds: float = 10.0) -> None:
        self.station_codes = station_codes
        self.timeout_seconds = timeout_seconds

    async def query(self, from_station: str, to_station: str, query: RouteQuery) -> list[dict[str, Any]]:
        station_codes = await self.station_codes.get_station_codes()
        try:
            from_code = station_codes[from_station]
            to_code = station_codes[to_station]
        except KeyError as exc:
            raise Railway12306Error(f"站名无法映射电报码：{exc}") from exc

        params = {
            "leftTicketDTO.train_date": query.date.isoformat(),
            "leftTicketDTO.from_station": from_code,
            "leftTicketDTO.to_station": to_code,
            "purpose_codes": "ADULT",
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=request_headers()) as client:
            try:
                await client.get(PUBLIC_QUERY_PAGE_URL)
                response = await client.get(PUBLIC_PRICE_URL, params=params)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise Railway12306Error(f"票价接口请求失败：{exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise Railway12306Error("票价接口返回非 JSON") from exc
        if not payload.get("status"):
            raise Railway12306Error(f"票价接口返回失败：{payload}")
        rows = payload.get("data", [])
        if not isinstance(rows, list):
            raise Railway12306Error("票价接口 data 不是列表")
        return rows


class Railway12306PublicPriceProvider(TrainDataProvider):
    name = "12306-public-price"

    def __init__(self, client: PublicPriceQueryClient | None = None) -> None:
        station_repository = StationCodeRepository()
        self.client = client or PublicPriceClient(station_repository)
        self.station_repository = station_repository

    async def search_segments(
        self,
        from_station: str,
        to_station: str,
        query: RouteQuery,
    ) -> list[TrainSegment]:
        updated_at = datetime.now(timezone.utc)
        rows = await self.client.query(from_station, to_station, query)
        segments = [build_segment_from_public_price(row, query, updated_at) for row in rows]
        return [segment for segment in segments if segment is not None]

    def candidate_transfer_stations(self, query: RouteQuery) -> list[str]:
        return []

    async def search_stations(self, keyword: str) -> list[str]:
        return await self.station_repository.search_stations(keyword)


def request_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        ),
        "Accept": "application/json,text/javascript,*/*;q=0.01",
        "Referer": PUBLIC_QUERY_PAGE_URL,
    }