"""Writes events and ingestion state. Every write is idempotent."""
from dataclasses import dataclass
from datetime import datetime

from usgs import Event

LOCK_ID = 7210001

UPSERT = """
INSERT INTO earthquakes (id, mag, mag_type, place, event_time, updated, latitude, longitude,
                         depth_km, status, event_type, url)
SELECT * FROM unnest(%s::text[], %s::float8[], %s::text[], %s::text[], %s::timestamptz[], %s::timestamptz[],
                     %s::float8[], %s::float8[], %s::float8[], %s::text[], %s::text[], %s::text[])
ON CONFLICT (id) DO UPDATE SET
    mag = EXCLUDED.mag, mag_type = EXCLUDED.mag_type, place = EXCLUDED.place,
    event_time = EXCLUDED.event_time, updated = EXCLUDED.updated, latitude = EXCLUDED.latitude,
    longitude = EXCLUDED.longitude, depth_km = EXCLUDED.depth_km, status = EXCLUDED.status,
    event_type = EXCLUDED.event_type, url = EXCLUDED.url, ingested_at = now()
WHERE earthquakes.updated < EXCLUDED.updated
"""

COLUMNS = ("id", "mag", "mag_type", "place", "event_time", "updated", "latitude", "longitude",
           "depth_km", "status", "event_type", "url")


@dataclass(frozen=True)
class State:
    last_success_at: datetime | None
    last_feed_generated: datetime | None
    last_upserted: int | None


def try_lock(conn) -> bool:
    return conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]


def upsert(conn, events: list[Event]) -> int:
    newest: dict[str, Event] = {}
    for e in events:
        if e.id not in newest or e.updated > newest[e.id].updated:
            newest[e.id] = e
    if not newest:
        return 0
    columns = [[getattr(e, name) for e in newest.values()] for name in COLUMNS]
    return conn.execute(UPSERT, columns).rowcount


def read_state(conn) -> State:
    row = conn.execute(
        "SELECT last_success_at, last_feed_generated, last_upserted FROM ingest_state WHERE id = 1"
    ).fetchone()
    return State(*row)


def write_state(conn, generated: datetime, upserted: int) -> None:
    conn.execute(
        "UPDATE ingest_state SET last_success_at = now(),"
        " last_feed_generated = GREATEST(last_feed_generated, %s), last_upserted = %s WHERE id = 1",
        (generated, upserted),
    )


def status(conn) -> dict:
    rows, oldest, newest = conn.execute(
        "SELECT count(*), min(event_time), max(event_time) FROM earthquakes"
    ).fetchone()
    generated = read_state(conn).last_feed_generated

    def iso(value):
        return value.isoformat() if value else None

    return {"rows": rows, "oldest": iso(oldest), "newest": iso(newest), "last_feed_generated": iso(generated)}
