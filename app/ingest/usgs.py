"""USGS earthquake data: which source covers a gap, how to fetch it, how to parse it."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

FDSN_LIMIT = 20000
USER_AGENT = "quakes-monitor/1.0"

# Each summary feed covers its window; one hour of margin absorbs clock skew and slow runs.
FEEDS = (
    ("all_day", timedelta(hours=23)),
    ("all_week", timedelta(days=6, hours=23)),
    ("all_month", timedelta(days=29, hours=23)),
)


class UsgsError(Exception):
    pass


@dataclass(frozen=True)
class Event:
    id: str
    mag: float | None
    mag_type: str | None
    place: str | None
    event_time: datetime
    updated: datetime
    latitude: float
    longitude: float
    depth_km: float | None
    status: str | None
    event_type: str
    url: str | None


def choose_feed(lag: timedelta | None) -> str | None:
    if lag is None:
        return "all_month"
    for name, window in FEEDS:
        if lag < window:
            return name
    return None


def feed_url(base: str, name: str) -> str:
    return f"{base}/earthquakes/feed/v1.0/summary/{name}.geojson"


def fdsn_url(base: str, start: datetime, end: datetime) -> str:
    query = urllib.parse.urlencode({
        "format": "geojson",
        "starttime": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        "orderby": "time-asc",
        "limit": FDSN_LIMIT,
    })
    return f"{base}/fdsnws/event/1/query?{query}"


def fetch_json(url: str, timeout: float = 20) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status == 204:  # FDSN: no events in the window
                return {"metadata": {"generated": int(time.time() * 1000)}, "features": []}
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise UsgsError(f"{url}: {exc}") from exc


def parse(doc: dict) -> tuple[datetime, list[Event], int]:
    try:
        generated = _time(doc["metadata"]["generated"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UsgsError(f"document without metadata.generated: {exc}") from exc
    events, skipped = [], 0
    for feature in doc.get("features") or []:
        try:
            events.append(_event(feature))
        except (KeyError, TypeError, ValueError):
            skipped += 1
    return generated, events, skipped


def _time(ms) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC)


def _float(value) -> float | None:
    return None if value is None else float(value)


def _event(feature: dict) -> Event:
    props = feature["properties"]
    if not isinstance(feature.get("id"), str) or not feature["id"]:
        raise ValueError("feature without id")
    lon, lat, *rest = feature["geometry"]["coordinates"]
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("coordinates out of range")
    url = props.get("url")
    return Event(
        id=feature["id"],
        mag=_float(props.get("mag")),
        mag_type=props.get("magType"),
        place=props.get("place"),
        event_time=_time(props["time"]),
        updated=_time(props["updated"]),
        latitude=lat,
        longitude=lon,
        depth_km=_float(rest[0]) if rest else None,
        status=props.get("status"),
        event_type=props.get("type") or "unknown",
        url=url if isinstance(url, str) and url.startswith("https://earthquake.usgs.gov/") else None,
    )
