# Canary

**Fires when** the Synthetics canary, which requests the page (`/`), `/health` and the three reports through CloudFront every 5 minutes, fails in 2 of 3 runs or stops reporting (`page-canary-failing`).

**Impact.** It is the only check that takes the user's path through CloudFront, so a failure can mean the site is down for everyone even while the ALB metrics look fine.

## Check

```bash
aws synthetics get-canary-runs --name "$(out CanaryName)" --max-results 5 \
  --query 'CanaryRuns[].[Timeline.Started,Status.State,Status.StateReason]' --output text
for p in / /health /api/quakes/significant /api/stats/daily-average /api/quakes/search; do
  echo "$p $(curl -s -o /dev/null -w '%{http_code} %{time_total}s' "$(out Url)$p")"
done
aws synthetics get-canary --name "$(out CanaryName)" --query 'Canary.[Status.State,RuntimeVersion]' --output text
aws cloudfront get-distribution --id "$(out DistributionId)" --query 'Distribution.Status' --output text
```

## Act

| What you see | Do |
| --- | --- |
| `curl` fails the same way | The site is down. Find the layer: `/health` 503 means the database ([database.md](database.md)); 502 or 504 means the ALB or tasks ([api-availability.md](api-availability.md)); a timeout or TLS error means CloudFront, so check the AWS Health Dashboard. |
| `/` returns 403, the API answers | The page is missing from the site bucket (an upload that never ran or was interrupted). Rerun `ops/deploy.sh`: it syncs the page and invalidates CloudFront. |
| `curl` works, the canary fails | The check itself is broken: a deprecated runtime, a changed response, or missing permissions. Read `Status.StateReason`, fix `infra/observability.yaml`, redeploy. |
| Canary state is not `RUNNING` | Restart it with `aws synthetics start-canary --name "$(out CanaryName)"` and find out who stopped it (`ops/drift.sh --region "$AWS_REGION"`). |

## Verify recovery

Two passing runs in a row; the alarm returns to OK.
