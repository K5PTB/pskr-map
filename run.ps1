#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& .venv\Scripts\pip install -q -r requirements.txt

# run.py sets WindowsSelectorEventLoopPolicy before uvicorn starts,
# which is required for aiomqtt on Windows (ProactorEventLoop lacks add_reader/remove_writer).
& .venv\Scripts\python run.py
