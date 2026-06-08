"""cookbook_helpers.py — validators + small helpers shared by the cookbook routes.
Extracted from cookbook_routes.py; the routes module imports the symbols it needs."""

import logging
import os
import re
import shlex
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Shared tmux/download log dir (used by cookbook + shell routes).
TMUX_LOG_DIR = Path(tempfile.gettempdir()) / "neatai-tmux"


# HuggingFace repo IDs are <org>/<name>, both alphanumerics plus ._-
# Rejecting anything else up front closes off shell-interpolation vectors.
_REPO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
# Ollama refs: name, namespace/name, or name:tag (tag may include + for quant variants).
_OLLAMA_MODEL_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?(?::[A-Za-z0-9._+-]+)?$"
)
# Include pattern is a glob: allow typical safe glyphs only.
_INCLUDE_RE = re.compile(r"^[A-Za-z0-9._\-*?/\[\]]+$")
# Remote host: user@host (optionally with :port-free hostname parts).
_REMOTE_HOST_RE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$")
# HF tokens and API tokens are url-safe base64-like.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=-]+$")
# Session IDs we mint look like "cookbook-deadbeef" or "serve-deadbeef".
# Anything beyond plain alphanumerics + dash + underscore could break out
# of the shell/PowerShell contexts the value lands in.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SSH_PORT_RE = re.compile(r"^\d{1,5}$")
_GPU_LIST_RE = re.compile(r"^\d+(?:,\d+)*$")
# A download target directory. Absolute or ~-relative path; safe path glyphs
# only (no quotes, shell metacharacters, or spaces) since it lands in a shell
# command. A leading ~ is expanded to $HOME at command-build time.
_LOCAL_DIR_RE = re.compile(r"^~?/[A-Za-z0-9._/-]*$|^~$")


def _validate_repo_id(v: str | None) -> str:
    v = (v or "").strip().rstrip(".")
    if not v or not _REPO_ID_RE.match(v):
        raise HTTPException(400, "Invalid repo_id — must be <org>/<name> using [A-Za-z0-9._-]")
    return v


def _validate_ollama_model(v: str | None) -> str:
    from services.ollama_library import normalize_ollama_model_ref

    try:
        ref = normalize_ollama_model_ref(v or "")
    except ValueError as exc:
        raise HTTPException(400, "Invalid Ollama model reference") from exc
    if not ref or not _OLLAMA_MODEL_RE.match(ref):
        raise HTTPException(
            400,
            "Invalid Ollama model — use name, namespace/name, or name:tag (e.g. qwen2.5:7b)",
        )
    return ref


def _validate_include(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not _INCLUDE_RE.match(v):
        raise HTTPException(400, "Invalid include pattern")
    return v


def _validate_remote_host(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not _REMOTE_HOST_RE.match(v):
        raise HTTPException(400, "Invalid remote_host — must be user@host, no SSH option syntax")
    return v


def _validate_token(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not _TOKEN_RE.match(v):
        raise HTTPException(400, "Invalid token characters")
    return v


def _validate_local_dir(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    v = v.rstrip("/") or "/"
    if not _LOCAL_DIR_RE.match(v):
        raise HTTPException(400, "Invalid local_dir — must be an absolute or ~ path with no spaces or shell metacharacters")
    return v


def _validate_ssh_port(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not _SSH_PORT_RE.fullmatch(str(v)):
        raise HTTPException(400, "Invalid ssh_port")
    port = int(v)
    if port < 1 or port > 65535:
        raise HTTPException(400, "Invalid ssh_port")
    return str(port)


def _validate_gpus(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not _GPU_LIST_RE.fullmatch(str(v)):
        raise HTTPException(400, "Invalid gpus — expected comma-separated GPU indexes")
    return str(v)


def _shell_path(p: str) -> str:
    """Render a validated path for a double-quoted shell context, expanding a
    leading ~ to $HOME (single quotes wouldn't expand it). Safe because
    _validate_local_dir already restricts the charset."""
    if p == "~":
        return '"$HOME"'
    if p.startswith("~/"):
        return '"$HOME/' + p[2:] + '"'
    return '"' + p + '"'


def _ps_squote(v: str) -> str:
    """Escape a value for PowerShell single-quoted string interpolation.
    Belt-and-suspenders on top of _validate_token's regex — if the regex
    is ever loosened, this still keeps the heredoc shell-safe."""
    return v.replace("'", "''")


def _bash_squote(v: str) -> str:
    """Escape a value for bash/sh single-quoted string interpolation."""
    return v.replace("'", "'\\''")


# Allow-list of binaries permitted as the leading token of `req.cmd` for /api/model/serve.
# Anything else is rejected before the cmd is interpolated into a tmux/PowerShell wrapper.
_SERVE_CMD_ALLOWLIST = {
    "vllm", "llama-server", "llama_server", "llama.cpp", "ollama",
    "python", "python3",
    "sglang", "lmdeploy",
    "node", "npx",
}


# The llama.cpp GGUF launcher (static/js/cookbook.js) emits a fixed-shape
# prelude that resolves the cached .gguf on the target host before serving:
#   MODEL_FILE=$( { find …; find …; } | head -1 ) && { [ -n "$MODEL_FILE" ] && \
#   [ -f "$MODEL_FILE" ]; } || { echo "ERROR…"; exit 1; } && <serve> || <serve>
# That legitimately needs $(...)/&&/||, so we recognise this exact shape and
# validate the serve binaries it guards rather than rejecting it wholesale.
_GGUF_PRELUDE_RE = re.compile(
    r'^MODEL_FILE=\$\([^\n]*?\)\s*&&\s*\{[^{}]*\}\s*\|\|\s*\{[^{}]*\}\s*&&\s*'
)


def _check_serve_binary(seg: str) -> None:
    """Validate that a single command segment starts with an allowlisted binary
    (after skipping leading env-var assignments like `CUDA_VISIBLE_DEVICES=0`)."""
    try:
        tokens = shlex.split(seg) if seg.strip() else []
    except ValueError:
        raise HTTPException(400, "Invalid cmd — could not parse")
    if not tokens:
        return
    env_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
    first = next((t for t in tokens if not env_re.match(t)), "")
    base = os.path.basename(first)
    if base not in _SERVE_CMD_ALLOWLIST:
        raise HTTPException(
            400,
            f"cmd binary '{base or '(empty)'}' is not allowed. Must start with one of: "
            f"{', '.join(sorted(_SERVE_CMD_ALLOWLIST))}",
        )


def _validate_serve_cmd(v: str | None) -> str | None:
    """Reject serve commands that aren't in the allowlist or contain shell metachars.

    `req.cmd` is dropped verbatim into a bash/PowerShell wrapper script and
    executed in a tmux session. Without this gate, an admin (or anyone in the
    pre-fix world) could pass arbitrary shell payloads.

    Leading env-var assignments (e.g. `CUDA_VISIBLE_DEVICES=0 python3 ...`)
    are stripped before checking the binary — several of our cmd builders
    prepend them, and they shouldn't trip the allowlist.
    """
    if v is None or v == "":
        return None
    # Collapse backslash-newline line continuations into single spaces. Serve
    # commands (vLLM especially) are routinely pasted multi-line with trailing
    # `\` — that's a safe shell/shlex continuation, so the command stays ONE
    # logical invocation and the leading-token allowlist below still governs.
    v = re.sub(r"\\[ \t]*\r?\n[ \t]*", " ", v).strip()
    # Backticks and raw newlines are never legitimate here.
    if any(c in v for c in ("`", "\n", "\r")):
        raise HTTPException(400, "Invalid characters in cmd")
    # Known GGUF launcher prelude → validate the serve invocation(s) it guards.
    m = _GGUF_PRELUDE_RE.match(v)
    if m:
        rest = v[m.end():]
        # rest is `[ENV=…] python3 -m llama_cpp.server … || [ENV=…] llama-server …`
        for part in rest.split("||"):
            _check_serve_binary(part.strip())
        return v
    # Otherwise: a single invocation — no shell metacharacters allowed.
    # (`$(` was the original intent; bare `$` is fine for shell-safe paths.)
    if any(c in v for c in (";", "&&", "||", "$(")):
        raise HTTPException(400, "Invalid characters in cmd")
    _check_serve_binary(v)
    return v


class ModelDownloadRequest(BaseModel):
    repo_id: str
    source: str = "hf"  # "hf" or "ollama"
    include: str | None = None  # glob pattern e.g. "*Q4_K_M*"
    hf_token: str | None = None
    env_prefix: str | None = None  # e.g. "source ~/venv/bin/activate"
    remote_host: str | None = None  # e.g. "gpu-box" — run download on this host via SSH
    ssh_port: str | None = None    # e.g. "8022" for Termux
    platform: str | None = None    # "linux", "termux", or "windows"
    local_dir: str | None = None   # base dir to download into (a per-model subfolder is created under it); None = default HF cache
    disable_hf_transfer: bool = False  # skip the Rust hf_transfer downloader — slower but far more reliable on large files (used by retries)


class ServeRequest(BaseModel):
    repo_id: str
    cmd: str
    remote_host: str | None = None
    ssh_port: str | None = None
    env_prefix: str | None = None
    hf_token: str | None = None
    gpus: str | None = None
    platform: str | None = None    # "linux", "termux", or "windows"


def _parse_serve_phase(snapshot: str, task_type: str = "serve") -> dict:
    """Parse a tmux snapshot of a serve task into structured phase info.

    Single source of truth for serve task status detection. Returns:
        { "phase": str, "status": "ready"|"running"|"", "tps": float|None,
          "reqs": int|None, "pct": int|None }
    """
    import re
    if task_type != "serve" or not snapshot:
        return {}
    # Strip newlines so tmux line-wrapping doesn't break regex matching
    flat = re.sub(r'\s+', ' ', snapshot)

    load_matches = re.findall(r'Loading safetensors.*?(\d+)%', flat)
    # Prefer "Downloading (incomplete total...)" (real aggregate bytes) over
    # "Fetching N files" (whole-file count, lags with hf_transfer's chunked pulls).
    downloading_matches = re.findall(r'Downloading.*?(\d+)%', flat)
    fetching_matches = re.findall(r'Fetching.*?(\d+)%', flat)
    dl_matches = downloading_matches if downloading_matches else fetching_matches
    # Match "Avg generation throughput: X tokens/s, Running: N reqs" (with line-wrap tolerance)
    tps_matches = re.findall(
        r'(?:Avg )?generation throughput:\s*([\d.]+)\s*tokens/s.*?Running:\s*(\d+)\s*reqs',
        flat,
    )

    # Check throughput FIRST — the throughput log line contains "GPU KV cache usage"
    # which would otherwise false-match the warmup check
    if tps_matches:
        tps_str, reqs_str = tps_matches[-1]
        tps = float(tps_str)
        reqs = int(reqs_str)
        return {
            "phase": f"{tps_str} tok/s" if reqs > 0 else "idle",
            "status": "ready",
            "tps": tps,
            "reqs": reqs,
        }
    if "Application startup complete" in flat:
        return {"phase": "ready", "status": "ready"}
    # HTTP access logs (e.g. GET /v1/models 200 OK) mean the server is up and serving
    if re.search(r'(?:GET|POST)\s+/[^\s]*\s+HTTP/[\d.]+"\s*\d{3}', flat):
        return {"phase": "idle", "status": "ready"}
    if "Loading weights took" in flat:
        return {"phase": "initializing", "status": "running"}
    # "GPU KV cache" alone (during allocation) — not "GPU KV cache usage" (runtime log)
    if "GPU KV cache" in flat and "GPU KV cache usage" not in flat:
        return {"phase": "warming up", "status": "running"}
    if load_matches:
        pct = int(load_matches[-1])
        return {"phase": f"loading {pct}%", "status": "running", "pct": pct}
    if dl_matches:
        pct = int(dl_matches[-1])
        return {"phase": f"downloading {pct}%", "status": "running", "pct": pct}
    return {}


def _ssh(host, cmd, port=None):
    """Build SSH command string with optional port."""
    pf = f"-p {port} " if port and port != "22" else ""
    return f"ssh {pf}{host} '{cmd}'"


def _safe_env_prefix(ep: str | None) -> str | None:
    """Rewrite a `source <path>` env_prefix so it no-ops if the path is missing.
    Prevents `line N: <path>: No such file or directory` errors when a serve
    task is launched against a host that doesn't have the expected venv.

    Also rewrites leading `~/` → `$HOME/` so the path expands inside double
    quotes (bash only tilde-expands unquoted tokens at word start)."""
    if not ep:
        return ep
    import shlex
    try:
        parts = shlex.split(ep, posix=True)
    except ValueError:
        raise HTTPException(400, "Invalid env_prefix")
    if len(parts) != 2 or parts[0] not in {"source", "."}:
        # Bash conda activation emitted by the frontend:
        #   eval "$(conda shell.bash hook)" && conda activate ENV
        m = re.fullmatch(r'eval "\$\(conda shell\.bash hook\)" && conda activate (.+)', ep)
        if m:
            env = m.group(1).strip()
            try:
                env_parts = shlex.split(env, posix=True)
            except ValueError:
                raise HTTPException(400, "Invalid env_prefix")
            if len(env_parts) != 1:
                raise HTTPException(400, "Invalid env_prefix")
            return 'eval "$(conda shell.bash hook)" && conda activate ' + shlex.quote(env_parts[0])

        # Plain conda activation, used by Windows/PowerShell and some manual callers.
        if len(parts) == 3 and parts[0] == "conda" and parts[1] == "activate":
            return "conda activate " + shlex.quote(parts[2])

        # PowerShell venv activation emitted by the frontend:
        #   & 'C:\path\Scripts\Activate.ps1'
        if len(parts) == 2 and parts[0] == "&":
            path = parts[1]
            if any(c in path for c in "\r\n;&|`$<>"):
                raise HTTPException(400, "Invalid env_prefix")
            # PowerShell single-quoted paths do not expand ~; use $env:USERPROFILE.
            if path.startswith("~/") or path.startswith("~\\"):
                rest = path[2:].replace("\\", "/")
                return f'& "$env:USERPROFILE/{rest}"'
            if path == "~":
                return '& "$env:USERPROFILE"'
            return "& '" + path.replace("'", "''") + "'"

        raise HTTPException(400, "Invalid env_prefix")
    path = parts[1]
    if any(c in path for c in "\r\n;&|`$<>"):
        raise HTTPException(400, "Invalid env_prefix")
    # Replace a leading "~/" with "$HOME/" so it survives quoting
    if path.startswith("~/"):
        path = "$HOME/" + path[2:]
    elif path == "~":
        path = "$HOME"
    path = path.replace('"', '\\"')
    return f'[ -f "{path}" ] && source "{path}" || true'


def _ssh_ps(host, script_path, port=None):
    """Build SSH command to run a PowerShell script on a Windows remote."""
    pf = f"-p {port} " if port and port != "22" else ""
    return f'ssh {pf}{host} "powershell -ExecutionPolicy Bypass -File {script_path}"'


_WINDOWS_POWERSHELL_EXE: str | None = None


def windows_powershell_exe() -> str:
    """Resolve PowerShell for local subprocess launches.

    NeatAi is often started from an IDE or service context where PATH does
    not include System32, so bare ``powershell.exe`` raises WinError 2.
    """
    global _WINDOWS_POWERSHELL_EXE
    if _WINDOWS_POWERSHELL_EXE:
        return _WINDOWS_POWERSHELL_EXE
    if sys.platform != "win32":
        _WINDOWS_POWERSHELL_EXE = "powershell"
        return _WINDOWS_POWERSHELL_EXE
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    candidates = [
        Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "PowerShell"
        / "7"
        / "pwsh.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "PowerShell"
        / "7"
        / "pwsh.exe",
    ]
    for path in candidates:
        if path.is_file():
            _WINDOWS_POWERSHELL_EXE = str(path)
            return _WINDOWS_POWERSHELL_EXE
    found = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh.exe")
    if found:
        _WINDOWS_POWERSHELL_EXE = found
        return _WINDOWS_POWERSHELL_EXE
    raise FileNotFoundError(
        "PowerShell not found. Install Windows PowerShell or add it to PATH."
    )


# Windows session dir — stored in user's temp on the remote
WIN_SESSION_DIR = "$env:TEMP\\\\neatai-sessions"

# Most new Ollama library models require a recent CLI (412 if too old).
OLLAMA_MIN_RECOMMENDED = (0, 30, 0)


def parse_ollama_version(text: str) -> tuple[int, int, int] | None:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def get_local_ollama_version() -> tuple[str | None, tuple[int, int, int] | None]:
    if not shutil.which("ollama"):
        return None, None
    import subprocess

    try:
        r = subprocess.run(
            ["ollama", "--version"],
            capture_output=True,
            text=True,
            timeout=12,
            errors="replace",
        )
        text = (r.stdout or r.stderr or "").strip()
        return text, parse_ollama_version(text)
    except Exception:
        return None, None


def ollama_version_outdated(ver: tuple[int, int, int] | None) -> bool:
    if not ver:
        return False
    return ver < OLLAMA_MIN_RECOMMENDED


def ollama_pull_ps_block(model_ref: str) -> list[str]:
    """Run ``ollama pull`` and mirror stderr progress/errors into session stdout log."""
    q = _ps_squote(model_ref)
    return [
        f"$pullOut = & ollama pull '{q}' 2>&1",
        "$pullOut | ForEach-Object { Write-Host $_ }",
        'if ($LASTEXITCODE -ne 0) { Write-Host ""; Write-Host "DOWNLOAD_FAILED (exit $LASTEXITCODE)"; exit $LASTEXITCODE }',
        'Write-Host ""',
        'Write-Host "DOWNLOAD_OK"',
    ]


def _run_python_script(script_path: Path, timeout: int = 120) -> tuple[bytes, bytes]:
    import subprocess as sp

    r = sp.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        timeout=timeout,
        cwd=str(Path.home()),
    )
    return r.stdout, r.stderr


def _run_shell_command(cmd: str, timeout: int = 120) -> tuple[bytes, bytes]:
    import subprocess as sp

    r = sp.run(
        cmd,
        shell=True,
        capture_output=True,
        timeout=timeout,
        cwd=str(Path.home()),
    )
    return r.stdout, r.stderr


_CACHE_REPO_ID_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def _validate_cache_repo_id(
    v: str | None,
    *,
    is_local_dir: bool = False,
    is_ollama: bool = False,
) -> str:
    """Validate a cached-model id (HF repo, local folder name, or Ollama ref)."""
    if is_ollama:
        return _validate_ollama_model(v)
    rid = (v or "").strip().rstrip(".")
    if not rid or ".." in rid or rid.startswith(("/","\\")):
        raise HTTPException(400, "Invalid repo_id")
    if is_local_dir:
        if not _CACHE_REPO_ID_RE.match(rid):
            raise HTTPException(400, "Invalid local model id")
        return rid
    return _validate_repo_id(rid)


def _resolve_cached_delete_target(
    repo_id: str,
    cache_path: str,
    *,
    is_local_dir: bool = False,
) -> Path:
    if cache_path and cache_path != "ollama":
        base = Path(os.path.expanduser(cache_path)).resolve()
    else:
        hf_home = Path(os.environ.get("HF_HOME") or (Path.home() / ".cache/huggingface"))
        base = (hf_home / "hub").resolve()
    if is_local_dir:
        target = (base / repo_id).resolve()
    else:
        target = (base / f"models--{repo_id.replace('/', '--')}").resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise HTTPException(400, "Invalid delete path") from exc
    return target


def delete_cached_model_local(
    repo_id: str,
    cache_path: str,
    *,
    is_local_dir: bool = False,
) -> dict[str, Any]:
    """Delete a cached HF model directory or local model folder."""
    target = _resolve_cached_delete_target(
        repo_id, cache_path, is_local_dir=is_local_dir,
    )
    if not target.exists():
        return {"ok": False, "error": f"Not found: {target}"}
    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as exc:
        logger.warning("Failed to delete cached model %s: %s", target, exc)
        return {"ok": False, "error": str(exc)}
    if target.exists():
        return {"ok": False, "error": f"Could not remove {target}"}
    return {"ok": True, "deleted": str(target)}


def delete_ollama_model_local(repo_id: str) -> dict[str, Any]:
    ref = _validate_ollama_model(repo_id)
    if not shutil.which("ollama"):
        return {"ok": False, "error": "Ollama is not installed"}
    import subprocess as sp

    try:
        r = sp.run(
            ["ollama", "rm", ref],
            capture_output=True,
            text=True,
            timeout=120,
            errors="replace",
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip() or f"ollama rm failed ({r.returncode})"
        return {"ok": False, "error": err}
    return {"ok": True, "deleted": ref}


def sync_chat_models_after_cache_delete(
    repo_id: str,
    *,
    is_ollama: bool = False,
) -> None:
    """Refresh endpoint model caches so the chat picker drops deleted models."""
    import json

    from core.database import ModelEndpoint, SessionLocal
    from routes.model_routes import _probe_endpoint, invalidate_models_cache
    from src.endpoint_resolver import normalize_base as _normalize_base

    short = repo_id.split("/")[-1] if "/" in repo_id else repo_id
    repo_base = short.removesuffix("-GGUF").removesuffix("_GGUF")

    def _matches_deleted(mid: Any) -> bool:
        mid_str = str(mid)
        mid_short = mid_str.replace("\\", "/").split("/")[-1]
        if mid_str == repo_id or mid_short == short or mid_short == repo_id:
            return True
        if repo_base and len(repo_base) >= 8:
            stem = mid_short.rsplit(".", 1)[0]
            if stem.startswith(repo_base) or repo_base in stem:
                return True
        return False

    db = SessionLocal()
    try:
        endpoints = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
        for ep in endpoints:
            base = _normalize_base(ep.base_url or "")
            is_ollama_ep = "11434" in base or "ollama" in (ep.name or "").lower()

            if is_ollama and is_ollama_ep:
                ids = _probe_endpoint(base, ep.api_key, timeout=3)
                ep.cached_models = json.dumps(ids) if ids else None
                continue

            if not ep.cached_models:
                continue
            try:
                models = json.loads(ep.cached_models)
            except Exception:
                continue
            if not isinstance(models, list):
                continue
            filtered = [mid for mid in models if not _matches_deleted(mid)]
            if len(filtered) != len(models):
                ep.cached_models = json.dumps(filtered) if filtered else None
        db.commit()
    except Exception as exc:
        logger.warning("sync_chat_models_after_cache_delete failed: %s", exc)
        db.rollback()
    finally:
        db.close()
    invalidate_models_cache()


def list_local_ollama_models() -> list[dict[str, Any]]:
    """Return locally pulled Ollama models for the Serve hub."""
    if not shutil.which("ollama"):
        return []
    import subprocess as sp

    try:
        r = sp.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=30,
            errors="replace",
        )
    except Exception:
        return []
    if r.returncode != 0:
        return []

    models: list[dict[str, Any]] = []
    for line in r.stdout.splitlines()[1:]:
        text = line.strip()
        if not text:
            continue
        parts = text.split()
        if not parts:
            continue
        name = parts[0]
        size = "?"
        for i, token in enumerate(parts):
            if token in ("GB", "MB", "TB", "KB") and i > 0:
                size = f"{parts[i - 1]} {token}"
                break
        models.append(
            {
                "repo_id": name,
                "size": size,
                "nb_files": 0,
                "has_incomplete": False,
                "status": "ready",
                "path": "ollama",
                "source": "ollama",
                "is_ollama": True,
            }
        )
    return models
