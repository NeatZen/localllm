# Run NeatAi on your local network so a phone/tablet on the same Wi-Fi can connect.
# Usage:
#   .\run-phone.ps1           # default port 7000
#   .\run-phone.ps1 -Port 8080
#
# On your phone, open the "On your phone" URL printed below (same Wi-Fi as this PC).

param(
    [int]$Port = 7000,
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# CUDA runtime DLLs for GPU llama-cpp-python (CUDA 13+ uses bin\x64).
if ($IsWindows) {
    $cudaRoot = Get-ChildItem "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA" -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        Select-Object -First 1
    if ($cudaRoot) {
        $x64 = Join-Path $cudaRoot.FullName "bin\x64"
        $bin = Join-Path $cudaRoot.FullName "bin"
        if (Test-Path $x64) { $env:PATH = "$x64;$env:PATH" }
        if (Test-Path $bin) { $env:PATH = "$bin;$env:PATH" }
        $env:CUDA_PATH = $cudaRoot.FullName
    }
}

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Host "Run install-turnkey.ps1 first (one-time setup)." -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path ".\data\auth.json")) {
    .\venv\Scripts\python.exe setup.py
}

# Best-effort LAN IPv4 (skip loopback and link-local APIPA addresses).
$lanIp = $null
try {
    $lanIp = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notmatch '^127\.' -and
            $_.IPAddress -notlike '169.254.*'
        } |
        Sort-Object InterfaceMetric, PrefixLength -Descending |
        Select-Object -First 1 -ExpandProperty IPAddress
} catch {
    # Get-NetIPAddress may be unavailable on some systems; fall back below.
}

if (-not $lanIp) {
    try {
        $lanIp = (
            Get-CimInstance Win32_NetworkAdapterConfiguration -Filter "IPEnabled=True" |
            ForEach-Object { $_.IPAddress } |
            Where-Object { $_ -and $_ -notmatch '^127\.' -and $_ -notlike '169.254.*' } |
            Select-Object -First 1
        )
    } catch {}
}

# Linux / macOS fallback (Get-NetIPAddress is Windows-only).
if (-not $lanIp -and -not $IsWindows) {
    try {
        $raw = (& hostname -I 2>$null)
        if ($raw) {
            foreach ($candidate in ($raw.ToString().Trim() -split '\s+')) {
                if ($candidate -and $candidate -notmatch '^127\.' -and $candidate -notlike '169.254.*') {
                    $lanIp = $candidate
                    break
                }
            }
        }
    } catch {}
}

if (-not $lanIp -and -not $IsWindows) {
    try {
        $routeOut = (& ip -4 route get 1.1.1.1 2>$null | Out-String)
        if ($routeOut -match 'src\s+(\d{1,3}(?:\.\d{1,3}){3})') {
            $lanIp = $Matches[1]
        }
    } catch {}
}

if (-not $lanIp) {
    $lanIp = "YOUR_PC_IP"
}

# Allow the phone browser origin through CORS (credentials require explicit origins).
$env:ALLOWED_ORIGINS = @(
    "http://localhost",
    "http://127.0.0.1",
    "http://localhost:$Port",
    "http://127.0.0.1:$Port",
    "http://${lanIp}:$Port"
) -join ","

Write-Host ""
Write-Host "=== NeatAi (phone / LAN access) ===" -ForegroundColor Cyan
Write-Host "On this PC:     http://127.0.0.1:$Port"
if ($lanIp -ne "YOUR_PC_IP") {
    Write-Host "On your phone:  http://${lanIp}:$Port" -ForegroundColor Green
} else {
    Write-Host "On your phone:  http://<this-PC-LAN-IP>:$Port" -ForegroundColor Yellow
    Write-Host "  (Could not detect LAN IP - run: ipconfig and use your Wi-Fi IPv4 address)"
}
Write-Host ""
Write-Host "Requirements:"
Write-Host "  - Phone on the same Wi-Fi as this PC"
Write-Host "  - Windows Firewall allows inbound TCP port $Port (Private network)"
Write-Host "  - Stop Docker NeatAi if port $Port is busy: docker compose down"
Write-Host "Note: ChromaDB warnings are normal without Docker. Built-in AI uses port 11435."
Write-Host ""

if ($OpenBrowser) {
    Start-Process "http://127.0.0.1:$Port"
}

.\venv\Scripts\uvicorn.exe app:app --host 0.0.0.0 --port $Port
