import time
from datetime import UTC, datetime, timedelta

import queries

NOW = datetime(2026, 10, 22, 15, 30, tzinfo=UTC)


def add(admin, id, mag, when, kind="earthquake"):
    admin.execute(
        "INSERT INTO earthquakes (id, mag, place, event_time, updated, latitude, longitude, event_type)"
        " VALUES (%s, %s, 'p', %s, %s, 0, 0, %s)",
        (id, mag, when, when, kind),
    )


def test_significant_is_strictly_above_4_in_the_last_24_hours(admin, reader):
    add(admin, "exactly4", 4.0, NOW - timedelta(hours=1))
    add(admin, "above", 4.1, NOW - timedelta(hours=1))
    add(admin, "newest", 6.0, NOW - timedelta(minutes=5))
    add(admin, "too-old", 5.0, NOW - timedelta(hours=24, minutes=1))
    add(admin, "blast", 4.5, NOW - timedelta(hours=2), kind="quarry blast")
    add(admin, "no-mag", None, NOW - timedelta(hours=1))
    assert [q["id"] for q in queries.significant(reader, NOW)] == ["newest", "above"]


def test_daily_average_uses_seven_complete_utc_days_and_counts_empty_days(admin, reader):
    add(admin, "today", 1.0, NOW - timedelta(hours=1))
    add(admin, "d1-late", 1.0, datetime(2026, 10, 21, 23, 59, tzinfo=UTC))
    add(admin, "d1-early", 1.0, datetime(2026, 10, 21, 0, 0, tzinfo=UTC))
    add(admin, "d7", 1.0, datetime(2026, 10, 15, 0, 0, tzinfo=UTC))
    add(admin, "d8", 1.0, datetime(2026, 10, 14, 23, 59, tzinfo=UTC))
    add(admin, "blast", 1.0, datetime(2026, 10, 20, 12, 0, tzinfo=UTC), kind="explosion")
    report = queries.daily_average(reader, NOW)
    assert [d["date"] for d in report["days"]] == [f"2026-10-{n}" for n in range(15, 22)]
    assert [d["count"] for d in report["days"]] == [1, 0, 0, 0, 0, 0, 2]
    assert report["average"] == round(3 / 7, 2)
    assert report["start"] == "2026-10-15T00:00:00+00:00" and report["end"] == "2026-10-22T00:00:00+00:00"


def test_search_returns_the_largest_first_within_bounds(admin, reader):
    for i, mag in enumerate([3.0, 6.1, 5.5, 4.2, 7.0, 2.0]):
        add(admin, f"q{i}", mag, NOW - timedelta(days=i + 1))
    add(admin, "outside", 9.0, NOW - timedelta(days=40))
    rows = queries.search(reader, NOW - timedelta(days=30), NOW, None, 3)
    assert [r["mag"] for r in rows] == [7.0, 6.1, 5.5]


def test_search_min_mag_is_inclusive(admin, reader):
    add(admin, "a", 4.9, NOW - timedelta(days=1))
    add(admin, "b", 5.0, NOW - timedelta(days=1))
    assert [r["id"] for r in queries.search(reader, NOW - timedelta(days=2), NOW, 5.0, 10)] == ["b"]


def test_last_feed_generated(admin, reader):
    assert queries.last_feed_generated(reader) is None
    admin.execute("UPDATE ingest_state SET last_feed_generated = %s", (NOW,))
    assert queries.last_feed_generated(reader) == NOW


def test_reports_stay_fast_on_a_year_of_data(admin, reader):
    admin.execute(
        "INSERT INTO earthquakes (id, mag, place, event_time, updated, latitude, longitude, event_type)"
        " SELECT 'syn' || g, (g %% 80) / 10.0, 'p', %(now)s - g * interval '3 minutes', now(), 0, 0, 'earthquake'"
        " FROM generate_series(1, 175000) AS g",
        {"now": NOW},
    )
    admin.execute("ANALYZE earthquakes")
    for report in (
        lambda: queries.significant(reader, NOW),
        lambda: queries.daily_average(reader, NOW),
        lambda: queries.search(reader, NOW - timedelta(days=365), NOW, None, 5),
    ):
        started = time.perf_counter()
        report()
        assert time.perf_counter() - started < 0.5
