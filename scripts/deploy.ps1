<#
.SYNOPSIS
  Deploys the AI CV Enhancer: packages the Lambdas, deploys the CloudFormation stack,
  writes frontend/config.js and uploads the website.

.EXAMPLE
  .\scripts\deploy.ps1 -SenderEmail you@example.com
  .\scripts\deploy.ps1 -SenderEmail you@example.com -ModelId eu.anthropic.claude-haiku-5-5
#>
param(
  [Parameter(Mandatory = $true)] [string] $SenderEmail,   # "From" address of the CV emails (verified in SES)
  [ValidateSet("true", "false")] [string] $CreateSenderIdentity = "true",
  [string] $StackName = "cv-enhancer",
  [string] $Region = "eu-central-1",
  [string] $ModelId = "eu.anthropic.claude-sonnet-5-5",
  [string] $AllowedOrigin = ""
)

$ErrorActionPreference = "Stop"
function Assert-Ok([string] $Step) { if ($LASTEXITCODE -ne 0) { throw "Failed: $Step" } }

$Root = Split-Path -Parent $PSScriptRoot
$Template = Join-Path $Root "infrastructure\template.yaml"
$Packaged = Join-Path $Root "infrastructure\packaged.yaml"
$Frontend = Join-Path $Root "frontend"

$Account = aws sts get-caller-identity --query Account --output text; Assert-Ok "aws sts get-caller-identity (is the AWS CLI configured?)"
$ArtifactsBucket = "$StackName-artifacts-$Account-$Region".ToLower()

Write-Host "==> Artifacts bucket: $ArtifactsBucket"
aws s3api head-bucket --bucket $ArtifactsBucket 2>$null
if ($LASTEXITCODE -ne 0) {
  if ($Region -eq "us-east-1") {
    aws s3api create-bucket --bucket $ArtifactsBucket --region $Region | Out-Null
  } else {
    aws s3api create-bucket --bucket $ArtifactsBucket --region $Region --create-bucket-configuration LocationConstraint=$Region | Out-Null
  }
  Assert-Ok "create artifacts bucket"
}

Write-Host "==> Packaging Lambda code"
aws cloudformation package --template-file $Template --s3-bucket $ArtifactsBucket --output-template-file $Packaged --region $Region
Assert-Ok "cloudformation package"

Write-Host "==> Deploying stack $StackName (first run takes ~5 minutes because of CloudFront)"
aws cloudformation deploy `
  --template-file $Packaged `
  --stack-name $StackName `
  --region $Region `
  --capabilities CAPABILITY_IAM `
  --no-fail-on-empty-changeset `
  --parameter-overrides "SenderEmail=$SenderEmail" "CreateSenderIdentity=$CreateSenderIdentity" "BedrockModelId=$ModelId" "AllowedOrigin=$AllowedOrigin"
Assert-Ok "cloudformation deploy"

$Outputs = aws cloudformation describe-stacks --stack-name $StackName --region $Region --query "Stacks[0].Outputs" --output json | ConvertFrom-Json
Assert-Ok "describe-stacks"
function Get-Output([string] $Key) { ($Outputs | Where-Object { $_.OutputKey -eq $Key }).OutputValue }

$ApiUrl = Get-Output "ApiUrl"
$ApiId = Get-Output "RestApiId"
$WebBucket = Get-Output "FrontendBucketName"
$DistributionId = Get-Output "DistributionId"
$WebsiteUrl = Get-Output "WebsiteUrl"

# CloudFormation does not redeploy an API Gateway stage when methods change, so do it explicitly.
Write-Host "==> Redeploying API stage"
aws apigateway create-deployment --rest-api-id $ApiId --stage-name prod --region $Region | Out-Null
Assert-Ok "apigateway create-deployment"

Write-Host "==> Writing frontend/config.js"
$ConfigJs = "window.CV_APP_CONFIG = {`n  apiUrl: `"$ApiUrl`"`n};`n"
[System.IO.File]::WriteAllText((Join-Path $Frontend "config.js"), $ConfigJs, (New-Object System.Text.UTF8Encoding $false))

Write-Host "==> Uploading website to s3://$WebBucket"
aws s3 sync $Frontend "s3://$WebBucket" --delete --region $Region --exclude "config.js"
Assert-Ok "s3 sync"
aws s3 cp (Join-Path $Frontend "config.js") "s3://$WebBucket/config.js" --cache-control "no-cache" --content-type "application/javascript" --region $Region
Assert-Ok "upload config.js"

aws cloudfront create-invalidation --distribution-id $DistributionId --paths "/*" | Out-Null
Assert-Ok "cloudfront invalidation"

Write-Host ""
Write-Host "Done."
Write-Host "  Website: $WebsiteUrl"
Write-Host "  API:     $ApiUrl"
Write-Host "  First deployment: click the Amazon SES verification link sent to $SenderEmail."
