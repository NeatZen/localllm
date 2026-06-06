# Enable NVIDIA GPU for Odysseus built-in AI (RTX / CUDA).
# Run once from PowerShell:  .\install-gpu.ps1
# Requires: NVIDIA driver (nvidia-smi) + CUDA Toolkit runtime DLLs + Python 3.12.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Get-LatestCpuBackup {
    Get-ChildItem -Directory -Filter "venv-cpu-backup-*" -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        Select-Object -First 1
}

function Restore-CpuVenv {
    param([string]$Reason)
    $bak = Get-LatestCpuBackup
    if (-not $bak) {
        Write-Host "No venv-cpu-backup-* folder found. Run .\install-turnkey.ps1 first." -ForegroundColor Red
        return $false
    }
    Write-Host "Restoring CPU environment from $($bak.Name) ($Reason)..." -ForegroundColor Yellow
    if (Test-Path ".\venv") {
        $failed = "venv-gpu-failed-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Rename-Item ".\venv" $failed -Force
    }
    Copy-Item $bak.FullName ".\venv" -Recurse
    return $true
}

function Get-Python312 {
    $candidates = @()
    try {
        $fromPy = (py -3.12 -c "import sys; print(sys.executable)" 2>$null).Trim()
        if ($fromPy) { $candidates += $fromPy }
    } catch {}
    $candidates += @(
        "$env:LOCALAPPDATA\Python\pythoncore-3.12-64\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "C:\Python312\python.exe"
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    return $null
}

function Get-NvidiaCudaVersion {
    try {
        $line = (nvidia-smi 2>$null | Select-String "CUDA Version").Line
        if ($line -match "CUDA Version:\s*(\d+)\.(\d+)") {
            return [int]$Matches[1], [int]$Matches[2]
        }
    } catch {}
    return $null, $null
}

function Find-CudaToolkitRoot {
    $roots = @(
        ${env:CUDA_PATH},
        "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
    ) | Where-Object { $_ -and (Test-Path $_) }

    foreach ($root in $roots) {
        if ($root -match "CUDA\\v") {
            return $root
        }
        $versions = Get-ChildItem $root -Directory -Filter "v*" -ErrorAction SilentlyContinue |
            Sort-Object { [version]($_.Name -replace '^v','') } -Descending
        if ($versions) {
            return $versions[0].FullName
        }
    }
    return $null
}

function Ensure-CudaRuntime {
    $root = Find-CudaToolkitRoot
    if ($root) {
        Write-Host "CUDA toolkit found: $root"
        return $root
    }

    Write-Host ""
    Write-Host "CUDA Toolkit not found. GPU wheels need runtime DLLs (cudart, cublas, etc.)." -ForegroundColor Yellow
    Write-Host "Installing NVIDIA CUDA Toolkit via winget (~2.3 GB download). This is a one-time step."
    Write-Host ""

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "winget not available. Install CUDA manually:" -ForegroundColor Red
        Write-Host "  https://developer.nvidia.com/cuda-downloads"
        return $null
    }

    winget install Nvidia.CUDA --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Host "CUDA Toolkit install failed." -ForegroundColor Red
        return $null
    }

    $root = Find-CudaToolkitRoot
    if (-not $root) {
        Write-Host "CUDA installed but toolkit folder not found. Re-open PowerShell and run this script again." -ForegroundColor Red
        return $null
    }
    Write-Host "CUDA toolkit installed: $root" -ForegroundColor Green
    return $root
}

function Copy-CudaDllsToLlamaLib {
    param([string]$CudaRoot, [string]$LlamaLib)
    if (-not $CudaRoot -or -not (Test-Path $LlamaLib)) { return }

    $searchDirs = @(
        (Join-Path $CudaRoot "bin\x64"),
        (Join-Path $CudaRoot "bin")
    ) | Where-Object { Test-Path $_ }

    $patterns = @("cudart64_*.dll", "cublas64_*.dll", "cublasLt64_*.dll", "nvrtc64_*.dll")
    $copied = 0
    foreach ($dir in $searchDirs) {
        foreach ($pat in $patterns) {
            Get-ChildItem $dir -Filter $pat -ErrorAction SilentlyContinue | ForEach-Object {
                $dest = Join-Path $LlamaLib $_.Name
                Copy-Item $_.FullName $dest -Force
                $copied++
            }
        }
    }
    if ($copied -gt 0) {
        Write-Host "Copied CUDA runtime DLL(s) into llama_cpp\lib ($copied files)"
    }
}

function Patch-LlamaWinmode {
    $path = ".\venv\Lib\site-packages\llama_cpp\_ctypes_extensions.py"
    if (-not (Test-Path $path)) { return }
    $content = Get-Content $path -Raw
    if ($content -match 'cdll_args\["winmode"\] = ctypes\.RTLD_GLOBAL') {
        $content = $content -replace '        cdll_args\["winmode"\] = ctypes\.RTLD_GLOBAL\r?\n', "        # winmode disabled for Windows DLL loading fix`n"
        Set-Content $path $content -NoNewline
        Write-Host "Applied Windows DLL loading patch"
    }
}

Write-Host ""
Write-Host "=== Odysseus GPU Setup (NVIDIA CUDA) ===" -ForegroundColor Cyan
Write-Host ""

if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    Write-Host "No NVIDIA GPU detected (nvidia-smi missing). GPU setup skipped." -ForegroundColor Red
    exit 1
}
nvidia-smi | Select-Object -First 8
Write-Host ""

$cudaMajor, $cudaMinor = Get-NvidiaCudaVersion
$wheelTag = "cu124"
if ($cudaMajor -ge 13) {
    $wheelTag = "cu132"
}
Write-Host "Driver reports CUDA $cudaMajor.$cudaMinor - using $wheelTag wheels (llama-cpp-python 0.3.25)"

$cudaRoot = Ensure-CudaRuntime
if (-not $cudaRoot) {
    Write-Host "Cannot continue without CUDA runtime DLLs." -ForegroundColor Red
    exit 1
}

$py312 = Get-Python312
if (-not $py312) {
    Write-Host "Python 3.12 not found. Install from https://www.python.org/downloads/ or run: py install 3.12" -ForegroundColor Red
    exit 1
}
Write-Host "Using Python: $py312"

Get-CimInstance Win32_Process -Filter "name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'llama_cpp\.server|uvicorn.*app:app' } |
    ForEach-Object {
        cmd /c "taskkill /PID $($_.ProcessId) /F >nul 2>&1"
    }
Start-Sleep -Seconds 2

if (Test-Path ".\venv") {
    $bak = "venv-cpu-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Write-Host "Backing up current venv to $bak ..."
    Rename-Item ".\venv" $bak
}

Write-Host "Creating GPU-enabled Python 3.12 environment..."
& $py312 -m venv venv
$venvPy = (.\venv\Scripts\python.exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($venvPy -ne "3.12") {
    Write-Host "venv is Python $venvPy, expected 3.12. Remove venv and re-run." -ForegroundColor Red
    exit 1
}

.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\pip.exe install -r requirements.txt
.\venv\Scripts\pip.exe install starlette-context
Write-Host "Installing llama-cpp-python 0.3.25 with CUDA ($wheelTag)..."
$indexUrl = "https://abetlen.github.io/llama-cpp-python/whl/$wheelTag"
.\venv\Scripts\pip.exe install "llama-cpp-python==0.3.25" --force-reinstall --no-cache-dir --extra-index-url $indexUrl

Patch-LlamaWinmode
$llamaLib = Resolve-Path ".\venv\Lib\site-packages\llama_cpp\lib"
Copy-CudaDllsToLlamaLib -CudaRoot $cudaRoot -LlamaLib $llamaLib

$cudaBin = Join-Path $cudaRoot "bin"
$cudaX64 = Join-Path $cudaBin "x64"
$env:CUDA_PATH = $cudaRoot
$env:PATH = "$cudaX64;$cudaBin;" + $env:PATH

$modelPath = ".\data\models\bundled\qwen2.5-3b-instruct-q4_k_m.gguf"
$check = @"
import os
os.environ['CUDA_PATH'] = r'$cudaRoot'
os.environ['PATH'] = r'$cudaX64' + os.pathsep + r'$cudaBin' + os.pathsep + os.environ.get('PATH','')
import llama_cpp.llama_cpp as lc
print('GPU offload supported:', lc.llama_supports_gpu_offload())
from llama_cpp import Llama
m = Llama(model_path=r'$((Resolve-Path $modelPath -ErrorAction SilentlyContinue).Path)', n_ctx=512, n_gpu_layers=-1, verbose=False)
print('GPU model load OK')
"@
if (-not (Test-Path $modelPath)) {
    Write-Host "Model not downloaded yet; run scripts\download_default_model.py after setup." -ForegroundColor Yellow
} else {
    .\venv\Scripts\python.exe -c $check
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "GPU verification failed. See data\logs\bundled-llm.log after starting Odysseus." -ForegroundColor Red
        Restore-CpuVenv "GPU verification failed" | Out-Null
        exit 1
    }
}

if (-not (Test-Path ".\data\auth.json")) {
    .\venv\Scripts\python.exe setup.py
}

Write-Host ""
Write-Host "=== GPU setup complete ===" -ForegroundColor Green
Write-Host "Restart Odysseus (Start Odysseus.bat). Built-in AI will use your RTX GPU."
Write-Host "Optional: set BUNDLED_LLM_N_GPU_LAYERS=-1 in .env (default: all layers on GPU)."
Write-Host ""
