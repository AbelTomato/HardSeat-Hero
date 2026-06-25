from datetime import datetime, time, timezone
from decimal import Decimal

from app.adapters.base import TrainDataProvider
from app.domain.models import RouteQuery, SeatPrice, TrainSegment


class MockTrainDataProvider(TrainDataProvider):
    name = "mock"

    def __init__(self) -> None:
        self._stations = ["北京", "济南西", "南京南", "上海", "天津南", "杭州东"]

    def search_stations(self, keyword: str) -> list[str]:
        if not keyword:
            return self._stations
        return [station for station in self._stations if keyword in station]

    def candidate_transfer_stations(self, query: RouteQuery) -> list[str]:
        blocked = {query.from_station, query.to_station}
        return [station for station in self._stations if station not in blocked]

    async def search_segments(
        self,
        from_station: str,
        to_station: str,
        query: RouteQuery,
    ) -> list[TrainSegment]:
        return [
            segment
            for segment in self._segments_for_date(query)
            if segment.from_station == from_station and segment.to_station == to_station
        ]

    def _segments_for_date(self, query: RouteQuery) -> list[TrainSegment]:
        updated_at = datetime.now(timezone.utc)

        def at(value: time) -> datetime:
            return datetime.combine(query.date, value, tzinfo=timezone.utc)

        def segment(
            train_no: str,
            from_station: str,
            to_station: str,
            depart: time,
            arrive: time,
            price: str,
        ) -> TrainSegment:
            depart_at = at(depart)
            arrive_at = at(arrive)
            return TrainSegment(
                train_no=train_no,
                from_station=from_station,
                to_station=to_station,
                depart_at=depart_at,
                arrive_at=arrive_at,
                duration_minutes=int((arrive_at - depart_at).total_seconds() // 60),
                prices=[SeatPrice(seat_type="二等座", price=Decimal(price), remaining="有票")],
                source=self.name,
                updated_at=updated_at,
            )

        return [
            segment("G101", "北京", "上海", time(8, 0), time(13, 30), "553.0"),
            segment("D711", "北京", "济南西", time(7, 30), time(9, 20), "184.5"),
            segment("G215", "济南西", "上海", time(10, 10), time(14, 30), "274.0"),
            segment("G133", "北京", "南京南", time(8, 20), time(12, 5), "318.0"),
            segment("D305", "南京南", "上海", time(12, 50), time(14, 20), "95.0"),
            segment("C201", "北京", "天津南", time(9, 0), time(9, 35), "54.5"),
            segment("G155", "天津南", "上海", time(9, 50), time(15, 10), "478.0"),
            segment("G721", "上海", "杭州东", time(15, 0), time(15, 50), "73.0"),
        ]
