import os
import uuid

import psycopg
import pytest
from psycopg import sql

ADMIN = {
    "host": os.environ.get("TEST_PG_HOST", "127.0.0.1"),
    "port": int(os.environ.get("TEST_PG_PORT", "55432")),
    "user": "postgres",
    "password": os.environ.get("TEST_PG_PASSWORD", "test"),
}
PASSWORDS = {"api_reader": "reader-local-only", "ingest_writer": "writer-local-only"}


@pytest.fixture(scope="session")
def database():
    name = f"quakes_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(**ADMIN, dbname="postgres", autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    os.environ.update(DB_HOST=ADMIN["host"], DB_PORT=str(ADMIN["port"]), DB_NAME=name, DB_SSLMODE="disable")
    yield name
    with psycopg.connect(**ADMIN, dbname="postgres", autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def migrated(database):
    import migrate

    with psycopg.connect(**ADMIN, dbname=database) as conn:
        migrate.apply(conn, PASSWORDS, database)
    return database


@pytest.fixture
def admin(migrated):
    with psycopg.connect(**ADMIN, dbname=migrated, autocommit=True) as conn:
        conn.execute("TRUNCATE earthquakes")
        conn.execute("UPDATE ingest_state SET last_success_at = NULL, last_feed_generated = NULL, last_upserted = NULL")
        yield conn


@pytest.fixture
def writer(admin):
    from shared import db

    with db.connect("ingest_writer", PASSWORDS["ingest_writer"]) as conn:
        yield conn


@pytest.fixture
def reader(admin):
    from shared import db

    with db.connect("api_reader", PASSWORDS["api_reader"]) as conn:
        yield conn
