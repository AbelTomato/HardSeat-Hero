from datetime import datetime, timezone
from decimal import Decimal

from app.adapters.base import TrainDataProvider
from app.domain.models import RouteQuery, RouteSearchResponse, TrainSegment, TransferPlan


class RouteSearchService:
    def __init__(self, provider: TrainDataProvider) -> None:
        self.provider = provider

    async def search(self, query: RouteQuery) -> RouteSearchResponse:
        plans: list[TransferPlan] = []
        direct_segments = await self.provider.search_segments(query.from_station, query.to_station, query)
        plans.extend(self._build_plan([segment]) for segment in direct_segments if segment.lowest_price is not None)

        if query.max_transfers >= 1:
            plans.extend(await self._search_one_transfer(query))

        plans = [plan for plan in plans if plan.total_duration_minutes <= query.max_total_duration_minutes]
        plans.sort(key=lambda plan: (plan.total_price, plan.total_duration_minutes, len(plan.transfer_stations)))

        return RouteSearchResponse(
            query_id=f"{query.date.isoformat()}:{query.from_station}:{query.to_station}",
            source=self.provider.name,
            updated_at=datetime.now(timezone.utc),
            plans=plans[:20],
        )

    async def _search_one_transfer(self, query: RouteQuery) -> list[TransferPlan]:
        plans: list[TransferPlan] = []
        for transfer_station in self.provider.candidate_transfer_stations(query):
            first_legs = await self.provider.search_segments(query.from_station, transfer_station, query)
            second_legs = await self.provider.search_segments(transfer_station, query.to_station, query)
            for first in first_legs:
                for second in second_legs:
                    if first.lowest_price is None or second.lowest_price is None:
                        continue
                    transfer_minutes = int((second.depart_at - first.arrive_at).total_seconds() // 60)
                    if transfer_minutes < query.min_transfer_minutes:
                        continue
                    plans.append(self._build_plan([first, second]))
        return plans

    def _build_plan(self, segments: list[TrainSegment]) -> TransferPlan:
        total_price = sum((segment.lowest_price or Decimal("0")) for segment in segments)
        total_duration = int((segments[-1].arrive_at - segments[0].depart_at).total_seconds() // 60)
        ride_minutes = sum(segment.duration_minutes for segment in segments)
        transfer_stations = [segment.to_station for segment in segments[:-1]]
        return TransferPlan(
            total_price=total_price,
            total_duration_minutes=total_duration,
            transfer_minutes=total_duration - ride_minutes,
            transfer_stations=transfer_stations,
            segments=segments,
        )


