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


def test_candidate_generator_prioritizes_cheap_transfer_score_over_centrality() -> None:
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
