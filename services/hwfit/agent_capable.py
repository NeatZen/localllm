"""Heuristic: can this model run NeatAi agent mode (tool calling)?"""

from __future__ import annotations

from typing import Any, Dict

# Keep in sync with src/agent_loop.py model keyword list (+ common Ollama tags).
_AGENT_MODEL_KEYWORDS = (
    "deepseek", "gpt-4", "gpt-5", "gpt-o", "claude", "gemini",
    "qwen3", "qwen2.5", "qwen2.5-coder", "qwen-coder", "mixtral", "mistral",
    "llama-3.1", "llama-3.2", "llama-3.3", "llama-4", "llama3", "llama3.1", "llama3.2",
    "minimax", "kimi", "yi-", "phi-3", "phi-4", "command-r", "command_r",
    "glm-4", "internlm", "hermes", "coder", "tool-use", "functionary",
)


def is_agent_capable(model: Dict[str, Any]) -> bool:
    """True if catalog or name suggests tool/agent use is realistic."""
    if model.get("is_image_gen"):
        return False
    caps = model.get("capabilities") or []
    if "tool_use" in caps:
        return True
    parts = [
        model.get("name") or "",
        model.get("architecture") or "",
        model.get("use_case") or "",
    ]
    combined = " ".join(parts).lower()
    return any(kw in combined for kw in _AGENT_MODEL_KEYWORDS)


def agent_capable_label(model: Dict[str, Any]) -> str:
    if not is_agent_capable(model):
        return "chat"
    caps = model.get("capabilities") or []
    if "tool_use" in caps:
        return "native"
    return "likely"
