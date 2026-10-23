# Global Earthquake Monitoring System

Ingests the USGS earthquake feeds every 5 minutes into PostgreSQL on AWS (us-east-2) and serves three reports through a JSON API and a page, both behind CloudFront: the page from S3, the API uncached from ECS. Everything is CloudFormation, deployed and removed by one script each, with SLOs, burn-rate alerts, a synthetic canary and a runbook for every alarm.

`ops/deploy.sh` prints the URL of the deployment it creates; [DEPLOY.md](DEPLOY.md) has the steps.

| Report | URL |
| --- | --- |
| Earthquakes above magnitude 4.0 in the last 24 hours | `/api/quakes/significant` |
| Average earthquakes per day over the last 7 days | `/api/stats/daily-average` |
| Historical search, top 5 by magnitude in the last 30 days by default | `/api/quakes/search?start=2026-01-01&end=2026-02-01&min_mag=5&limit=10` |

## Documents

- [docs/architecture.md](docs/architecture.md): design, security, failure modes, SLOs, alerting, error budget policy, cost.
- [DEPLOY.md](DEPLOY.md): deploying into a new account and removing everything afterwards.
- [docs/runbooks/](docs/runbooks/README.md): one runbook per alarm family; [docs/postmortem-template.md](docs/postmortem-template.md).

## Layout

```
infra/     bootstrap, root and nested stacks: network, database, alb, ingestion, app, edge, observability
infra/guard/  cfn-guard rules every template must pass
app/       api/ (FastAPI, Dockerfile), web/ (the page, served from S3), ingest/ and migrate/ (Lambdas), shared/
ops/       preflight.sh, deploy.sh, destroy.sh, audit.sh, drift.sh, lint.sh, test-db.sh
tests/     unit and PostgreSQL tests; tests/e2e runs against a deployed stack
```

## Tests

Needs Docker and Python 3.14 (cfn-lint 1.58.0 does not support 3.15 yet).

```bash
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
ops/test-db.sh          # PostgreSQL 18 on 127.0.0.1:55432
ops/lint.sh             # cfn-lint and cfn-guard on every template (cfn-guard 3.2.1 must be on PATH)
pytest -q
BASE_URL=https://<distribution>.cloudfront.net ALB_DNS=<AlbDnsName output> pytest -m e2e tests/e2e
```

CI (`.github/workflows/ci.yml`) runs the lint and the tests against a PostgreSQL 18 service container on every push and pull request. Actions are pinned by commit SHA and the workflow token is read-only.
