# API latency

**Fires when** too many minutes have a p95 `TargetResponseTime` above 300 ms (`page-latency-*`, `ticket-latency-budget-burn`).

**Impact.** Reports load slowly. Nothing is wrong yet, but it often comes before 5xx: each query has a 2-second statement timeout and the pool waits at most 2 seconds for a connection.

## Check

```bash
TG="$(aws ecs describe-services --cluster "$(out ClusterName)" --services "$(out ServiceName)" --query 'services[0].loadBalancers[0].targetGroupArn' --output text)"
LB="$(aws elbv2 describe-target-groups --target-group-arns "$TG" --query 'TargetGroups[0].LoadBalancerArns[0]' --output text)"
aws cloudwatch get-metric-statistics --namespace AWS/ApplicationELB --metric-name TargetResponseTime \
  --dimensions Name=LoadBalancer,Value="${LB#*:loadbalancer/}" \
  --start-time "$(date -u -v-60M +%FT%TZ)" --end-time "$(date -u +%FT%TZ)" --period 60 --extended-statistics p95 \
  --query 'sort_by(Datapoints,&Timestamp)[-10:].[Timestamp,ExtendedStatistics.p95]' --output text
aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name CPUUtilization --dimensions Name=DBInstanceIdentifier,Value="$(out DbInstanceId)" \
  --start-time "$(date -u -v-60M +%FT%TZ)" --end-time "$(date -u +%FT%TZ)" --period 300 --statistics Maximum \
  --query 'max(Datapoints[].Maximum)' --output text
aws logs filter-log-events --log-group-name "/$STACK/ecs/api" --start-time $(( ($(date +%s) - 900) * 1000 )) \
  --filter-pattern '{ $.ms > 300 }' --query 'events[-20:].message' --output text
aws ecs describe-services --cluster "$(out ClusterName)" --services "$(out ServiceName)" --query 'services[0].[runningCount,desiredCount]' --output text
```

The dashboard (`$(out DashboardName)`) shows the same numbers side by side; it is quicker than the CLI.

## Act

| What you see | Do |
| --- | --- |
| One path is slow (`path` in the log lines) | check that query with `EXPLAIN ANALYZE` against a copy; a missing index is the usual cause |
| DB CPU high | [database.md](database.md) |
| Tasks at 4 of 4 and CPU high | traffic is above what the service is sized for; raise `MaxCount` in `infra/app.yaml` and redeploy |
| `database_unavailable` events in the API log | pool timeouts; [database.md](database.md) |

## Verify recovery

p95 under 300 ms for 10 minutes and the alarm returns to OK.
