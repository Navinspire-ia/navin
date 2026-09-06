foreach ($t in @('cargo', 'rustup', 'npm', 'node', 'winget')) {
    $c = Get-Command $t -ErrorAction SilentlyContinue
    if ($c) { Write-Host "OK      $t -> $($c.Source)" } else { Write-Host "MISSING $t" }
}
$msvc = Test-Path 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC'
Write-Host "VS BuildTools MSVC present: $msvc"
$vsFull = Test-Path 'C:\Program Files\Microsoft Visual Studio\2022'
Write-Host "VS 2022 full present: $vsFull"
