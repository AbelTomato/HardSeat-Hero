from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import httpx

from app.adapters.provider_factory import create_train_data_provider
from app.adapters.railway_12306_public_price import (
    Railway12306Error,
    Railway12306PublicPriceProvider,
    build_segment_from_public_price,
    describe_httpx_error,
    parse_duration_minutes,
    parse_price,
    parse_station_codes,
)
from app.adapters.static_price_fallback_provider import StaticPriceFallbackProvider
from app.adapters.static_price_provider import LocalStaticPriceProvider
from app.domain.models import RouteQuery
from app.services.transfer_candidates import CandidateTransferStationGenerator, StationMetadataRepository


def public_price_row(**overrides):
    dto = {
        "train_no": "240000G54700",
        "station_train_code": "G547",
        "from_station_name": "北京南",
        "to_station_name": "上海虹桥",
        "start_time": "06:18",
        "arrive_time": "12:11",
        "lishi": "05:53",
        "day_difference": "0",
        "swz_price": "27820",
        "zy_price": "12720",
        "ze_price": "07950",
    }
    dto.update(overrides)
    return {"queryLeftNewDTO": dto}


def test_parse_station_codes_extracts_name_to_telecode() -> None:
    text = "var station_names ='@bjb|北京北|VAP|beijingbei|bjb|0@bjn|北京南|VNP|beijingnan|bjn|1';"

    stations = parse_station_codes(text)

    assert stations["北京北"] == "VAP"
    assert stations["北京南"] == "VNP"


def test_parse_station_codes_rejects_unexpected_format() -> None:
    with pytest.raises(Railway12306Error):
        parse_station_codes("<html></html>")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("07950", Decimal("795")),
        ("02510", Decimal("251")),
        ("--", None),
        ("", None),
        (None, None),
        ("abc", None),
    ],
)
def test_parse_price(raw, expected) -> None:
    assert parse_price(raw) == expected


def test_parse_duration_minutes() -> None:
    assert parse_duration_minutes("05:53") == 353


def test_describe_httpx_error_includes_status_code() -> None:
    request = httpx.Request("GET", "https://kyfw.12306.cn/test")
    response = httpx.Response(302, request=request, headers={"location": "/login"})
    error = httpx.HTTPStatusError("redirect", request=request, response=response)

    assert describe_httpx_error(error) == "HTTPStatusError: HTTP 302 Found"


def test_describe_httpx_error_includes_request_context() -> None:
    request = httpx.Request("GET", "https://kyfw.12306.cn/test")
    error = httpx.ConnectError("connection refused", request=request)

    assert describe_httpx_error(error) == "ConnectError: GET https://kyfw.12306.cn/test 请求失败：connection refused"


def test_build_segment_from_public_price_maps_high_speed_fields() -> None:
    query = RouteQuery(from_station="北京南", to_station="上海虹桥", date=date(2026, 7, 1))
    updated_at = datetime(2026, 6, 25, tzinfo=timezone.utc)

    segment = build_segment_from_public_price(public_price_row(), query, updated_at)

    assert segment is not None
    assert segment.train_no == "G547"
    assert segment.from_station == "北京南"
    assert segment.to_station == "上海虹桥"
    assert segment.duration_minutes == 353
    assert segment.lowest_price == Decimal("795")
    assert {price.seat_type: price.price for price in segment.prices} == {
        "商务座": Decimal("2782"),
        "一等座": Decimal("1272"),
        "二等座": Decimal("795"),
    }
    assert segment.source == "12306-public-price"


def test_build_segment_from_public_price_maps_normal_train_fields() -> None:
    query = RouteQuery(from_station="北京", to_station="广州", date=date(2026, 7, 1))
    updated_at = datetime(2026, 6, 25, tzinfo=timezone.utc)

    segment = build_segment_from_public_price(
        public_price_row(
            station_train_code="K599",
            from_station_name="北京",
            to_station_name="广州",
            start_time="05:41",
            arrive_time="10:36",
            lishi="28:55",
            day_difference="1",
            swz_price="",
            zy_price="",
            ze_price="",
            yz_price="02510",
            yw_price="04560",
            rw_price="07840",
        ),
        query,
        updated_at,
    )

    assert segment is not None
    assert segment.train_no == "K599"
    assert segment.arrive_at.date().isoformat() == "2026-07-02"
    assert {price.seat_type: price.price for price in segment.prices} == {
        "硬座": Decimal("251"),
        "硬卧": Decimal("456"),
        "软卧": Decimal("784"),
    }


def test_build_segment_skips_rows_without_prices() -> None:
    query = RouteQuery(from_station="北京南", to_station="上海虹桥", date=date(2026, 7, 1))

    segment = build_segment_from_public_price(
        public_price_row(swz_price="", zy_price="", ze_price=""),
        query,
        datetime.now(timezone.utc),
    )

    assert segment is None


class FakePublicPriceClient:
    async def query(self, from_station, to_station, query):
        return [public_price_row()]


@pytest.mark.asyncio
async def test_provider_normalizes_client_rows() -> None:
    provider = Railway12306PublicPriceProvider(client=FakePublicPriceClient())
    query = RouteQuery(from_station="北京南", to_station="上海虹桥", date=date(2026, 7, 1))

    segments = await provider.search_segments("北京南", "上海虹桥", query)

    assert len(segments) == 1
    assert segments[0].train_no == "G547"


def test_provider_factory_uses_mock_by_default(monkeypatch) -> None:
    monkeypatch.delenv("TRAIN_DATA_PROVIDER", raising=False)

    provider = create_train_data_provider()

    assert provider.name == "mock"


def test_provider_factory_supports_12306(monkeypatch) -> None:
    monkeypatch.setenv("TRAIN_DATA_PROVIDER", "12306-public-price")

    provider = create_train_data_provider()

    assert provider.name == "12306-public-price"


def test_provider_factory_supports_static_price(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "static_prices.sqlite3"
    monkeypatch.setenv("TRAIN_DATA_PROVIDER", "static-price")
    monkeypatch.setenv("STATIC_PRICE_DB", str(db_path))
    monkeypatch.setenv("STATIC_PRICE_MAX_AGE_DAYS", "30")

    provider = create_train_data_provider()

    assert isinstance(provider, LocalStaticPriceProvider)
    assert provider.name == "static-price"
    assert provider.repository.db_path == db_path
    assert provider.max_age is not None
    assert provider.max_age.days == 30


def test_provider_factory_supports_static_price_remote_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TRAIN_DATA_PROVIDER", "static-price")
    monkeypatch.setenv("STATIC_PRICE_DB", str(tmp_path / "static_prices.sqlite3"))
    monkeypatch.setenv("STATIC_PRICE_MODE", "static-with-remote-fallback")
    monkeypatch.setenv("STATIC_PRICE_FALLBACK_PROVIDER", "12306-public-price")

    provider = create_train_data_provider()

    assert isinstance(provider, StaticPriceFallbackProvider)
    assert provider.name == "static-price"
    assert provider.fallback_provider.name == "12306-public-price"


def test_provider_factory_static_price_rejects_unknown_mode(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TRAIN_DATA_PROVIDER", "static-price")
    monkeypatch.setenv("STATIC_PRICE_DB", str(tmp_path / "static_prices.sqlite3"))
    monkeypatch.setenv("STATIC_PRICE_MODE", "unknown")

    with pytest.raises(ValueError, match="Unsupported STATIC_PRICE_MODE"):
        create_train_data_provider()


def test_provider_factory_static_price_rejects_unknown_fallback_provider(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TRAIN_DATA_PROVIDER", "static-price")
    monkeypatch.setenv("STATIC_PRICE_DB", str(tmp_path / "static_prices.sqlite3"))
    monkeypatch.setenv("STATIC_PRICE_MODE", "static-with-remote-fallback")
    monkeypatch.setenv("STATIC_PRICE_FALLBACK_PROVIDER", "unknown")

    with pytest.raises(ValueError, match="Unsupported STATIC_PRICE_FALLBACK_PROVIDER"):
        create_train_data_provider()


def test_provider_factory_static_price_requires_db(monkeypatch) -> None:
    monkeypatch.setenv("TRAIN_DATA_PROVIDER", "static-price")
    monkeypatch.delenv("STATIC_PRICE_DB", raising=False)

    with pytest.raises(ValueError, match="STATIC_PRICE_DB is required"):
        create_train_data_provider()


def test_provider_factory_static_price_rejects_invalid_max_age(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TRAIN_DATA_PROVIDER", "static-price")
    monkeypatch.setenv("STATIC_PRICE_DB", str(tmp_path / "static_prices.sqlite3"))
    monkeypatch.setenv("STATIC_PRICE_MAX_AGE_DAYS", "0")

    with pytest.raises(ValueError, match="STATIC_PRICE_MAX_AGE_DAYS must be greater than 0"):
        create_train_data_provider()


def test_provider_returns_generated_transfer_candidates() -> None:
    provider = Railway12306PublicPriceProvider(
        client=FakePublicPriceClient(),
        transfer_generator=CandidateTransferStationGenerator(StationMetadataRepository()),
    )
    query = RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1))

    candidates = provider.candidate_transfer_stations(query)

    assert candidates
    assert "南京南" in candidates
    assert "北京" not in candidates
    assert "上海" not in candidates