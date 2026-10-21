# Run with Windows PowerShell 5.1. No Docker daemon, network or model key needed.
# powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/test_windows_launcher.ps1
$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts/Start-SaaSBridge.ps1')
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('SaaSBridge launcher [space] ' + [Guid]::NewGuid().ToString('N'))
$null = [IO.Directory]::CreateDirectory($temporary)
$script:ProjectRoot = $temporary
$script:LogPath = Join-Path $temporary 'test.log'
$script:Checks = 0

function Assert-True($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:Checks++
}
function Assert-Throws([scriptblock]$Action, [string]$Pattern) {
    try { & $Action } catch {
        Assert-True ($_.Exception.Message -like $Pattern) "Unexpected error: $($_.Exception.Message)"
        return
    }
    throw "Expected error: $Pattern"
}
function Reset-Scenario([int]$EngineFailures = 0, [string]$EngineType = 'linux', [int]$ComposeExit = 0) {
    $script:EngineFailures = $EngineFailures
    $script:EngineType = $EngineType
    $script:ComposeExit = $ComposeExit
    $script:DesktopStarts = 0
    $script:ComposeStarts = 0
    $script:Probes = 0
    $script:VersionExit = 0
    $script:Commands = @()
}

try {
    foreach ($file in @('compose.yaml', 'compose.app.yaml')) {
        [IO.File]::WriteAllText((Join-Path $temporary $file), 'test')
    }
    # Missing configuration must fail before it starts or mutates Docker services.
    Assert-Throws { Start-SaaSBridgeStack } '*private .env file is missing*'
    [IO.File]::WriteAllText((Join-Path $temporary '.env'), 'PRIVATE_TEST_SENTINEL=do-not-log-this')

    # Exercise the real process runner: paths with spaces, both pipes, exit codes,
    # cancellation, time limits and failed process creation.
    $script:DockerExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $result = Invoke-DockerCommand '-NoProfile -Command "[Console]::Out.WriteLine([Environment]::CurrentDirectory); 1..5000 | ForEach-Object { [Console]::Out.WriteLine(''out''); [Console]::Error.WriteLine(''err'') }; exit 7"' 30
    Assert-True ($result.ExitCode -eq 7) 'Child exit code was lost.'
    Assert-True ($result.Output.Contains($temporary)) 'Working directory with spaces was lost.'
    Assert-True ($result.Output.Length -gt 15000 -and $result.Error.Length -gt 15000) 'Both output streams must be drained.'
    Assert-Throws { Invoke-DockerCommand '-NoProfile -Command "Start-Sleep 30"' 1 } '*did not finish*'
    $script:Cancelled = $true
    Assert-Throws { Invoke-DockerCommand '-NoProfile -Command "Start-Sleep 30"' } '*cancelled*'
    $script:Cancelled = $false
    $script:DockerExe = Join-Path $temporary 'missing.exe'
    Assert-Throws { Invoke-DockerCommand 'test' } '*'

    function Find-Docker { return 'mock-docker.exe' }
    function Start-DockerDesktop { $script:DesktopStarts++ }
    function Wait-Launcher([int]$Milliseconds) { }
    function Test-SaaSBridgeReady { $script:Probes++; return ($script:Probes -ge 2) }
    function Invoke-DockerCommand([string]$Arguments, [int]$TimeoutSeconds) {
        $script:Commands += $Arguments
        if ($Arguments -eq 'compose version --short') {
            return [pscustomobject]@{ ExitCode = $script:VersionExit; Output = '2.39.0'; Error = '' }
        }
        if ($Arguments -eq 'info --format {{.OSType}}') {
            if ($script:EngineFailures -gt 0) {
                $script:EngineFailures--
                return [pscustomobject]@{ ExitCode = 1; Output = ''; Error = 'Engine not ready' }
            }
            return [pscustomobject]@{ ExitCode = 0; Output = $script:EngineType; Error = '' }
        }
        if ($Arguments -like 'compose --project-name saasbridge * up *') {
            $script:ComposeStarts++
            Assert-True ($Arguments -like '*-f compose.yaml -f compose.app.yaml up -d --build --wait --wait-timeout 240') 'Startup must target this project and wait for health.'
            return [pscustomobject]@{ ExitCode = $script:ComposeExit; Output = ''; Error = '' }
        }
        throw "Unexpected Docker command: $Arguments"
    }

    Reset-Scenario
    Start-SaaSBridgeStack
    Assert-True ($script:DesktopStarts -eq 0 -and $script:ComposeStarts -eq 1 -and $script:Probes -eq 2) 'Running Docker must be reused, and readiness must be retried.'

    Reset-Scenario -EngineFailures 2
    Start-SaaSBridgeStack
    Assert-True ($script:DesktopStarts -eq 1 -and $script:ComposeStarts -eq 1) 'Stopped Docker must start once before Compose runs.'

    Reset-Scenario -EngineType 'windows'
    Assert-Throws { Start-SaaSBridgeStack } '*Linux containers*'
    Assert-True ($script:ComposeStarts -eq 0) 'Do not start services against the wrong engine.'

    Reset-Scenario -ComposeExit 1
    Assert-Throws { Start-SaaSBridgeStack } '*could not build or start*'
    Assert-True ($script:Probes -eq 0) 'A failed build must never be reported as ready.'

    Reset-Scenario
    $script:VersionExit = 1
    Assert-Throws { Start-SaaSBridgeStack } '*Compose is unavailable*'
    Assert-True ($script:ComposeStarts -eq 0) 'Missing Compose must stop startup.'
    Assert-True (-not ([IO.File]::ReadAllText($script:LogPath).Contains('do-not-log-this'))) 'The launcher must not read or log private configuration.'
    Write-Output "PASS: $script:Checks Windows launcher checks."
} finally {
    # Only remove the uniquely created test directory under the OS temp folder.
    $resolved = [IO.Path]::GetFullPath($temporary)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
        [IO.Path]::GetFileName($resolved).StartsWith('SaaSBridge launcher [space] ')) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
