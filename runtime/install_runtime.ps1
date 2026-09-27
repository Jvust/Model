param(
    [switch]$Repair,
    [switch]$NoStart,
    [switch]$SelfTest,
    [string]$CacheRoot = "D:\Model"
)

$ErrorActionPreference = "Stop"
$sourceRuntime = Split-Path -Parent $MyInvocation.MyCommand.Path
$baseDir = Join-Path $env:LOCALAPPDATA "JvustModel"
$appDir = Join-Path $baseDir "app"
$configPath = Join-Path $baseDir "runtime.json"
$logsDir = Join-Path $baseDir "logs"
$cacheDir = $CacheRoot
$trayPidPath = Join-Path $baseDir "tray.pid"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$runName = "JvustModelRuntime"
$siteUrl = "http://127.0.0.1:8765/"

function Test-LlamaPath([string]$path) {
    return (-not [string]::IsNullOrWhiteSpace($path) -and (Test-Path -LiteralPath $path -PathType Leaf))
}
function Find-LlamaServer {
    $command = Get-Command "llama-server.exe" -ErrorAction SilentlyContinue
    if ($command -and (Test-LlamaPath $command.Source)) { return $command.Source }
    $candidates = @()
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA "Programs\Ollama\lib\ollama\llama-server.exe") }
    if ($env:ProgramFiles) { $candidates += (Join-Path $env:ProgramFiles "Ollama\lib\ollama\llama-server.exe") }
    $programFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    if ($programFilesX86) { $candidates += (Join-Path $programFilesX86 "Ollama\lib\ollama\llama-server.exe") }
    $candidate = $candidates | Where-Object { $_ -and (Test-LlamaPath $_) } | Select-Object -First 1
    if ($candidate) { return (Resolve-Path -LiteralPath $candidate).Path }
    return $null
}
function Pick-LlamaServer {
    Add-Type -AssemblyName System.Windows.Forms
    $file = New-Object System.Windows.Forms.OpenFileDialog
    $file.Title = "Model: choose llama-server.exe once"
    $file.Filter = "llama-server.exe|llama-server.exe|Executable (*.exe)|*.exe"
    $file.CheckFileExists = $true
    $file.Multiselect = $false
    if ($file.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { throw "Installation cancelled before choosing llama-server.exe." }
    return (Resolve-Path -LiteralPath $file.FileName).Path
}
function Read-Config {
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { return $null }
    try { return Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json } catch { return $null }
}
function Save-Config([string]$llamaPath, [string]$cachePath, $existingConfig) {
    $remoteEnabled = $false; $remoteUrl = ""; $remoteToken = ""; $remoteMode = ""; $remoteServePort = 8443
    if ($existingConfig) {
        if ($null -ne $existingConfig.remoteEnabled) { $remoteEnabled = [bool]$existingConfig.remoteEnabled }
        if ($existingConfig.remoteUrl) { $remoteUrl = [string]$existingConfig.remoteUrl }
        if ($existingConfig.remoteToken) { $remoteToken = [string]$existingConfig.remoteToken }
        if ($existingConfig.remoteMode) { $remoteMode = [string]$existingConfig.remoteMode }
        if ($existingConfig.remoteServePort) { $remoteServePort = [int]$existingConfig.remoteServePort }
    }
    $nextConfig = [PSCustomObject]@{
        llamaServerPath = $llamaPath
        cacheRoot = $cachePath
        bridgePort = 8765
        modelPort = 8080
        gpuLayers = 0
        loadMode = "none"
        readyWarnSeconds = 300
        installMode = "tray-autostart"
        remoteEnabled = $remoteEnabled
        remoteUrl = $remoteUrl
        remoteToken = $remoteToken
        remoteMode = $remoteMode
        remoteServePort = $remoteServePort
    }
    $nextConfig | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $configPath -Encoding UTF8
}
function Stop-ExistingTray {
    if (-not (Test-Path -LiteralPath $trayPidPath -PathType Leaf)) { return }
    try {
        $trayPid = [int](Get-Content -LiteralPath $trayPidPath -Raw)
        if ($trayPid -gt 0 -and $trayPid -ne $PID) {
            Stop-Process -Id $trayPid -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 300
        }
    } catch {}
    Remove-Item -LiteralPath $trayPidPath -Force -ErrorAction SilentlyContinue
}
function Stop-ExistingRuntime {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" -TimeoutSec 1
        if (-not $health -or $health.service -ne "Drive Model Local Runtime") { return }
    } catch { return }
    try {
        $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($listener -and $listener.OwningProcess) {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 400
        }
    } catch {}
}
function Stop-InstalledRuntimeProcess {
    $installedExe = Join-Path $appDir "ModelRuntime.exe"
    if (-not (Test-Path -LiteralPath $installedExe -PathType Leaf)) { return }
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        $running = @(Get-CimInstance Win32_Process -Filter "Name='ModelRuntime.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.ExecutablePath -ieq $installedExe })
        if ($running.Count -eq 0) { return }
        foreach ($process in $running) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Milliseconds 250
    }
    throw "Installed ModelRuntime.exe is still running. Close it and run Install.cmd again."
}
function Startup-Command {
    $background = Join-Path $appDir "background.ps1"
    return 'powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $background + '"'
}
$required = @("ModelRuntime.exe", "background.ps1")
if ($SelfTest) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRuntime "background.ps1") -PathType Leaf)) { throw "Installer self-test missing background.ps1" }
    Write-Host "Installer script self-test passed."
    exit 0
}
Write-Host "Model Runtime Installer" -ForegroundColor Cyan
Write-Host "One-time installation. Google Drive Desktop is not required." -ForegroundColor Green
$config = Read-Config
if ($config -and $config.cacheRoot -and $CacheRoot -eq "D:\Model") {
    $CacheRoot = [string]$config.cacheRoot
    $cacheDir = $CacheRoot
}
New-Item -ItemType Directory -Force -Path $baseDir, $appDir, $logsDir, $cacheDir | Out-Null
$llamaPath = $null
$bundleFile = Join-Path $sourceRuntime "llama\BUNDLE.json"
if (Test-Path -LiteralPath $bundleFile -PathType Leaf) {
    $bundle = Get-Content -LiteralPath $bundleFile -Raw | ConvertFrom-Json
    if ([string]$bundle.release -ne "b11146") { throw "Unexpected bundled llama.cpp release." }
    $engineDir = Join-Path $baseDir "engines\b11146"
    $engineExe = Join-Path $engineDir ([string]$bundle.executable)
    if (-not (Test-LlamaPath $engineExe)) {
        New-Item -ItemType Directory -Force $engineDir | Out-Null
        Copy-Item -Path (Join-Path $sourceRuntime "llama\*") -Destination $engineDir -Recurse -Force
    }
    if (-not (Test-LlamaPath $engineExe)) { throw "Bundled llama-server.exe is missing." }
    $llamaPath = $engineExe
}
if ($config -and (Test-LlamaPath ([string]$config.llamaServerPath))) { $llamaPath = (Resolve-Path -LiteralPath ([string]$config.llamaServerPath)).Path }
if (-not $llamaPath) { $llamaPath = Find-LlamaServer }
if (-not $llamaPath) { $llamaPath = Pick-LlamaServer }
Stop-ExistingTray
Stop-ExistingRuntime
Stop-InstalledRuntimeProcess
foreach ($name in $required) { Copy-Item -LiteralPath (Join-Path $sourceRuntime $name) -Destination (Join-Path $appDir $name) -Force }
Save-Config $llamaPath $cacheDir $config
New-Item -Path $runKey -Force | Out-Null
New-ItemProperty -Path $runKey -Name $runName -PropertyType String -Value (Startup-Command) -Force | Out-Null
Write-Host "Installed   : $appDir"
Write-Host "Cache       : $cacheDir"
Write-Host "llama       : $llamaPath"
Write-Host "Auto-start  : enabled for current Windows user"
if (-not $NoStart) {
    $backgroundPath = Join-Path $appDir "background.ps1"
    $backgroundArgs = '-NoProfile -ExecutionPolicy Bypass -File "' + $backgroundPath + '"'
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList $backgroundArgs
    Start-Sleep -Seconds 1
    Start-Process $siteUrl
}
Write-Host "Done. Use the tray icon or http://127.0.0.1:8765/ to open Model." -ForegroundColor Green
