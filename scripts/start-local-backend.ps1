param(
    [string]$EnvFile = '',
    [string]$PythonExe = '',
    [string]$TopicId = '',
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
if (!$EnvFile) { $EnvFile = if ($env:RETAIL_ENV_FILE) { $env:RETAIL_ENV_FILE } else { Join-Path $projectDirectory 'backend\.env' } }
if (!$PythonExe) { $PythonExe = Join-Path $projectDirectory '.venv\Scripts\python.exe' }
if (!(Test-Path -LiteralPath $EnvFile -PathType Leaf)) { throw 'The local environment file was not found.' }
if (!(Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'The configured Python executable was not found.' }

if (!$env:RETAIL_ENV_FILE) { $env:RETAIL_ENV_FILE = (Resolve-Path -LiteralPath $EnvFile).Path }
# Only an explicit TopicId overrides the user's configuration. Never target the
# original author's topic or silently enable publishing in another account.
if ($TopicId) { $env:OCI_NOTIFICATION_TOPIC_ID = $TopicId }

Push-Location $projectDirectory
try {
    if ($TopicId) {
        & $PythonExe scripts/check_notification_readiness.py --env-file $env:RETAIL_ENV_FILE --topic-id $TopicId
        if ($LASTEXITCODE -ne 0) { throw 'Notification preflight failed. No notification was sent.' }
    } elseif ($CheckOnly) { throw 'CheckOnly requires -TopicId for the read-only notification check.' }
    if ($CheckOnly) { return }
    Write-Host 'Using your private runtime configuration. Notifications still require explicit in-app approval and dispatch.'
    & $PythonExe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
    if ($LASTEXITCODE -ne 0) { throw 'Backend stopped with an error. Check whether port 8000 is already in use.' }
}
finally { Pop-Location }
