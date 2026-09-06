# Kill only the overnight Navin build processes, identified by their command
# line (NavinBuild staging paths / the build scripts), never by bare name.
$patterns = 'NavinBuild', 'build-offline.ps1', 'build-desktop.ps1', 'run_full_windows_build.ps1'
$procs = Get-CimInstance Win32_Process | Where-Object {
    $cl = $_.CommandLine
    if (-not $cl) { return $false }
    foreach ($p in $patterns) { if ($cl -like "*$p*") { return $true } }
    return $false
}
foreach ($proc in $procs) {
    if ($proc.ProcessId -eq $PID) { continue }
    Write-Host "Killing $($proc.ProcessId) $($proc.Name): $($proc.CommandLine.Substring(0, [Math]::Min(120, $proc.CommandLine.Length)))"
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Host "done"
