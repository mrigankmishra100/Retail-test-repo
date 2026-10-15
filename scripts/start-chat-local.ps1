param(
    [string]$EnvFile,
    [string]$PythonExe,
    [int]$Port = 8000
)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
if (!$EnvFile) { $EnvFile = Join-Path $projectDirectory '.env' }
if (!$PythonExe) { $PythonExe = Join-Path $projectDirectory '.venv\Scripts\python.exe' }
if (!(Test-Path -LiteralPath $EnvFile -PathType Leaf)) { throw 'Repository .env was not found.' }
if (!(Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Repository Python environment was not found.' }
# Explicitly select the requested file; preserve notification and routing settings.
$env:RETAIL_ENV_FILE = (Resolve-Path -LiteralPath $EnvFile).Path
Push-Location $projectDirectory
try {
    & $PythonExe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Backend stopped. Check configuration and whether the port is already in use.' }
}
finally { Pop-Location }
