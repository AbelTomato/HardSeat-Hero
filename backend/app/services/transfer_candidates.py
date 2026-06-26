from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

from app.domain.models import RouteQuery, StationMetadata


EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class CandidateTransferConfig:
    max_candidates: int = 50
    min_corridor_km: float = 50
    max_corridor_km: float = 300
    corridor_ratio: float = 0.2
    endpoint_exclusion_km: float = 25


LOW_PRICE_TRANSFER_HUBS = {
    "赣州",
    "龙岩",
    "梅州",
    "惠州",
    "河源",
    "龙川",
    "兴宁",
    "东莞东",
    "广州",
    "广州北",
    "韶关",
    "韶关东",
    "郴州",
    "衡阳",
    "株洲",
    "长沙",
    "吉安",
    "南昌",
    "广州南",
    "广州东",
    "深圳北",
    "长沙南",
    "厦门北",
    "南昌西",
    "福州南",
    "柳州",
    "南宁",
    "杭州",
    "杭州东",
    "宁波",
    "温州",
    "温州南",
    "上饶",
    "鹰潭",
    "萍乡",
    "岳阳",
    "岳阳东",
    "怀化",
    "永州",
    "娄底",
    "邵阳",
    "徐州",
    "南京",
    "济南",
    "天津",
}


CHEAP_TRANSFER_SCORES = {
    "赣州": 100,
    "龙岩": 96,
    "长沙": 94,
    "南昌": 92,
    "郑州": 90,
    "徐州": 88,
    "广州": 86,
    "衡阳": 82,
    "株洲": 80,
    "南京": 78,
    "济南": 76,
    "天津": 74,
    "韶关东": 72,
    "龙川": 70,
    "惠州": 68,
    "东莞东": 66,
}


class StationMetadataRepository:
    def __init__(self, stations: list[StationMetadata] | None = None) -> None:
        self._stations = stations or default_station_metadata()

    def get(self, name: str) -> StationMetadata | None:
        return next((station for station in self._stations if station.name == name), None)

    def all(self) -> list[StationMetadata]:
        return list(self._stations)


class CandidateTransferStationGenerator:
    def __init__(
        self,
        repository: StationMetadataRepository | None = None,
        config: CandidateTransferConfig | None = None,
    ) -> None:
        self.repository = repository or StationMetadataRepository()
        self.config = config or CandidateTransferConfig()

    def generate(self, query: RouteQuery) -> list[str]:
        origin = self.repository.get(query.from_station)
        destination = self.repository.get(query.to_station)
        if origin is None or destination is None:
            return self._fallback_low_price_hubs(query)

        ab_distance = haversine_km(origin, destination)
        if ab_distance == 0:
            return []

        corridor_km = max(
            self.config.min_corridor_km,
            min(self.config.max_corridor_km, self.config.corridor_ratio * ab_distance),
        )
        scored: list[tuple[tuple[float, float, float, str], str]] = []
        for station in self.repository.all():
            if station.name in {origin.name, destination.name}:
                continue
            projection, perpendicular = project_station(origin, destination, station)
            projection_margin_km = ab_distance * 0.25
            if projection < -projection_margin_km or projection > ab_distance + projection_margin_km:
                continue
            if projection < self.config.endpoint_exclusion_km or projection > ab_distance - self.config.endpoint_exclusion_km:
                continue
            cheap_transfer_score = self._cheap_transfer_score(station.name)
            if cheap_transfer_score > 0:
                max_perpendicular = max(corridor_km, min(500, ab_distance * 0.75))
                if perpendicular > max_perpendicular:
                    continue
            elif perpendicular > corridor_km:
                continue
            score = (-cheap_transfer_score, -station.centrality_score, perpendicular, station.name)
            scored.append((score, station.name))

        scored.sort(key=lambda item: item[0])
        return [name for _, name in scored[: self.config.max_candidates]]

    def _fallback_low_price_hubs(self, query: RouteQuery) -> list[str]:
        blocked = {query.from_station, query.to_station}
        hubs = [
            station.name
            for station in self.repository.all()
            if station.name in LOW_PRICE_TRANSFER_HUBS and station.name not in blocked
        ]
        return hubs[: self.config.max_candidates]

    def _cheap_transfer_score(self, station_name: str) -> float:
        if station_name in CHEAP_TRANSFER_SCORES:
            return CHEAP_TRANSFER_SCORES[station_name]
        if station_name in LOW_PRICE_TRANSFER_HUBS:
            return 50
        return 0


def project_station(origin: StationMetadata, destination: StationMetadata, station: StationMetadata) -> tuple[float, float]:
    ax, ay = lon_lat_to_xy(origin.longitude, origin.latitude, origin.latitude)
    bx, by = lon_lat_to_xy(destination.longitude, destination.latitude, origin.latitude)
    px, py = lon_lat_to_xy(station.longitude, station.latitude, origin.latitude)
    abx = bx - ax
    aby = by - ay
    apx = px - ax
    apy = py - ay
    ab_len_sq = abx * abx + aby * aby
    if ab_len_sq == 0:
        return 0, haversine_km(origin, station)
    projection_ratio = (apx * abx + apy * aby) / ab_len_sq
    projection_km = projection_ratio * sqrt(ab_len_sq)
    closest_x = ax + projection_ratio * abx
    closest_y = ay + projection_ratio * aby
    perpendicular_km = sqrt((px - closest_x) ** 2 + (py - closest_y) ** 2)
    return projection_km, perpendicular_km


def lon_lat_to_xy(longitude: float, latitude: float, reference_latitude: float) -> tuple[float, float]:
    x = radians(longitude) * EARTH_RADIUS_KM * cos(radians(reference_latitude))
    y = radians(latitude) * EARTH_RADIUS_KM
    return x, y


def haversine_km(a: StationMetadata, b: StationMetadata) -> float:
    d_lat = radians(b.latitude - a.latitude)
    d_lon = radians(b.longitude - a.longitude)
    lat1 = radians(a.latitude)
    lat2 = radians(b.latitude)
    value = sin(d_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(value))


def default_station_metadata() -> list[StationMetadata]:
    return dedupe_station_metadata([
        StationMetadata(name="北京", telecode="BJP", latitude=39.902, longitude=116.427, centrality_score=95),
        StationMetadata(name="北京南", telecode="VNP", latitude=39.865, longitude=116.379, centrality_score=95),
        StationMetadata(name="天津", telecode="TJP", latitude=39.142, longitude=117.217, centrality_score=88),
        StationMetadata(name="天津南", telecode="TIP", latitude=39.056, longitude=117.061, centrality_score=70),
        StationMetadata(name="德州", telecode="DZP", latitude=37.451, longitude=116.307, centrality_score=72),
        StationMetadata(name="济南", telecode="JNK", latitude=36.676, longitude=117.001, centrality_score=88),
        StationMetadata(name="济南西", telecode="JGK", latitude=36.668, longitude=116.892, centrality_score=82),
        StationMetadata(name="兖州", telecode="YZK", latitude=35.553, longitude=116.833, centrality_score=70),
        StationMetadata(name="徐州", telecode="XCH", latitude=34.264, longitude=117.190, centrality_score=90),
        StationMetadata(name="蚌埠", telecode="BBH", latitude=32.944, longitude=117.383, centrality_score=78),
        StationMetadata(name="滁州北", telecode="CUH", latitude=32.318, longitude=118.313, centrality_score=62),
        StationMetadata(name="南京", telecode="NJH", latitude=32.088, longitude=118.797, centrality_score=92),
        StationMetadata(name="南京南", telecode="NKH", latitude=31.970, longitude=118.792, centrality_score=90),
        StationMetadata(name="镇江", telecode="ZJH", latitude=32.208, longitude=119.452, centrality_score=70),
        StationMetadata(name="常州", telecode="CZH", latitude=31.779, longitude=119.973, centrality_score=76),
        StationMetadata(name="无锡", telecode="WXH", latitude=31.586, longitude=120.300, centrality_score=78),
        StationMetadata(name="苏州", telecode="SZH", latitude=31.330, longitude=120.606, centrality_score=82),
        StationMetadata(name="上海", telecode="SHH", latitude=31.249, longitude=121.455, centrality_score=90),
        StationMetadata(name="上海虹桥", telecode="AOH", latitude=31.194, longitude=121.320, centrality_score=96),
        StationMetadata(name="厦门", telecode="XMS", latitude=24.468, longitude=118.111, centrality_score=82),
        StationMetadata(name="厦门北", telecode="XKS", latitude=24.642, longitude=118.074, centrality_score=84),
        StationMetadata(name="漳州", telecode="ZUS", latitude=24.513, longitude=117.647, centrality_score=76),
        StationMetadata(name="龙岩", telecode="LYS", latitude=25.091, longitude=117.017, centrality_score=72),
        StationMetadata(name="三明", telecode="SVS", latitude=26.265, longitude=117.639, centrality_score=68),
        StationMetadata(name="福州", telecode="FZS", latitude=26.074, longitude=119.296, centrality_score=86),
        StationMetadata(name="赣州", telecode="GZG", latitude=25.829, longitude=114.933, centrality_score=88),
        StationMetadata(name="吉安", telecode="VAG", latitude=27.113, longitude=114.992, centrality_score=74),
        StationMetadata(name="南昌", telecode="NCG", latitude=28.682, longitude=115.858, centrality_score=90),
        StationMetadata(name="梅州", telecode="MOQ", latitude=24.288, longitude=116.122, centrality_score=65),
        StationMetadata(name="兴宁", telecode="ENQ", latitude=24.138, longitude=115.731, centrality_score=60),
        StationMetadata(name="龙川", telecode="LUQ", latitude=24.101, longitude=115.260, centrality_score=78),
        StationMetadata(name="河源", telecode="VIQ", latitude=23.743, longitude=114.702, centrality_score=70),
        StationMetadata(name="惠州", telecode="HCQ", latitude=23.083, longitude=114.416, centrality_score=75),
        StationMetadata(name="东莞东", telecode="DMQ", latitude=22.974, longitude=114.003, centrality_score=76),
        StationMetadata(name="深圳", telecode="SZQ", latitude=22.532, longitude=114.118, centrality_score=92),
        StationMetadata(name="深圳东", telecode="BJQ", latitude=22.602, longitude=114.118, centrality_score=82),
        StationMetadata(name="汕尾", telecode="OGQ", latitude=22.785, longitude=115.375, centrality_score=64),
        StationMetadata(name="潮州", telecode="CKQ", latitude=23.657, longitude=116.622, centrality_score=62),
        StationMetadata(name="汕头", telecode="OTQ", latitude=23.354, longitude=116.682, centrality_score=64),
        StationMetadata(name="揭阳", telecode="JYA", latitude=23.549, longitude=116.372, centrality_score=62),
        StationMetadata(name="广州北", telecode="GBQ", latitude=23.376, longitude=113.203, centrality_score=78),
        StationMetadata(name="韶关", telecode="SNQ", latitude=24.779, longitude=113.598, centrality_score=78),
        StationMetadata(name="韶关东", telecode="SGQ", latitude=24.805, longitude=113.594, centrality_score=82),
        StationMetadata(name="郴州", telecode="CZQ", latitude=25.798, longitude=113.033, centrality_score=76),
        StationMetadata(name="衡阳", telecode="HYQ", latitude=26.894, longitude=112.572, centrality_score=86),
        StationMetadata(name="株洲", telecode="ZZQ", latitude=27.827, longitude=113.152, centrality_score=84),
        StationMetadata(name="长沙", telecode="CSQ", latitude=28.198, longitude=112.976, centrality_score=92),
        StationMetadata(name="杭州东", telecode="HGH", latitude=30.291, longitude=120.213, centrality_score=85),
        StationMetadata(name="广州", telecode="GZQ", latitude=23.149, longitude=113.257, centrality_score=92),
    ] + southeast_station_metadata())


def dedupe_station_metadata(stations: list[StationMetadata]) -> list[StationMetadata]:
    by_name: dict[str, StationMetadata] = {}
    for station in stations:
        by_name[station.name] = station
    return list(by_name.values())


def southeast_station_metadata() -> list[StationMetadata]:
    return [
        StationMetadata(name="广州南", telecode="", latitude=22.991459, longitude=113.264122, centrality_score=95),
        StationMetadata(name="深圳北", telecode="", latitude=22.611958, longitude=114.023922, centrality_score=95),
        StationMetadata(name="广州东", telecode="", latitude=23.152693, longitude=113.319170, centrality_score=95),
        StationMetadata(name="深圳", telecode="", latitude=22.534299, longitude=114.112069, centrality_score=95),
        StationMetadata(name="长沙南", telecode="", latitude=28.150177, longitude=113.059419, centrality_score=95),
        StationMetadata(name="福州", telecode="", latitude=26.117115, longitude=119.315178, centrality_score=95),
        StationMetadata(name="广州", telecode="", latitude=23.151582, longitude=113.252242, centrality_score=95),
        StationMetadata(name="珠海", telecode="", latitude=22.218163, longitude=113.544287, centrality_score=95),
        StationMetadata(name="宁波", telecode="", latitude=29.864087, longitude=121.532164, centrality_score=95),
        StationMetadata(name="温州南", telecode="", latitude=27.969502, longitude=120.581081, centrality_score=95),
        StationMetadata(name="杭州东", telecode="", latitude=30.293492, longitude=120.208384, centrality_score=95),
        StationMetadata(name="长沙", telecode="", latitude=28.197261, longitude=113.007342, centrality_score=95),
        StationMetadata(name="厦门北", telecode="", latitude=24.638980, longitude=118.069110, centrality_score=95),
        StationMetadata(name="南昌", telecode="", latitude=28.665400, longitude=115.914324, centrality_score=95),
        StationMetadata(name="厦门", telecode="", latitude=24.470271, longitude=118.111180, centrality_score=95),
        StationMetadata(name="北海", telecode="", latitude=21.455194, longitude=109.123971, centrality_score=95),
        StationMetadata(name="湘潭", telecode="", latitude=27.878751, longitude=112.907681, centrality_score=95),
        StationMetadata(name="南宁", telecode="", latitude=22.829528, longitude=108.311197, centrality_score=95),
        StationMetadata(name="杭州", telecode="", latitude=30.245946, longitude=120.178407, centrality_score=95),
        StationMetadata(name="潮汕", telecode="", latitude=23.539379, longitude=116.583575, centrality_score=91),
        StationMetadata(name="南昌西", telecode="", latitude=28.625739, longitude=115.787560, centrality_score=90),
        StationMetadata(name="龙岩", telecode="", latitude=25.098759, longitude=117.001526, centrality_score=89),
        StationMetadata(name="汕头", telecode="", latitude=23.374581, longitude=116.750151, centrality_score=82),
        StationMetadata(name="柳州", telecode="", latitude=24.310159, longitude=109.384492, centrality_score=80),
        StationMetadata(name="温州", telecode="", latitude=27.983440, longitude=120.681014, centrality_score=77),
        StationMetadata(name="苍南", telecode="", latitude=27.531027, longitude=120.407201, centrality_score=77),
        StationMetadata(name="深圳东", telecode="", latitude=22.603841, longitude=114.113686, centrality_score=76),
        StationMetadata(name="湛江西", telecode="", latitude=21.218425, longitude=110.276244, centrality_score=75),
        StationMetadata(name="福州南", telecode="", latitude=25.988878, longitude=119.385674, centrality_score=75),
        StationMetadata(name="赣州", telecode="", latitude=25.822298, longitude=114.959611, centrality_score=72),
        StationMetadata(name="肇庆", telecode="", latitude=23.084038, longitude=112.442563, centrality_score=69),
        StationMetadata(name="深圳西", telecode="", latitude=22.531105, longitude=113.902647, centrality_score=68),
        StationMetadata(name="百色", telecode="", latitude=23.888555, longitude=106.660827, centrality_score=68),
        StationMetadata(name="湛江", telecode="", latitude=21.189448, longitude=110.385913, centrality_score=66),
        StationMetadata(name="邵阳", telecode="", latitude=27.216658, longitude=111.455568, centrality_score=66),
        StationMetadata(name="新会", telecode="", latitude=22.520350, longitude=113.065717, centrality_score=65),
        StationMetadata(name="茂名", telecode="", latitude=21.683697, longitude=110.843485, centrality_score=64),
        StationMetadata(name="防城港北", telecode="", latitude=21.716351, longitude=108.372269, centrality_score=64),
        StationMetadata(name="深圳坪山", telecode="", latitude=22.710511, longitude=114.321406, centrality_score=63),
        StationMetadata(name="玉林", telecode="", latitude=22.600245, longitude=110.152361, centrality_score=63),
        StationMetadata(name="张家界", telecode="", latitude=29.107435, longitude=110.481632, centrality_score=62),
        StationMetadata(name="信宜", telecode="", latitude=22.333301, longitude=110.961314, centrality_score=61),
        StationMetadata(name="怀化", telecode="", latitude=27.560605, longitude=109.962743, centrality_score=61),
        StationMetadata(name="福鼎", telecode="", latitude=27.285252, longitude=120.185287, centrality_score=61),
        StationMetadata(name="永州", telecode="", latitude=26.457695, longitude=111.565697, centrality_score=60),
        StationMetadata(name="东莞东", telecode="", latitude=22.969893, longitude=114.033952, centrality_score=57),
        StationMetadata(name="衡阳东", telecode="", latitude=26.899890, longitude=112.704232, centrality_score=57),
        StationMetadata(name="上饶", telecode="", latitude=28.493490, longitude=118.002529, centrality_score=56),
        StationMetadata(name="嘉兴南", telecode="", latitude=30.693299, longitude=120.795516, centrality_score=56),
        StationMetadata(name="岳阳东", telecode="", latitude=29.368933, longitude=113.202239, centrality_score=56),
        StationMetadata(name="贺州", telecode="", latitude=24.457268, longitude=111.534004, centrality_score=56),
        StationMetadata(name="金城江", telecode="", latitude=24.702257, longitude=108.060829, centrality_score=56),
        StationMetadata(name="丽水", telecode="", latitude=28.444819, longitude=119.949523, centrality_score=55),
        StationMetadata(name="井冈山", telecode="", latitude=26.731302, longitude=114.292142, centrality_score=55),
        StationMetadata(name="台州", telecode="", latitude=28.688904, longitude=121.285848, centrality_score=55),
        StationMetadata(name="常德", telecode="", latitude=29.074366, longitude=111.689446, centrality_score=55),
        StationMetadata(name="桂林", telecode="", latitude=25.263879, longitude=110.278209, centrality_score=55),
        StationMetadata(name="汕尾", telecode="", latitude=22.813301, longitude=115.423704, centrality_score=55),
        StationMetadata(name="石门县北", telecode="", latitude=29.603133, longitude=111.428829, centrality_score=55),
        StationMetadata(name="萍乡", telecode="", latitude=27.650400, longitude=113.835965, centrality_score=55),
        StationMetadata(name="岳阳", telecode="", latitude=29.378943, longitude=113.114104, centrality_score=54),
        StationMetadata(name="惠州", telecode="", latitude=23.154265, longitude=114.411621, centrality_score=54),
        StationMetadata(name="梧州南", telecode="", latitude=23.395885, longitude=111.217659, centrality_score=54),
        StationMetadata(name="饶平", telecode="", latitude=23.714379, longitude=116.928733, centrality_score=54),
        StationMetadata(name="凭祥", telecode="", latitude=22.083031, longitude=106.738467, centrality_score=53),
        StationMetadata(name="宁海", telecode="", latitude=29.339033, longitude=121.415405, centrality_score=53),
        StationMetadata(name="广州北", telecode="", latitude=23.379599, longitude=113.199136, centrality_score=53),
        StationMetadata(name="梅州", telecode="", latitude=24.261733, longitude=116.126468, centrality_score=53),
        StationMetadata(name="河源", telecode="", latitude=23.762084, longitude=114.678540, centrality_score=53),
        StationMetadata(name="漳州", telecode="", latitude=24.460015, longitude=117.711063, centrality_score=53),
        StationMetadata(name="衡阳", telecode="", latitude=26.893414, longitude=112.625838, centrality_score=53),
        StationMetadata(name="诏安", telecode="", latitude=23.775026, longitude=117.093783, centrality_score=53),
        StationMetadata(name="金华南", telecode="", latitude=29.120105, longitude=119.729452, centrality_score=53),
        StationMetadata(name="鳌江", telecode="", latitude=27.629163, longitude=120.541079, centrality_score=53),
        StationMetadata(name="鹰潭", telecode="", latitude=28.238617, longitude=117.022681, centrality_score=53),
        StationMetadata(name="三明北", telecode="", latitude=26.379135, longitude=117.801796, centrality_score=52),
        StationMetadata(name="东安东", telecode="", latitude=26.381196, longitude=111.332242, centrality_score=52),
        StationMetadata(name="乐清", telecode="", latitude=28.087719, longitude=120.855440, centrality_score=52),
        StationMetadata(name="大埔", telecode="", latitude=24.412592, longitude=116.572800, centrality_score=52),
        StationMetadata(name="惠州南", telecode="", latitude=22.787114, longitude=114.487596, centrality_score=52),
        StationMetadata(name="景德镇", telecode="", latitude=29.288159, longitude=117.216553, centrality_score=52),
        StationMetadata(name="泉州", telecode="", latitude=24.975919, longitude=118.562456, centrality_score=52),
        StationMetadata(name="玉山", telecode="", latitude=28.663652, longitude=118.231394, centrality_score=52),
        StationMetadata(name="益阳", telecode="", latitude=28.499887, longitude=112.389426, centrality_score=52),
        StationMetadata(name="莆田", telecode="", latitude=25.353479, longitude=119.057848, centrality_score=52),
        StationMetadata(name="三明", telecode="", latitude=26.243634, longitude=117.602622, centrality_score=51),
        StationMetadata(name="三门县", telecode="", latitude=29.058074, longitude=121.358870, centrality_score=51),
        StationMetadata(name="兴安北", telecode="", latitude=25.677298, longitude=110.661673, centrality_score=51),
        StationMetadata(name="南丰", telecode="", latitude=27.289640, longitude=116.595022, centrality_score=51),
        StationMetadata(name="南平南", telecode="", latitude=26.629656, longitude=118.209775, centrality_score=51),
        StationMetadata(name="奉化", telecode="", latitude=29.630822, longitude=121.460073, centrality_score=51),
        StationMetadata(name="娄底", telecode="", latitude=27.742841, longitude=112.000766, centrality_score=51),
        StationMetadata(name="定南", telecode="", latitude=24.777776, longitude=115.000449, centrality_score=51),
        StationMetadata(name="新晃", telecode="", latitude=27.351795, longitude=109.192231, centrality_score=51),
        StationMetadata(name="泰宁", telecode="", latitude=26.930065, longitude=117.148114, centrality_score=51),
        StationMetadata(name="湖州", telecode="", latitude=30.865561, longitude=120.018333, centrality_score=51),
        StationMetadata(name="澧县", telecode="", latitude=29.822118, longitude=111.599038, centrality_score=51),
        StationMetadata(name="瑞金", telecode="", latitude=25.866463, longitude=116.050355, centrality_score=51),
        StationMetadata(name="绍兴北", telecode="", latitude=30.102284, longitude=120.534429, centrality_score=51),
        StationMetadata(name="郴州", telecode="", latitude=25.811128, longitude=113.027642, centrality_score=51),
    ]