param([int]$Port = 8001)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path (Split-Path -Parent $projectDirectory) '.audit-venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) { $pythonExecutable = (Get-Command python).Source }
$serverProcess = Start-Process -FilePath $pythonExecutable -ArgumentList @('manage.py', 'runserver', "127.0.0.1:$Port", '--noreload') -WorkingDirectory $projectDirectory -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectDirectory "tmp/local-report-$Port.stdout.log") -RedirectStandardError (Join-Path $projectDirectory "tmp/local-report-$Port.stderr.log") -PassThru
Write-Output "Serveur local lancé (PID $($serverProcess.Id)) : http://127.0.0.1:$Port/rapports/situation-septembre-2026/"
