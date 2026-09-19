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

    # Published-copy step of build-desktop.ps1: a destination held open by
    # another process (previous installer running, antivirus scan, preview
    # pane) must be retried, and the final error must point at the blocking
    # process instead of dying with a bare Copy-Item message. Get-Process and
    # Start-Sleep are replaced so the scenario is fast and deterministic.
    $AstBuild = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $Root "packaging\windows\build-desktop.ps1"), [ref]$Tokens, [ref]$Errors
    )
    if ($Errors.Count) { throw ($Errors | Out-String) }
    $CopyPublished = $AstBuild.Find({ param($Node)
        $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $Node.Name -eq "Copy-PublishedFile"
    }, $false)
    if (-not $CopyPublished) { throw "Missing published-copy helper: Copy-PublishedFile" }
    . ([scriptblock]::Create($CopyPublished.Extent.Text))

    $SrcFile = Join-Path $Temp "signed.bin"
    [System.IO.File]::WriteAllBytes($SrcFile, [byte[]](0..255 + 0..127))

    # A free destination is replaced with byte-identical content.
    $Free = Join-Path $Temp "published.bin"
    Copy-PublishedFile -Src $SrcFile -Dst $Free -MaxAttempts 2 -RetrySeconds 0
    $Got = [System.IO.File]::ReadAllBytes($Free)
    $Want = [System.IO.File]::ReadAllBytes($SrcFile)
    if ($Got.Length -ne $Want.Length) { throw "Published copy length differs" }
    for ($i = 0; $i -lt $Want.Length; $i++) {
        if ($Got[$i] -ne $Want[$i]) { throw "Published copy differs at byte $i" }
    }

    function Start-Sleep { param($Seconds) $script:Sleeps++ }
    function Get-Process {
        [CmdletBinding()] param()
        if ($script:FakeBlockingProcess) {
            @([pscustomobject]@{
                Path = $script:FakeBlockingProcess
                Id = 4242
                ProcessName = "Navin-Desktop-2.0.6-windows-x64-setup"
            })
        }
    }

    $Locked = Join-Path $Temp "locked.bin"
    [System.IO.File]::WriteAllBytes($Locked, [byte[]](1, 2, 3))

    # Culprit found: the error names the process holding the file.
    $script:FakeBlockingProcess = $Locked
    $script:Sleeps = 0
    $CulpritMessage = ""
    $Holder = [System.IO.File]::Open(
        $Locked, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read
    )
    try {
        try { Copy-PublishedFile -Src $SrcFile -Dst $Locked -MaxAttempts 3 -RetrySeconds 0 }
        catch { $CulpritMessage = $_.Exception.Message }
    } finally {
        $Holder.Close()
    }
    if ($CulpritMessage -notmatch "PID 4242") {
        throw "Locked destination did not name the blocking process: $CulpritMessage"
    }

    # Culprit not identifiable: the error stays actionable and the retry
    # budget is respected (2 pauses for 3 attempts).
    $script:FakeBlockingProcess = $null
    $script:Sleeps = 0
    $GenericMessage = ""
    $Holder = [System.IO.File]::Open(
        $Locked, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read
    )
    try {
        try { Copy-PublishedFile -Src $SrcFile -Dst $Locked -MaxAttempts 3 -RetrySeconds 0 }
        catch { $GenericMessage = $_.Exception.Message }
    } finally {
        $Holder.Close()
    }
    if ($GenericMessage -notmatch "Could not publish") {
        throw "Locked destination error is not actionable: $GenericMessage"
    }
    if ($script:Sleeps -ne 2) { throw "Retry pause count is wrong: $($script:Sleeps)" }

    Write-Output "PASS signing retry policy: 403/401/expired stop, 503 retries, secrets redacted, scripts parse, published copy retries locked destinations"
} finally {
    $env:AZURE_CLIENT_SECRET = $PreviousSecret
    $env:AZURE_CLIENT_ID = $PreviousClient
    Remove-Item -LiteralPath $Temp -Recurse -Force
}
