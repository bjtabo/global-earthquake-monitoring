#!/usr/bin/env bash
# Sourced by every ops script. Pins the region and the naming scheme.
set -euo pipefail
# inherit_errexit (4.4+) keeps errexit on inside $(...). A failure then stops the script only through a plain
# assignment, x="$(f)"; inside an argument or [[ ]] it is lost, so every capture whose failure matters is an assignment.
(( BASH_VERSINFO[0] * 100 + BASH_VERSINFO[1] >= 404 )) || { echo "error: bash 4.4 or newer is required (macOS ships 3.2: brew install bash)" >&2; exit 1; }
shopt -s inherit_errexit

export AWS_REGION=us-east-2 AWS_DEFAULT_REGION=us-east-2 AWS_PAGER=""
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
