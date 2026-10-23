"""CloudWatch Embedded Metric Format: one JSON line on stdout per call, no PutMetricData."""
import json
import os
import time

UNITS = {"DataLagSeconds": "Seconds", "IngestSuccess": "Count", "EventsUpserted": "Count", "EventsSkipped": "Count"}


def emit(**values: float) -> None:
    print(json.dumps({
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": os.environ.get("METRICS_NAMESPACE", "quakes/Ingest"),
                "Dimensions": [["Service"]],
                "Metrics": [{"Name": name, "Unit": UNITS[name]} for name in values],
            }],
        },
        "Service": "ingest",
        **values,
    }), flush=True)
