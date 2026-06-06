"""Model Download Hub — browse, download, and activate bundled GGUF models."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

from services import bundled_llm

logger = logging.getLogger(__name__)

CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "model-hub" / "catalog.json"
)

_downloads: dict[str, dict[str, Any]] = {}
_download_lock = asyncio.Lock()
_running: set[str] = set()


def _catalog_models() -> list[dict[str, Any]]:
    if not CATALOG_PATH.is_file():
        return []
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        models = data.get("models")
        return models if isinstance(models, list) else []
    except Exception as e:
        logger.warning("Failed to load model catalog: %s", e)
        return []


def get_catalog_entry(model_id: str) -> Optional[dict[str, Any]]:
    for entry in _catalog_models():
        if entry.get("id") == model_id:
            return entry
    return None


def list_installed() -> list[dict[str, Any]]:
    models_dir = bundled_llm.models_dir()
    if not models_dir.is_dir():
        return []
    active = bundled_llm.get_active_model_config()
    active_file = active.get("file", "")
    out: list[dict[str, Any]] = []
    for path in sorted(models_dir.glob("*.gguf")):
        if not bundled_llm.is_model_downloaded_at(path):
            continue
        out.append(
            {
                "file": path.name,
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "active": path.name == active_file,
            }
        )
    return out


def get_download_status(model_id: str) -> Optional[dict[str, Any]]:
    return _downloads.get(model_id)


def get_hub_status() -> dict[str, Any]:
    active = bundled_llm.get_active_model_config()
    bundled = bundled_llm.get_status()
    catalog = []
    installed_files = {item["file"] for item in list_installed()}
    active_file = active.get("file", "")

    for entry in _catalog_models():
        model_id = str(entry.get("id") or "")
        filename = str(entry.get("file") or "")
        dl = _downloads.get(model_id) or {}
        catalog.append(
            {
                **entry,
                "installed": filename in installed_files,
                "active": filename == active_file,
                "download": {
                    "state": dl.get("state", "idle"),
                    "progress": dl.get("progress", 0),
                    "message": dl.get("message", ""),
                    "error": dl.get("error"),
                },
            }
        )

    return {
        "enabled": bundled_llm.is_enabled(),
        "bundled": bundled,
        "active": active,
        "catalog": catalog,
        "installed": list_installed(),
    }


def _set_download_status(model_id: str, **kwargs: Any) -> None:
    current = dict(_downloads.get(model_id) or {})
    current.update(kwargs)
    _downloads[model_id] = current


def _download_sync(model_id: str, entry: dict[str, Any]) -> None:
    repo = str(entry["repo"])
    filename = str(entry["file"])
    dest = bundled_llm.models_dir() / filename

    def on_progress(pct: int, message: str) -> None:
        _set_download_status(
            model_id,
            state="downloading",
            progress=pct,
            message=message,
            error=None,
        )
        active = bundled_llm.get_active_model_config()
        if active.get("file") == filename:
            bundled_llm._set_status(
                state="downloading",
                progress=pct,
                message=message,
                error=None,
            )

    try:
        if bundled_llm.is_model_downloaded_at(dest):
            _set_download_status(
                model_id,
                state="ready",
                progress=100,
                message="Already downloaded",
                error=None,
            )
            return

        _set_download_status(
            model_id,
            state="downloading",
            progress=0,
            message=f"Downloading {filename}...",
            error=None,
        )
        bundled_llm.download_gguf_sync(repo, filename, dest=dest, on_progress=on_progress)
        _set_download_status(
            model_id,
            state="ready",
            progress=100,
            message="Download complete",
            error=None,
        )
    except Exception as e:
        logger.exception("Model hub download failed for %s", model_id)
        _set_download_status(
            model_id,
            state="error",
            progress=0,
            message="Download failed",
            error=str(e),
        )
        active = bundled_llm.get_active_model_config()
        if active.get("file") == filename:
            bundled_llm._set_status(state="error", error=str(e), message="Model download failed")


async def start_download(model_id: str) -> dict[str, Any]:
    entry = get_catalog_entry(model_id)
    if not entry:
        return {"ok": False, "error": f"Unknown model: {model_id}"}

    if not bundled_llm.is_enabled():
        return {"ok": False, "error": "Built-in AI is disabled"}

    async with _download_lock:
        if model_id in _running:
            return {"ok": True, "message": "Download already in progress", **get_hub_status()}
        _running.add(model_id)

    try:
        await asyncio.to_thread(_download_sync, model_id, entry)
        return {"ok": True, **get_hub_status()}
    finally:
        _running.discard(model_id)


async def activate_model(model_id: str) -> dict[str, Any]:
    entry = get_catalog_entry(model_id)
    if not entry:
        return {"ok": False, "error": f"Unknown model: {model_id}"}

    if not bundled_llm.is_enabled():
        return {"ok": False, "error": "Built-in AI is disabled"}

    filename = str(entry["file"])
    dest = bundled_llm.models_dir() / filename
    if not bundled_llm.is_model_downloaded_at(dest):
        return {"ok": False, "error": "Model not downloaded yet"}

    bundled_llm.set_active_model_config(
        model_id=model_id,
        repo=str(entry["repo"]),
        file=filename,
    )

    bundled_llm.stop_server()
    ok = await bundled_llm.start_server()
    if ok:
        bundled_llm.register_endpoint()

    return {
        "ok": ok,
        "active": bundled_llm.get_active_model_config(),
        **bundled_llm.get_status(),
    }
