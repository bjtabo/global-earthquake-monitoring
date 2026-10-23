# Deploying into a new account

Every `ops/` script takes the region as `--region`, and it is required: nothing falls back to the region of a profile. The examples use `us-east-2`, the region the brief asks for and the only one this stack has been deployed and tested in. The scripts accept only regions where CloudFront VPC origins work in every Availability Zone (the list is in `ops/lib.sh`); other regions may still lack a service the stack uses.

The stack name defaults to `quakes` (`STACK=<name>` in front of every `ops/` command picks another one: 3 to 14 lowercase letters, digits or single hyphens, starting with a letter and not ending in a hyphen).

## 1. Prerequisites

- AWS CLI v2, Docker running on the default socket or `DOCKER_HOST` (the deploy builds with its own Docker config, which drops any other Docker context), git, curl, bash 4.4 or newer (macOS ships 3.2: `brew install bash`), and Python 3 with pip.
- The repository:

```bash
git clone https://github.com/bjtabo/global-earthquake-monitoring.git
cd global-earthquake-monitoring
```

- A virtual environment for the end-to-end tests, with Python 3.14 (cfn-lint does not support 3.15 yet):

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

## 2. Credentials

The deployer needs administrator-level access: the stacks create IAM roles, a VPC, RDS, ECS, an ALB, CloudFront, WAF, Lambda, Scheduler, Synthetics, SLOs and alarms.

With an IAM user and access keys:

1. IAM console, **Users**, **Create user**. No console access. **Attach policies directly**: `AdministratorAccess`.
2. Open the user, **Security credentials**, **Create access key**, use case **Command Line Interface (CLI)**.
3. Load the keys:

```bash
aws configure
```

It asks for the access key ID, the secret access key, the region (`us-east-2`) and the output format (`json`). IAM Identity Center works as well: `aws configure sso`, then `export AWS_PROFILE=<profile>`.

Check that the shell points at the target account:

```bash
aws sts get-caller-identity
```

Check the account plan:

```bash
aws freetier get-account-plan-state --region us-east-1
```

`accountPlanType` `FREE` means the deploy needs `--free-plan`: the AWS Free plan rejects RDS backup retention above 1 day. `PAID` means no flag (7 days).

## 3. Preflight

```bash
ops/preflight.sh --region us-east-2
```

Read-only. It ends in `preflight ok`, or stops with a message when something would make the deploy fail:

| Message | Meaning |
| --- | --- |
| `missing tool`, `AWS CLI v2 required`, `python3 has no pip`, `Docker is not running` | install or start it |
| `no usable AWS credentials` | the credentials above are not active in this shell |
| `stack ... is <status>` | a stack from an earlier attempt is stuck or rolled back; run `ops/destroy.sh --region <region> --purge` first |
| `VPCs` or `Elastic IPs: need N free` | the account is at its quota; the deploy needs 1 VPC and 2 Elastic IPs |
| a DB instance, secrets, log groups or an ECR repository with the project's name already exist | an earlier deploy was not purged, or the name is taken; purge it or use another `STACK` |

## 4. Deploy

```bash
ops/deploy.sh --region us-east-2 --email <address for alarms>              # standard account
ops/deploy.sh --region us-east-2 --email <address for alarms> --free-plan  # AWS Free plan account
```

It prints the account, region and caller, and asks you to type the account id. Nothing is created before that.

What it does, in order: bootstrap stack (artifacts bucket, image repository); Lambda packages; the API image, built for ARM and pushed to ECR; the root stack with all nested stacks; the page, uploaded to S3; a 390-day backfill from USGS; one ingestion run; then it waits until `/health` answers through CloudFront. Any failed step stops the script with a non-zero exit and the failing command's error, including a single failed backfill window. It takes about 30 minutes, most of it the Multi-AZ database and the CloudFront VPC origin, which are created in parallel. The last line is `URL: https://<distribution>.cloudfront.net`.

Confirm the email from AWS Notifications (**Confirm subscription**), or alarms will not reach anyone. On a first deploy `<stack>-page-canary-failing` starts in ALARM, because a canary with no runs yet counts as failing, and returns to OK once the canary passes, usually within 10 to 20 minutes.

`--budget <USD>` adds a monthly AWS Budgets alert to the same address.

## 5. Check

```bash
aws cloudformation describe-stacks --region us-east-2 --stack-name quakes --query "Stacks[0].Outputs" --output table
```

Take `Url` and `AlbDnsName` from the table, then:

```bash
open <Url>
BASE_URL=<Url> ALB_DNS=<AlbDnsName> .venv/bin/pytest -m e2e tests/e2e -v
ops/drift.sh --region us-east-2      # every stack IN_SYNC
```

CloudWatch console in us-east-2: the dashboard named after the stack, the three SLOs under **Service Level Objectives (SLO)** with their attainment and remaining error budget, the alarms with the stack prefix, and the canary `quakes-canary`.

## 6. Teardown

```bash
ops/destroy.sh --region us-east-2 --purge
```

It asks for the account id again, turns off deletion protection on the database, deletes the root stack, empties and deletes the bootstrap stack, then removes the final database snapshot, the project's secrets without a recovery window, the deregistered task definitions and the canary log groups. It finishes by running `ops/audit.sh`, which must print `audit clean`. It takes about 35 minutes, most of it waiting for Lambda and CloudFront network interfaces to leave the VPC. After a teardown the tagging index keeps listing some deleted EC2 and ECS resources for a while; the audit asks those services directly and counts them in a `note:` line instead of reporting them as left.

Then remove the credentials: delete the access key or the IAM user, and the keys from `~/.aws/credentials` (`aws sso logout` with IAM Identity Center).

## 7. If something fails

- `aws cloudformation deploy` stops waiting after 60 minutes; the script then waits again while the root stack is still in progress.
- Rerun `ops/deploy.sh`: every step converges, the backfill only runs when history is short, and an image already in ECR is not rebuilt.
- A root stack in `ROLLBACK_COMPLETE` cannot be updated: `ops/destroy.sh --region us-east-2 --purge`, then deploy again.
- `ops/destroy.sh` retries a stack delete up to 3 times and waits for the VPC to drain between attempts. Lambda and CloudFront network interfaces can take several minutes to go away after their owners are deleted.
- The stack events show the first failure: `aws cloudformation describe-stack-events --region us-east-2 --stack-name <stack> --query "StackEvents[?ResourceStatus=='CREATE_FAILED']"`.
