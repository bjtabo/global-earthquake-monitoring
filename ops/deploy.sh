#!/usr/bin/env bash
# Deploys or updates everything. Safe to rerun.
source "$(dirname "$0")/lib.sh"

EMAIL="" BUDGET=0 BACKUP_DAYS=7
while [[ $# -gt 0 ]]; do
  case "$1" in
    --email) [[ $# -ge 2 ]] || die "--email needs a value"; EMAIL="$2"; shift 2 ;;
    --budget) [[ $# -ge 2 ]] || die "--budget needs a value"; BUDGET="$2"; shift 2 ;;
    --free-plan) BACKUP_DAYS=1; shift ;; # AWS Free plan accounts reject RDS backup retention above 1 day
    --yes) YES=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done
# Same pattern as the AlertEmail parameter, checked here so a typo fails before anything is built.
[[ "$EMAIL" =~ ^[^@[:space:]]+@[^@[:space:]]+$ ]] || die "usage: deploy.sh --email you@example.com [--budget USD] [--free-plan] [--yes]"
[[ "$BUDGET" =~ ^[0-9]+$ ]] || die "--budget takes whole dollars"

"$ROOT/ops/preflight.sh"
confirm_account
mkdir -p "$BUILD"

log "bootstrap stack"
aws cloudformation deploy --stack-name "$BOOTSTRAP_STACK" --template-file "$ROOT/infra/bootstrap.yaml" \
  --parameter-overrides "Prefix=$STACK" --tags "Project=$STACK" --no-fail-on-empty-changeset
BUCKET="$(stack_output "$BOOTSTRAP_STACK" ArtifactsBucketName)"

aws iam get-role --role-name AWSServiceRoleForECS >/dev/null 2>&1 \
  || aws iam create-service-linked-role --aws-service-name ecs.amazonaws.com >/dev/null

log "Lambda packages"
curl -fsSL https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem -o "$BUILD/rds-ca.pem"
rm -rf "$BUILD/lambda-deps"
python3 -m pip install --quiet --target "$BUILD/lambda-deps" --platform manylinux_2_28_aarch64 \
  --implementation cp --python-version 3.14 --only-binary=:all: -r "$ROOT/app/requirements-lambda.txt"

package_lambda() { # name -> prints the S3 key of its zip
  local name="$1" dir="$BUILD/lambda/$1" hash key
  rm -rf "$dir" && mkdir -p "$dir"
  cp -R "$BUILD/lambda-deps/." "$dir/"
  cp -R "$ROOT/app/shared" "$dir/shared"
  cp "$ROOT/app/$name/"*.py "$dir/"
  if [[ -f "$ROOT/app/$name/schema.sql" ]]; then cp "$ROOT/app/$name/schema.sql" "$dir/"; fi
  cp "$BUILD/rds-ca.pem" "$dir/rds-ca.pem"
  find "$dir" -name __pycache__ -prune -exec rm -rf {} +
  hash="$(cd "$dir" && find . -type f | LC_ALL=C sort | xargs shasum -a 256 | shasum -a 256 | cut -c1-16)"
  key="lambda/$name-$hash.zip"
  if ! aws s3api head-object --bucket "$BUCKET" --key "$key" >/dev/null 2>&1; then
    python3 -c 'import shutil, sys; shutil.make_archive(sys.argv[1], "zip", sys.argv[2])' "$BUILD/$name" "$dir"
    aws s3 cp --only-show-errors "$BUILD/$name.zip" "s3://$BUCKET/$key"
  fi
  echo "$key"
}
MIGRATE_KEY="$(package_lambda migrate)"
INGEST_KEY="$(package_lambda ingest)"
log "API image"
REPO_URI="$(stack_output "$BOOTSTRAP_STACK" ImageRepositoryUri)"
REPO_NAME="$(stack_output "$BOOTSTRAP_STACK" ImageRepositoryName)"
TAG="$(git -C "$ROOT" rev-parse --short=12 HEAD)"
DIRTY="$(git -C "$ROOT" status --porcelain -- app)"
[[ -z "$DIRTY" ]] || TAG="$TAG-dirty-$(date +%s)"
if ! aws ecr describe-images --repository-name "$REPO_NAME" --image-ids imageTag="$TAG" >/dev/null 2>&1; then
  # A private Docker config keeps the 12-hour ECR token out of ~/.docker and bypasses credential helpers.
  DOCKER_AUTH="$BUILD/docker"
  trap 'rm -rf "$DOCKER_AUTH"' EXIT
  rm -rf "$DOCKER_AUTH" && mkdir -p "$DOCKER_AUTH"
  printf '{"cliPluginsExtraDirs": ["%s"]}\n' "$HOME/.docker/cli-plugins" >"$DOCKER_AUTH/config.json"
  docker --config "$DOCKER_AUTH" build --platform linux/arm64 -f "$ROOT/app/api/Dockerfile" -t "$REPO_URI:$TAG" "$ROOT/app"
  aws ecr get-login-password | docker --config "$DOCKER_AUTH" login --username AWS --password-stdin "${REPO_URI%%/*}" >/dev/null
  docker --config "$DOCKER_AUTH" push "$REPO_URI:$TAG" >/dev/null
  rm -rf "$DOCKER_AUTH"
fi
IMAGE_URI="$REPO_URI:$TAG"

PREFIX_LIST="$(aws ec2 describe-managed-prefix-lists \
  --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
  --query 'PrefixLists[0].PrefixListId' --output text)"

log "root stack"
aws cloudformation package --template-file "$ROOT/infra/main.yaml" --s3-bucket "$BUCKET" \
  --s3-prefix templates --output-template-file "$BUILD/packaged.yaml" >/dev/null
if ! aws cloudformation deploy --stack-name "$STACK" --template-file "$BUILD/packaged.yaml" \
  --capabilities CAPABILITY_IAM --tags "Project=$STACK" --no-fail-on-empty-changeset \
  --parameter-overrides "CloudFrontPrefixListId=$PREFIX_LIST" "BackupRetentionDays=$BACKUP_DAYS" \
    "ArtifactsBucket=$BUCKET" "MigrateCodeKey=$MIGRATE_KEY" "IngestCodeKey=$INGEST_KEY" \
    "ImageUri=$IMAGE_URI" "ImageRepositoryName=$REPO_NAME" "AlertEmail=$EMAIL" "MonthlyBudgetUsd=$BUDGET"; then
  # The deploy command stops waiting after 60 minutes while CloudFormation keeps going.
  ST="$(stack_status "$STACK")"
  case "$ST" in
    CREATE_IN_PROGRESS) log "still creating; waiting again"; aws cloudformation wait stack-create-complete --stack-name "$STACK" ;;
    UPDATE_IN_PROGRESS|UPDATE_COMPLETE_CLEANUP_IN_PROGRESS)
      log "still updating; waiting again"; aws cloudformation wait stack-update-complete --stack-name "$STACK" ;;
    *) die "root stack deploy failed; the stack is $ST" ;;
  esac
fi

# The page goes up first: the distribution already serves the bucket, and the canary checks it.
log "page"
SITE_BUCKET="$(stack_output "$STACK" SiteBucketName)"
DISTRIBUTION_ID="$(stack_output "$STACK" DistributionId)"
aws s3 sync "$ROOT/app/web" "s3://$SITE_BUCKET" --delete --exclude '.*' --only-show-errors
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/*" >/dev/null

invoke() { # function payload -> response body on stdout; dies on a function error
  local out="$BUILD/invoke-$1.json" err
  err="$(aws lambda invoke --function-name "$1" --cli-binary-format raw-in-base64-out --cli-read-timeout 300 \
    --payload "$2" --query FunctionError --output text "$out")"
  [[ "$err" == None ]] || die "$1 returned $err: $(head -c 2000 "$out")"
  cat "$out"
}
INGEST_FN="$(stack_output "$STACK" IngestFunctionName)"

backfill_window() {
  local w="$1" attempt resp
  for attempt in 1 2 3 4 5 6; do
    resp="$(invoke "$INGEST_FN" "{\"mode\":\"backfill\",\"window\":$w}")"  # an assignment, so a failed run stops the deploy
    [[ "$resp" == *'"skipped"'* ]] || return 0
    sleep 10  # a scheduled run holds the lock
  done
  die "backfill window $w never got the ingestion lock"
}
needs_backfill="$(invoke "$INGEST_FN" '{"mode":"status"}' | python3 -c '
import json, sys
from datetime import datetime, timedelta, timezone
oldest = json.load(sys.stdin)["oldest"]
print("yes" if not oldest or datetime.fromisoformat(oldest) > datetime.now(timezone.utc) - timedelta(days=360) else "no")')"
if [[ "$needs_backfill" == yes ]]; then
  for w in $(seq 0 12); do log "backfill window $w of 12"; backfill_window "$w"; done
fi
FIRST_RUN="$(invoke "$INGEST_FN" '{"mode":"scheduled"}')"
log "first scheduled run: $FIRST_RUN"

URL="$(stack_output "$STACK" Url)"
log "waiting for $URL/health"
code=000
for _ in $(seq 1 60); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "$URL/health" || true)"
  [[ "$code" == 200 ]] && break
  sleep 10
done
[[ "$code" == 200 ]] || die "$URL/health returned $code after 10 minutes"
log "confirm the SNS subscription sent to $EMAIL, or alarms will not reach you"
log "deploy finished"
echo "URL: $URL"
