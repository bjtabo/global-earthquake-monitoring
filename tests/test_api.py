import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

from conftest import PASSWORDS

REPORTS = ("/api/quakes/significant", "/api/stats/daily-average", "/api/quakes/search")


@pytest.fixture
def client(admin, monkeypatch):
    monkeypatch.setenv("DB_USER", "api_reader")
    monkeypatch.setenv("DB_PASSWORD", PASSWORDS["api_reader"])
    import main

    with TestClient(main.app) as c:
        yield c


def add(admin, id, mag, when):
    admin.execute(
        "INSERT INTO earthquakes (id, mag, place, event_time, updated, latitude, longitude, event_type)"
        " VALUES (%s, %s, 'p', %s, %s, 0, 0, 'earthquake')",
        (id, mag, when, when),
    )


def test_significant_reports_data_age(client, admin):
    now = datetime.now(UTC)
    add(admin, "big", 5.2, now - timedelta(hours=2))
    add(admin, "small", 3.0, now - timedelta(hours=2))
    admin.execute("UPDATE ingest_state SET last_feed_generated = %s", (now - timedelta(minutes=2),))
    body = client.get("/api/quakes/significant").json()
    assert body["count"] == 1 and body["earthquakes"][0]["id"] == "big"
    assert 110 <= body["data_age_seconds"] <= 200


def test_reports_are_never_cached(client):
    # A cached report would keep saying "no recent earthquakes" while the data behind it is stale.
    for path in REPORTS:
        response = client.get(path)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"


def test_health_endpoints_are_never_cached(client):
    for path in ("/health", "/livez"):
        response = client.get(path)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"


def test_daily_average_has_seven_days(client):
    body = client.get("/api/stats/daily-average").json()
    assert len(body["days"]) == 7 and body["average"] == 0


def test_search_defaults_to_the_top_five_of_the_last_30_days(client, admin):
    now = datetime.now(UTC)
    for i in range(8):
        add(admin, f"q{i}", 3.0 + i / 10, now - timedelta(days=i + 1))
    add(admin, "too-old", 9.0, now - timedelta(days=31))
    body = client.get("/api/quakes/search").json()
    assert body["limit"] == 5
    assert [q["id"] for q in body["earthquakes"]] == ["q7", "q6", "q5", "q4", "q3"]


@pytest.mark.parametrize(
    "query",
    [
        "start=2026-10-10&end=2026-10-01",
        "start=2024-01-01&end=2026-01-01",
        "start=2025-01-01&end=2026-01-03",  # 367 days
        "limit=0",
        "limit=101",
        "min_mag=11",
        "start=yesterday",
    ],
)
def test_search_rejects_bad_input(client, query):
    response = client.get(f"/api/quakes/search?{query}")
    assert response.status_code == 422 and response.headers["cache-control"] == "no-store"


def test_search_accepts_plain_dates(client):
    assert client.get("/api/quakes/search?start=2026-09-01&end=2026-10-01").status_code == 200


def test_search_accepts_exactly_366_days(client):
    assert client.get("/api/quakes/search?start=2025-01-01&end=2026-01-02").status_code == 200


def test_an_unhandled_error_is_logged_as_500(admin, monkeypatch, caplog):
    # The runbook finds 5xx in the access log; a crash must show up there too.
    monkeypatch.setenv("DB_USER", "api_reader")
    monkeypatch.setenv("DB_PASSWORD", PASSWORDS["api_reader"])
    import main

    def crash(conn, now):
        raise RuntimeError("bug")

    monkeypatch.setattr(main.queries, "significant", crash)
    with TestClient(main.app, raise_server_exceptions=False) as c, caplog.at_level("INFO", logger="api"):
        assert c.get("/api/quakes/significant").status_code == 500
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "api" and r.getMessage().startswith("{")]
    assert {"path": "/api/quakes/significant", "status": 500}.items() <= next(l for l in lines if "status" in l).items()


def test_database_outage_returns_503_the_edge_will_not_cache(client, monkeypatch):
    def broken():
        raise PoolTimeout("no connection available")

    monkeypatch.setattr(client.app.state.pool, "connection", broken)
    for path in REPORTS:
        response = client.get(path)
        assert response.status_code == 503 and response.headers["cache-control"] == "no-store"
    assert client.get("/health").status_code == 503
    assert client.get("/livez").status_code == 200


def test_write_methods_are_not_allowed(client):
    assert client.post("/api/quakes/search").status_code == 405


def test_no_interactive_docs(client):
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


def test_the_page_is_not_served_by_the_api(client):
    # The page and its assets live in S3 behind CloudFront.
    assert client.get("/").status_code == 404 and client.get("/static/app.js").status_code == 404
