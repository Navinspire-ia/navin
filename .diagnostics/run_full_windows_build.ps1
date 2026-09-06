# Full Windows rebuild: backend (portable sidecar + Inno setup), then the
# Tauri desktop app. PATH is prepended because this WSL session predates the
# Rust/Node installs on the Windows host.
$env:Path = 'C:\Users\gadhg\.cargo\bin;C:\Program Files\nodejs;' + $env:Path
$env:NAVIN_SKIP_WEB_BUILD = '1'
$Packaging = '\\wsl.localhost\Ubuntu\home\aymen\projects\deploy7\navin-ai-v2\packaging\windows'

& (Join-Path $Packaging 'build-offline.ps1')
if ($LASTEXITCODE -ne 0) { Write-Error "build-offline failed"; exit 1 }

& (Join-Path $Packaging 'build-desktop.ps1')
exit $LASTEXITCODE
