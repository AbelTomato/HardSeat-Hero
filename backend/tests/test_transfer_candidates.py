from datetime import date

from app.domain.models import RouteQuery, StationMetadata
from app.services.transfer_candidates import (
    CandidateTransferConfig,
    CandidateTransferStationGenerator,
    StationMetadataRepository,
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
