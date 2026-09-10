param([ValidateSet('start', 'stop', 'status')][string]$Action = 'start')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$postgresData = Join-Path $projectRoot 'tmp/postgres-local-16'
$postgresLog = Join-Path $projectRoot 'tmp/postgres-local-16.log'
$pgCtl = 'C:\Program Files\PostgreSQL\16\bin\pg_ctl.exe'
if (-not (Test-Path -LiteralPath $pgCtl)) { throw 'PostgreSQL 16 introuvable.' }
if (-not (Test-Path -LiteralPath (Join-Path $postgresData 'PG_VERSION'))) {
    throw 'Instance locale absente. Voir docs/finance-postgresql.md.'
}
$pgIsReady = Join-Path (Split-Path -Parent $pgCtl) 'pg_isready.exe'
if ($Action -eq 'start') {
    & $pgIsReady -h 127.0.0.1 -p 55440 -q
    if ($LASTEXITCODE -eq 0) { Write-Output 'PostgreSQL local est deja demarre.'; exit 0 }
    & $pgCtl start -D $postgresData -l $postgresLog -o '-h 127.0.0.1 -p 55440' -w -t 30
} elseif ($Action -eq 'stop') {
    & $pgCtl stop -D $postgresData -m fast -w -t 30
} else {
    & $pgIsReady -h 127.0.0.1 -p 55440
}
exit $LASTEXITCODE
