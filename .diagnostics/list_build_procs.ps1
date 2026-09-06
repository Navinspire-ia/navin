Get-Process |
    Where-Object { $_.ProcessName -match 'python|node|cargo|rustc|powershell|ISCC|pwsh|msbuild|link' } |
    Sort-Object StartTime -Descending -ErrorAction SilentlyContinue |
    Select-Object Id, ProcessName, CPU, StartTime |
    Format-Table -AutoSize
