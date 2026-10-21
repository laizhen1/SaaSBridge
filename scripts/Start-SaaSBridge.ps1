# Windows PowerShell 5.1; dot-source to use the startup functions without a GUI.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ProjectRoot = Split-Path -Parent $PSScriptRoot
$script:LogPath = $null
$script:DockerExe = $null
$script:Cancelled = $false
$script:OnProgress = { param($Message) }
$script:PumpEvents = { }

function Write-LauncherLog([string]$Message) {
    if ($script:LogPath) {
        [IO.File]::AppendAllText($script:LogPath, "$(Get-Date -Format 'HH:mm:ss') $Message`r`n")
    }
}

function Update-LauncherProgress([string]$Message) {
    Write-LauncherLog $Message
    & $script:OnProgress $Message
}

function Invoke-LauncherPump {
    & $script:PumpEvents
    if ($script:Cancelled) {
        throw [OperationCanceledException]::new('Startup was cancelled. Containers already started may still be running in Docker Desktop.')
    }
}

function Wait-Launcher([int]$Milliseconds = 1500) {
    $until = [DateTime]::UtcNow.AddMilliseconds($Milliseconds)
    while ([DateTime]::UtcNow -lt $until) {
        Invoke-LauncherPump
        Start-Sleep -Milliseconds 100
    }
}

function Find-Docker {
    $command = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
        "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    throw 'Docker Desktop is not installed or could not be found. Install Docker Desktop for Windows, finish its setup, then double-click the launcher again.'
}

function Start-DockerDesktop {
    # Also supports a nonstandard installation discovered through PATH.
    $installRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $script:DockerExe))
    $candidates = @(
        (Join-Path $installRoot 'Docker Desktop.exe'),
        "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe",
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            Start-Process -FilePath $candidate -WindowStyle Hidden | Out-Null
            return
        }
    }
    throw 'The Docker engine is stopped and Docker Desktop could not be found. Open Docker Desktop, then try again.'
}

function Invoke-DockerCommand([string]$Arguments, [int]$TimeoutSeconds = 30) {
    # Only fixed command arguments are used. Paths go through FileName/WorkingDirectory,
    # never a shell command; spaces and non-ASCII project paths remain supported.
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $script:DockerExe
    $info.Arguments = $Arguments
    $info.WorkingDirectory = $script:ProjectRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.EnvironmentVariables['NO_COLOR'] = '1'
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    $started = $false
    Write-LauncherLog "docker $Arguments"
    try {
        $started = $process.Start()
        if (-not $started) { throw 'Docker could not be started.' }
        # Drain both pipes concurrently so a large build log cannot deadlock startup.
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
        while (-not $process.HasExited) {
            Invoke-LauncherPump
            if ([DateTime]::UtcNow -ge $deadline) {
                throw "Docker did not finish within $TimeoutSeconds seconds. Check Docker Desktop and the launcher log, then try again."
            }
            Start-Sleep -Milliseconds 100
        }
        $process.WaitForExit()
        $out = $stdout.GetAwaiter().GetResult()
        $err = $stderr.GetAwaiter().GetResult()
        if ($out) { Write-LauncherLog $out.TrimEnd() }
        if ($err) { Write-LauncherLog $err.TrimEnd() }
        return [pscustomobject]@{ ExitCode = $process.ExitCode; Output = $out.Trim(); Error = $err.Trim() }
    } finally {
        if ($started -and -not $process.HasExited) {
            # Stop only this CLI process, never Docker Desktop or unrelated containers.
            $process.Kill()
            $null = $process.WaitForExit(3000)
        }
        $process.Dispose()
    }
}

function Test-SaaSBridgeReady {
    try {
        # WebRequest has explicit timeouts on Windows PowerShell 5.1.
        foreach ($url in @('http://127.0.0.1:8000/health', 'http://127.0.0.1:5175/api/python/health', 'http://127.0.0.1:5175/')) {
            $request = [Net.HttpWebRequest]::Create($url)
            $request.Proxy = $null  # Never send local probes through a system proxy.
            $request.Timeout = 2500
            $request.ReadWriteTimeout = 2500
            $response = $request.GetResponse()
            try {
                if ([int]$response.StatusCode -ne 200) { return $false }
                if ($url.EndsWith('/health')) {
                    $reader = New-Object IO.StreamReader($response.GetResponseStream())
                    try {
                        $health = $reader.ReadToEnd() | ConvertFrom-Json
                        if ($health.status -ne 'ok') { return $false }
                    } finally { $reader.Dispose() }
                }
            } finally { $response.Dispose() }
        }
        return $true
    } catch { return $false }
}

function Start-SaaSBridgeStack {
    foreach ($name in @('compose.yaml', 'compose.app.yaml', '.env')) {
        if (-not (Test-Path -LiteralPath (Join-Path $script:ProjectRoot $name) -PathType Leaf)) {
            if ($name -eq '.env') {
                throw 'The private .env file is missing. Copy .env.example to .env in the project folder and configure your model provider, then try again.'
            }
            throw "Missing $name. Keep the launcher in the complete SaaSBridge project folder."
        }
    }
    Update-LauncherProgress 'Checking Docker Desktop...'
    $script:DockerExe = Find-Docker
    $version = Invoke-DockerCommand 'compose version --short'
    if ($version.ExitCode -ne 0) { throw 'Docker Compose is unavailable. Update or repair Docker Desktop, then try again.' }
    $engine = Invoke-DockerCommand 'info --format {{.OSType}}' 10
    if ($engine.ExitCode -ne 0) {
        Update-LauncherProgress 'Starting Docker Desktop. Waiting for its engine...'
        Start-DockerDesktop
        $deadline = [DateTime]::UtcNow.AddMinutes(3)
        do {
            Wait-Launcher 2000
            $engine = Invoke-DockerCommand 'info --format {{.OSType}}' 10
        } while ($engine.ExitCode -ne 0 -and [DateTime]::UtcNow -lt $deadline)
    }
    if ($engine.ExitCode -ne 0) { throw 'Docker Desktop is not ready after three minutes. Open it to finish any setup or resolve an engine error, then try again.' }
    if ($engine.Output -ne 'linux') { throw 'SaaSBridge needs Linux containers. Switch Docker Desktop to Linux containers, then try again.' }

    Update-LauncherProgress 'Building and starting SaaSBridge. The first run can take several minutes...'
    # Reuses cached build layers, and automatically includes later source changes.
    $result = Invoke-DockerCommand 'compose --project-name saasbridge -f compose.yaml -f compose.app.yaml up -d --build --wait --wait-timeout 240' 1800
    if ($result.ExitCode -ne 0) {
        throw 'Docker could not build or start SaaSBridge. Open the launcher log for details. Check internet access, Docker disk space, and whether ports 5175, 8000, 8001 or 6379 are already in use.'
    }
    Update-LauncherProgress 'Checking the backend, web console and API connection...'
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        Invoke-LauncherPump
        if (Test-SaaSBridgeReady) {
            Update-LauncherProgress 'SaaSBridge is ready. Opening your browser...'
            return
        }
        Wait-Launcher
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Containers started, but the web console or API is not ready. Check the saasbridge and frontend containers in Docker Desktop, then try again.'
}

function Show-SaaSBridgeLauncher {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [Windows.Forms.Application]::EnableVisualStyles()
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $id = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($script:ProjectRoot.ToLowerInvariant()))).Replace('-', '') }
    finally { $hash.Dispose() }
    $mutex = New-Object Threading.Mutex($false, "Local\SaaSBridgeLauncher-$id")
    $ownsMutex = $false
    try {
        try { $ownsMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
        if (-not $ownsMutex) {
            $null = [Windows.Forms.MessageBox]::Show('The SaaSBridge launcher is already open.', 'SaaSBridge')
            return
        }
        $script:Cancelled = $false
        $script:Busy = $false
        $form = New-Object Windows.Forms.Form
        $form.Text = 'SaaSBridge'
        $form.ClientSize = New-Object Drawing.Size(540, 245)
        $form.StartPosition = 'CenterScreen'
        $form.FormBorderStyle = 'FixedDialog'
        $form.MaximizeBox = $false
        $form.Font = New-Object Drawing.Font('Segoe UI', 10)

        $title = New-Object Windows.Forms.Label
        $title.Text = 'Starting SaaSBridge'
        $title.Font = New-Object Drawing.Font('Segoe UI', 15, [Drawing.FontStyle]::Bold)
        $title.SetBounds(24, 20, 490, 34)
        $status = New-Object Windows.Forms.Label
        $status.Text = 'Preparing your local customer support runtime...'
        $status.SetBounds(24, 66, 490, 72)
        $progress = New-Object Windows.Forms.ProgressBar
        $progress.Style = 'Marquee'
        $progress.SetBounds(24, 145, 490, 12)
        $elapsed = New-Object Windows.Forms.Label
        $elapsed.SetBounds(24, 170, 270, 24)
        $logButton = New-Object Windows.Forms.Button
        $logButton.Text = 'Open log'
        $logButton.SetBounds(294, 196, 105, 32)
        $logButton.Enabled = $false
        $closeButton = New-Object Windows.Forms.Button
        $closeButton.Text = 'Cancel'
        $closeButton.SetBounds(409, 196, 105, 32)
        $form.Controls.AddRange(@($title, $status, $progress, $elapsed, $logButton, $closeButton))
        $script:Ui = @{
            Form = $form; Title = $title; Status = $status; Progress = $progress
            Elapsed = $elapsed; LogButton = $logButton; CloseButton = $closeButton
            Clock = [Diagnostics.Stopwatch]::StartNew()
        }
        $script:OnProgress = {
            param($Message)
            $script:Ui.Status.Text = $Message
            $script:Ui.Form.Refresh()
        }
        $script:PumpEvents = {
            $script:Ui.Elapsed.Text = 'Elapsed: ' + $script:Ui.Clock.Elapsed.ToString('mm\:ss')
            [Windows.Forms.Application]::DoEvents()
        }
        $closeButton.Add_Click({ $script:Ui.Form.Close() })
        $form.Add_FormClosing({ if ($script:Busy) { $script:Cancelled = $true } })
        $logButton.Add_Click({
            if ($script:LogPath -and (Test-Path -LiteralPath $script:LogPath)) {
                Start-Process -FilePath "$env:SystemRoot\System32\notepad.exe" -ArgumentList ('"' + $script:LogPath + '"')
            }
        })
        $form.Add_Shown({
            $script:Busy = $true
            try {
                $logDirectory = Join-Path $script:ProjectRoot 'logs'
                $null = [IO.Directory]::CreateDirectory($logDirectory)
                $script:LogPath = Join-Path $logDirectory 'launcher.log'
                [IO.File]::WriteAllText($script:LogPath, "SaaSBridge startup log (local only)`r`n")
                $script:Ui.LogButton.Enabled = $true
                Start-SaaSBridgeStack
                Invoke-LauncherPump
                try { Start-Process 'http://127.0.0.1:5175/' }
                catch { throw 'SaaSBridge is running. Your browser could not be opened automatically; open http://127.0.0.1:5175/ yourself.' }
                $script:Busy = $false
                $script:Ui.Form.Close()
            } catch {
                if (-not $script:Cancelled) {
                    $script:Ui.Title.Text = 'SaaSBridge needs attention'
                    $script:Ui.Status.Text = $_.Exception.Message
                    $script:Ui.Status.Height = 100
                    $script:Ui.Progress.Visible = $false
                    $script:Ui.CloseButton.Text = 'Close'
                    try { Write-LauncherLog $_.Exception.Message } catch { }
                }
            } finally { $script:Busy = $false }
        })
        $null = $form.ShowDialog()
        $form.Dispose()
    } finally {
        if ($ownsMutex) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

if ($MyInvocation.InvocationName -ne '.') { Show-SaaSBridgeLauncher }
