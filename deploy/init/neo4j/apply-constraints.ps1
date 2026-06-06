param(
  [string]$EnvFile = "../../env/local.env.example",
  [string]$ComposeFile = "../../compose/docker-compose.local.yml",
  [string]$ConstraintFile = "constraints.cypher"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$envPath = Resolve-Path (Join-Path $scriptDir $EnvFile)
$composePath = Resolve-Path (Join-Path $scriptDir $ComposeFile)
$constraintPath = Resolve-Path (Join-Path $scriptDir $ConstraintFile)

Get-Content -LiteralPath $envPath | ForEach-Object {
  if ($_ -match "^\s*#" -or $_ -notmatch "=") {
    return
  }

  $name, $value = $_ -split "=", 2
  if ($name) {
    Set-Item -Path "Env:$name" -Value $value
  }
}

$authParts = $env:NEO4J_AUTH -split "/", 2
if ($authParts.Count -ne 2) {
  throw "NEO4J_AUTH must use username/password format."
}

$user = $authParts[0]
$password = $authParts[1]
$cypher = Get-Content -Raw -LiteralPath $constraintPath

$cypher | docker compose --env-file $envPath -f $composePath exec -T neo4j cypher-shell -u $user -p $password
