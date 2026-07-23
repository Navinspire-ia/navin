# Build navin.exe from NavinLauncher.cs using the C# compiler that ships
# with the .NET Framework on every Windows machine (no SDK required).
#
# Usage (from the repo root, in PowerShell or via WSL interop):
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build-launcher.ps1
#
# Output: dist\navin.exe

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Source = Join-Path $PSScriptRoot "NavinLauncher.cs"
$OutDir = Join-Path $Root "dist"
$Output = Join-Path $OutDir "navin.exe"
$IconPath = Join-Path $PSScriptRoot "navin.ico"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$Csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $Csc)) {
    $Csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path $Csc)) {
    throw ".NET Framework C# compiler (csc.exe) not found."
}

$CscArgs = @(
    "/nologo",
    "/target:exe",
    "/platform:anycpu",
    "/optimize+",
    "/out:$Output",
    $Source
)
if (Test-Path $IconPath) {
    $CscArgs = @("/win32icon:$IconPath") + $CscArgs
}

& $Csc @CscArgs
if ($LASTEXITCODE -ne 0) {
    throw "csc.exe failed with exit code $LASTEXITCODE"
}

$Size = [math]::Round((Get-Item $Output).Length / 1KB, 1)
Write-Host "Built $Output ($Size KB)"
