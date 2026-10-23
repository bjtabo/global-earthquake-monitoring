CREATE TABLE IF NOT EXISTS earthquakes (
    id          text PRIMARY KEY,
    mag         double precision,
    mag_type    text,
    place       text,
    event_time  timestamptz NOT NULL,
    updated     timestamptz NOT NULL,
    latitude    double precision NOT NULL,
    longitude   double precision NOT NULL,
    depth_km    double precision,
    status      text,
    event_type  text NOT NULL,
    url         text,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    CHECK (latitude BETWEEN -90 AND 90),
    CHECK (longitude BETWEEN -180 AND 180),
    CHECK (url IS NULL OR url LIKE 'https://earthquake.usgs.gov/%')
);

-- The reports only read event_type = 'earthquake'.
CREATE INDEX IF NOT EXISTS earthquakes_time_idx ON earthquakes (event_time) WHERE event_type = 'earthquake';
CREATE INDEX IF NOT EXISTS earthquakes_mag_idx ON earthquakes (mag DESC NULLS LAST) WHERE event_type = 'earthquake';

CREATE TABLE IF NOT EXISTS ingest_state (
    id                  smallint PRIMARY KEY CHECK (id = 1),
    last_success_at     timestamptz,
    last_feed_generated timestamptz,
    last_upserted       integer
);
INSERT INTO ingest_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
