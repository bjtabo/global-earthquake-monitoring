"""Creates the schema and the two application roles. The only code that reads the master secret."""
import json
import os
import traceback
import urllib.request
from pathlib import Path

from psycopg import sql

from shared import db

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()
CREATE_ROLE = sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}")
ALTER_ROLE = sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}")
GRANTS = [
    "GRANT CONNECT ON DATABASE {db} TO api_reader, ingest_writer",
    "GRANT USAGE ON SCHEMA public TO api_reader, ingest_writer",
    "GRANT SELECT ON earthquakes, ingest_state TO api_reader",
    "GRANT SELECT, INSERT, UPDATE ON earthquakes TO ingest_writer",
    "GRANT SELECT, UPDATE ON ingest_state TO ingest_writer",
]


def apply(conn, passwords: dict[str, str], dbname: str) -> None:
    with conn.transaction():
        conn.execute(SCHEMA)
        for role, password in passwords.items():
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            stmt = ALTER_ROLE if exists else CREATE_ROLE
            conn.execute(stmt.format(sql.Identifier(role), sql.Literal(_verifier(conn, role, password))))
        for grant in GRANTS:
            conn.execute(sql.SQL(grant).format(db=sql.Identifier(dbname)))


def _verifier(conn, role: str, password: str) -> str:
    # The server receives, stores and can log only the SCRAM verifier, never the password.
    return conn.pgconn.encrypt_password(password.encode(), role.encode(), b"scram-sha-256").decode()


def handler(event, context):
    if "ResponseURL" not in event:
        return run()
    # CloudFormation custom resource: always answer, or the stack waits until ServiceTimeout.
    status, reason = "SUCCESS", ""
    try:
        if event["RequestType"] != "Delete":
            run()
    except Exception as exc:
        traceback.print_exc()
        status, reason = "FAILED", f"{type(exc).__name__}: {exc}"[:1000]
    respond(event, status, reason)


def respond(event: dict, status: str, reason: str) -> None:
    body = {"Status": status, "Reason": reason, "PhysicalResourceId": event.get("PhysicalResourceId", "schema"),
            "StackId": event["StackId"], "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"]}
    request = urllib.request.Request(event["ResponseURL"], data=json.dumps(body).encode(), method="PUT",
                                     headers={"Content-Type": ""})
    urllib.request.urlopen(request, timeout=10).close()


def run() -> dict:
    master = db.read_secret(os.environ["MASTER_SECRET_ARN"])
    passwords = {
        "api_reader": db.read_secret(os.environ["API_READER_SECRET_ARN"])["password"],
        "ingest_writer": db.read_secret(os.environ["INGEST_WRITER_SECRET_ARN"])["password"],
    }
    with db.connect(master["username"], master["password"]) as conn:
        apply(conn, passwords, os.environ.get("DB_NAME", "quakes"))
    return {"status": "ok"}
