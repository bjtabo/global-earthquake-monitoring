import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import usgs

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "feed.json").read_text())


@pytest.mark.parametrize(
    "lag, feed",
    [
        (None, "all_month"),
        (timedelta(minutes=5), "all_day"),
        (timedelta(hours=22, minutes=59), "all_day"),
        (timedelta(hours=23), "all_week"),
        (timedelta(days=6, hours=23), "all_month"),
        (timedelta(days=29, hours=23), None),
    ],
)
def test_choose_feed_picks_the_smallest_feed_that_covers_the_gap(lag, feed):
    assert usgs.choose_feed(lag) == feed


def test_parse_keeps_valid_events_and_counts_the_rest():
    generated, events, skipped = usgs.parse(FIXTURE)
    assert generated == datetime.fromtimestamp(1761148800, UTC)
    assert [e.id for e in events] == ["us7000aaaa", "ak0001", "uu0001"]
    assert skipped == 2


def test_parse_maps_fields():
    _, (quake, no_mag, blast), _ = usgs.parse(FIXTURE)
    assert (quake.mag, quake.latitude, quake.longitude, quake.depth_km) == (4.6, -20.1, -70.5, 35.0)
    assert quake.event_time == datetime.fromtimestamp(1761145200, UTC)
    assert quake.updated == datetime.fromtimestamp(1761146000, UTC)
    assert no_mag.mag is None and no_mag.depth_km is None
    assert blast.event_type == "quarry blast"


def test_parse_keeps_only_usgs_urls():
    _, events, _ = usgs.parse(FIXTURE)
    assert events[0].url == "https://earthquake.usgs.gov/earthquakes/eventpage/us7000aaaa"
    assert events[2].url is None


def test_parse_rejects_a_document_without_metadata():
    with pytest.raises(usgs.UsgsError):
        usgs.parse({"features": []})


def test_fdsn_url_has_bounds_limit_and_format():
    url = usgs.fdsn_url("https://earthquake.usgs.gov", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 31, tzinfo=UTC))
    assert url.startswith("https://earthquake.usgs.gov/fdsnws/event/1/query?")
    assert "starttime=2026-01-01T00%3A00%3A00" in url and "endtime=2026-01-31T00%3A00%3A00" in url
    assert "limit=20000" in url and "format=geojson" in url and "orderby=time-asc" in url


def test_feed_url():
    assert usgs.feed_url("https://earthquake.usgs.gov", "all_day") == (
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
    )


def test_fetch_json_wraps_network_errors():
    with pytest.raises(usgs.UsgsError):
        usgs.fetch_json("http://127.0.0.1:9/nothing", timeout=1)


def test_parse_skips_a_feature_without_id():
    feature = dict(FIXTURE["features"][0], id=None)
    _, events, skipped = usgs.parse({"metadata": FIXTURE["metadata"], "features": [feature]})
    assert (events, skipped) == ([], 1)
