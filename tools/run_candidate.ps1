param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $root
$cache = 'D:\Model'
if (-not (Test-Path 'D:\')) { throw 'D: is required. Model weights will not be silently redirected to C:.' }
New-Item -ItemType Directory -Force $cache | Out-Null
# Do not terminate an existing Runtime or use its older embedded website.
$health = $null
try { $health = Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 2 } catch {}
if ($health) {
    if ([int]$health.version -eq 19) {
        if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8765/' }
        Write-Host 'Model 0.19 is already running. No cache or task data was changed.'
        exit 0
    }
    throw 'An older Runtime uses port 8765. Exit its tray app first, then run this launcher again.'
}
$env:PYTHONHOME = $null
$env:PYTHONPATH = $null
$env:PYTHONNOUSERSITE = '1'
$env:MODEL_CACHE_ROOT = $cache
$env:HF_HOME = Join-Path $cache 'hf-cache'
$env:PIP_CACHE_DIR = Join-Path $cache 'pip-cache'
$env:TEMP = Join-Path $cache 'tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
$private = Join-Path $cache 'candidate-launcher\python-3.11.9'
$python = Join-Path $private 'python.exe'
New-Item -ItemType Directory -Force $private | Out-Null
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if (-not (Test-Path $python)) {
    Write-Host 'Preparing private Python 3.11.9 on D: (system Python is not changed)...'
    $archive = Join-Path $private 'python.zip'
    Invoke-WebRequest 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip' -OutFile $archive -UseBasicParsing
    Expand-Archive -LiteralPath $archive -DestinationPath $private -Force
    Get-FileHash -LiteralPath $archive -Algorithm SHA256 | ConvertTo-Json | Set-Content (Join-Path $private 'python-archive-observed-hash.json')
    Remove-Item -LiteralPath $archive -Force
}
# Always verify, including reuse after an interrupted/failed first setup.
$signature = Get-AuthenticodeSignature -LiteralPath $python
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
    throw 'Python executable signature could not be verified. No Python was started.'
}
$site = Join-Path $private 'Lib\site-packages'
New-Item -ItemType Directory -Force $site | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $private 'python311._pth'), "python311.zip`n.`nLib/site-packages`n$root`n$(Join-Path $root 'runtime')`nimport site`n", $utf8)
if (-not (Test-Path (Join-Path $site 'pip\__main__.py'))) {
    $meta = Invoke-RestMethod 'https://pypi.org/pypi/pip/25.1.1/json'
    $wheel = @($meta.urls | Where-Object { $_.filename -eq 'pip-25.1.1-py3-none-any.whl' })
    if ($wheel.Count -ne 1) { throw 'Pinned pip wheel metadata is ambiguous.' }
    $zip = Join-Path $private 'pip.zip'
    Invoke-WebRequest $wheel[0].url -OutFile $zip -UseBasicParsing
    if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $wheel[0].digests.sha256) { throw 'pip checksum mismatch.' }
    Expand-Archive -LiteralPath $zip -DestinationPath $site -Force
    Remove-Item -LiteralPath $zip -Force
}
& $python -s -c 'import py7zr'
if ($LASTEXITCODE -ne 0) {
    & $python -s -m pip install --disable-pip-version-check 'py7zr==0.22.0'
    if ($LASTEXITCODE -ne 0) { throw 'Private archive component installation failed; see the output above.' }
}
$llama = Get-ChildItem -LiteralPath (Join-Path $root 'runtime\llama') -Filter llama-server.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $llama) {
    Write-Host 'Preparing the checksum-verified CPU chat engine...'
    & $python -s -m tools.prepare_llama
    if ($LASTEXITCODE -ne 0) { throw 'CPU engine preparation failed; no model weights were downloaded.' }
    $llama = Get-ChildItem -LiteralPath (Join-Path $root 'runtime\llama') -Filter llama-server.exe -Recurse | Select-Object -First 1
}
$env:LLAMA_SERVER_PATH = $llama.FullName
if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        for ($i=0; $i -lt 30; $i++) {
            try {
                $h=Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 1
                if ([int]$h.version -eq 19) { Start-Process 'http://127.0.0.1:8765/'; break }
            } catch {}
            Start-Sleep -Seconds 1
        }
    } | Out-Null
}
Write-Host 'Model 0.19 candidate. Keep this window open. Ctrl+C stops this Runtime; cached weights and completed tasks remain.'
Push-Location $root
try {
    & $python -s -u -m runtime.application
    if ($LASTEXITCODE -ne 0) { throw 'Runtime exited with an error.' }
} finally { Pop-Location }
