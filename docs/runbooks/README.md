# Runbooks

One runbook per alarm family. Every alarm description names its file. Each runbook starts from the same two lines, which work in any account the stack was deployed to (the `date -v` flags are macOS; on Linux use `date -d '-30 min'`):

```bash
export AWS_REGION=us-east-2 STACK=quakes   # the --region and STACK the stack was deployed with
out() { aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
```

| Runbook | Alarms |
| --- | --- |
| [api-availability.md](api-availability.md) | `page-availability-fast-burn`, `page-availability-slow-burn`, `ticket-availability-budget-burn`, `ticket-unhealthy-tasks` |
| [api-latency.md](api-latency.md) | `page-latency-fast-burn`, `page-latency-slow-burn`, `ticket-latency-budget-burn` |
| [data-freshness.md](data-freshness.md) | `page-freshness-fast-burn`, `page-freshness-slow-burn`, `ticket-freshness-budget-burn`, `ticket-ingest-errors`, `ticket-schedule-dlq` |
| [database.md](database.md) | `ticket-db-cpu`, `ticket-db-storage`, `ticket-db-memory` |
| [canary.md](canary.md) | `page-canary-failing` |
| [deploy-rollback.md](deploy-rollback.md) | `deploy-target-5xx` and any failed ECS deployment |

Page alarms: acknowledge, name an incident commander, follow the runbook, write the first timeline entry in UTC. Ticket alarms: next working day. After an incident that spends more than 20% of a budget, write a postmortem from [../postmortem-template.md](../postmortem-template.md).
