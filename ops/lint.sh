#!/usr/bin/env bash
# Static checks for every template; same entry point locally and in CI.
set -euo pipefail
cd "$(dirname "$0")/.."
shopt -s nullglob

templates=(infra/*.yaml)
if (( ${#templates[@]} )); then
  cfn-lint "${templates[@]}"
  cfn-guard validate --rules infra/guard/rules.guard --data infra --show-summary fail
fi

summary="$(cfn-guard validate --rules infra/guard/rules.guard --data tests/guard/bad.yaml --show-summary all || true)"
for rule in s3_private_and_encrypted rds_private_and_encrypted no_ingress_from_internet \
            no_standalone_ingress_from_internet iam_no_wildcard_action log_groups_expire lambdas_in_vpc; do
  grep -Eq "/${rule} +FAIL$" <<<"$summary" || { echo "guard rule $rule did not reject tests/guard/bad.yaml" >&2; exit 1; }
done
cfn-guard validate --rules infra/guard/rules.guard --data tests/guard/good.yaml --show-summary fail
echo "lint ok"
