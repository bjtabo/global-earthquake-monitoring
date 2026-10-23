#!/usr/bin/env bash
# Sourced by every ops script. Sets the naming scheme; each script takes the region as --region.
set -euo pipefail
# inherit_errexit (4.4+) keeps errexit on inside $(...). A failure then stops the script only through a plain
# assignment, x="$(f)"; inside an argument or [[ ]] it is lost, so every capture whose failure matters is an assignment.
(( BASH_VERSINFO[0] * 100 + BASH_VERSINFO[1] >= 404 )) || { echo "error: bash 4.4 or newer is required (macOS ships 3.2: brew install bash)" >&2; exit 1; }
shopt -s inherit_errexit

export AWS_PAGER=""
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK="${STACK:-quakes}"
BOOTSTRAP_STACK="$STACK-bootstrap"
BUILD="$ROOT/build"

die() { echo "error: $*" >&2; exit 1; }
log() { echo "==> $*" >&2; }

# The RDS identifier "$STACK-db" allows no double hyphen and no trailing one.
[[ "$STACK" =~ ^[a-z][a-z0-9-]{2,13}$ && "$STACK" != *--* && "$STACK" != *- ]] \
  || die "STACK must be 3 to 14 lowercase letters, digits or single hyphens, starting with a letter"

account_id() { aws sts get-caller-identity --query Account --output text; }

stack_status() { # NONE only when the stack does not exist; any other error stops the script
  local out
  if out="$(aws cloudformation describe-stacks --stack-name "$1" --query 'Stacks[0].StackStatus' --output text 2>&1)"; then
    echo "$out"
  elif [[ "$out" == *"does not exist"* ]]; then
    echo NONE
  else
    die "describe-stacks $1: $out"
  fi
}

stack_output() {
  aws cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text
}

confirm_account() {
  local acct; acct="$(account_id)"
  echo "Account: $acct  Region: $AWS_REGION  Stack: $STACK"
  echo "Caller:  $(aws sts get-caller-identity --query Arn --output text)"
  [[ "${YES:-0}" == 1 ]] && return 0
  local typed; read -r -p "Type the account id to continue: " typed
  [[ "$typed" == "$acct" ]] || die "account id did not match; nothing was changed"
}

# Regions with CloudFront VPC origins in every AZ (CloudFront Developer Guide, "Supported AWS Regions for VPC origins").
# us-east-1, us-west-1, ap-northeast-1 and ca-central-1 are left out: each has an AZ without VPC origins, and the
# templates take the first two AZs. Only us-east-2 has been deployed and tested.
REGIONS="us-east-2 us-west-2 af-south-1 ap-east-1 ap-east-2 ap-south-1 ap-south-2 ap-northeast-2 ap-northeast-3
  ap-southeast-1 ap-southeast-2 ap-southeast-3 ap-southeast-4 ap-southeast-5 ap-southeast-6 ap-southeast-7 ca-west-1
  eu-central-1 eu-central-2 eu-north-1 eu-south-1 eu-south-2 eu-west-1 eu-west-2 eu-west-3 il-central-1 me-central-1
  me-south-1 mx-central-1 sa-east-1"

use_region() { # region; every AWS call after this goes to it, whatever the profile says
  [[ -n "${1:-}" ]] || die "--region is required, for example --region us-east-2"
  local r
  for r in $REGIONS; do
    [[ "$1" == "$r" ]] && { export AWS_REGION="$1" AWS_DEFAULT_REGION="$1"; return 0; }
  done
  die "region $1 is not supported; use one of: $(echo $REGIONS)"
}

region_only() { # for scripts whose only argument is --region
  local region=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --region) [[ $# -ge 2 ]] || die "--region needs a value"; region="$2"; shift 2 ;;
      *) die "unknown argument: $1" ;;
    esac
  done
  use_region "$region"
}
