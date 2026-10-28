<#
.SYNOPSIS
  Clears the per-IP upload counters (useful after testing, since you hit your own limit quickly).
#>
param(
  [string] $StackName = "cv-enhancer",
  [string] $Region = "eu-central-1"
)
$ErrorActionPreference = "Stop"

$Table = aws cloudformation describe-stacks --stack-name $StackName --region $Region `
  --query "Stacks[0].Outputs[?OutputKey=='IpLimitTableName'].OutputValue" --output text
$Keys = aws dynamodb scan --table-name $Table --region $Region --projection-expression ipHash --query "Items[].ipHash.S" --output json | ConvertFrom-Json

foreach ($Key in $Keys) {
  $KeyJson = @{ ipHash = @{ S = $Key } } | ConvertTo-Json -Compress
  $TempFile = New-TemporaryFile
  [System.IO.File]::WriteAllText($TempFile, $KeyJson)
  aws dynamodb delete-item --table-name $Table --region $Region --key "file://$TempFile"
  Remove-Item $TempFile
}
Write-Host "Cleared $(@($Keys).Count) IP counter(s) from $Table."
