from datetime import UTC, datetime, timedelta

import store
from conftest import PASSWORDS
from usgs import Event

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def event(id="ev1", mag=4.5, updated=T0, **overrides):
    fields = dict(id=id, mag=mag, mag_type="mb", place="somewhere", event_time=T0 - timedelta(hours=1),
                  updated=updated, latitude=1.0, longitude=2.0, depth_km=10.0, status="reviewed",
                  event_type="earthquake", url=None)
    return Event(**(fields | overrides))


def mags(conn):
    return dict(conn.execute("SELECT id, mag FROM earthquakes ORDER BY id").fetchall())


def test_upsert_inserts_new_rows(writer):
    assert store.upsert(writer, [event("a"), event("b", mag=None)]) == 2
    assert mags(writer) == {"a": 4.5, "b": None}


def test_newer_update_wins(writer):
    store.upsert(writer, [event(mag=4.5)])
    assert store.upsert(writer, [event(mag=4.9, updated=T0 + timedelta(minutes=5))]) == 1
    assert mags(writer) == {"ev1": 4.9}


def test_equal_or_older_update_is_ignored(writer):
    store.upsert(writer, [event(mag=4.5)])
    assert store.upsert(writer, [event(mag=9.9)]) == 0
    assert store.upsert(writer, [event(mag=9.9, updated=T0 - timedelta(minutes=1))]) == 0
    assert mags(writer) == {"ev1": 4.5}


def test_duplicate_ids_in_one_batch_keep_the_newest(writer):
    batch = [event(mag=4.1), event(mag=4.7, updated=T0 + timedelta(minutes=1))]
    assert store.upsert(writer, batch) == 1
    assert mags(writer) == {"ev1": 4.7}


def test_empty_batch_writes_nothing(writer):
    assert store.upsert(writer, []) == 0


def test_feed_time_never_moves_backwards(writer):
    store.write_state(writer, T0, 10)
    store.write_state(writer, T0 - timedelta(hours=1), 3)
    state = store.read_state(writer)
    assert state.last_feed_generated == T0 and state.last_upserted == 3


def test_only_one_connection_holds_the_lock(writer):
    from shared import db

    assert store.try_lock(writer) is True
    with db.connect("ingest_writer", PASSWORDS["ingest_writer"]) as other:
        assert store.try_lock(other) is False


def test_status_is_json_ready(writer):
    store.upsert(writer, [event("a"), event("b")])
    status = store.status(writer)
    assert status["rows"] == 2 and isinstance(status["oldest"], str) and status["last_feed_generated"] is None
