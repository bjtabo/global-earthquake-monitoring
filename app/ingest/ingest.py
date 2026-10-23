"""Lambda entry point: scheduled ingestion, one backfill window, or a read-only status report."""
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import metrics
import store
import usgs
from shared import db

BASE_URL = os.environ.get("USGS_BASE_URL", "https://earthquake.usgs.gov")
WINDOW = timedelta(days=30)
OVERLAP = timedelta(hours=1)

Fetch = Callable[[str], dict]


def handler(event, context):
    secret = db.read_secret(os.environ["WRITER_SECRET_ARN"])
    with db.connect(secret["username"], secret["password"]) as conn:
        return dispatch(conn, event or {}, datetime.now(UTC), usgs.fetch_json)


def dispatch(conn, event: dict, now: datetime, fetch: Fetch) -> dict:
    mode = event.get("mode", "scheduled")
    if mode == "status":
        return store.status(conn)
    if mode not in ("scheduled", "backfill"):
        raise ValueError(f"unknown mode {mode!r}")
    if not store.try_lock(conn):
        return {"status": "skipped", "reason": "another run holds the ingestion lock"}
    if mode == "backfill":
        return backfill(conn, int(event["window"]), now, fetch)
    return scheduled(conn, now, fetch)


def scheduled(conn, now: datetime, fetch: Fetch) -> dict:
    state = store.read_state(conn)
    conn.commit()
    lag = None if state.last_feed_generated is None else now - state.last_feed_generated
    feed = usgs.choose_feed(lag)
    try:
        if feed:
            docs = iter([fetch(usgs.feed_url(BASE_URL, feed))])
        else:
            docs = fetch_range(fetch, state.last_feed_generated - OVERLAP, now)
        generated, upserted, skipped = write_docs(conn, docs)
        store.write_state(conn, generated, upserted)
        conn.commit()
    except Exception:
        metrics.emit(IngestSuccess=0, **({"DataLagSeconds": lag.total_seconds()} if lag is not None else {}))
        raise
    metrics.emit(IngestSuccess=1, DataLagSeconds=(now - generated).total_seconds(),
                 EventsUpserted=upserted, EventsSkipped=skipped)
    return {"status": "ok", "source": feed or "fdsn", "upserted": upserted}


def backfill(conn, window: int, now: datetime, fetch: Fetch) -> dict:
    end = now - window * WINDOW
    start = end - WINDOW
    _, upserted, _ = write_docs(conn, fetch_window(fetch, start, end))
    return {"status": "ok", "window": window, "start": start.isoformat(), "end": end.isoformat(), "upserted": upserted}


def fetch_range(fetch: Fetch, start: datetime, end: datetime) -> Iterator[dict]:
    while start < end:
        stop = min(start + WINDOW, end)
        yield from fetch_window(fetch, start, stop)
        start = stop


def fetch_window(fetch: Fetch, start: datetime, end: datetime) -> Iterator[dict]:
    doc = fetch(usgs.fdsn_url(BASE_URL, start, end))
    if len(doc.get("features") or []) >= usgs.FDSN_LIMIT:  # truncated: split until each half fits
        middle = start + (end - start) / 2
        yield from fetch_window(fetch, start, middle)
        yield from fetch_window(fetch, middle, end)
    else:
        yield doc


def write_docs(conn, docs: Iterator[dict]) -> tuple[datetime, int, int]:
    generated, upserted, skipped = None, 0, 0
    for doc in docs:
        generated, events, bad = usgs.parse(doc)
        upserted += store.upsert(conn, events)
        skipped += bad
        conn.commit()
    return generated, upserted, skipped
