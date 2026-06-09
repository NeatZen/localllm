"""Generate a sample Ollama serve runner and validate PowerShell parses it."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from routes.cookbook_helpers import TMUX_LOG_DIR, _ps_serve_ok_ollama, _write_ps_runner

session_dir = Path(__file__).resolve().parents[1] / "data"
ps_lines = [
    f'$sessionDir = "{session_dir}"',
    'New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null',
    "if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {",
    '  Write-Host "Ollama not found. Install from https://ollama.com/download/windows"; exit 1',
    '}',
    'ollama pull openchat:7b',
    'if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }',
    _ps_serve_ok_ollama('openchat:7b'),
    'while ($true) { Start-Sleep -Seconds 3600 }',
    'Write-Host ""',
    'Write-Host "=== Process exited with code $LASTEXITCODE ==="',
]
out = TMUX_LOG_DIR / "serve-test_run.ps1"
_write_ps_runner(out, ps_lines)
print("wrote", out)

parse_ps = (
    f"$e=$null; $t=$null; "
    f"[void][System.Management.Automation.Language.Parser]::ParseFile('{out}', [ref]$t, [ref]$e); "
    "if ($e) { $e | ForEach-Object { $_.ToString() }; exit 1 } "
    "else { Write-Host 'PARSE OK' }"
)
r = subprocess.run(
    ["powershell", "-NoProfile", "-Command", parse_ps],
    capture_output=True,
    text=True,
)
print(r.stdout, r.stderr, end="")
raise SystemExit(r.returncode)
