# Deploying into a new account

Region is fixed to us-east-2; the stack name defaults to `quakes` (`STACK=<name>` picks another one: 3 to 14 lowercase letters, digits or single hyphens, starting with a letter and not ending in a hyphen).

## 1. Prerequisites

- AWS CLI v2, Docker running on the default socket or `DOCKER_HOST` (the deploy builds with its own Docker config, which drops any other Docker context), git, Python 3 with pip, curl, bash 4.4 or newer (macOS ships 3.2: `brew install bash`).
- `git clone` this repository and `cd` into it.
- Credentials for the target account in any standard way: `aws configure sso` and `AWS_PROFILE`, or environment variables. The deployer needs administrator-level access: the stacks create IAM roles, a VPC, RDS, ECS, an ALB, CloudFront, WAF, Lambda, Scheduler, Synthetics, SLOs and alarms.

## 2. Preflight

```bash
ops/preflight.sh
```

Read-only. It stops with a message when something would make the deploy fail:

| Message | Meaning |
| --- | --- |
| `missing tool`, `AWS CLI v2 required`, `python3 has no pip`, `Docker is not running` | install or start it |
| `no usable AWS credentials` | the credentials above are not active in this shell |
| `stack ... is <status>` | a stack from an earlier attempt is stuck or rolled back; run `ops/destroy.sh --purge` first |
| `VPCs` or `Elastic IPs: need N free` | the account is at its quota; the deploy needs 1 VPC and 2 Elastic IPs |
| a DB instance, secrets, log groups or an ECR repository with the project's name already exist | an earlier deploy was not purged, or the name is taken; purge it or use another `STACK` |

## 3. Deploy

```bash
ops/deploy.sh --email <address for alarms>
```

It prints the account, region and caller, and asks you to type the account id. Nothing is created before that.

What it does, in order: bootstrap stack (artifacts bucket, image repository); Lambda packages; the API image, built for ARM and pushed to ECR; the root stack with all nested stacks; the page, uploaded to S3; a 390-day backfill from USGS; one ingestion run; then it waits until `/health` answers through CloudFront. Any failed step stops the script with a non-zero exit and the failing command's error, including a single failed backfill window. The last line is `URL: https://<distribution>.cloudfront.net`.

Timing measured in the owner's account on 2026-10-23, one run from an empty account: 30 minutes. Preflight, bootstrap stack, Lambda packages and the image took 1 minute; the network stack 3; then the database stack 18 (Multi-AZ RDS) while the ALB stack (2) and the edge stack (12: the CloudFront VPC origin, which AWS says can take up to 15 minutes to reach Deployed, then the distribution) ran alongside it; ingestion 5 and app 3 in parallel; observability 1; the page, the backfill and the health check 1. Plan for 30 to 35 minutes.

Confirm the email from AWS Notifications, or alarms will not reach anyone. On a first deploy `<stack>-page-canary-failing` starts in ALARM, because a canary with no runs yet counts as failing, and returns to OK once the canary passes: after 20 minutes on 2026-10-22 and 10 minutes on 2026-10-23.

`--budget <USD>` adds a monthly AWS Budgets alert to the same address. `--free-plan` is only for accounts on the AWS Free plan, which rejects RDS backup retention above 1 day; leave it out in a standard account (7 days).

## 4. While it runs

- CloudFormation console: the root stack `quakes` and its nested stacks appearing in dependency order.
- `docs/architecture.md`: the diagrams and the SLO section.

## 5. After

```bash
open "$(aws cloudformation describe-stacks --region us-east-2 --stack-name quakes --query "Stacks[0].Outputs[?OutputKey=='Url'].OutputValue" --output text)"
BASE_URL=<URL> ALB_DNS=<AlbDnsName output> pytest -m e2e tests/e2e -v
ops/drift.sh            # every stack IN_SYNC
```

CloudWatch: the dashboard named after the stack, the three SLOs under Application Signals, the alarms with the stack prefix, and the canary `quakes-canary`.

## 6. Teardown

```bash
ops/destroy.sh --purge
```

It turns off deletion protection on the database, deletes the root stack, empties and deletes the bootstrap stack, then removes the final database snapshot, the project's secrets without a recovery window, the deregistered task definitions and the canary log groups. It finishes by running `ops/audit.sh`, which must print `audit clean`. The full teardown took 37 minutes on 2026-10-22 and 34 minutes on 2026-10-23 in the owner's account. After a teardown the tagging index keeps listing some deleted EC2 and ECS resources for a while; the audit asks those services directly and counts them in a `note:` line instead of reporting them as left.

## 7. If something fails

- `aws cloudformation deploy` stops waiting after 60 minutes; the script then waits again while the root stack is still in progress.
- Rerun `ops/deploy.sh`: every step converges, the backfill only runs when history is short, and an image already in ECR is not rebuilt.
- A root stack in `ROLLBACK_COMPLETE` cannot be updated: `ops/destroy.sh --purge`, then deploy again.
- `ops/destroy.sh` retries a stack delete up to 3 times and waits for the VPC to drain between attempts. Lambda and CloudFront network interfaces can take several minutes to go away after their owners are deleted.
- The stack events show the first failure: `aws cloudformation describe-stack-events --region us-east-2 --stack-name <stack> --query "StackEvents[?ResourceStatus=='CREATE_FAILED']"`.
