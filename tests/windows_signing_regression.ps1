# Exercise production retry decisions without invoking Azure or SignTool.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Tokens = $null
$Errors = $null
$Source = Join-Path $Root "packaging\windows\sign-windows.ps1"
$Ast = [System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$Tokens, [ref]$Errors)
if ($Errors.Count) { throw ($Errors | Out-String) }
foreach ($Name in @("Log", "Get-SigningFailureHint")) {
    $Definition = $Ast.Find({ param($Node)
        $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $Node.Name -eq $Name
    }, $false)
    if (-not $Definition) { throw "Missing signing function: $Name" }
    . ([scriptblock]::Create($Definition.Extent.Text))
}
$Retry = $Ast.Find({ param($Node)
    $Node -is [System.Management.Automation.Language.ForEachStatementAst] -and
    $Node.Variable.VariablePath.UserPath -eq "Attempt"
}, $true)
if (-not $Retry) { throw "Missing signing retry loop" }
$RetryBlock = [scriptblock]::Create($Retry.Extent.Text)
$Temp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $Temp | Out-Null
$script:LogFile = Join-Path $Temp "sign.log"
$PreviousSecret = $env:AZURE_CLIENT_SECRET
$PreviousClient = $env:AZURE_CLIENT_ID
try {
    $env:AZURE_CLIENT_SECRET = "test-secret-never-log"
    $env:AZURE_CLIENT_ID = "test-app"
    $TsAccount = "Navin"; $TsProfile = "navin-certif"; $TsEndpoint = "https://neu.codesigning.azure.net"
    Log "Example error containing $env:AZURE_CLIENT_SECRET"
    if ((Get-Content $script:LogFile -Raw).Contains($env:AZURE_CLIENT_SECRET)) {
        throw "Secret leaked into signing log"
    }
    function Invoke-Logged {
        $script:Calls++
        $script:LastToolOutput = $script:Failure
        if ($script:Recover -and $script:Calls -eq 2) { return 0 }
        return 1
    }
    function Start-Sleep { $script:Sleeps++ }
    function Wait-FileWritable { return $true }
    $BlockedSubscription = '"state": "Warned"' + "`nStatus: 403 (Forbidden)"
    if ((Get-SigningFailureHint $BlockedSubscription) -notmatch 'subscription state is Warned') {
        throw "Blocked subscription was incorrectly diagnosed as missing RBAC"
    }
    foreach ($Failure in @($BlockedSubscription, "Status: 403 (Forbidden)", "Status: 401 (Unauthorized)", "AADSTS7000222: expired")) {
        $script:Failure = $Failure; $script:Calls = 0; $script:Sleeps = 0; $script:Recover = $false
        $Caught = $false
        try { & $RetryBlock } catch { $Caught = $true }
        if (-not $Caught -or $script:Calls -ne 1 -or $script:Sleeps -ne 0) {
            throw "Permanent signing failure was retried: $Failure"
        }
    }
    $script:Failure = "Status: 503 (Service Unavailable)"
    $script:Calls = 0; $script:Sleeps = 0; $script:Recover = $true
    & $RetryBlock
    if ($script:Calls -ne 2 -or $script:Sleeps -ne 1) { throw "Transient failure did not recover" }
    $script:Calls = 0; $script:Sleeps = 0; $script:Recover = $false
    & $RetryBlock
    if ($script:Calls -ne 6 -or $script:Sleeps -ne 5) { throw "Retry budget or final delay is wrong" }
    foreach ($Name in @("build-desktop.ps1", "build-offline.ps1")) {
        [System.Management.Automation.Language.Parser]::ParseFile(
            (Join-Path $Root "packaging\windows\$Name"), [ref]$Tokens, [ref]$Errors
        ) | Out-Null
        if ($Errors.Count) { throw ($Errors | Out-String) }
    }
    Write-Output "PASS signing retry policy: 403/401/expired stop, 503 retries, secrets redacted, scripts parse"
} finally {
    $env:AZURE_CLIENT_SECRET = $PreviousSecret
    $env:AZURE_CLIENT_ID = $PreviousClient
    Remove-Item -LiteralPath $Temp -Recurse -Force
}
