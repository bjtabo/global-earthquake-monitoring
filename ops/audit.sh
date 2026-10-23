#!/usr/bin/env bash
# Lists anything left for $STACK. Exit 0 only when nothing is left.
source "$(dirname "$0")/lib.sh"
region_only "$@"

LEFT=0
ERR="$(mktemp)"
trap 'rm -f "$ERR"' EXIT
show() { # kind values
  local values; values="$(echo "$2" | xargs)"
  if [[ -n "$values" && "$values" != None ]]; then echo "LEFT: $1: $values"; LEFT=1; else echo "ok: $1"; fi
}
report() { # kind command... ; a NotFound error means nothing is left, any other error stops the audit
  local kind="$1" values; shift
  if ! values="$("$@" 2>"$ERR")"; then
    grep -q NotFound "$ERR" || die "$kind: $(cat "$ERR")"
    values=""
  fi
  show "$kind" "$values"
}

# The tagging index keeps listing deleted EC2 and ECS resources for hours, so ask the service itself.
# Each case names the lookup; one call runs it. An empty answer or a not-found error means gone. Any other
# failure, and any type not listed, counts as left.
gone() { # region arn
  local region="$1" arn="$2" svc path kind cluster out
  IFS=: read -r _ _ svc _ _ path <<<"$arn"
  IFS=/ read -r kind cluster _ <<<"$path"
  local id="${arn##*/}"
  case "$svc:$kind" in
    ec2:subnet) set -- ec2 describe-subnets --filters Name=subnet-id,Values="$id" --query 'Subnets[].SubnetId' ;;
    ec2:natgateway) set -- ec2 describe-nat-gateways --filter Name=nat-gateway-id,Values="$id" --query "NatGateways[?State!='deleted'].NatGatewayId" ;;
    ec2:vpc-endpoint) set -- ec2 describe-vpc-endpoints --filters Name=vpc-endpoint-id,Values="$id" --query "VpcEndpoints[?State!='deleted'].VpcEndpointId" ;;
    ec2:vpc-flow-log) set -- ec2 describe-flow-logs --filter Name=flow-log-id,Values="$id" --query 'FlowLogs[].FlowLogId' ;;
    ecs:cluster) set -- ecs describe-clusters --clusters "$arn" --query "clusters[?status!='INACTIVE'].clusterArn" ;;
    ecs:service) set -- ecs describe-services --cluster "$cluster" --services "$arn" --query "services[?status!='INACTIVE'].serviceArn" ;;
    ecs:task) set -- ecs describe-tasks --cluster "$cluster" --tasks "$arn" --query "tasks[?lastStatus!='STOPPED'].taskArn" ;;
    ecs:task-definition) set -- ecs describe-task-definition --task-definition "$arn" \
      --query "[taskDefinition][?status!='DELETE_IN_PROGRESS'].taskDefinitionArn" ;;
    *) return 1 ;;
  esac
  if ! out="$(aws "$@" --region "$region" --output text 2>"$ERR")"; then
    # ECS lookups by identifier fail once ECS has forgotten the cluster or the revision.
    grep -q -e ClusterNotFoundException -e 'Unable to describe task definition' "$ERR" && return 0
    cat "$ERR" >&2
    return 1
  fi
  [[ -z "$out" ]]
}

report stacks aws cloudformation list-stacks \
  --query "StackSummaries[?StackStatus!='DELETE_COMPLETE' && (StackName=='$STACK' || starts_with(StackName, '$STACK-'))].StackName" --output text
# us-east-1 too: CloudFront and other global services keep their resources there.
for region in $(printf '%s\n' "$AWS_REGION" us-east-1 | sort -u); do
  live="" stale=0
  arns="$(aws resourcegroupstaggingapi get-resources --region "$region" \
    --tag-filters Key=Project,Values="$STACK" --query 'ResourceTagMappingList[].ResourceARN' --output text)"
  for arn in $arns; do
    if gone "$region" "$arn"; then stale=$((stale + 1)); else live="$live $arn"; fi
  done
  [[ "$stale" == 0 ]] || echo "note: $stale tag entries in $region point to resources that are deleted, inactive or stopped"
  show "tagged resources ($region)" "$live"
done
report "db instance" aws rds describe-db-instances --query "DBInstances[?DBInstanceIdentifier=='$STACK-db'].DBInstanceIdentifier" --output text
report "db snapshots" aws rds describe-db-snapshots --db-instance-identifier "$STACK-db" --query 'DBSnapshots[].DBSnapshotIdentifier' --output text
report secrets aws secretsmanager list-secrets --include-planned-deletion --filters Key=name,Values="$STACK/" --query 'SecretList[].Name' --output text
for prefix in "/$STACK/" "/aws/rds/instance/$STACK-db" "/aws/lambda/cwsyn-$STACK-canary"; do
  report "log groups $prefix" aws logs describe-log-groups --log-group-name-prefix "$prefix" --query 'logGroups[].logGroupName' --output text
done
report "ECR repository" aws ecr describe-repositories --query "repositories[?repositoryName=='$STACK-api'].repositoryName" --output text
report "CloudFront VPC origins" aws cloudfront list-vpc-origins --query "VpcOriginList.Items[?starts_with(Name, '$STACK-')].Id" --output text
report SLOs aws application-signals list-service-level-objectives --query "SloSummaries[?starts_with(Name, '$STACK-')].Name" --output text
ACCOUNT="$(account_id)"
report budgets aws budgets describe-budgets --account-id "$ACCOUNT" --query "Budgets[?BudgetName=='$STACK-monthly'].BudgetName" --output text

[[ "$LEFT" == 0 ]] && echo "audit clean"
exit "$LEFT"
