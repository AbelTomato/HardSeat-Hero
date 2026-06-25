import os

from app.adapters.base import TrainDataProvider
from app.adapters.mock_provider import MockTrainDataProvider
from app.adapters.railway_12306_public_price import Railway12306PublicPriceProvider


def create_train_data_provider() -> TrainDataProvider:
    provider_name = os.getenv("TRAIN_DATA_PROVIDER", "mock").strip().lower()
    if provider_name == "mock":
        return MockTrainDataProvider()
    if provider_name in {"12306", "12306-public-price", "railway_12306_public_price"}:
        return Railway12306PublicPriceProvider()
    raise ValueError(f"Unsupported TRAIN_DATA_PROVIDER: {provider_name}")