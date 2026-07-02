from datetime import date

import httpx
import pytest

from app.adapters.railway_12306_public_price import Railway12306Error
from app.adapters.railway_12306_snapshot import Railway12306SnapshotClient, SnapshotTrainName, SnapshotTrainStop


@pytest.mark.asyncio
async def test_fetch_train_names_deduplicates_train_no() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["date"] == "2026-07-12"
        return httpx.Response(
            200,
            json={
                "status": True,
                "data": [
                    {"train_no": "G1NO", "station_train_code": "G1"},
                    {"train_no": "G1NO", "station_train_code": "G1(复)"},
                    {"train_no": "D1NO", "station_train_code": "D1"},
                ],
            },
        )

    client = Railway12306SnapshotClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    trains = await client.fetch_train_names(date(2026, 7, 12))

    assert trains == [SnapshotTrainName(train_no="G1NO", train_code="G1"), SnapshotTrainName(train_no="D1NO", train_code="D1")]
    await client.http_client.aclose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_fetch_train_stops_sorts_and_normalizes_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["leftTicketDTO.train_no"] == "G1NO"
        return httpx.Response(
            200,
            json={
                "status": True,
                "data": {
                    "data": [
                        {
                            "station_name": "上海",
                            "station_train_code": "G1(北京-上海)",
                            "station_no": "02",
                            "arrive_time": "10:00",
                            "start_time": "10:05",
                            "arrive_day_diff": "0",
                            "running_time": "02:00",
                            "start_station_name": "北京",
                            "end_station_name": "上海",
                        },
                        {
                            "station_name": "北京",
                            "station_train_code": "G1(北京-上海)",
                            "station_no": "01",
                            "arrive_time": "----",
                            "start_time": "08:00",
                            "arrive_day_diff": "0",
                            "running_time": "00:00",
                            "start_station_name": "北京",
                            "end_station_name": "上海",
                        },
                    ]
                },
            },
        )

    client = Railway12306SnapshotClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    stops = await client.fetch_train_stops(date(2026, 7, 12), "G1NO")

    assert [stop.station_name for stop in stops] == ["北京", "上海"]
    assert all(isinstance(stop, SnapshotTrainStop) for stop in stops)
    assert stops[0].station_train_code == "G1"
    assert stops[0].arrive_time is None
    assert stops[0].start_time == "08:00"
    await client.http_client.aclose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_snapshot_client_rejects_non_json() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html></html>")

    client = Railway12306SnapshotClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with pytest.raises(Railway12306Error, match="返回非 JSON"):
        await client.fetch_train_names(date(2026, 7, 12))
    await client.http_client.aclose()  # type: ignore[union-attr]