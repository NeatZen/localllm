"""Hugging Face model discovery — trending and most-downloaded lists."""

from __future__ import annotations

import re
from typing import Any

import httpx

EXCLUDE_TAG_SUBSTRINGS = (
    "lora",
    "adapter",
    "peft",
    "qlora",
    "dataset",
    "embeddings",
    "merge",
    "control-lora",
    "diffusion-lora",
    "stable-diffusion-lora",
    "text-classification",
    "token-classification",
    "feature-extraction",
    "sentence-similarity",
)
EXCLUDE_NAME_SUBSTRINGS = (
    "lora",
    "adapter",
    "peft",
    "qlora",
    "embedding",
    "embed-",
    "dataset",
)


def _est_vram_fp16(repo_id: str) -> float | None:
    m = re.search(r"[-_/](\d+(?:\.\d+)?)\s*[Bb](?![a-zA-Z])", repo_id)
    if not m:
        return None
    return float(m.group(1)) * 2.0


def _quant_factor(repo_id: str, tags: list) -> float:
    text = (repo_id + " " + " ".join(tags or [])).lower()
    if any(
        k in text
        for k in ("fp4", "nf4", "int4", "4bit", "q4", "awq", "gptq")
    ):
        return 0.25
    if any(k in text for k in ("int8", "8bit", "q8", "fp8")):
        return 0.5
    if "bf16" in text or "fp16" in text:
        return 1.0
    return 1.0


def _is_excluded(repo_id: str, tags: list) -> bool:
    text = repo_id.lower()
    for s in EXCLUDE_NAME_SUBSTRINGS:
        if s in text:
            return True
    tag_text = " ".join(t.lower() for t in (tags or []))
    for s in EXCLUDE_TAG_SUBSTRINGS:
        if s in tag_text:
            return True
    return False


def _parse_hf_entry(entry: dict[str, Any], *, vram_gb: float) -> dict[str, Any] | None:
    repo_id = entry.get("modelId") or entry.get("id") or ""
    if not repo_id:
        return None
    tags = entry.get("tags") or []
    pipeline_tag = entry.get("pipeline_tag") or ""
    if pipeline_tag and pipeline_tag != "text-generation":
        return None
    if _is_excluded(repo_id, tags):
        return None

    est_fp16 = _est_vram_fp16(repo_id)
    quant_mult = _quant_factor(repo_id, tags)
    est_vram = (est_fp16 * quant_mult) if est_fp16 else None
    needed_vram = (est_vram * 1.3) if est_vram else None
    if vram_gb > 0 and needed_vram is not None and needed_vram > vram_gb:
        return None
    if est_vram is None:
        return None

    return {
        "repo_id": repo_id,
        "downloads": entry.get("downloads", 0),
        "likes": entry.get("likes", 0),
        "createdAt": entry.get("createdAt", ""),
        "tags": tags[:5],
        "pipeline_tag": pipeline_tag,
        "est_vram_gb": round(est_vram, 1) if est_vram else None,
        "needed_vram_gb": round(needed_vram, 1) if needed_vram else None,
    }


async def fetch_hf_models(
    *,
    sort: str = "trendingScore",
    vram_gb: float = 0,
    limit: int = 12,
    pipeline: str = "text-generation",
) -> dict[str, Any]:
    """Fetch filtered HF models. sort: trendingScore | downloads."""
    sort_key = "downloads" if sort == "downloads" else "trendingScore"
    pool_size = max(limit * 15, 100)
    url = (
        "https://huggingface.co/api/models"
        f"?sort={sort_key}&direction=-1&limit={pool_size}&filter={pipeline}"
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {"models": [], "error": f"HF API HTTP {resp.status_code}"}
            raw = resp.json()
    except Exception as e:
        return {"models": [], "error": str(e)}

    out: list[dict[str, Any]] = []
    for entry in raw:
        parsed = _parse_hf_entry(entry, vram_gb=vram_gb)
        if not parsed:
            continue
        out.append(parsed)
        if len(out) >= limit:
            break
    return {"models": out}
