# Serve this folder on http://127.0.0.1:8000 so the pages are a secure context
# (WebUSB and Web MIDI require one; file:// will not work).
#
#   powershell -ExecutionPolicy Bypass -File .\serve.ps1
#
# Localhost only: nothing is exposed to the network. Ctrl+C to stop the server.

$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$page = Join-Path $here 'read-backup.html'
$flash = Join-Path $here 'flash.html'

if (-not (Test-Path $page)) {
    Write-Host "read-backup.html is not in $here" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $flash)) {
    Write-Host "warning: flash.html is not in $here (read page only)" -ForegroundColor Yellow
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host 'python was not found on PATH.' -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host ' Nanocore firmware tools (WebUSB)' -ForegroundColor Cyan
Write-Host " folder : $here"
Write-Host ' read   : http://127.0.0.1:8000/read-backup.html' -ForegroundColor Green
Write-Host ' flash  : http://127.0.0.1:8000/flash.html' -ForegroundColor Green
Write-Host ' browser: desktop Chrome or Edge'
Write-Host ' quit   : Ctrl+C'
Write-Host ''
Write-Host ' flash.html WRITES to the pedal. Its default mode is a dry run (Stage 0).' -ForegroundColor Yellow
Write-Host ' Before step 1: quit ToneCommand and unplug the audio/headphone cable.' -ForegroundColor Yellow
Write-Host ''

& $python.Source -m http.server 8000 --bind 127.0.0.1 --directory $here
