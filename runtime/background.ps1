param([switch]$SelfTest)
$ErrorActionPreference = "Stop"
$baseDir = Join-Path $env:LOCALAPPDATA "JvustModel"
$appDir = Join-Path $baseDir "app"
$configPath = Join-Path $baseDir "runtime.json"
$logsDir = Join-Path $baseDir "logs"
$cacheDir = "D:\Model"
$stdoutPath = Join-Path $logsDir "runtime.stdout.log"
$stderrPath = Join-Path $logsDir "runtime.stderr.log"
$trayPidPath = Join-Path $baseDir "tray.pid"
$siteUrl = "http://127.0.0.1:8765/"
$bridgeUrl = "http://127.0.0.1:8765"
function Read-Config {
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw "Runtime config is missing. Run Install.cmd again." }
    return Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
}
function Start-Bridge {
    param($config)
    $runtimeExe = Join-Path $appDir "ModelRuntime.exe"
    if (-not (Test-Path -LiteralPath $runtimeExe -PathType Leaf)) { throw "Installed ModelRuntime.exe is missing. Run Install.cmd again." }
    if (-not (Test-Path -LiteralPath ([string]$config.llamaServerPath) -PathType Leaf)) { throw "llama-server.exe path is invalid. Run Install.cmd again." }
    New-Item -ItemType Directory -Force -Path $logsDir, $cacheDir | Out-Null
    Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    $env:LLAMA_SERVER_PATH = [string]$config.llamaServerPath
    $env:MODEL_BRIDGE_PORT = [string]$config.bridgePort
    $env:MODEL_SERVER_PORT = [string]$config.modelPort
    $env:MODEL_GPU_LAYERS = [string]$config.gpuLayers
    $env:MODEL_LOAD_MODE = [string]$config.loadMode
    $env:MODEL_READY_WARN_SECONDS = [string]$config.readyWarnSeconds
    $env:MODEL_CACHE_ROOT = $cacheDir
    if ($config.remoteToken) { $env:MODEL_REMOTE_TOKEN = [string]$config.remoteToken } else { Remove-Item Env:MODEL_REMOTE_TOKEN -ErrorAction SilentlyContinue }
    return Start-Process -FilePath $runtimeExe -WorkingDirectory $appDir -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
}
function Stop-Bridge($process) {
    $headers = @{}
    if ($script:config -and $script:config.remoteToken) { $headers["Authorization"] = "Bearer " + [string]$script:config.remoteToken }
    foreach ($route in @("models", "tasks", "packages", "native", "image", "video")) {
        try { Invoke-RestMethod -Uri "$bridgeUrl/v1/$route/stop" -Method Post -Headers $headers -ContentType "application/json" -Body "{}" -TimeoutSec 2 | Out-Null } catch {}
    }
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
}
if ($SelfTest) { Write-Host "Background script self-test passed."; exit 0 }
$createdNew = $false
$mutex = [System.Threading.Mutex]::new($true, "JvustModelRuntimeTray", [ref]$createdNew)
if (-not $createdNew) { exit 0 }
New-Item -ItemType Directory -Force -Path $baseDir | Out-Null
Set-Content -LiteralPath $trayPidPath -Value ([string]$PID) -Encoding ASCII
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$config = Read-Config
if ($config.cacheRoot) { $cacheDir = [string]$config.cacheRoot }
$bridgeProcess = $null
$closing = $false
$lastStart = [DateTime]::MinValue
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Application
$notify.Text = "Model Runtime"
$notify.Visible = $true
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$openItem = $menu.Items.Add("Open Model")
$statusItem = $menu.Items.Add("Runtime: starting")
$logsItem = $menu.Items.Add("Open logs")
$restartItem = $menu.Items.Add("Restart Runtime")
$menu.Items.Add("-") | Out-Null
$exitItem = $menu.Items.Add("Exit Runtime")
$notify.ContextMenuStrip = $menu
$startBridgeAction = {
    if ($script:closing) { return }
    try {
        $health = Invoke-RestMethod -Uri "$bridgeUrl/health" -TimeoutSec 1
        if ($health.ok) { $statusItem.Text = "Runtime: connected"; return }
    } catch {}
    if ($script:bridgeProcess -and -not $script:bridgeProcess.HasExited) { return }
    if (([DateTime]::Now - $script:lastStart).TotalSeconds -lt 2) { return }
    $script:lastStart = [DateTime]::Now
    try {
        $script:config = Read-Config
        if ($script:config.cacheRoot) { $script:cacheDir = [string]$script:config.cacheRoot }
        $script:bridgeProcess = Start-Bridge $script:config
        $statusItem.Text = "Runtime: starting"
    } catch {
        $statusItem.Text = "Runtime: failed"
        $notify.ShowBalloonTip(5000, "Model Runtime", $_.Exception.Message, [System.Windows.Forms.ToolTipIcon]::Error)
    }
}
$openItem.add_Click({ Start-Process $siteUrl })
$notify.add_DoubleClick({ Start-Process $siteUrl })
$logsItem.add_Click({ New-Item -ItemType Directory -Force -Path $logsDir | Out-Null; Start-Process explorer.exe $logsDir })
$restartItem.add_Click({ Stop-Bridge $script:bridgeProcess; $script:bridgeProcess = $null; $script:lastStart = [DateTime]::MinValue; & $startBridgeAction })
$exitItem.add_Click({ $script:closing = $true; Stop-Bridge $script:bridgeProcess; $notify.Visible = $false; [System.Windows.Forms.Application]::Exit() })
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 2500
$timer.add_Tick({
    if ($script:closing) { return }
    try {
        $health = Invoke-RestMethod -Uri "$bridgeUrl/health" -TimeoutSec 1
        if ($health.ok) { $statusItem.Text = "Runtime: connected"; return }
    } catch {}
    $statusItem.Text = "Runtime: disconnected"
    & $startBridgeAction
})
& $startBridgeAction
$notify.ShowBalloonTip(2500, "Model Runtime", "Background engine started. Open Model anytime.", [System.Windows.Forms.ToolTipIcon]::Info)
$timer.Start()
try { [System.Windows.Forms.Application]::Run() }
finally {
    $timer.Stop()
    Stop-Bridge $script:bridgeProcess
    $notify.Visible = $false
    Remove-Item -LiteralPath $trayPidPath -Force -ErrorAction SilentlyContinue
    try { $mutex.ReleaseMutex() | Out-Null } catch {}
    $mutex.Dispose()
}
