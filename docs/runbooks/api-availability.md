# API availability

**Fires when** the ALB returns 5xx faster than the 99.9% SLO allows (`page-availability-*`, `ticket-availability-budget-burn`), or a task fails ALB health checks for 5 minutes (`ticket-unhealthy-tasks`).

**Impact.** Report requests fail and the page shows that a section could not load. The page itself still loads from S3.

## Check

```bash
curl -s "$(out Url)/health"; echo
aws ecs describe-services --cluster "$(out ClusterName)" --services "$(out ServiceName)" \
  --query 'services[0].{running:runningCount,desired:desiredCount,deployments:deployments[].[status,rolloutState,taskDefinition],events:events[:5].message}'
TG="$(aws ecs describe-services --cluster "$(out ClusterName)" --services "$(out ServiceName)" --query 'services[0].loadBalancers[0].targetGroupArn' --output text)"
aws elbv2 describe-target-health --target-group-arn "$TG" --query 'TargetHealthDescriptions[].[Target.Id,TargetHealth.State,TargetHealth.Reason]' --output text
aws logs filter-log-events --log-group-name "/$STACK/ecs/api" --start-time $(( ($(date +%s) - 900) * 1000 )) \
  --filter-pattern '{ $.status >= 500 }' --query 'events[-20:].message' --output text
```

Why tasks stopped:

```bash
for t in $(aws ecs list-tasks --cluster "$(out ClusterName)" --desired-status STOPPED --query 'taskArns[:5]' --output text); do
  aws ecs describe-tasks --cluster "$(out ClusterName)" --tasks "$t" --query 'tasks[0].[stoppedAt,stoppedReason,containers[0].reason]' --output text
done
```

## Act

| What you see | Do |
| --- | --- |
| `/health` says `"database": "unavailable"` | [database.md](database.md) |
| A deployment is `IN_PROGRESS` or just finished | [deploy-rollback.md](deploy-rollback.md) |
| Tasks stopped with `OutOfMemory` or an exit code, no deploy | `aws ecs update-service --cluster "$(out ClusterName)" --service "$(out ServiceName)" --force-new-deployment`, then open a ticket for the cause |
| 5xx only from the ALB (`HTTPCode_ELB_5XX_Count`), targets healthy | check the ALB and CloudFront VPC origin in the console; AWS Health Dashboard |
| One or two 5xx in the access log and none since | at canary-only traffic one failed request is enough to page (architecture.md, section 5, "Low traffic"); open a ticket with the log line and let the alarm return to OK |
| A flood from a few IPs | the WAF rate rule should block them; check `BlockedRequests` for `rate-limit-per-viewer` on the dashboard |

## Verify recovery

`HealthyHostCount` back to 2 on the dashboard, 5xx at zero for 10 minutes, the alarm returns to OK and emails it.
