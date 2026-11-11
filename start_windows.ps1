# Run this from the extracted ShortsPilot project folder in PowerShell.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host 'Python environment missing. Run: py -3 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt' -ForegroundColor Yellow
    exit 1
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    Write-Host 'FFmpeg and FFprobe must be installed and available on PATH.' -ForegroundColor Red
    exit 1
}
if (-not $env:SHORTSPILOT_ADMIN_TOKEN) {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $env:SHORTSPILOT_ADMIN_TOKEN = [Convert]::ToHexString($bytes)
}
Write-Host 'Local dashboard: http://127.0.0.1:8000' -ForegroundColor Green
Write-Host "Admin token (paste into dashboard): $env:SHORTSPILOT_ADMIN_TOKEN" -ForegroundColor Yellow
Write-Host 'Keep this token private. Press Ctrl+C to stop.'
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
