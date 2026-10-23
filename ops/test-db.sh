#!/usr/bin/env bash
# Local PostgreSQL 18 for the unit tests on 127.0.0.1:55432. Idempotent.
set -euo pipefail
name=quakes-test
if ! docker ps --format '{{.Names}}' | grep -qx "$name"; then
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker run -d --name "$name" -e POSTGRES_PASSWORD=test -p 127.0.0.1:55432:5432 postgres:18 >/dev/null
fi
for _ in $(seq 1 30); do
  docker exec "$name" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && { echo "postgres ready on 127.0.0.1:55432"; exit 0; }
  sleep 1
done
echo "postgres did not become ready" >&2
exit 1
