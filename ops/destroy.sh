#!/usr/bin/env bash
# Deletes every stack for $STACK. --purge also removes the final snapshot and secrets now.
source "$(dirname "$0")/lib.sh"

PURGE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge) PURGE=1; shift ;;
    --yes) YES=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done
confirm_account
DB_ID="$STACK-db"

wait_vpc_drained() {
  [[ -n "${VPC_ID:-}" && "$VPC_ID" != None ]] || return 0
  local i enis sgs
  for i in $(seq 1 60); do
    enis="$(aws ec2 describe-network-interfaces --filters Name=vpc-id,Values="$VPC_ID" --query 'length(NetworkInterfaces)' --output text 2>/dev/null || echo 0)"
    sgs="$(aws ec2 describe-security-groups --filters Name=vpc-id,Values="$VPC_ID" Name=group-name,Values=CloudFront-VPCOrigins-Service-SG --query 'length(SecurityGroups)' --output text 2>/dev/null || echo 0)"
    [[ "$enis" == 0 && "$sgs" == 0 ]] && return 0
    log "waiting for VPC $VPC_ID to drain: $enis ENIs, $sgs CloudFront SG (try $i/60)"
    sleep 30
  done
}

delete_stack() {
  local name="$1" attempt st
  st="$(stack_status "$name")"
  [[ "$st" == NONE ]] && return 0
  for attempt in 1 2 3; do
    log "deleting $name (attempt $attempt)"
    aws cloudformation delete-stack --stack-name "$name"
    aws cloudformation wait stack-delete-complete --stack-name "$name" 2>/dev/null && return 0
    aws cloudformation describe-stack-events --stack-name "$name" \
      --query "StackEvents[?ResourceStatus=='DELETE_FAILED'].[LogicalResourceId,ResourceStatusReason]" \
      --output text | head -5 >&2 || true
    wait_vpc_drained
  done
  die "could not delete $name"
}

empty_bucket() { # name; a bucket already gone (a delete that failed further on) is skipped, any other error stops
  local err
  [[ -n "$1" && "$1" != None ]] || return 0
  if ! err="$(aws s3api head-bucket --bucket "$1" 2>&1 >/dev/null)"; then
    [[ "$err" == *"(404)"* ]] && return 0
    die "head-bucket $1: $err"
  fi
  log "emptying s3://$1"
  aws s3 rm "s3://$1" --recursive --only-show-errors
}

ROOT_STATUS="$(stack_status "$STACK")"
if [[ "$ROOT_STATUS" != NONE ]]; then
  VPC_ID="$(stack_output "$STACK" VpcId)"
  SITE_BUCKET="$(stack_output "$STACK" SiteBucketName)"
  empty_bucket "$SITE_BUCKET"
  if aws rds describe-db-instances --db-instance-identifier "$DB_ID" >/dev/null 2>&1; then
    log "turning off deletion protection on $DB_ID"
    aws rds modify-db-instance --db-instance-identifier "$DB_ID" --no-deletion-protection --apply-immediately >/dev/null
  fi
  delete_stack "$STACK"
fi

BOOTSTRAP_STATUS="$(stack_status "$BOOTSTRAP_STACK")"
if [[ "$BOOTSTRAP_STATUS" != NONE ]]; then
  BUCKET="$(stack_output "$BOOTSTRAP_STACK" ArtifactsBucketName)"
  empty_bucket "$BUCKET"
  delete_stack "$BOOTSTRAP_STACK"
fi

if [[ "$PURGE" == 1 ]]; then
  # Only what this project tagged, or log groups named after this stack's canary: --purge must never touch
  # someone else's data in a shared account.
  SNAPSHOTS="$(aws rds describe-db-snapshots --db-instance-identifier "$DB_ID" --snapshot-type manual \
    --query "DBSnapshots[?TagList[?Key=='Project' && Value=='$STACK']].DBSnapshotIdentifier" --output text)"
  for snap in $SNAPSHOTS; do
    log "deleting snapshot $snap"
    aws rds delete-db-snapshot --db-snapshot-identifier "$snap" >/dev/null
  done
  SECRETS="$(aws secretsmanager list-secrets --include-planned-deletion \
    --filters Key=name,Values="$STACK/" Key=tag-key,Values=Project Key=tag-value,Values="$STACK" \
    --query 'SecretList[].ARN' --output text)"
  for arn in $SECRETS; do
    log "deleting secret $arn"
    aws secretsmanager delete-secret --secret-id "$arn" --force-delete-without-recovery >/dev/null
  done
  # CloudFormation deregisters task definitions (INACTIVE); deleting them is a separate call.
  TASK_DEFS="$(aws ecs list-task-definitions --family-prefix "$STACK-api" --status INACTIVE \
    --query 'taskDefinitionArns' --output text)"
  for td in $TASK_DEFS; do
    owner="$(aws ecs describe-task-definition --task-definition "$td" --include TAGS \
      --query "tags[?key=='Project'].value | [0]" --output text)"
    [[ "$owner" == "$STACK" ]] || continue
    log "deleting task definition ${td##*/}"
    failed="$(aws ecs delete-task-definitions --task-definitions "$td" --query 'failures[].reason' --output text)"
    [[ -z "$failed" ]] || die "delete ${td##*/}: $failed"
  done
  CANARY_LOGS="$(aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/cwsyn-$STACK-canary" \
    --query 'logGroups[].logGroupName' --output text)"
  for lg in $CANARY_LOGS; do
    log "deleting log group $lg"
    aws logs delete-log-group --log-group-name "$lg"
  done
  "$ROOT/ops/audit.sh"
else
  log "kept: final snapshot of $DB_ID and secrets in their recovery window; rerun with --purge to remove them"
fi
