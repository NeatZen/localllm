# Start Odysseus so phones can reach it (LAN + Tailscale).
# Binds to 0.0.0.0:7000 — required for http://192.168.x.x:7000 and http://100.x.x.x:7000

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Host "Run install-turnkey.ps1 first (one-time setup)." -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path ".\data\auth.json")) {
    .\venv\Scripts\python.exe setup.py
}

$listening = Get-NetTCPConnection -LocalPort 7000 -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host "Port 7000 is already in use. Close the other Odysseus window first." -ForegroundColor Yellow
    exit 1
}

# CUDA runtime DLLs for GPU llama-cpp-python (CUDA 13+ uses bin\x64).
$cudaRoot = Get-ChildItem "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA" -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1
if ($cudaRoot) {
    $binX64 = Join-Path $cudaRoot.FullName "bin\x64"
    $bin = Join-Path $cudaRoot.FullName "bin"
    if (Test-Path $binX64) { $env:PATH = "$binX64;$env:PATH" }
    if (Test-Path $bin) { $env:PATH = "$bin;$env:PATH" }
    $env:CUDA_PATH = $cudaRoot.FullName
}

function Get-LanIPv4 {
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notlike "127.*" -and
            $_.IPAddress -notlike "169.254.*" -and
            $_.PrefixOrigin -ne "WellKnown"
        } |
        Sort-Object InterfaceMetric |
        Select-Object -ExpandProperty IPAddress -First 1
}

$lanIp = Get-LanIPv4
$tailscaleIp = $null
if (Get-Command tailscale -ErrorAction SilentlyContinue) {
    try { $tailscaleIp = (tailscale ip -4 2>$null | Select-Object -First 1).Trim() } catch {}
}

Write-Host ""
Write-Host "=== Odysseus (network mode) ===" -ForegroundColor Cyan
Write-Host "On this PC:     http://localhost:7000"
if ($lanIp) { Write-Host "On home Wi-Fi:  http://${lanIp}:7000" -ForegroundColor Green }
if ($tailscaleIp) { Write-Host "On phone (anywhere, Tailscale ON):  http://${tailscaleIp}:7000" -ForegroundColor Green }
if (-not $lanIp -and -not $tailscaleIp) {
    Write-Host "Could not detect LAN/Tailscale IP — still listening on 0.0.0.0:7000" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Press Ctrl+C to stop."
Write-Host ""

.\venv\Scripts\uvicorn.exe app:app --host 0.0.0.0 --port 7000
