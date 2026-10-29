#!/usr/bin/env bash
# Deploys the AI CV Enhancer (Linux / macOS / Git Bash / WSL).
# Usage: ./scripts/deploy.sh you@example.com [stack-name] [region] [model-id]
set -euo pipefail

EMAIL="${1:?Usage: deploy.sh <notification-email> [stack-name] [region] [model-id]}"
STACK="${2:-cv-enhancer}"
REGION="${3:-eu-central-1}"
MODEL_ID="${4:-eu.amazon.nova-2-lite-v1:0}"
ALLOWED_ORIGIN="${ALLOWED_ORIGIN:-}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
ARTIFACTS="$(echo "${STACK}-artifacts-${ACCOUNT}-${REGION}" | tr '[:upper:]' '[:lower:]')"

echo "==> Artifacts bucket: $ARTIFACTS"
if ! aws s3api head-bucket --bucket "$ARTIFACTS" 2>/dev/null; then
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$ARTIFACTS" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$ARTIFACTS" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
  fi
fi

echo "==> Packaging Lambda code"
aws cloudformation package \
  --template-file "$ROOT/infrastructure/template.yaml" \
  --s3-bucket "$ARTIFACTS" \
  --output-template-file "$ROOT/infrastructure/packaged.yaml" \
  --region "$REGION"

echo "==> Deploying stack $STACK"
aws cloudformation deploy \
  --template-file "$ROOT/infrastructure/packaged.yaml" \
  --stack-name "$STACK" --region "$REGION" \
  --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset \
  --parameter-overrides "NotificationEmail=$EMAIL" "BedrockModelId=$MODEL_ID" "AllowedOrigin=$ALLOWED_ORIGIN"

out() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
API_URL="$(out ApiUrl)"; API_ID="$(out RestApiId)"; WEB_BUCKET="$(out FrontendBucketName)"
DIST_ID="$(out DistributionId)"; WEBSITE="$(out WebsiteUrl)"

echo "==> Redeploying API stage"
aws apigateway create-deployment --rest-api-id "$API_ID" --stage-name prod --region "$REGION" >/dev/null

echo "==> Writing frontend/config.js"
printf 'window.CV_APP_CONFIG = {\n  apiUrl: "%s"\n};\n' "$API_URL" > "$ROOT/frontend/config.js"

echo "==> Uploading website"
aws s3 sync "$ROOT/frontend" "s3://$WEB_BUCKET" --delete --region "$REGION" --exclude config.js
aws s3 cp "$ROOT/frontend/config.js" "s3://$WEB_BUCKET/config.js" \
  --cache-control no-cache --content-type application/javascript --region "$REGION"
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" >/dev/null

echo
echo "Done."
echo "  Website: $WEBSITE"
echo "  API:     $API_URL"
echo "  Confirm the SNS subscription email sent to $EMAIL."

# When running in GitHub Actions, show the links on the workflow run page.
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### CV Enhancer deployed"
    echo "- Website: $WEBSITE"
    echo "- API: $API_URL"
    echo "- Stack: \`$STACK\` in \`$REGION\`, model \`$MODEL_ID\`"
  } >> "$GITHUB_STEP_SUMMARY"
fi
