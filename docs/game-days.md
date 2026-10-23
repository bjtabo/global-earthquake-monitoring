# Game days

A game day causes a failure on purpose, on a schedule, with people watching. It checks three things: that the failure moves the metric we expect, that the right alarm fires and nothing else does, and that the runbook leads to the fix. Each game day below states a hypothesis, how to cause the failure, what should happen, how to confirm it, and how to undo it. Expected times come from AWS documentation where it gives one, and are estimates from the design otherwise.

## Ground rules

- **Roles** follow [section 10 of the architecture](architecture.md#10-incident-response). The incident commander runs the clock and calls the abort. The operations lead is the only person typing commands. Communications writes the timeline in UTC.
- **Before starting:** the dashboard is open, all 16 alarms are in OK, and the SLO report with the remaining budget is saved. Anyone else using the account has been told.
- **One at a time.** The next game day starts only after every alarm is back in OK.
- **Abort** when something outside the expected list happens: an unexpected page, 5xx where none were expected, or a step taking more than twice the expected time. Revert first, investigate after.
- **Afterwards,** compare expected with observed. Every difference is a finding. Fix the alarm, the runbook or this document, and write a postmortem from [postmortem-template.md](postmortem-template.md) if the exercise spent more than 20% of a budget.

Commands assume the two setup lines from [runbooks/README.md](runbooks/README.md) (`AWS_REGION`, `STACK` and the `out` helper).

## Summary

| # | Game day | What it proves | Pages expected |
| --- | --- | --- | --- |
| 1 | An API task dies | Losing a task is invisible to users | No |
| 2 | A broken deploy | The circuit breaker rolls back a bad image without dropping traffic | No |
| 3 | USGS is down | The page keeps serving the last data, and the freshness SLO and alarms catch the outage | Yes, freshness |
| 4 | RDS fails over | Multi-AZ failover recovers without manual action, and the outage is visible, not hidden | Yes, availability |
| 5 | Rate limit with a spoofed IP | The WAF counts the IP CloudFront saw, so forged headers do not evade it | No |

They are ordered from lowest to highest impact. Number 5 goes last because it blocks the operator's IP for a few minutes.

## 1. An API task dies

**Hypothesis.** With 2 tasks in 2 AZs, stopping one causes no user-visible errors.

**Cause it.**

```bash
TASK=$(aws ecs list-tasks --cluster "$(out ClusterName)" --service-name "$(out ServiceName)" --query 'taskArns[0]' --output text)
aws ecs stop-task --cluster "$(out ClusterName)" --task "$TASK" --reason "game day"
```

**Expect.** The other task keeps serving. ECS starts a replacement because the service wants 2 tasks. The 30-second deregistration delay and uvicorn's 20-second graceful shutdown let requests in flight finish. No alarm fires: `ticket-unhealthy-tasks` watches `UnHealthyHostCount`, and a task that is deregistered lowers `HealthyHostCount` without raising `UnHealthyHostCount`.

**Confirm.** The "Healthy tasks" graph goes from 2 to 1 and back to 2. "Requests and 5xx" shows no 5xx. The availability SLO and the canary do not move.

**Undo.** Nothing to undo; ECS has already replaced the task.

**Pass** if the replacement is healthy, there were zero 5xx and no alarm fired. With only canary traffic, a single 5xx pages ([architecture, section 5, Low traffic](architecture.md#5-slis-and-slos)), so any 5xx here is a finding.

## 2. A broken deploy

**Hypothesis.** A release whose image cannot start never takes traffic, and ECS rolls it back without anyone acting.

**Cause it.** Register a revision of the task definition whose image tag does not exist, and apply it with `aws ecs update-service --cluster "$(out ClusterName)" --service "$(out ServiceName)" --task-definition <revision>`.

**Expect.** The new tasks never reach RUNNING. The circuit breaker marks the deployment FAILED at its threshold (50% of the desired count, minimum 3, so 3 failed tasks here) and rolls back to the last deployment that completed. With `MinimumHealthyPercent` at 100, the old tasks are never stopped.

**Confirm.** `aws ecs describe-services` shows `rolloutState` FAILED and a new deployment that comes from the rollback. The ALB shows zero 5xx for the whole attempt. [runbooks/deploy-rollback.md](runbooks/deploy-rollback.md) explains the events.

**Undo.** The rollback is the undo. Deregister the broken revision so `ops/destroy.sh --purge` can remove it later.

**Pass** if the rollback happened on its own and no request failed.

## 3. USGS is down

**Hypothesis.** When the data source fails, the API keeps answering with the last data and says how old it is, and the freshness SLO notices within about 20 minutes.

**Cause it.** Save the Lambda environment, point it at a host that does not exist, and apply the whole map (an update replaces every variable):

```bash
F=$(out IngestFunctionName)
aws lambda get-function-configuration --function-name "$F" --query Environment > env-saved.json
jq '.Variables.USGS_BASE_URL = "https://usgs.invalid"' env-saved.json > env-broken.json
aws lambda update-function-configuration --function-name "$F" --environment file://env-broken.json
```

**Expect.** Every run fails, publishes `IngestSuccess=0` and a `DataLagSeconds` that grows by about 300 s per run, and raises the error so that Lambda counts it in `Errors`. The page keeps showing the last data and adds "Updates are delayed" once the data is more than 15 minutes old. Estimated timings:

- `ticket-ingest-errors` after 15 minutes, when 3 of 3 five-minute periods have errors.
- `page-freshness-fast-burn` when the first 10-minute period with lag over 600 s closes, 15 to 20 minutes after the cut. At that point the 10-minute burn rate is 100 and the 60-minute one is 16.7 (1 bad period in 6 against a 1% budget), both above 14.4.
- `ops/drift.sh` reports the Lambda as MODIFIED for as long as the change lasts.

**Confirm.** "Data lag" climbs past 600 s, "Ingestion runs" shows success at 0, both alarms arrive by email naming [runbooks/data-freshness.md](runbooks/data-freshness.md), and the page shows the delayed-data notice instead of an error.

**Undo.** `aws lambda update-function-configuration --function-name "$F" --environment file://env-saved.json`. Running `ops/deploy.sh` does not undo it: the templates did not change, so the change set is empty. The next run catches up on its own, because it picks its source by how far behind it is.

**Pass** if the page never failed, both alarms fired once and cleared, lag went back under 600 s after the revert, and the weekly report shows no missing day.

## 4. RDS fails over

**Hypothesis.** A Multi-AZ failover recovers without manual action. While it lasts, the API reports the outage instead of hiding it.

**Cause it.**

```bash
aws rds reboot-db-instance --db-instance-identifier "$(out DbInstanceId)" --force-failover
```

**Expect.** AWS documents a Multi-AZ failover as typically taking 60 to 120 seconds. During that time `/health` and the reports answer 503 and the page says the section could not load; no cache masks it. The tasks stay in the target group, because the ALB health check is `/livez`, which does not touch the database. The connection pool reconnects without restarting tasks. With only canary traffic, the 503s page `page-availability-fast-burn` (section 5, Low traffic). A single failed canary run does not page, because `page-canary-failing` needs 2 of 3 periods.

**Confirm.** The failover events appear in the database runbook's `describe-events` command ([runbooks/database.md](runbooks/database.md)). "Healthy tasks" stays at 2. 5xx appear and stop without any restart. The SLO report shows how much availability budget was spent; write that number down.

**Undo.** Nothing is required: the standby is now the primary in the other AZ. A second failover moves it back if wanted.

**Pass** if service came back on its own within AWS's typical range, the alarm that fired named the availability runbook, and the budget spent was recorded.

## 5. Rate limit with a spoofed IP

**Hypothesis.** One client cannot pass 300 API requests in 5 minutes, even if it forges the headers that carry its IP.

**Cause it.** A `curl` loop against `/health`, which is not cached and therefore always reaches the WAF, sending invented `x-viewer-ip` and `X-Forwarded-For` headers.

**Expect.** The CloudFront Function overwrites `x-viewer-ip` with the address CloudFront saw, and the WAF rule counts that header, so forged headers do not change the count. Once a client crosses 300 requests, it gets 403, the WAF default. AWS states that rate limiting is not exact and that detecting an excess usually takes under 30 seconds. The page at `/` keeps answering 200, because it is served from S3.

**Confirm.** The `rate-limit-per-viewer` line in "WAF blocked requests per rule" rises, and the API log shows the real IP in `viewer_ip`. There are no 5xx and no page alarm, because a 403 does not count against the availability SLO.

**Undo.** Stop the loop. The block lifts by itself once that IP falls back under the limit.

**Pass** if forged headers did not delay the block, the block lifted on its own, and no alarm fired.

## Not covered

Losing a whole Availability Zone is the failure Multi-AZ exists for. Simulating it needs AWS Fault Injection Service, which is not available on the AWS Free plan. In a standard account it would be the sixth game day.
