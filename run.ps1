#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

# Find Python 3.11+ — tries explicit version names first so a newer install
# is preferred over an older one that may also be on PATH as plain "python".
$python = $null
foreach ($candidate in @("python3.12", "python3.11", "python3", "python")) {
    try {
        $ver = & $candidate --version 2>&1
        if ($ver -match "Python 3\.(\d+)" -and [int]$Matches[1] -ge 11) {
            $python = $candidate
            break
        }
    } catch { continue }
}

if (-not $python) {
    Write-Error "Python 3.11 or later is required but was not found on PATH.`nInstall from https://www.python.org/downloads/ and check 'Add python.exe to PATH'."
    exit 1
}

if (-not (Test-Path ".venv")) {
    & $python -m venv .venv
}

& .venv\Scripts\pip install -q -r requirements.txt

# run.py sets WindowsSelectorEventLoopPolicy before uvicorn starts,
# which is required for aiomqtt on Windows (ProactorEventLoop lacks add_reader/remove_writer).
& .venv\Scripts\python run.py
