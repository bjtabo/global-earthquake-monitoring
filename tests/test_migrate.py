import io
import json

import psycopg
import pytest

import migrate
from conftest import ADMIN, PASSWORDS


def test_apply_twice_keeps_one_state_row(migrated):
    with psycopg.connect(**ADMIN, dbname=migrated) as conn:
        migrate.apply(conn, PASSWORDS, migrated)
        assert conn.execute("SELECT count(*) FROM ingest_state").fetchone()[0] == 1


def test_reader_can_read_both_tables(reader):
    reader.execute("SELECT count(*) FROM earthquakes").fetchone()
    reader.execute("SELECT last_feed_generated FROM ingest_state").fetchone()


def test_reader_cannot_write(reader):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reader.execute(
            "INSERT INTO earthquakes (id, event_time, updated, latitude, longitude, event_type)"
            " VALUES ('x', now(), now(), 0, 0, 'earthquake')"
        )


def test_writer_cannot_delete(writer):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writer.execute("DELETE FROM earthquakes")


def test_writer_cannot_create_objects(writer):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writer.execute("CREATE TABLE intruder (id int)")


def test_app_roles_have_no_elevated_attributes_and_own_nothing(admin):
    rows = admin.execute(
        "SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles"
        " WHERE rolname IN ('api_reader', 'ingest_writer') ORDER BY rolname"
    ).fetchall()
    assert rows == [("api_reader", False, False, False, False), ("ingest_writer", False, False, False, False)]
    owners = {r[0] for r in admin.execute(
        "SELECT tableowner FROM pg_tables WHERE tablename IN ('earthquakes', 'ingest_state')"
    )}
    assert owners.isdisjoint({"api_reader", "ingest_writer"})


def test_constraints_reject_impossible_rows(writer):
    with pytest.raises(psycopg.errors.CheckViolation):
        writer.execute(
            "INSERT INTO earthquakes (id, event_time, updated, latitude, longitude, event_type)"
            " VALUES ('x', now(), now(), 91, 0, 'earthquake')"
        )


def test_role_passwords_never_reach_the_server_in_plain_text(migrated):
    sent = []
    with psycopg.connect(**ADMIN, dbname=migrated) as conn:
        execute = conn.execute

        def recording(query, params=None, **kwargs):
            sent.append(query.as_string(conn) if hasattr(query, "as_string") else str(query))
            return execute(query, params, **kwargs)

        conn.execute = recording
        migrate.apply(conn, PASSWORDS, migrated)
    assert sent and not any(password in statement for statement in sent for password in PASSWORDS.values())


CFN_EVENT = {"ResponseURL": "https://example.invalid/response", "StackId": "arn:stack", "RequestId": "req-1",
             "LogicalResourceId": "Migration", "ResourceProperties": {}}


@pytest.fixture
def cfn(monkeypatch):
    calls = {"run": 0, "sent": []}

    def run():
        calls["run"] += 1
        if calls.get("fail"):
            raise RuntimeError("database unreachable")
        return {"status": "ok"}

    def urlopen(request, timeout):
        calls["sent"].append((request.get_method(), request.full_url, request.get_header("Content-type"),
                              json.loads(request.data)))
        return io.BytesIO()

    monkeypatch.setattr(migrate, "run", run)
    monkeypatch.setattr(migrate.urllib.request, "urlopen", urlopen)
    return calls


def test_custom_resource_create_migrates_and_reports_success(cfn):
    migrate.handler(CFN_EVENT | {"RequestType": "Create"}, None)
    method, url, content_type, body = cfn["sent"][0]
    assert cfn["run"] == 1 and (method, url, content_type) == ("PUT", CFN_EVENT["ResponseURL"], "")
    assert body == {"Status": "SUCCESS", "Reason": "", "PhysicalResourceId": "schema", "StackId": "arn:stack",
                    "RequestId": "req-1", "LogicalResourceId": "Migration"}


def test_custom_resource_failure_is_reported_not_raised(cfn):
    cfn["fail"] = True
    migrate.handler(CFN_EVENT | {"RequestType": "Update", "PhysicalResourceId": "schema"}, None)
    body = cfn["sent"][0][3]
    assert body["Status"] == "FAILED" and body["Reason"] == "RuntimeError: database unreachable"


def test_custom_resource_delete_leaves_the_schema(cfn):
    migrate.handler(CFN_EVENT | {"RequestType": "Delete", "PhysicalResourceId": "schema"}, None)
    assert cfn["run"] == 0 and cfn["sent"][0][3]["Status"] == "SUCCESS"


def test_direct_invoke_migrates_without_a_response(cfn):
    assert migrate.handler({}, None) == {"status": "ok"} and cfn["run"] == 1 and cfn["sent"] == []
