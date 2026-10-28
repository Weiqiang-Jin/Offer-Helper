$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the Python virtual environment.' }
    & '.venv\Scripts\python.exe' -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install dependencies. Check your network and retry.' }
}
& '.venv\Scripts\python.exe' run.py @args
exit $LASTEXITCODE

