"""Built-in local LLM for turnkey installs (llama-cpp-python, no Ollama).

Downloads a default GGUF model on first run, serves it on a fixed local port,
and registers an OpenAI-compatible endpoint in NeatAi automatically.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

# Qwen2.5 3B Instruct — strong quality for ~2 GB disk (Q4_K_M).
DEFAULT_MODEL_REPO = os.getenv(
    "BUNDLED_LLM_MODEL_REPO", "Qwen/Qwen2.5-3B-Instruct-GGUF"
)
DEFAULT_MODEL_FILE = os.getenv(
    "BUNDLED_LLM_MODEL_FILE", "qwen2.5-3b-instruct-q4_k_m.gguf"
)
BUNDLED_LLM_PORT = int(os.getenv("BUNDLED_LLM_PORT", "11435"))
BUNDLED_LLM_HOST = os.getenv("BUNDLED_LLM_HOST", "127.0.0.1")
BUNDLED_LLM_START_TIMEOUT = int(os.getenv("BUNDLED_LLM_START_TIMEOUT", "300"))
ENDPOINT_NAME = "Built-in AI (local)"

_process: Optional[subprocess.Popen] = None
_ready_lock: Optional[asyncio.Lock] = None
_status: dict[str, Any] = {
    "state": "idle",
    "progress": 0,
    "message": "",
    "error": None,
}


def is_enabled() -> bool:
    raw = os.getenv("BUNDLED_LLM_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def models_dir() -> Path:
    override = os.getenv("BUNDLED_LLM_MODELS_DIR", "").strip()
    if override:
        return Path(override)
    return project_root() / "data" / "models" / "bundled"


def _model_id_for_path(path: Path, *, separator: str | None = None) -> str:
    """Relative model id (matches llama_cpp.server on this platform)."""
    try:
        rel = path.resolve().relative_to(project_root().resolve())
        mid = str(rel)
    except ValueError:
        mid = str(path.resolve())
    if separator == "\\":
        return mid.replace("/", "\\")
    if separator == "/":
        return mid.replace("\\", "/")
    return mid


def _active_model_path() -> Path:
    return models_dir() / "active.json"


def get_active_model_config() -> dict[str, str]:
    """Active bundled model (persisted in data/models/bundled/active.json)."""
    path = _active_model_path()
    if path.is_file():
        try:
            import json

            data = json.loads(path.read_text(encoding="utf-8"))
            return {
                "model_id": str(data.get("model_id") or "qwen2.5-3b"),
                "repo": str(data.get("repo") or DEFAULT_MODEL_REPO),
                "file": str(data.get("file") or DEFAULT_MODEL_FILE),
            }
        except Exception:
            pass
    return {
        "model_id": "qwen2.5-3b",
        "repo": DEFAULT_MODEL_REPO,
        "file": DEFAULT_MODEL_FILE,
    }


def set_active_model_config(*, model_id: str, repo: str, file: str) -> None:
    import json

    models_dir().mkdir(parents=True, exist_ok=True)
    payload = {"model_id": model_id, "repo": repo, "file": file}
    _active_model_path().write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def model_path() -> Path:
    return models_dir() / get_active_model_config()["file"]


def base_url() -> str:
    return f"http://{BUNDLED_LLM_HOST}:{BUNDLED_LLM_PORT}/v1"


def serving_n_ctx() -> int:
    """Actual --n_ctx the bundled llama_cpp.server was started with."""
    try:
        return int(os.getenv("BUNDLED_LLM_N_CTX", "8192"))
    except ValueError:
        return 8192


def _serving_n_ctx_marker() -> Path:
    return models_dir() / "serving_n_ctx.txt"


def _read_serving_n_ctx_marker() -> str | None:
    path = _serving_n_ctx_marker()
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


def _write_serving_n_ctx_marker() -> None:
    models_dir().mkdir(parents=True, exist_ok=True)
    _serving_n_ctx_marker().write_text(f"{serving_n_ctx()}\n", encoding="utf-8")


def get_status() -> dict[str, Any]:
    active = get_active_model_config()
    out = dict(_status)
    out["enabled"] = is_enabled()
    out["model_downloaded"] = is_model_downloaded()
    out["model_path"] = str(model_path())
    out["base_url"] = base_url()
    out["model_repo"] = active["repo"]
    out["model_file"] = active["file"]
    out["model_id"] = active["model_id"]
    return out


def is_model_downloaded_at(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 50_000_000


def is_model_downloaded() -> bool:
    return is_model_downloaded_at(model_path())


def _set_status(**kwargs: Any) -> None:
    _status.update(kwargs)


async def is_server_healthy() -> bool:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base_url()}/models", timeout=3.0)
            return r.status_code == 200
    except Exception:
        return False


def _has_configured_endpoints() -> bool:
    from core.database import SessionLocal, ModelEndpoint

    db = SessionLocal()
    try:
        return db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled.is_(True)).count() > 0
    finally:
        db.close()


def _disable_stale_docker_endpoints() -> None:
    """Disable Docker-only Ollama endpoints on native Windows installs."""
    from core.database import SessionLocal, ModelEndpoint

    db = SessionLocal()
    try:
        stale = (
            db.query(ModelEndpoint)
            .filter(ModelEndpoint.base_url.like("%host.docker.internal%"))
            .filter(ModelEndpoint.is_enabled.is_(True))
            .all()
        )
        for ep in stale:
            ep.is_enabled = False
            logger.info("Disabled stale Docker endpoint: %s", ep.base_url)
        if stale:
            db.commit()
    except Exception as e:
        logger.warning("Failed to disable stale Docker endpoints: %s", e)
        db.rollback()
    finally:
        db.close()


def _bundled_chat_url() -> str:
    return f"{base_url()}/chat/completions"


def _discover_bundled_model_id() -> str:
    fallback = os.getenv("BUNDLED_LLM_MODEL_ID", str(model_path()))
    try:
        import httpx

        r = httpx.get(f"{base_url()}/models", timeout=5)
        if r.status_code == 200:
            data = r.json().get("data") or []
            if data and data[0].get("id"):
                return str(data[0]["id"])
    except Exception:
        pass
    return fallback


def _migrate_stale_chat_targets(endpoint_id: str) -> None:
    """Move sessions and default settings off dead Docker Ollama endpoints."""
    import json
    from core.database import Session as ChatSession, SessionLocal

    chat_url = _bundled_chat_url()
    model_id = _discover_bundled_model_id()

    db = SessionLocal()
    try:
        stale_sessions = (
            db.query(ChatSession)
            .filter(
                (ChatSession.endpoint_url.like("%host.docker.internal%"))
                | (
                    ChatSession.endpoint_url.like("%127.0.0.1:11435%")
                    & (ChatSession.model != model_id)
                )
            )
            .all()
        )
        for sess in stale_sessions:
            sess.endpoint_url = chat_url
            sess.model = model_id
        for sess in stale_sessions:
            sess.mode = "chat"
        if stale_sessions:
            db.commit()
            logger.info("Migrated %d chat session(s) to bundled LLM", len(stale_sessions))
    except Exception as e:
        logger.warning("Failed to migrate stale chat sessions: %s", e)
        db.rollback()
    finally:
        db.close()

    settings_path = Path(__file__).resolve().parent.parent / "data" / "settings.json"
    if not settings_path.is_file():
        return
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        old_ep = str(settings.get("default_endpoint_id") or "")
        old_model = str(settings.get("default_model") or "")
        dockerish = (
            old_ep == "cca41b77"
            or "heretic" in old_model.lower()
            or "cipher" in old_model.lower()
            or "host.docker.internal" in old_model.lower()
        )
        if dockerish or not old_ep:
            settings["default_endpoint_id"] = endpoint_id
            settings["default_model"] = model_id
            settings_path.write_text(
                json.dumps(settings, indent=2) + "\n",
                encoding="utf-8",
            )
            logger.info("Updated default chat model to bundled LLM (%s)", model_id)
    except Exception as e:
        logger.warning("Failed to update default settings for bundled LLM: %s", e)


def _get_ready_lock() -> asyncio.Lock:
    global _ready_lock
    if _ready_lock is None:
        _ready_lock = asyncio.Lock()
    return _ready_lock


def _subprocess_env() -> dict[str, str]:
    """Child process env: ensure CUDA runtime DLLs are visible when GPU layers are used."""
    env = os.environ.copy()
    cuda_path = env.get("CUDA_PATH", "").strip()
    if not cuda_path:
        toolkit = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA")
        if toolkit.is_dir():
            versions = sorted(
                toolkit.glob("v*"),
                key=lambda p: p.name,
                reverse=True,
            )
            if versions:
                cuda_path = str(versions[0])
    if cuda_path:
        bin_dir = str(Path(cuda_path) / "bin")
        x64_dir = str(Path(bin_dir) / "x64")
        env["CUDA_PATH"] = cuda_path
        parts = [p for p in (x64_dir, bin_dir) if Path(p).is_dir()]
        if parts:
            env["PATH"] = f"{os.pathsep.join(parts)}{os.pathsep}{env.get('PATH', '')}"
        try:
            import llama_cpp

            llama_lib = str(Path(llama_cpp.__file__).resolve().parent / "lib")
            if Path(llama_lib).is_dir():
                env["PATH"] = f"{llama_lib}{os.pathsep}{env['PATH']}"
        except Exception:
            pass
    return env


def _resolve_n_gpu_layers() -> str:
    n_gpu_layers = os.getenv("BUNDLED_LLM_N_GPU_LAYERS", "").strip()
    if n_gpu_layers:
        return n_gpu_layers
    try:
        import llama_cpp.llama_cpp as _lc

        return "-1" if _lc.llama_supports_gpu_offload() else "0"
    except Exception:
        return "0"


def _pids_listening_on_port(port: int) -> list[int]:
    """Return PIDs listening on a TCP port (Windows netstat)."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        needle = f":{port}"
        pids: list[int] = []
        for line in result.stdout.splitlines():
            if "LISTENING" not in line or needle not in line:
                continue
            parts = line.split()
            if parts and parts[-1].isdigit():
                pids.append(int(parts[-1]))
        return list(dict.fromkeys(pids))
    except Exception as e:
        logger.warning("Failed to list PIDs on port %s: %s", port, e)
        return []


def _kill_orphaned_server_processes() -> None:
    """Stop leftover llama_cpp.server processes from prior NeatAi runs."""
    global _process
    keep_pid = _process.pid if _process is not None and _process.poll() is None else None
    killed: set[int] = set()
    try:
        if sys.platform == "win32":
            ps_cmd = (
                "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
                "Where-Object { $_.CommandLine -match 'llama_cpp.server' "
                f"-and $_.CommandLine -match '--port {BUNDLED_LLM_PORT}' }} | "
                "Select-Object -ExpandProperty ProcessId"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line.isdigit():
                    continue
                pid = int(line)
                if keep_pid is not None and pid == keep_pid:
                    continue
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F", "/T"],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                killed.add(pid)
                logger.info("Stopped orphaned bundled LLM process (pid=%s)", pid)
            for pid in _pids_listening_on_port(BUNDLED_LLM_PORT):
                if keep_pid is not None and pid == keep_pid:
                    continue
                if pid in killed:
                    continue
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F", "/T"],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                logger.info("Stopped process holding bundled port (pid=%s)", pid)
        if keep_pid is None:
            _process = None
    except Exception as e:
        logger.debug("Orphaned server cleanup: %s", e)


def _live_model_ids() -> list[str]:
    try:
        r = httpx.get(f"{base_url()}/models", timeout=5)
        if r.status_code == 200:
            data = r.json().get("data") or []
            return [str(m["id"]) for m in data if m.get("id")]
    except Exception:
        pass
    return []


def is_bundled_endpoint_url(url: str) -> bool:
    normalized = (url or "").rstrip("/")
    if normalized.endswith("/chat/completions"):
        normalized = normalized[: -len("/chat/completions")]
    return normalized == base_url().rstrip("/") or normalized.endswith(
        f":{BUNDLED_LLM_PORT}/v1"
    )


def sync_endpoint_models() -> list[str]:
    """Publish all installed bundled GGUF files to the endpoint model picker."""
    import json

    from core.database import SessionLocal, ModelEndpoint

    live = _live_model_ids()
    sep = "\\" if live and "\\" in live[0] else "/"
    model_ids: list[str] = []
    seen: set[str] = set()

    for mid in live:
        if mid not in seen:
            model_ids.append(mid)
            seen.add(mid)

    for path in sorted(models_dir().glob("*.gguf")):
        if not is_model_downloaded_at(path):
            continue
        mid = _model_id_for_path(path, separator=sep)
        if mid not in seen:
            model_ids.append(mid)
            seen.add(mid)

    if not model_ids:
        return []

    db = SessionLocal()
    try:
        ep = (
            db.query(ModelEndpoint)
            .filter(ModelEndpoint.base_url == base_url())
            .first()
        )
        if not ep:
            ep = (
                db.query(ModelEndpoint)
                .filter(ModelEndpoint.name == ENDPOINT_NAME)
                .first()
            )
        if not ep:
            return []
        ep.cached_models = json.dumps(model_ids)
        db.commit()
        logger.info("Synced %d bundled model(s) to endpoint picker", len(model_ids))
        return model_ids
    except Exception as e:
        logger.warning("Failed to sync bundled endpoint models: %s", e)
        db.rollback()
        return []
    finally:
        db.close()


def _invalidate_models_cache() -> None:
    try:
        from routes.model_routes import invalidate_models_cache

        invalidate_models_cache()
    except Exception:
        pass


def register_endpoint() -> bool:
    """Register the bundled endpoint in the DB if missing."""
    from core.database import SessionLocal, ModelEndpoint

    _disable_stale_docker_endpoints()
    url = base_url()
    db = SessionLocal()
    try:
        existing = db.query(ModelEndpoint).filter(ModelEndpoint.base_url == url).first()
        if existing:
            if not existing.is_enabled:
                existing.is_enabled = True
            if existing.supports_tools is None:
                existing.supports_tools = False
                db.commit()
            else:
                db.commit()
            sync_endpoint_models()
            _invalidate_models_cache()
            _migrate_stale_chat_targets(existing.id)
            return True
        ep = ModelEndpoint(
            id=str(uuid.uuid4())[:8],
            name=ENDPOINT_NAME,
            base_url=url,
            is_enabled=True,
            supports_tools=False,
        )
        db.add(ep)
        db.commit()
        logger.info("Auto-registered bundled LLM endpoint at %s", url)
        sync_endpoint_models()
        _invalidate_models_cache()
        _migrate_stale_chat_targets(ep.id)
        return True
    except Exception as e:
        logger.warning("Failed to register bundled LLM endpoint: %s", e)
        db.rollback()
        return False
    finally:
        db.close()


def _gguf_download_url(repo: str, filename: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/main/{filename}"


def download_gguf_sync(
    repo: str,
    filename: str,
    *,
    dest: Optional[Path] = None,
    on_progress: Optional[Callable[[int, str], None]] = None,
) -> Path:
    """Download a GGUF from Hugging Face (blocking, resumable)."""
    models_dir().mkdir(parents=True, exist_ok=True)
    dest = dest or (models_dir() / filename)
    if is_model_downloaded_at(dest):
        if on_progress:
            on_progress(100, "Model already downloaded")
        return dest

    tmp = dest.with_suffix(dest.suffix + ".part")
    if tmp.exists() and tmp.stat().st_size > 0:
        print(f"Resuming partial download ({tmp.stat().st_size // (1024 * 1024)} MB so far)...")

    try:
        import httpx
        from tqdm import tqdm

        headers = {}
        token = os.getenv("HF_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        resume_from = tmp.stat().st_size if tmp.exists() else 0
        req_headers = dict(headers)
        if resume_from > 0:
            req_headers["Range"] = f"bytes={resume_from}-"

        url = _gguf_download_url(repo, filename)
        timeout = httpx.Timeout(60.0, read=300.0)

        with httpx.stream("GET", url, headers=req_headers, follow_redirects=True, timeout=timeout) as resp:
            if resp.status_code == 416:
                tmp.unlink(missing_ok=True)
                return download_gguf_sync(
                    repo, filename, dest=dest, on_progress=on_progress
                )
            resp.raise_for_status()

            total = int(resp.headers.get("content-length", 0))
            if resp.status_code == 206:
                content_range = resp.headers.get("content-range", "")
                if "/" in content_range:
                    total = int(content_range.rsplit("/", 1)[-1]) - resume_from
            elif resume_from == 0:
                total = int(resp.headers.get("content-length", 0))

            mode = "ab" if resume_from > 0 and resp.status_code == 206 else "wb"
            if mode == "wb" and tmp.exists():
                tmp.unlink(missing_ok=True)
                resume_from = 0

            with open(tmp, mode) as out:
                bar = tqdm(
                    total=total or None,
                    initial=0,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=filename,
                )
                try:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        out.write(chunk)
                        bar.update(len(chunk))
                        if total:
                            pct = min(99, int((out.tell() / (resume_from + total)) * 100))
                            msg = f"Downloading... {pct}%"
                            if on_progress:
                                on_progress(pct, msg)
                finally:
                    bar.close()

        tmp.replace(dest)
        if on_progress:
            on_progress(100, "Model downloaded")
        return dest
    except Exception as e:
        if on_progress:
            on_progress(0, "Model download failed")
        raise


def download_model_sync() -> Path:
    """Download the active bundled GGUF (blocking). Used by install scripts."""
    active = get_active_model_config()
    dest = model_path()
    if is_model_downloaded():
        _set_status(state="ready", progress=100, message="Model already downloaded")
        return dest

    _set_status(
        state="downloading",
        progress=0,
        message=f"Downloading {active['file']} (one-time)...",
        error=None,
    )

    def on_progress(pct: int, message: str) -> None:
        _set_status(progress=pct, message=message)

    try:
        path = download_gguf_sync(
            active["repo"],
            active["file"],
            dest=dest,
            on_progress=on_progress,
        )
        _set_status(state="ready", progress=100, message="Model downloaded")
        return path
    except Exception as e:
        _set_status(state="error", error=str(e), message="Model download failed")
        raise


async def download_model() -> None:
    await asyncio.to_thread(download_model_sync)


async def start_server() -> bool:
    """Start llama-cpp-python server if the model is present."""
    global _process

    if not is_model_downloaded():
        _set_status(state="missing_model", message="Model not downloaded yet")
        return False

    expected_ctx = str(serving_n_ctx())
    marker_ctx = _read_serving_n_ctx_marker()
    owned = _process is not None and _process.poll() is None

    if (
        owned
        and marker_ctx == expected_ctx
        and await is_server_healthy()
    ):
        _set_status(state="running", message="Built-in AI is running")
        return True

    if await is_server_healthy() and marker_ctx != expected_ctx:
        logger.info(
            "Bundled LLM n_ctx changed (%s -> %s); recycling server",
            marker_ctx or "unknown",
            expected_ctx,
        )

    if _process is not None and _process.poll() is None:
        stop_server()

    _kill_orphaned_server_processes()
    await asyncio.sleep(1)

    if _process is not None and _process.poll() is None:
        for _ in range(BUNDLED_LLM_START_TIMEOUT):
            if await is_server_healthy():
                _set_status(state="running", message="Built-in AI is running")
                return True
            await asyncio.sleep(1)
        _set_status(state="error", error="Server process stuck", message="Failed to start")
        return False

    try:
        import llama_cpp  # noqa: F401
    except ImportError:
        _set_status(
            state="error",
            error="llama-cpp-python not installed",
            message="Run: pip install -r requirements-bundled.txt",
        )
        return False

    if await is_server_healthy() and marker_ctx == expected_ctx:
        _set_status(state="running", message="Built-in AI is running")
        register_endpoint()
        return True

    _set_status(state="starting", message="Starting built-in AI (first load may take 1-2 min)...")

    log_dir = Path(__file__).resolve().parent.parent / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "bundled-llm.log"

    n_threads = os.getenv("BUNDLED_LLM_N_THREADS", "").strip()
    if not n_threads:
        n_threads = str(max(4, (os.cpu_count() or 4) - 1))

    n_gpu_layers = _resolve_n_gpu_layers()
    gpu_attempts = [n_gpu_layers]
    if n_gpu_layers not in ("0", ""):
        gpu_attempts.append("0")

    for attempt_idx, layers in enumerate(gpu_attempts):
        if attempt_idx > 0:
            logger.warning(
                "Bundled LLM GPU start failed; retrying with n_gpu_layers=0 (CPU)"
            )
            _kill_orphaned_server_processes()
            _set_status(
                state="starting",
                message="Retrying built-in AI on CPU (GPU start failed)...",
            )

        cmd = [
            sys.executable,
            "-m",
            "llama_cpp.server",
            "--model",
            str(model_path()),
            "--host",
            BUNDLED_LLM_HOST,
            "--port",
            str(BUNDLED_LLM_PORT),
            "--n_ctx",
            os.getenv("BUNDLED_LLM_N_CTX", "8192"),
            "--n_threads",
            n_threads,
            "--n_gpu_layers",
            layers,
        ]

        try:
            log_file = open(log_path, "a", encoding="utf-8")
            log_file.write(
                f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"n_gpu_layers={layers} ---\n"
            )
            log_file.flush()
            _process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=_subprocess_env(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            _set_status(state="error", error=str(e), message="Failed to launch server")
            return False

        for _ in range(BUNDLED_LLM_START_TIMEOUT):
            if _process.poll() is not None:
                tail = ""
                try:
                    tail = log_path.read_text(encoding="utf-8", errors="replace")[-800:]
                except Exception:
                    pass
                if "0xc000001d" in tail:
                    logger.error(
                        "Bundled LLM illegal-instruction crash. "
                        "Use Python 3.12, llama-cpp-python==0.3.25, and run install-gpu.ps1 "
                        "(avoid 0.3.26; ensure CUDA Toolkit bin\\x64 is on PATH)."
                    )
                if attempt_idx + 1 < len(gpu_attempts):
                    break
                _set_status(
                    state="error",
                    error=tail[-200:] or "process exited",
                    message="Server crashed on start (see data/logs/bundled-llm.log)",
                )
                return False
            if await is_server_healthy():
                _set_status(state="running", message="Built-in AI is ready")
                _write_serving_n_ctx_marker()
                register_endpoint()
                return True
            await asyncio.sleep(1)
        else:
            _set_status(
                state="error",
                error="timeout",
                message=f"Server did not become ready within {BUNDLED_LLM_START_TIMEOUT}s",
            )
            return False

    return False


def stop_server() -> None:
    global _process
    if _process is not None and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            _process.kill()
    _process = None
    marker = _serving_n_ctx_marker()
    if marker.is_file():
        try:
            marker.unlink()
        except Exception:
            pass
    _set_status(state="stopped", message="Built-in AI stopped")


async def restart_server() -> bool:
    """Recycle bundled llama_cpp.server (e.g. after n_ctx change)."""
    stop_server()
    _kill_orphaned_server_processes()
    await asyncio.sleep(2)
    if _pids_listening_on_port(BUNDLED_LLM_PORT):
        logger.error(
            "Port %s still in use after restart attempt — "
            "end the old Python/llama process in Task Manager, then retry.",
            BUNDLED_LLM_PORT,
        )
        _set_status(
            state="error",
            error=f"Port {BUNDLED_LLM_PORT} in use",
            message="Stop the old built-in AI process and restart NeatAi",
        )
        return False
    return await start_server()


async def ensure_ready() -> bool:
    """Download (if allowed), start server, register endpoint — turnkey path."""
    if not is_enabled():
        return False

    async with _get_ready_lock():
        _disable_stale_docker_endpoints()

        auto_dl = os.getenv("BUNDLED_LLM_AUTO_DOWNLOAD", "1").strip().lower() not in (
            "0",
            "false",
            "no",
        )

        if not is_model_downloaded():
            if not auto_dl:
                _set_status(
                    state="missing_model",
                    message="Run install-turnkey.ps1 or scripts/download_default_model.py",
                )
                return False
            try:
                await download_model()
            except Exception:
                return False

        if not await start_server():
            return False

        register_endpoint()
        return True
