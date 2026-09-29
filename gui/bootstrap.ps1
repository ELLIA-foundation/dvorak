# Create gui/.venv and install the desktop GUI plus bench instrument packages.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = $null
foreach ($candidate in @("py", "python")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) {
        $Python = $cmd.Source
        break
    }
}
if (-not $Python) {
    throw "Python 3.12+ was not found on PATH."
}

$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    & $Python -m venv (Join-Path $PSScriptRoot ".venv")
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
Write-Host "Ready. Launch with:"
Write-Host "  gui\run.cmd"
