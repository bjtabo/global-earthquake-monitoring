"""HTTP API. Nothing is cached: a stale report would hide new earthquakes. The page itself is served from S3."""
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import psycopg
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool, PoolTimeout

import queries
from shared import db

NO_STORE = "no-store"
MAX_RANGE = timedelta(days=366)
FRESH_SECONDS = 900

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = db.conninfo(os.environ.get("DB_USER", "api_reader"), os.environ.get("DB_PASSWORD", ""))
    app.state.pool = ConnectionPool(
        kwargs=settings | {"options": "-c statement_timeout=2000"},
        min_size=1, max_size=4, timeout=2, open=False, check=ConnectionPool.check_connection,
    )
    app.state.pool.open(wait=False)  # start even when the database is down; /livez stays up
    yield
    app.state.pool.close()


app = FastAPI(title="Earthquake monitor", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


class DatabaseUnavailable(Exception):
    pass


@app.exception_handler(DatabaseUnavailable)
async def database_unavailable(request: Request, exc: DatabaseUnavailable):
    return JSONResponse({"detail": "database unavailable"}, status_code=503)


@app.middleware("http")
async def access_log(request: Request, call_next):
    started = time.perf_counter()
    status = 500  # an unhandled exception becomes a 500 after this middleware, so log it as one
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers.setdefault("Cache-Control", NO_STORE)  # also FastAPI's own 404, 405 and 422
        return response
    finally:
        log.info(json.dumps({
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "viewer_ip": request.headers.get("x-viewer-ip"),
        }))


def _read(report, *args):
    try:
        with app.state.pool.connection() as conn:
            return report(conn, *args), queries.last_feed_generated(conn)
    except (PoolTimeout, psycopg.Error) as exc:
        log.warning(json.dumps({"event": "database_unavailable", "error": type(exc).__name__}))
        raise DatabaseUnavailable from exc


def _age(now: datetime, generated: datetime | None) -> int | None:
    return None if generated is None else int((now - generated).total_seconds())


def _report(now: datetime, generated: datetime | None, **body) -> JSONResponse:
    payload = {"generated_at": now.isoformat(), "data_age_seconds": _age(now, generated), **body}
    return JSONResponse(payload)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@app.get("/livez")
def livez():
    return JSONResponse({"status": "ok"})


@app.get("/health")
def health():
    now = datetime.now(UTC)
    try:
        with app.state.pool.connection() as conn:
            age = _age(now, queries.last_feed_generated(conn))
    except (PoolTimeout, psycopg.Error):
        return JSONResponse({"status": "degraded", "database": "unavailable"}, status_code=503)
    body = {"status": "ok", "database": "ok", "data_age_seconds": age, "fresh": age is not None and age <= FRESH_SECONDS}
    return JSONResponse(body)


@app.get("/api/quakes/significant")
def significant():
    now = datetime.now(UTC)
    rows, generated = _read(queries.significant, now)
    return _report(now, generated, count=len(rows), earthquakes=rows)


@app.get("/api/stats/daily-average")
def daily_average():
    now = datetime.now(UTC)
    report, generated = _read(queries.daily_average, now)
    return _report(now, generated, **report)


@app.get("/api/quakes/search")
def search(
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    min_mag: float | None = Query(None, ge=-2, le=10),
    limit: int = Query(5, ge=1, le=100),
):
    now = datetime.now(UTC)
    end = _utc(end) if end else now
    start = _utc(start) if start else end - timedelta(days=30)
    if start >= end:
        raise HTTPException(422, "start must be before end")
    if end - start > MAX_RANGE:
        raise HTTPException(422, "the range is limited to 366 days")
    rows, generated = _read(queries.search, start, end, min_mag, limit)
    return _report(now, generated, start=start.isoformat(), end=end.isoformat(), min_mag=min_mag,
                   limit=limit, count=len(rows), earthquakes=rows)
