param(
    [Parameter(Mandatory = $true)]
    [string]$LlamaServerPath,
    [string]$CacheRoot = "",
    [int]$BridgePort = 8765,
    [int]$ModelPort = 8080,
    [int]$GpuLayers = 0,
    [int]$Threads = 0,
    [ValidateSet("auto", "none", "mmap", "mlock", "mmap+mlock", "dio")]
    [string]$LoadMode = "none",
    [int]$ReadyWarnSeconds = 300
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $LlamaServerPath -PathType Leaf)) { throw "llama-server does not exist: $LlamaServerPath" }
if ($BridgePort -le 0 -or $ModelPort -le 0) { throw "BridgePort and ModelPort must be positive." }
if ($GpuLayers -lt 0) { throw "GpuLayers cannot be negative." }
if ($ReadyWarnSeconds -le 0) { throw "ReadyWarnSeconds must be positive." }
if (-not $CacheRoot) { $CacheRoot = "D:\Model" }
New-Item -ItemType Directory -Force -Path $CacheRoot | Out-Null
$env:LLAMA_SERVER_PATH = (Resolve-Path -LiteralPath $LlamaServerPath).Path
$env:MODEL_CACHE_ROOT = (Resolve-Path -LiteralPath $CacheRoot).Path
$env:MODEL_BRIDGE_PORT = "$BridgePort"
$env:MODEL_SERVER_PORT = "$ModelPort"
$env:MODEL_GPU_LAYERS = "$GpuLayers"
$env:MODEL_LOAD_MODE = "$LoadMode"
$env:MODEL_READY_WARN_SECONDS = "$ReadyWarnSeconds"
if ($Threads -gt 0) { $env:MODEL_THREADS = "$Threads" }
Write-Host "Drive Model Runtime v0.17" -ForegroundColor Cyan
Write-Host "Cache: $env:MODEL_CACHE_ROOT"
Write-Host "Bridge: http://127.0.0.1:$BridgePort"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Split-Path -Parent $scriptDir)
python runtime\application.py
