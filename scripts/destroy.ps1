<#
.SYNOPSIS
  Deletes everything: empties the buckets (CloudFormation can't delete non-empty buckets) and deletes the stack.
#>
param(
  [string] $StackName = "cv-enhancer",
  [string] $Region = "eu-central-1"
)
$ErrorActionPreference = "Stop"

$Outputs = aws cloudformation describe-stacks --stack-name $StackName --region $Region --query "Stacks[0].Outputs" --output json | ConvertFrom-Json
foreach ($Key in "FrontendBucketName", "CvBucketName") {
  $Bucket = ($Outputs | Where-Object { $_.OutputKey -eq $Key }).OutputValue
  if ($Bucket) {
    Write-Host "==> Emptying s3://$Bucket"
    aws s3 rm "s3://$Bucket" --recursive --region $Region | Out-Null
  }
}

Write-Host "==> Deleting stack $StackName"
aws cloudformation delete-stack --stack-name $StackName --region $Region
aws cloudformation wait stack-delete-complete --stack-name $StackName --region $Region

$Account = aws sts get-caller-identity --query Account --output text
$ArtifactsBucket = "$StackName-artifacts-$Account-$Region".ToLower()
Write-Host "==> Deleting artifacts bucket $ArtifactsBucket"
aws s3 rb "s3://$ArtifactsBucket" --force --region $Region 2>$null
Write-Host "Done."
