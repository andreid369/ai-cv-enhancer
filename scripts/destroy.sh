#!/usr/bin/env bash
# Deletes everything: empties the buckets (CloudFormation can't delete non-empty buckets) and deletes the stack.
# Usage: bash scripts/destroy.sh [stack-name] [region]
set -euo pipefail

STACK="${1:-cv-enhancer}"
REGION="${2:-eu-central-1}"

out() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

for KEY in FrontendBucketName CvBucketName; do
  BUCKET="$(out "$KEY")"
  if [ -n "$BUCKET" ] && [ "$BUCKET" != "None" ]; then
    echo "==> Emptying s3://$BUCKET"
    aws s3 rm "s3://$BUCKET" --recursive --region "$REGION" >/dev/null
  fi
done

echo "==> Deleting stack $STACK (CloudFront takes 5-15 minutes)"
aws cloudformation delete-stack --stack-name "$STACK" --region "$REGION"
aws cloudformation wait stack-delete-complete --stack-name "$STACK" --region "$REGION"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
ARTIFACTS="$(echo "${STACK}-artifacts-${ACCOUNT}-${REGION}" | tr '[:upper:]' '[:lower:]')"
echo "==> Deleting artifacts bucket $ARTIFACTS"
aws s3 rb "s3://$ARTIFACTS" --force --region "$REGION" 2>/dev/null || true
echo "Done."
