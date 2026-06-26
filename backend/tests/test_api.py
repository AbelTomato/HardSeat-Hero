from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_route_search_api() -> None:
    response = client.post(
        "/api/routes/search",
        json={
            "from_station": "北京",
            "to_station": "上海",
            "date": "2026-07-01",
            "max_transfers": 1,
            "min_transfer_minutes": 30,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "mock"
    assert len(body["plans"]) > 0


def test_station_search_api() -> None:
    response = client.get("/api/stations/search", params={"q": "南"})

    assert response.status_code == 200
    assert "南京南" in response.json()["stations"]


def test_provider_status_exposes_transfer_diagnostics() -> None:
    response = client.get("/api/providers/status")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "mock"
    assert body["transfer_candidate_enabled"] is False
    assert body["max_remote_queries"] > 0
    assert body["max_concurrent_remote_queries"] > 0
