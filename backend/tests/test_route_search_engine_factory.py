import pytest

from app.adapters.mock_provider import MockTrainDataProvider
from app.services.route_search_engine_factory import create_route_search_engine
from app.services.time_expanded_route_search import TimeExpandedRouteSearchEngine


def test_create_route_search_engine_defaults_to_candidate(monkeypatch) -> None:
    monkeypatch.delenv("ROUTE_SEARCH_ENGINE", raising=False)

    engine = create_route_search_engine(MockTrainDataProvider())

    assert engine.status["search_engine"] == "candidate"
    assert engine.status["provider"] == "mock"


def test_create_route_search_engine_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setenv("ROUTE_SEARCH_ENGINE", "bad")

    with pytest.raises(ValueError, match="Unsupported ROUTE_SEARCH_ENGINE"):
        create_route_search_engine(MockTrainDataProvider())


def test_create_route_search_engine_supports_time_expanded(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "time_expanded.sqlite3"
    monkeypatch.setenv("ROUTE_SEARCH_ENGINE", "time-expanded")
    monkeypatch.setenv("TIME_EXPANDED_GRAPH_DB", str(db_path))
    monkeypatch.setenv("TIME_EXPANDED_GRAPH_PROVIDER", "provider-x")

    engine = create_route_search_engine(MockTrainDataProvider())

    assert isinstance(engine, TimeExpandedRouteSearchEngine)
    assert engine.status["search_engine"] == "time-expanded"
    assert engine.status["provider"] == "provider-x"


def test_create_route_search_engine_time_expanded_requires_db(monkeypatch) -> None:
    monkeypatch.setenv("ROUTE_SEARCH_ENGINE", "time-expanded")
    monkeypatch.delenv("TIME_EXPANDED_GRAPH_DB", raising=False)
    monkeypatch.delenv("STATIC_PRICE_DB", raising=False)

    with pytest.raises(ValueError, match="TIME_EXPANDED_GRAPH_DB or STATIC_PRICE_DB is required"):
        create_route_search_engine(MockTrainDataProvider())