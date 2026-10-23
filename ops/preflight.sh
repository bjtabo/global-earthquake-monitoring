#!/usr/bin/env bash
# Read-only checks before a deploy. Changes nothing.
source "$(dirname "$0")/lib.sh"

need() { command -v "$1" >/dev/null || die "missing tool: $1"; }
for t in aws docker git python3 curl; do need "$t"; done
aws --version 2>&1 | grep -q '^aws-cli/2' || die "AWS CLI v2 required"
python3 -m pip --version >/dev/null 2>&1 || die "python3 has no pip (needed to package the Lambdas)"
docker info >/dev/null 2>&1 || die "Docker is not running"
aws sts get-caller-identity >/dev/null 2>&1 || die "no usable AWS credentials"
log "caller $(aws sts get-caller-identity --query Arn --output text) in $AWS_REGION"

for s in "$STACK" "$BOOTSTRAP_STACK"; do
  st="$(stack_status "$s")"
  case "$st" in
    NONE|CREATE_COMPLETE|UPDATE_COMPLETE|UPDATE_ROLLBACK_COMPLETE) ;;
    *) die "stack $s is $st; fix or delete it first" ;;
  esac
done

quota() { aws service-quotas get-service-quota --service-code "$1" --quota-code "$2" --query 'Quota.Value' --output text 2>/dev/null || echo unknown; }
headroom() { # label needed used limit
  [[ "$4" == unknown ]] && { log "WARN $1: quota not readable, skipping"; return 0; }
  awk -v n="$2" -v u="$3" -v l="$4" 'BEGIN { exit !(l - u >= n) }' || die "$1: need $2 free, used $3 of $4"
  log "ok $1: used $3 of $4"
}
ROOT_STATUS="$(stack_status "$STACK")"
if [[ "$ROOT_STATUS" == NONE ]]; then
  VPCS="$(aws ec2 describe-vpcs --query 'length(Vpcs)' --output text)"
  ADDRESSES="$(aws ec2 describe-addresses --query 'length(Addresses)' --output text)"
  headroom "VPCs" 1 "$VPCS" "$(quota vpc L-F678F1CE)"
  headroom "Elastic IPs" 2 "$ADDRESSES" "$(quota ec2 L-0263D0A3)"
  # Names this project will create must be free, so deploy and destroy never touch anything else.
  aws rds describe-db-instances --db-instance-identifier "$STACK-db" >/dev/null 2>&1 \
    && die "a DB instance named $STACK-db already exists; use another STACK"
  SECRETS="$(aws secretsmanager list-secrets --include-planned-deletion --filters Key=name,Values="$STACK/" --query 'SecretList[].Name' --output text)"
  [[ -z "$SECRETS" ]] || die "secrets named $STACK/... exist (an earlier deploy not purged?); run destroy.sh --purge or use another STACK"
  LOG_GROUPS="$(aws logs describe-log-groups --log-group-name-prefix "/$STACK/" --query 'logGroups[].logGroupName' --output text)"
  [[ -z "$LOG_GROUPS" ]] || die "log groups under /$STACK/ already exist; use another STACK"
  BOOTSTRAP_STATUS="$(stack_status "$BOOTSTRAP_STACK")"
  if [[ "$BOOTSTRAP_STATUS" == NONE ]]; then
    aws ecr describe-repositories --repository-names "$STACK-api" >/dev/null 2>&1 \
      && die "an ECR repository named $STACK-api already exists; use another STACK"
  fi
fi
headroom "Fargate vCPU" 1 0 "$(quota fargate L-3032A538)"
log "Lambda account concurrency: $(aws lambda get-account-settings --query AccountLimit.ConcurrentExecutions --output text)"

aws ec2 describe-managed-prefix-lists --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
  --query 'PrefixLists[0].PrefixListId' --output text | grep -q '^pl-' || die "CloudFront origin-facing prefix list not found"
log "preflight ok"
