param(
  [string]$EnvFile = "../../env/local.env.example",
  [string]$ComposeFile = "../../compose/docker-compose.local.yml"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$envPath = Resolve-Path (Join-Path $scriptDir $EnvFile)
$composePath = Resolve-Path (Join-Path $scriptDir $ComposeFile)

Get-Content -LiteralPath $envPath | ForEach-Object {
  if ($_ -match "^\s*#" -or $_ -notmatch "=") {
    return
  }

  $name, $value = $_ -split "=", 2
  if ($name) {
    Set-Item -Path "Env:$name" -Value $value
  }
}

$bucket = $env:MINIO_BUCKET
if ([string]::IsNullOrWhiteSpace($bucket)) {
  throw "MINIO_BUCKET is required."
}

docker compose --env-file $envPath -f $composePath exec -T minio sh -c "mc alias set local http://localhost:9000 `"$env:MINIO_ROOT_USER`" `"$env:MINIO_ROOT_PASSWORD`" >/dev/null && mc mb --ignore-existing local/$bucket"
