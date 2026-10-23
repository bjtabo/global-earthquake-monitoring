import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest

pytestmark = pytest.mark.e2e
BASE = os.environ.get("BASE_URL", "").rstrip("/")


@pytest.fixture(scope="module")
def http():
    if not BASE:
        pytest.skip("BASE_URL is not set")
    with httpx.Client(base_url=BASE, timeout=15) as client:
        yield client


def test_health_reports_fresh_data(http):
    body = http.get("/health").json()
    assert body["database"] == "ok" and body["fresh"] is True


def test_significant_events_are_above_4_and_recent(http):
    now = datetime.now(UTC)
    for quake in http.get("/api/quakes/significant").json()["earthquakes"]:
        assert quake["mag"] > 4.0
        assert now - datetime.fromisoformat(quake["event_time"]) <= timedelta(hours=24, minutes=2)


def test_daily_average_covers_seven_days(http):
    body = http.get("/api/stats/daily-average").json()
    assert len(body["days"]) == 7 and body["average"] > 0


def test_default_search_is_the_top_five_by_magnitude(http):
    mags = [q["mag"] for q in http.get("/api/quakes/search").json()["earthquakes"]]
    assert len(mags) == 5 and mags == sorted(mags, reverse=True)


def test_history_reaches_back_a_year(http):
    today = datetime.now(UTC).date()
    params = {"start": str(today - timedelta(days=360)), "end": str(today - timedelta(days=330)), "limit": 1}
    assert http.get("/api/quakes/search", params=params).json()["count"] == 1


def test_bad_input_is_rejected(http):
    assert http.get("/api/quakes/search", params={"limit": 0}).status_code == 422


def test_http_redirects_to_https():
    if not BASE:
        pytest.skip("BASE_URL is not set")
    response = httpx.get(BASE.replace("https://", "http://", 1) + "/health", timeout=15)
    assert response.status_code == 301 and response.headers["location"].startswith("https://")


def test_security_headers(http):
    response = http.get("/")
    assert "max-age" in response.headers["strict-transport-security"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_page_and_assets_come_from_the_edge(http):
    page = http.get("/")
    assert page.status_code == 200 and "<h1>Earthquake monitor</h1>" in page.text
    for asset in ("/app.js", "/style.css"):
        assert http.get(asset).status_code == 200


def test_reports_are_never_cached(http):
    for path in ("/api/quakes/significant", "/api/stats/daily-average", "/api/quakes/search", "/health"):
        http.get(path)
        again = http.get(path)  # a first request is always a Miss; a cached path would Hit the second time
        assert again.headers["cache-control"] == "no-store"
        assert "Hit" not in again.headers.get("x-cache", "")


def test_alb_is_not_reachable_from_the_internet():
    alb = os.environ.get("ALB_DNS")
    if not alb:
        pytest.skip("ALB_DNS is not set")
    with pytest.raises(httpx.HTTPError):
        httpx.get(f"http://{alb}/livez", timeout=5)
