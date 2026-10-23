# Database

**Fires when** RDS CPU is above 80% for 15 minutes (`ticket-db-cpu`), free storage drops below 2 GiB (`ticket-db-storage`) or freeable memory stays below 100 MiB for 15 minutes (`ticket-db-memory`). Also the place to go when `/health` reports `"database": "unavailable"`.

**Impact.** Slower or failing reports. Ingestion may fail, which shows up later as data freshness.

## Check

```bash
aws rds describe-db-instances --db-instance-identifier "$(out DbInstanceId)" \
  --query 'DBInstances[0].[DBInstanceStatus,MultiAZ,AvailabilityZone,SecondaryAvailabilityZone,AllocatedStorage]' --output text
aws rds describe-events --source-type db-instance --source-identifier "$(out DbInstanceId)" --duration 180 \
  --query 'Events[].[Date,Message]' --output text
for m in CPUUtilization FreeStorageSpace FreeableMemory DatabaseConnections; do
  echo "$m $(aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name $m --dimensions Name=DBInstanceIdentifier,Value="$(out DbInstanceId)" \
    --start-time "$(date -u -v-30M +%FT%TZ)" --end-time "$(date -u +%FT%TZ)" --period 300 --statistics Maximum --query 'max(Datapoints[].Maximum)' --output text)"
done
```

## Act

| What you see | Do |
| --- | --- |
| Status `available`, events show a failover | RDS already moved to the standby; connections recover on their own. Check `/health` again in 2 minutes. |
| Primary impaired, no automatic failover | `aws rds reboot-db-instance --db-instance-identifier "$(out DbInstanceId)" --force-failover`. AWS documents failover as typically 60 to 120 seconds. |
| Storage low | Raise `AllocatedStorage` in `infra/database.yaml` and run `ops/deploy.sh`, so the change lives in the template. Storage can grow, never shrink. |
| CPU or connections high | Compare with API and ingestion traffic on the dashboard. A backfill or a hand-run ingestion explains a short spike. |
| Data lost or corrupted | The data is a copy of USGS. Usually the fastest fix is to re-ingest: with fewer than 360 days stored, `ops/deploy.sh` backfills 390 days. To inspect old data instead, restore to a new instance; the stack keeps pointing at the original. |

Point-in-time restore to a new instance (inside the backup retention window):

```bash
aws rds describe-db-instances --db-instance-identifier "$(out DbInstanceId)" \
  --query 'DBInstances[0].[DBSubnetGroup.DBSubnetGroupName,VpcSecurityGroups[0].VpcSecurityGroupId,LatestRestorableTime]' --output text
aws rds restore-db-instance-to-point-in-time --source-db-instance-identifier "$(out DbInstanceId)" \
  --target-db-instance-identifier "$STACK-db-restore" --restore-time <UTC time> \
  --db-subnet-group-name <subnet group> --vpc-security-group-ids <security group> --no-multi-az
```

Delete the restored instance when done; `ops/audit.sh` does not look for it.

## Verify recovery

`/health` reports `"database": "ok"`, the metric is back under its threshold and the alarm returns to OK.
