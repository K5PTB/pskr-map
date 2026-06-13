#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& .venv\Scripts\pip install -q -r requirements.txt

$host_addr = if ($env:HOST) { $env:HOST } else { "0.0.0.0" }
$port      = if ($env:PORT) { $env:PORT } else { "8765" }

& .venv\Scripts\uvicorn backend.main:app --host $host_addr --port $port --log-level info
