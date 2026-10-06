param(
    [string]$EnvFile = 'C:\Projects\deployment_v3\.env',
    [string]$PythonExe = 'C:\Projects\deployment_v3\.venv\Scripts\python.exe',
    [string]$TopicId = 'ocid1.onstopic.oc1.ap-hyderabad-1.amaaaaaau6bxbdqaj5w2cgkahgv3qaldjeop6xrvubxplviuoork2s6mguxa',
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
if (!(Test-Path -LiteralPath $EnvFile -PathType Leaf)) { throw 'The local environment file was not found.' }
if (!(Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'The configured Python executable was not found.' }

if (!$env:RETAIL_ENV_FILE) { $env:RETAIL_ENV_FILE = (Resolve-Path -LiteralPath $EnvFile).Path }
# Run in the SAME terminal as the working backend. Keep its AI, authentication,
# database and routing settings, including process-level overrides.
# Existing shared topic, verified against runtime-config-private/backend-v1.json.
# Override TopicId explicitly for a different environment; no credentials are stored here.
$env:OCI_NOTIFICATION_TOPIC_ID = $TopicId
$env:OCI_NOTIFICATION_SHARED_POC_ENABLED = 'true'
$env:OCI_NOTIFICATION_PUBLISH_ENABLED = 'true'

Push-Location $projectDirectory
try {
    & $PythonExe scripts/check_notification_readiness.py --env-file $env:RETAIL_ENV_FILE --topic-id $TopicId
    if ($LASTEXITCODE -ne 0) { throw 'Notification preflight failed. No notification was sent.' }
    if ($CheckOnly) { return }
    Write-Host 'Shared POC mode: all active email subscribers receive explicitly approved notifications.'
    Write-Host 'Create a fresh draft; old dedicated-supplier drafts are not retargeted.'
    & $PythonExe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
    if ($LASTEXITCODE -ne 0) { throw 'Backend stopped with an error. Check whether port 8000 is already in use.' }
}
finally { Pop-Location }
