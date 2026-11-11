# One-time setup, run in Windows PowerShell from the extracted project folder.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
py -3 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python 3 is required (install Python 3.11 or newer).' }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed' }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency install failed' }
Write-Host 'Setup finished. Install FFmpeg, then run: .\start_windows.ps1' -ForegroundColor Green
