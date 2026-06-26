import os
from datetime import timedelta

from app.adapters.base import TrainDataProvider
from app.adapters.mock_provider import MockTrainDataProvider
from app.adapters.railway_12306_public_price import Railway12306PublicPriceProvider
from app.adapters.static_price_fallback_provider import StaticPriceFallbackProvider
from app.adapters.static_price_provider import LocalStaticPriceProvider
from app.services.static_price_repository import SQLiteStaticPriceRepository


def create_train_data_provider() -> TrainDataProvider:
    provider_name = os.getenv("TRAIN_DATA_PROVIDER", "mock").strip().lower()
    if provider_name == "mock":
        return MockTrainDataProvider()
    if provider_name in {"12306", "12306-public-price", "railway_12306_public_price"}:
        return Railway12306PublicPriceProvider()
    if provider_name in {"static", "static-price", "local-static-price"}:
        return _create_static_price_provider()
    raise ValueError(f"Unsupported TRAIN_DATA_PROVIDER: {provider_name}")


def _create_static_price_provider() -> TrainDataProvider:
    repository = SQLiteStaticPriceRepository(_required_env("STATIC_PRICE_DB"))
    local_provider = LocalStaticPriceProvider(
        repository=repository,
        max_age=_static_price_max_age(),
    )
    mode = os.getenv("STATIC_PRICE_MODE", "static-only").strip().lower()
    if mode in {"", "static-only", "local-only"}:
        return local_provider
    if mode in {"static-with-remote-fallback", "remote-fallback", "fallback"}:
        return StaticPriceFallbackProvider(
            local_provider=local_provider,
            fallback_provider=_create_static_price_fallback_provider(),
            repository=repository,
        )
    raise ValueError(f"Unsupported STATIC_PRICE_MODE: {mode}")


def _create_static_price_fallback_provider() -> TrainDataProvider:
    fallback_name = os.getenv("STATIC_PRICE_FALLBACK_PROVIDER", "12306-public-price").strip().lower()
    if fallback_name in {"12306", "12306-public-price", "railway_12306_public_price"}:
        return Railway12306PublicPriceProvider()
    raise ValueError(f"Unsupported STATIC_PRICE_FALLBACK_PROVIDER: {fallback_name}")


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required when TRAIN_DATA_PROVIDER=static-price")
    return value


def _static_price_max_age() -> timedelta | None:
    value = os.getenv("STATIC_PRICE_MAX_AGE_DAYS", "").strip()
    if not value:
        return None
    days = int(value)
    if days <= 0:
        raise ValueError("STATIC_PRICE_MAX_AGE_DAYS must be greater than 0")
    return timedelta(days=days)