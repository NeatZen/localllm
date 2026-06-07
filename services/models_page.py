"""Models discovery page — hardware-fit recommendations + HF popular lists."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from services import model_hub
from services.hf_discover import fetch_hf_models


def _fit_level_for_vram(vram_gb: float, needed_gb: float) -> str:
    if vram_gb <= 0 or needed_gb <= 0:
        return "unknown"
    if vram_gb >= needed_gb * 1.2:
        return "perfect"
    if vram_gb >= needed_gb:
        return "good"
    return "marginal"


def _hardware_summary(system: dict[str, Any]) -> dict[str, Any]:
    gpus = system.get("gpus") or []
    return {
        "has_gpu": bool(system.get("has_gpu")),
        "gpu_name": system.get("gpu_name"),
        "gpu_vram_gb": system.get("gpu_vram_gb") or 0,
        "gpu_count": system.get("gpu_count") or 0,
        "available_ram_gb": system.get("available_ram_gb") or 0,
        "backend": system.get("backend") or "cpu",
        "gpus": [
            {"name": g.get("name"), "vram_gb": g.get("vram_gb")}
            for g in gpus[:4]
        ],
    }


def _rank_best_for_hardware(system: dict[str, Any], *, limit: int = 15) -> list[dict[str, Any]]:
    from services.hwfit.fit import rank_models
    from services.hwfit.models import get_models

    if not get_models():
        return []
    if system.get("error"):
        return []

    ranked = rank_models(system, limit=60, sort="score")
    out: list[dict[str, Any]] = []
    for row in ranked:
        if not row.get("gguf_sources"):
            continue
        if row.get("fit_level") == "too_tight":
            continue
        gguf = row["gguf_sources"][0] if row["gguf_sources"] else {}
        out.append(
            {
                "name": row.get("name"),
                "provider": row.get("provider"),
                "parameter_count": row.get("parameter_count"),
                "params_b": row.get("params_b"),
                "fit_level": row.get("fit_level"),
                "run_mode": row.get("run_mode"),
                "quant": row.get("quant"),
                "required_gb": row.get("required_gb"),
                "speed_tps": row.get("speed_tps"),
                "score": row.get("score"),
                "use_case": row.get("use_case"),
                "gguf_repo": gguf.get("repo") or "",
                "gguf_file": gguf.get("file") or "",
            }
        )
        if len(out) >= limit:
            break
    return out


def _bundled_quick_installs(system: dict[str, Any]) -> list[dict[str, Any]]:
    vram = float(system.get("gpu_vram_gb") or 0)
    hub = model_hub.get_hub_status()
    out: list[dict[str, Any]] = []
    for entry in hub.get("catalog") or []:
        needed = float(entry.get("vram_gb") or 0)
        out.append(
            {
                **entry,
                "fit_level": _fit_level_for_vram(vram, needed),
                "source": "bundled",
            }
        )
    return out


async def get_discover_data(*, fresh: bool = False) -> dict[str, Any]:
    from services.hwfit.hardware import detect_system

    system = deepcopy(detect_system(fresh=fresh))
    hardware = _hardware_summary(system)
    vram_gb = float(hardware.get("gpu_vram_gb") or 0)

    best_task = asyncio.to_thread(_rank_best_for_hardware, system)
    trending_task = fetch_hf_models(sort="trendingScore", vram_gb=vram_gb, limit=12)
    downloads_task = fetch_hf_models(sort="downloads", vram_gb=vram_gb, limit=12)

    best, trending_res, downloads_res = await asyncio.gather(
        best_task, trending_task, downloads_task
    )

    trending = trending_res.get("models") or []
    most_downloaded = downloads_res.get("models") or []

    if not trending and vram_gb > 0:
        fallback = await fetch_hf_models(sort="trendingScore", vram_gb=0, limit=12)
        trending = fallback.get("models") or []
    if not most_downloaded and vram_gb > 0:
        fallback = await fetch_hf_models(sort="downloads", vram_gb=0, limit=12)
        most_downloaded = fallback.get("models") or []

    hub_status = model_hub.get_hub_status()
    return {
        "hardware": hardware,
        "bundled": _bundled_quick_installs(system),
        "best_for_hardware": best,
        "trending": trending,
        "most_downloaded": most_downloaded,
        "installed": hub_status.get("installed") or [],
        "active": hub_status.get("active") or {},
        "errors": {
            "trending": trending_res.get("error"),
            "most_downloaded": downloads_res.get("error"),
            "hardware": system.get("error"),
        },
    }
