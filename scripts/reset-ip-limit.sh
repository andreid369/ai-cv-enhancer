#!/usr/bin/env bash
# Clears the per-IP upload counters (useful after testing, since you hit your own limit quickly).
# Usage: bash scripts/reset-ip-limit.sh [stack-name] [region]
set -euo pipefail

STACK="${1:-cv-enhancer}"
REGION="${2:-eu-central-1}"

TABLE="$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='IpLimitTableName'].OutputValue" --output text)"

COUNT=0
for KEY in $(aws dynamodb scan --table-name "$TABLE" --region "$REGION" \
    --projection-expression ipHash --query "Items[].ipHash.S" --output text); do
  aws dynamodb delete-item --table-name "$TABLE" --region "$REGION" \
    --key "{\"ipHash\":{\"S\":\"$KEY\"}}"
  COUNT=$((COUNT + 1))
done
echo "Cleared $COUNT IP counter(s) from $TABLE."
