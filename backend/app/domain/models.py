from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator


class SeatPrice(BaseModel):
    seat_type: str
    price: Decimal = Field(ge=0)
    remaining: str = "unknown"


class TrainSegment(BaseModel):
    train_no: str
    from_station: str
    to_station: str
    depart_at: datetime
    arrive_at: datetime
    duration_minutes: int = Field(gt=0)
    prices: list[SeatPrice]
    source: str = "mock"
    updated_at: datetime

    @model_validator(mode="after")
    def validate_time_order(self) -> "TrainSegment":
        if self.arrive_at <= self.depart_at:
            raise ValueError("arrive_at must be later than depart_at")
        return self

    @property
    def lowest_price(self) -> Decimal | None:
        if not self.prices:
            return None
        return min(price.price for price in self.prices)


class RouteQuery(BaseModel):
    from_station: str = Field(min_length=1)
    to_station: str = Field(min_length=1)
    date: date
    max_transfers: int = Field(default=1, ge=0, le=2)
    min_transfer_minutes: int = Field(default=30, ge=0, le=360)
    max_total_duration_minutes: int = Field(default=24 * 60, ge=60)


class TransferPlan(BaseModel):
    total_price: Decimal
    total_duration_minutes: int
    transfer_minutes: int
    transfer_stations: list[str]
    segments: list[TrainSegment]


class RouteSearchResponse(BaseModel):
    query_id: str
    source: str
    updated_at: datetime
    plans: list[TransferPlan]


class StationSearchResponse(BaseModel):
    stations: list[str]
