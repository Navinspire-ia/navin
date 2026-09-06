# Wrapper: fixes the interop PATH (WSL session started before Rust/Node were
# installed on Windows) and runs the Tauri desktop build.
$env:Path = 'C:\Users\gadhg\.cargo\bin;C:\Program Files\nodejs;' + $env:Path
$env:NAVIN_SKIP_WEB_BUILD = '1'
& '\\wsl.localhost\Ubuntu\home\aymen\projects\deploy7\navin-ai-v2\packaging\windows\build-desktop.ps1'
exit $LASTEXITCODE
