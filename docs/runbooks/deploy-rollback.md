# Deploy rollback

**Fires when** a new task definition returns 5xx, 10 or more per minute for 2 minutes (`deploy-target-5xx`, which ECS watches during a deployment), or tasks never become healthy (deployment circuit breaker). In both cases ECS rolls back to the last completed deployment without help.

**Impact.** None if the rollback works: `MinimumHealthyPercent` is 100, so the old tasks keep serving until the new ones are healthy.

## Check

```bash
aws ecs describe-services --cluster "$(out ClusterName)" --services "$(out ServiceName)" \
  --query 'services[0].deployments[].[status,rolloutState,rolloutStateReason,taskDefinition,createdAt]' --output text
aws ecs describe-services --cluster "$(out ClusterName)" --services "$(out ServiceName)" --query 'services[0].events[:10].message' --output text
```

A `FAILED` deployment followed by a new one whose reason mentions a rollback means ECS did its job. The service is back on the previous task definition.

## Act

1. Confirm the service is healthy again (`runningCount` equals `desiredCount`, targets healthy).
2. Bring the template back in line with what runs: check out the last good commit and run `ops/deploy.sh`, or fix the change and deploy again. Until then the stack's parameters name an image that is not running.
3. If ECS did not roll back (for example both deployments fail), deploy the last good commit with `ops/deploy.sh`. Avoid pointing the service at an old task definition by hand: it leaves the stack drifted.

## Verify recovery

One deployment, `COMPLETED`, 2 healthy targets, and `ops/drift.sh --region "$AWS_REGION"` reports `IN_SYNC` for the app stack.
