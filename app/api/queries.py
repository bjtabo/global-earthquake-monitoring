"""The three reports. Only event_type 'earthquake' counts; every query is bounded on an indexed column."""
from datetime import UTC, datetime, timedelta

FIELDS = ("id", "mag", "mag_type", "place", "event_time", "latitude", "longitude", "depth_km", "url")
COLUMNS = ", ".join(FIELDS)

SIGNIFICANT = f"""
SELECT {COLUMNS} FROM earthquakes
WHERE event_type = 'earthquake' AND event_time >= %(since)s AND event_time <= %(now)s AND mag > 4.0
ORDER BY event_time DESC
LIMIT 500
"""

DAILY = """
SELECT (event_time AT TIME ZONE 'UTC')::date AS day, count(*) FROM earthquakes
WHERE event_type = 'earthquake' AND event_time >= %(start)s AND event_time < %(end)s
GROUP BY 1
"""

SEARCH = f"""
SELECT {COLUMNS} FROM earthquakes
WHERE event_type = 'earthquake' AND event_time >= %(start)s AND event_time < %(end)s
  AND (%(min_mag)s::float8 IS NULL OR mag >= %(min_mag)s::float8)
ORDER BY mag DESC NULLS LAST, event_time DESC
LIMIT %(limit)s
"""


def _event(row) -> dict:
    event = dict(zip(FIELDS, row))
    event["event_time"] = event["event_time"].isoformat()
    return event


def significant(conn, now: datetime) -> list[dict]:
    rows = conn.execute(SIGNIFICANT, {"since": now - timedelta(hours=24), "now": now}).fetchall()
    return [_event(r) for r in rows]


def daily_average(conn, now: datetime) -> dict:
    end = datetime.combine(now.astimezone(UTC).date(), datetime.min.time(), UTC)
    start = end - timedelta(days=7)
    counts = dict(conn.execute(DAILY, {"start": start, "end": end}).fetchall())
    days = [(start + timedelta(days=n)).date() for n in range(7)]
    per_day = [{"date": d.isoformat(), "count": counts.get(d, 0)} for d in days]
    return {
        "days": per_day,
        "average": round(sum(d["count"] for d in per_day) / 7, 2),
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def search(conn, start: datetime, end: datetime, min_mag: float | None, limit: int) -> list[dict]:
    rows = conn.execute(SEARCH, {"start": start, "end": end, "min_mag": min_mag, "limit": limit}).fetchall()
    return [_event(r) for r in rows]


def last_feed_generated(conn) -> datetime | None:
    return conn.execute("SELECT last_feed_generated FROM ingest_state WHERE id = 1").fetchone()[0]
