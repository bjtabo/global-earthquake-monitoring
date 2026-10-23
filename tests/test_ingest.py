import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import ingest
import store
import usgs
from conftest import PASSWORDS

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "feed.json").read_text())
GENERATED = datetime.fromtimestamp(1761148800, UTC)
NOW = GENERATED + timedelta(seconds=90)


def emf_lines(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]


def recorder(urls, doc=FIXTURE):
    def fetch(url):
        urls.append(url)
        return doc
    return fetch


def down(url):
    raise usgs.UsgsError("timeout")


def test_first_run_reads_the_month_feed_and_records_state(writer, capsys):
    urls = []
    result = ingest.dispatch(writer, {"mode": "scheduled"}, NOW, recorder(urls))
    assert result == {"status": "ok", "source": "all_month", "upserted": 3}
    assert urls == ["https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_month.geojson"]
    assert store.read_state(writer).last_feed_generated == GENERATED
    (line,) = emf_lines(capsys)
    assert (line["IngestSuccess"], line["DataLagSeconds"], line["EventsUpserted"], line["EventsSkipped"]) == (1, 90, 3, 2)
    assert line["Service"] == "ingest"
    assert line["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [["Service"]]


def test_a_recent_success_means_the_day_feed(writer):
    store.write_state(writer, NOW - timedelta(minutes=5), 0)
    urls = []
    ingest.dispatch(writer, {}, NOW, recorder(urls))
    assert urls[0].endswith("/all_day.geojson")


def test_usgs_failure_reports_lag_from_the_last_success_and_raises(writer, capsys):
    store.write_state(writer, NOW - timedelta(minutes=12), 0)
    writer.commit()
    with pytest.raises(usgs.UsgsError):
        ingest.dispatch(writer, {}, NOW, down)
    (line,) = emf_lines(capsys)
    assert line["IngestSuccess"] == 0 and line["DataLagSeconds"] == 720
    assert store.read_state(writer).last_feed_generated == NOW - timedelta(minutes=12)


def test_failure_without_history_reports_no_lag(writer, capsys):
    with pytest.raises(usgs.UsgsError):
        ingest.dispatch(writer, {}, NOW, down)
    (line,) = emf_lines(capsys)
    assert line["IngestSuccess"] == 0 and "DataLagSeconds" not in line


def test_a_gap_over_a_month_uses_fdsn_in_30_day_windows(writer):
    store.write_state(writer, NOW - timedelta(days=45), 0)
    urls = []
    assert ingest.dispatch(writer, {}, NOW, recorder(urls))["source"] == "fdsn"
    assert len(urls) == 2 and all("/fdsnws/event/1/query?" in u for u in urls)


def test_backfill_loads_one_window_and_leaves_state_alone(writer):
    result = ingest.dispatch(writer, {"mode": "backfill", "window": 3}, NOW, recorder([]))
    assert (result["status"], result["window"], result["upserted"]) == ("ok", 3, 3)
    assert result["end"] == (NOW - timedelta(days=90)).isoformat()
    assert store.read_state(writer).last_feed_generated is None


def test_backfill_halves_a_window_that_hits_the_row_limit(writer, monkeypatch):
    monkeypatch.setattr(usgs, "FDSN_LIMIT", 3)
    urls = []

    def fetch(url):
        urls.append(url)
        return FIXTURE if len(urls) == 1 else FIXTURE | {"features": FIXTURE["features"][:1]}

    ingest.dispatch(writer, {"mode": "backfill", "window": 0}, NOW, fetch)
    assert len(urls) == 3


def test_a_run_is_skipped_while_another_holds_the_lock(writer):
    from shared import db

    with db.connect("ingest_writer", PASSWORDS["ingest_writer"]) as other:
        assert store.try_lock(other)
        assert ingest.dispatch(writer, {}, NOW, recorder([]))["status"] == "skipped"


def test_a_backfill_is_skipped_while_another_holds_the_lock(writer):
    from shared import db

    with db.connect("ingest_writer", PASSWORDS["ingest_writer"]) as other:
        assert store.try_lock(other)
        urls = []
        assert ingest.dispatch(writer, {"mode": "backfill", "window": 0}, NOW, recorder(urls))["status"] == "skipped"
        assert urls == []


def test_status_mode_needs_no_lock(writer):
    assert ingest.dispatch(writer, {"mode": "status"}, NOW, recorder([]))["rows"] == 0


def test_unknown_mode_is_rejected(writer):
    with pytest.raises(ValueError):
        ingest.dispatch(writer, {"mode": "drop"}, NOW, recorder([]))
