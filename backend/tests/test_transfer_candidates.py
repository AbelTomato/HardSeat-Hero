from datetime import date
from pathlib import Path

from app.domain.models import RouteQuery, StationMetadata
from app.services.transfer_candidates import (
    CandidateTransferConfig,
    CandidateTransferStationGenerator,
    StationMetadataRepository,
    load_station_metadata,
)


def test_candidate_generator_filters_station_far_from_corridor() -> None:
    generator = CandidateTransferStationGenerator(
        StationMetadataRepository(
            [
                StationMetadata(name="A", telecode="AAA", latitude=0, longitude=0),
                StationMetadata(name="B", telecode="BBB", latitude=0, longitude=10),
                StationMetadata(name="Near", telecode="NAR", latitude=0.1, longitude=5, centrality_score=1),
                StationMetadata(name="Far", telecode="FAR", latitude=8, longitude=5, centrality_score=100),
            ]
        ),
        CandidateTransferConfig(max_candidates=10, min_corridor_km=50, max_corridor_km=300, corridor_ratio=0.2),
    )

    candidates = generator.generate(RouteQuery(from_station="A", to_station="B", date=date(2026, 7, 1)))

    assert "Near" in candidates
    assert "Far" not in candidates


def test_candidate_generator_filters_station_outside_direction_projection() -> None:
    generator = CandidateTransferStationGenerator(
        StationMetadataRepository(
            [
                StationMetadata(name="A", telecode="AAA", latitude=0, longitude=0),
                StationMetadata(name="B", telecode="BBB", latitude=0, longitude=10),
                StationMetadata(name="BeforeA", telecode="BEF", latitude=0, longitude=-1, centrality_score=100),
                StationMetadata(name="AfterB", telecode="AFT", latitude=0, longitude=11, centrality_score=100),
                StationMetadata(name="Middle", telecode="MID", latitude=0, longitude=5, centrality_score=1),
            ]
        )
    )

    candidates = generator.generate(RouteQuery(from_station="A", to_station="B", date=date(2026, 7, 1)))

    assert candidates == ["Middle"]


def test_candidate_generator_prioritizes_hub_score_when_spatial_conditions_equal() -> None:
    generator = CandidateTransferStationGenerator(
        StationMetadataRepository(
            [
                StationMetadata(name="A", telecode="AAA", latitude=0, longitude=0),
                StationMetadata(name="B", telecode="BBB", latitude=0, longitude=10),
                StationMetadata(name="LowHub", telecode="LOW", latitude=0, longitude=5, centrality_score=10),
                StationMetadata(name="HighHub", telecode="HIG", latitude=0, longitude=5, centrality_score=90),
            ]
        )
    )

    candidates = generator.generate(RouteQuery(from_station="A", to_station="B", date=date(2026, 7, 1)))

    assert candidates[:2] == ["HighHub", "LowHub"]


def test_candidate_generator_prioritizes_transfer_prior_score_over_centrality() -> None:
    generator = CandidateTransferStationGenerator(
        StationMetadataRepository(
            [
                StationMetadata(name="A", telecode="AAA", latitude=0, longitude=0),
                StationMetadata(name="B", telecode="BBB", latitude=0, longitude=10),
                StationMetadata(name="赣州", telecode="GZG", latitude=0, longitude=5, centrality_score=10),
                StationMetadata(name="HighHub", telecode="HIG", latitude=0, longitude=5, centrality_score=100),
            ]
        )
    )

    candidates = generator.generate(RouteQuery(from_station="A", to_station="B", date=date(2026, 7, 1)))

    assert candidates[:2] == ["赣州", "HighHub"]


def test_candidate_limit_controls_result_count() -> None:
    generator = CandidateTransferStationGenerator(
        StationMetadataRepository(
            [
                StationMetadata(name="A", telecode="AAA", latitude=0, longitude=0),
                StationMetadata(name="B", telecode="BBB", latitude=0, longitude=10),
                StationMetadata(name="T1", telecode="T01", latitude=0, longitude=3, centrality_score=30),
                StationMetadata(name="T2", telecode="T02", latitude=0, longitude=4, centrality_score=20),
                StationMetadata(name="T3", telecode="T03", latitude=0, longitude=5, centrality_score=10),
            ]
        )
    )

    candidates = generator.generate(RouteQuery(from_station="A", to_station="B", date=date(2026, 7, 1), candidate_limit=2))

    assert candidates == ["T1", "T2"]


def test_wide_detour_strategy_allows_reasonable_off_corridor_station() -> None:
    generator = CandidateTransferStationGenerator(
        StationMetadataRepository(
            [
                StationMetadata(name="A", telecode="AAA", latitude=0, longitude=0),
                StationMetadata(name="B", telecode="BBB", latitude=0, longitude=10),
                StationMetadata(name="Corridor", telecode="COR", latitude=0, longitude=5, centrality_score=10),
                StationMetadata(name="Detour", telecode="DET", latitude=3, longitude=5, centrality_score=100),
            ]
        ),
        CandidateTransferConfig(max_candidates=10, min_corridor_km=50, max_corridor_km=50, corridor_ratio=0.01),
    )

    conservative = generator.generate(RouteQuery(from_station="A", to_station="B", date=date(2026, 7, 1)))
    wide = generator.generate(
        RouteQuery(
            from_station="A",
            to_station="B",
            date=date(2026, 7, 1),
            candidate_strategy="wide_detour",
            max_detour_ratio=0.2,
        )
    )

    assert "Corridor" in conservative
    assert "Detour" not in conservative
    assert "Detour" in wide


def test_lhasa_query_injects_strategic_corridor_hubs() -> None:
    generator = CandidateTransferStationGenerator(
        StationMetadataRepository(
            [
                StationMetadata(name="东莞", telecode="RTQ", latitude=23.02, longitude=113.75),
                StationMetadata(name="拉萨", telecode="LSO", latitude=29.65, longitude=91.10),
                StationMetadata(name="郑州", telecode="ZZF", latitude=34.75, longitude=113.62, centrality_score=95),
                StationMetadata(name="西安", telecode="XAY", latitude=34.34, longitude=108.94, centrality_score=95),
                StationMetadata(name="兰州", telecode="LZJ", latitude=36.06, longitude=103.84, centrality_score=90),
                StationMetadata(name="西宁", telecode="XNO", latitude=36.62, longitude=101.78, centrality_score=88),
                StationMetadata(name="格尔木", telecode="GRO", latitude=36.40, longitude=94.90, centrality_score=80),
            ]
        )
    )

    candidates = generator.generate(RouteQuery(from_station="东莞", to_station="拉萨", date=date(2026, 7, 1), candidate_limit=10))

    assert {"郑州", "西安", "兰州", "西宁", "格尔木"}.issubset(candidates)


def test_default_candidates_include_normal_train_transfer_hubs() -> None:
    generator = CandidateTransferStationGenerator()

    candidates = generator.generate(RouteQuery(from_station="北京", to_station="上海", date=date(2026, 7, 1)))

    assert "徐州" in candidates
    assert "南京" in candidates
    assert "南京南" in candidates


def test_station_metadata_repository_loads_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "station_metadata.csv"
    csv_path.write_text(
        "name,telecode,latitude,longitude,centrality_score\n"
        "北京,BJP,39.90151,116.478602,80\n"
        "上海,SHH,31.249,121.455,90\n",
        encoding="utf-8",
    )

    stations = load_station_metadata(csv_path)

    assert [station.name for station in stations] == ["北京", "上海"]
    assert stations[0].telecode == "BJP"
    assert stations[0].latitude == 39.90151
    assert stations[0].longitude == 116.478602


def test_station_metadata_repository_falls_back_when_csv_missing(tmp_path: Path) -> None:
    stations = load_station_metadata(tmp_path / "missing.csv")

    assert any(station.name == "北京" for station in stations)
    assert any(station.name == "上海" for station in stations)


def test_candidate_generator_filters_endpoint_neighbor_stations() -> None:
    generator = CandidateTransferStationGenerator(
        StationMetadataRepository(
            [
                StationMetadata(name="A", telecode="AAA", latitude=0, longitude=0),
                StationMetadata(name="B", telecode="BBB", latitude=0, longitude=10),
                StationMetadata(name="NearA", telecode="NEA", latitude=0, longitude=0.05, centrality_score=100),
                StationMetadata(name="Middle", telecode="MID", latitude=0, longitude=5, centrality_score=1),
            ]
        )
    )

    candidates = generator.generate(RouteQuery(from_station="A", to_station="B", date=date(2026, 7, 1)))

    assert "NearA" not in candidates
    assert candidates == ["Middle"]
