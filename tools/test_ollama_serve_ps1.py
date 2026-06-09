"""Launch Ollama serve via API and verify generated .ps1 parses."""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from routes.cookbook_helpers import TMUX_LOG_DIR

BASE = "http://127.0.0.1:7000"
token = next(iter(json.loads((ROOT / "data" / "sessions.json").read_text(encoding="utf-8"))))

body = json.dumps({
    "repo_id": "openchat:7b",
    "cmd": "ollama pull openchat:7b",
    "platform": "windows",
    "remote_host": "",
}).encode()

req = urllib.request.Request(
    f"{BASE}/api/model/serve",
    data=body,
    headers={
        "Content-Type": "application/json",
        "Cookie": f"neatai_session={token}",
    },
    method="POST",
)
with urllib.request.urlopen(req, timeout=60) as resp:
    data = json.loads(resp.read())

print("serve_response", data)
if not data.get("ok"):
    raise SystemExit(1)

session_id = data.get("session_id")
runner = TMUX_LOG_DIR / f"{session_id}_run.ps1"
if not runner.exists():
    raise SystemExit(f"missing runner: {runner}")

text = runner.read_text(encoding="utf-8-sig")
print("runner_preview:")
print("\n".join(text.splitlines()[:15]))
if "—" in text:
    raise SystemExit("runner still contains unicode em dash")

parse_ps = (
    f"$e=$null; $t=$null; "
    f"[void][System.Management.Automation.Language.Parser]::ParseFile('{runner}', [ref]$t, [ref]$e); "
    "if ($e) { $e | ForEach-Object { $_.ToString() }; exit 1 } "
    "else { Write-Host 'PARSE OK' }"
)
r = subprocess.run(["powershell", "-NoProfile", "-Command", parse_ps], capture_output=True, text=True)
print(r.stdout, r.stderr, end="")
if r.returncode != 0:
    raise SystemExit(r.returncode)

# Give serve a few seconds to start
time.sleep(5)
status_req = urllib.request.Request(
    f"{BASE}/api/cookbook/tasks/status",
    headers={"Cookie": f"neatai_session={token}"},
)
with urllib.request.urlopen(status_req, timeout=30) as resp:
    status = json.loads(resp.read())
task = next((t for t in status.get("tasks", []) if t.get("session_id") == session_id), None)
print("task_status", task.get("status") if task else "not found")
if task and task.get("status") == "crashed":
    print("log_tail", (task.get("output") or "")[-500:])
    raise SystemExit("serve task crashed")

print("INTEGRATION OK")
