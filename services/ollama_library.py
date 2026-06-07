"""Search the public Ollama model library (ollama.com/search).

Ollama does not expose an official registry search API; we parse the public
search/library HTML the same way the site renders it.
"""

from __future__ import annotations

import html
import math
import re
import time
from typing import Any

_SEARCH_URL = "https://ollama.com/search"
_LIBRARY_URL = "https://ollama.com/library"
_USER_AGENT = "Odysseus/1.0 (+https://github.com/odysseus)"
_SIZE_TAG_RE = re.compile(r"^(\d+(?:\.\d+)?[bB]|latest)$")
_CAP_TAG_RE = re.compile(r"^(vision|tools|thinking|cloud|embedding)$", re.I)
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_TTL = 120.0
# Typical Ollama pull quant (Q4-class) plus KV headroom multiplier.
_VRAM_BYTES_PER_PARAM = 0.55
_VRAM_HEADROOM = 1.3
_VRAM_BASE_GB = 1.2
# Curated searches merged for hardware-fit picks (popular page alone is too small).
_HARDWARE_POOL_QUERIES = (
    "",
    "qwen",
    "llama",
    "deepseek",
    "mistral",
    "gemma",
    "phi",
    "codestral",
    "nemotron",
    "glm",
)
_SKIP_HARDWARE_NAMES = (
    "guard",
    "embed",
    "embedding",
    "rerank",
    "translat",
    "bge-",
    "nomic-embed",
)


def parse_pulls_count(text: str) -> float:
    """Parse ``32.1M Pulls`` / ``1,701 Pulls`` into a numeric count."""
    s = (text or "").strip().replace(",", "")
    if not s:
        return 0.0
    m = re.match(r"^([\d.]+)\s*([KMB])?\s*Pulls?$", s, re.I)
    if m:
        val = float(m.group(1))
        mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get((m.group(2) or "").upper(), 1.0)
        return val * mult
    m = re.match(r"^([\d.]+)\s*([KMB])?$", s, re.I)
    if not m:
        return 0.0
    val = float(m.group(1))
    mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get((m.group(2) or "").upper(), 1.0)
    return val * mult


def params_from_size_tag(tag: str) -> float | None:
    if not tag or tag.lower() == "latest":
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)[bB]$", tag.strip())
    return float(m.group(1)) if m else None


def estimate_vram_gb(params_b: float) -> float:
    return round(params_b * _VRAM_BYTES_PER_PARAM * _VRAM_HEADROOM + _VRAM_BASE_GB, 1)


def fit_level(needed_gb: float | None, vram_gb: float) -> str:
    if vram_gb <= 0 or needed_gb is None:
        return "unknown"
    ratio = needed_gb / vram_gb
    if ratio <= 0.72:
        return "perfect"
    if ratio <= 0.92:
        return "good"
    if ratio <= 1.05:
        return "tight"
    return "no_fit"


def pick_best_size_tag(size_tags: list[str], vram_gb: float) -> tuple[str | None, float | None, list[str]]:
    """Return best tag, its estimated VRAM, and all tags that fit."""
    size_tags = _sanitize_size_tags(size_tags)
    numeric: list[tuple[str, float, float]] = []
    for tag in size_tags or []:
        params = params_from_size_tag(tag)
        if params is None:
            continue
        need = estimate_vram_gb(params)
        numeric.append((tag.lower(), params, need))

    fitting = [t for t, _, need in numeric if vram_gb <= 0 or need <= vram_gb * 1.05]
    if numeric and vram_gb > 0:
        candidates = [(t, need) for t, _, need in numeric if need <= vram_gb * 1.05]
        if candidates:
            best_tag, best_need = max(candidates, key=lambda x: x[1])
            return best_tag, best_need, fitting

    if "latest" in [t.lower() for t in (size_tags or [])]:
        return "latest", None, fitting or ["latest"]
    if fitting:
        best = max(fitting, key=lambda t: params_from_size_tag(t) or 0)
        pb = params_from_size_tag(best)
        return best, estimate_vram_gb(pb) if pb else None, fitting
    if numeric and vram_gb <= 0:
        best_tag, _, best_need = max(numeric, key=lambda x: x[1])
        return best_tag, best_need, [best_tag]
    return None, None, fitting


def _sanitize_size_tags(size_tags: list[str]) -> list[str]:
    """Drop MoE 'active parameter' tags scraped from descriptions (e.g. 12b vs 120b)."""
    tags = [t.lower() for t in (size_tags or []) if t]
    numeric: list[tuple[str, float]] = []
    for tag in tags:
        params = params_from_size_tag(tag)
        if params is not None:
            numeric.append((tag, params))

    if len(numeric) >= 2:
        max_pb = max(p for _, p in numeric)
        min_pb = min(p for _, p in numeric)
        cutoff = max(max_pb / 8.0, min_pb * 2.0)
        if max_pb / max(min_pb, 0.1) >= 8.0:
            numeric = [(t, p) for t, p in numeric if p >= cutoff]

    keep = {t for t, _ in numeric}
    out: list[str] = []
    for tag in tags:
        if tag in keep or params_from_size_tag(tag) is None:
            out.append(tag)
    return out or tags


def enrich_ollama_model(model: dict[str, Any], vram_gb: float) -> dict[str, Any] | None:
    caps = [c.lower() for c in (model.get("capabilities") or [])]
    size_tags = _sanitize_size_tags(model.get("size_tags") or [])

    # Cloud-only listings have no local size tags to pull.
    if "cloud" in caps and not any(params_from_size_tag(t) for t in size_tags):
        return None

    best_tag, needed_gb, fitting_tags = pick_best_size_tag(size_tags, vram_gb)
    if vram_gb > 0 and not fitting_tags and best_tag is None:
        return None

    name = model.get("name") or ""
    if best_tag and best_tag != "latest":
        recommended_ref = f"{name}:{best_tag}"
    else:
        recommended_ref = name

    level = fit_level(needed_gb, vram_gb)
    if vram_gb > 0 and level == "no_fit":
        return None

    pulls_num = parse_pulls_count(model.get("pulls") or "")
    params_b = params_from_size_tag(best_tag or "") if best_tag else None
    score = _hardware_score(
        name=name,
        params_b=params_b,
        needed_gb=needed_gb,
        vram_gb=vram_gb,
        fit_level=level,
        pulls_num=pulls_num,
        capabilities=caps,
    )

    out = dict(model)
    out.update(
        {
            "recommended_tag": best_tag or "",
            "recommended_ref": recommended_ref,
            "needed_vram_gb": needed_gb,
            "fit_level": level,
            "fitting_tags": fitting_tags,
            "pulls_num": pulls_num,
            "score": round(score, 3),
        }
    )
    return out


def _hardware_score(
    *,
    name: str,
    params_b: float | None,
    needed_gb: float | None,
    vram_gb: float,
    fit_level: str,
    pulls_num: float,
    capabilities: list[str],
) -> float:
    """Prefer the largest high-quality model that fits, then popularity."""
    need = needed_gb or 0.0
    fit_mult = {"perfect": 1.0, "good": 1.0, "tight": 0.82, "unknown": 0.65}.get(fit_level, 0.4)
    size_pts = need if vram_gb > 0 else (params_b or 0.0) * 0.55

    pop_pts = min(math.log10(pulls_num + 10.0) * 4.0, 24.0)

    name_l = (name or "").lower()
    brand_pts = 0.0
    if any(k in name_l for k in ("qwen", "deepseek", "llama3", "mistral", "gemma", "phi", "codestral", "glm")):
        brand_pts = 3.0

    cap_pts = 0.0
    if "tools" in capabilities:
        cap_pts += 2.5
    if "thinking" in capabilities:
        cap_pts += 1.5

    return round(size_pts * fit_mult + pop_pts + brand_pts + cap_pts, 3)


def _skip_hardware_model(name: str, capabilities: list[str]) -> bool:
    caps = [c.lower() for c in (capabilities or [])]
    if caps == ["embedding"] or (len(caps) == 1 and caps[0] == "embedding"):
        return True
    name_l = (name or "").lower()
    return any(s in name_l for s in _SKIP_HARDWARE_NAMES)


def build_ollama_hardware_pool(per_query: int = 15) -> list[dict[str, Any]]:
    """Merge curated Ollama search results into one deduped pool."""
    per_query = max(5, min(int(per_query or 15), 30))
    seen: set[str] = set()
    pool: list[dict[str, Any]] = []
    for query in _HARDWARE_POOL_QUERIES:
        for model in search_ollama_models(query, per_query):
            slug = model.get("name") or ""
            if not slug or slug in seen:
                continue
            seen.add(slug)
            pool.append(model)
    return pool


def rank_ollama_for_hardware(
    models: list[dict[str, Any]],
    vram_gb: float = 0,
    limit: int = 10,
    *,
    agent_only: bool = False,
    skip_embedding: bool = True,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for model in models:
        name = model.get("name") or ""
        caps = [c.lower() for c in (model.get("capabilities") or [])]
        if agent_only and "tools" not in caps:
            continue
        if skip_embedding and _skip_hardware_model(name, caps):
            continue
        row = enrich_ollama_model(model, vram_gb)
        if row:
            enriched.append(row)
    enriched.sort(key=lambda m: (m.get("score", 0), m.get("pulls_num", 0)), reverse=True)
    return enriched[: max(1, min(int(limit or 10), 50))]


def _fetch(url: str, timeout: float = 20.0) -> str:
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": _USER_AGENT}) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text


def _parse_search_cards(page_html: str, limit: int) -> list[dict[str, Any]]:
    blocks = re.split(r'href="/library/([^"]+)"', page_html)
    models: list[dict[str, Any]] = []
    seen: set[str] = set()

    for i in range(1, len(blocks), 2):
        slug = blocks[i].strip()
        if not slug or slug in seen:
            continue
        chunk = blocks[i + 1][:3000] if i + 1 < len(blocks) else ""
        desc_m = re.search(r"<p[^>]*>([^<]{12,400})</p>", chunk)
        pulls_m = re.search(r"([\d,.]+[KMB]?)\s*Pulls", chunk, re.I)
        raw_tags = re.findall(
            r"\b(\d+(?:\.\d+)?[bB]|latest|cloud|vision|tools|thinking|embedding)\b",
            chunk,
            re.I,
        )
        size_tags: list[str] = []
        caps: list[str] = []
        for t in raw_tags:
            tl = t.lower()
            if _SIZE_TAG_RE.match(tl) and tl not in size_tags:
                size_tags.append(tl)
            elif _CAP_TAG_RE.match(tl) and tl not in caps:
                caps.append(tl)

        models.append(
            {
                "name": slug,
                "model_ref": slug if "latest" in size_tags else (f"{slug}:{size_tags[0]}" if size_tags else slug),
                "description": html.unescape(desc_m.group(1).strip()) if desc_m else "",
                "pulls": pulls_m.group(1) if pulls_m else "",
                "pulls_num": parse_pulls_count(pulls_m.group(1) if pulls_m else ""),
                "size_tags": size_tags[:16],
                "capabilities": caps[:8],
                "url": f"{_LIBRARY_URL}/{slug}",
            }
        )
        seen.add(slug)
        if len(models) >= limit:
            break
    return models


def search_ollama_models(query: str = "", limit: int = 15) -> list[dict[str, Any]]:
    """Return models from ollama.com/search matching *query* (empty = popular)."""
    from urllib.parse import quote_plus

    limit = max(1, min(int(limit or 15), 50))
    q = (query or "").strip()
    cache_key = f"{q.lower()}|{limit}"
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    url = f"{_SEARCH_URL}?q={quote_plus(q)}" if q else _SEARCH_URL
    page = _fetch(url)
    models = _parse_search_cards(page, limit)
    _CACHE[cache_key] = (now, models)
    return models


def normalize_ollama_model_ref(raw: str) -> str:
    """Accept ``model``, ``model:tag``, or an ollama.com/library URL."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty model reference")
    text = re.sub(r"^ollama:\s*", "", text, flags=re.I)
    url_m = re.match(r"^https?://(?:www\.)?ollama\.com/library/([^/?#]+)", text, re.I)
    if url_m:
        text = url_m.group(1)
    pull_m = re.match(r"^ollama\s+pull\s+(\S+)", text, re.I)
    if pull_m:
        text = pull_m.group(1)
    return text.rstrip("/")
