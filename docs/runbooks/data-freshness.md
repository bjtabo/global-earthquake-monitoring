# Data freshness

**Fires when** the newest data served is more than 10 minutes old in too many 10-minute periods (`page-freshness-*`, `ticket-freshness-budget-burn`), the ingest Lambda failed in each of the last 15 minutes (`ticket-ingest-errors`), or Scheduler could not deliver a run (`ticket-schedule-dlq`).

**Impact.** Users see older data and, past 15 minutes, a delay banner. Nothing returns an error.

## Check

```bash
curl -s "$(out Url)/health"; echo                       # data_age_seconds
aws lambda invoke --function-name "$(out IngestFunctionName)" --cli-binary-format raw-in-base64-out \
  --payload '{"mode":"status"}' /dev/stdout --query StatusCode --output text
aws logs filter-log-events --log-group-name "/$STACK/lambda/ingest" --start-time $(( ($(date +%s) - 1800) * 1000 )) \
  --filter-pattern '?ERROR ?Traceback' --query 'events[-10:].message' --output text
curl -s -o /dev/null -w '%{http_code}\n' https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson
aws scheduler get-schedule --name "$STACK-ingest" --query State --output text
DLQ="$(aws sqs list-queues --queue-name-prefix "$STACK-Ingestion" --query 'QueueUrls[0]' --output text)"
aws sqs get-queue-attributes --queue-url "$DLQ" --attribute-names ApproximateNumberOfMessages --output text
```

`status` returns `rows`, `oldest`, `newest` and `last_feed_generated` (the time USGS generated the last feed we loaded).

## Act

| What you see | Do |
| --- | --- |
| USGS returns 5xx or times out | Nothing to fix on our side. Post a status note, keep watching. When USGS is back, the next run picks the smallest feed that covers the gap (FDSN beyond 30 days). Per the error budget policy, a USGS outage does not freeze releases. |
| Errors in the Lambda log after a deploy | Roll back that deploy: check out the previous commit and run `ops/deploy.sh`. |
| `password authentication failed` or `connection failed` | [database.md](database.md) |
| Schedule `DISABLED` | Someone changed it by hand. Enable it again in the EventBridge Scheduler console (`$STACK-ingest`), then `ops/drift.sh` should report `IN_SYNC`. A `deploy.sh` with no template changes does not undo it: the change set is empty. |
| Messages in the DLQ | Fix the cause first. The messages only record missed runs; the next run catches up, so the queue can then be purged: `aws sqs purge-queue --queue-url "$DLQ"`. |

Run one ingestion by hand, for example after a fix:

```bash
aws lambda invoke --function-name "$(out IngestFunctionName)" --cli-binary-format raw-in-base64-out \
  --payload '{"mode":"scheduled"}' /dev/stdout
```

A response of `skipped` means a scheduled run holds the lock; wait a few seconds and retry.

## Verify recovery

`data_age_seconds` under 300 on `/health`, `IngestSuccess` 1 on the dashboard, the alarm returns to OK.
