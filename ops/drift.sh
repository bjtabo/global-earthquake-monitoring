#!/usr/bin/env bash
# Drift detection on the root stack and every nested stack. Exit 1 if anything drifted.
source "$(dirname "$0")/lib.sh"
region_only "$@"

NESTED="$(aws cloudformation list-stack-resources --stack-name "$STACK" \
  --query "StackResourceSummaries[?ResourceType=='AWS::CloudFormation::Stack'].PhysicalResourceId" --output text)"
read -r -a nested <<<"$NESTED"
DRIFTED=0
for s in "$STACK" "${nested[@]}"; do
  id="$(aws cloudformation detect-stack-drift --stack-name "$s" --query StackDriftDetectionId --output text)"
  while :; do
    out="$(aws cloudformation describe-stack-drift-detection-status --stack-drift-detection-id "$id" \
      --query '[DetectionStatus,StackDriftStatus]' --output text)"
    read -r status drift <<<"$out"
    [[ "$status" == DETECTION_IN_PROGRESS ]] || break
    sleep 5
  done
  name="${s#*:stack/}"
  echo "$drift  ${name%%/*}"
  if [[ "$drift" == DRIFTED ]]; then
    DRIFTED=1
    aws cloudformation describe-stack-resource-drifts --stack-name "$s" \
      --stack-resource-drift-status-filters MODIFIED DELETED \
      --query 'StackResourceDrifts[].[LogicalResourceId,StackResourceDriftStatus]' --output text
  fi
done
exit "$DRIFTED"
