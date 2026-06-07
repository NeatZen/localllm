"""Path sandbox and filesystem helpers for Agent Workspace projects."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.constants import DATA_DIR

WORKSPACES_ROOT = os.path.join(DATA_DIR, "workspaces")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass
class WorkspaceContext:
    project_id: str
    owner: str
    root_path: Path
    session_id: Optional[str] = None
    project_name: str = ""
    slug: str = ""
    plan_json: Optional[Dict[str, Any]] = None


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", (name or "project").strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return (s[:64] or "project")


def validate_slug(slug: str) -> str:
    slug = slugify(slug) if slug else "project"
    if not _SLUG_RE.match(slug):
        raise ValueError("Invalid project slug")
    return slug


def project_root_for(owner: str, slug: str) -> Path:
    safe_owner = re.sub(r"[^a-zA-Z0-9_@.-]+", "_", owner or "default")
    return Path(WORKSPACES_ROOT) / safe_owner / slug


def resolve_workspace_path(ctx: WorkspaceContext, user_path: str) -> Path:
    """Resolve a user/agent path strictly inside the project root."""
    if not user_path or not str(user_path).strip():
        raise ValueError("Path is required")
    raw = str(user_path).strip().replace("\\", "/")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        candidate = Path(raw)
    else:
        candidate = (ctx.root_path / raw).resolve()
    root = ctx.root_path.resolve()
    try:
        candidate.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {user_path}") from exc
    return candidate


def read_text_file(path: Path, max_chars: int = 200_000) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        data = f.read(max_chars + 1)
    if len(data) > max_chars:
        return data[:max_chars] + f"\n... [truncated at {max_chars} chars]"
    return data


def build_tree(root: Path, *, max_depth: int = 8, _depth: int = 0) -> List[Dict[str, Any]]:
    if _depth > max_depth or not root.exists():
        return []
    items: List[Dict[str, Any]] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        rel = entry.relative_to(root).as_posix()
        if entry.is_dir():
            items.append({
                "name": entry.name,
                "path": rel,
                "type": "dir",
                "children": build_tree(entry, max_depth=max_depth, _depth=_depth + 1),
            })
        else:
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            items.append({"name": entry.name, "path": rel, "type": "file", "size": size})
    return items


def make_diff_preview(old: Optional[str], new: Optional[str], limit: int = 4000) -> str:
    if old is None or old == "":
        preview = new or ""
        prefix = "+ "
    else:
        preview = new or ""
        prefix = "~ "
    lines = (preview or "").splitlines()
    out = "\n".join(prefix + ln for ln in lines[:80])
    if len(out) > limit:
        out = out[:limit] + "\n..."
    return out


def parse_test_output(text: str) -> Dict[str, Any]:
    """Heuristic PASS/FAIL summary from common test runners (Phase 2)."""
    if not text:
        return {"summary": "No output", "pass": 0, "fail": 0, "items": []}
    items: List[Dict[str, str]] = []
    for line in text.splitlines():
        m = re.match(r"^\s*(PASS|FAIL|OK|FAILED)\s+(.+)$", line.strip(), re.I)
        if m:
            status = m.group(1).upper()
            name = m.group(2).strip()
            norm = "PASS" if status in ("PASS", "OK") else "FAIL"
            items.append({"name": name, "status": norm})
    pass_n = sum(1 for i in items if i["status"] == "PASS")
    fail_n = sum(1 for i in items if i["status"] == "FAIL")
    if not items:
        if re.search(r"\b0 failed\b|\bAll tests passed\b", text, re.I):
            return {"summary": "PASS (all tests passed)", "pass": 1, "fail": 0, "items": []}
        if re.search(r"\bfailed\b|\bFAIL\b|\berror\b", text, re.I):
            return {"summary": "FAIL (errors detected)", "pass": 0, "fail": 1, "items": []}
        return {"summary": "Completed", "pass": 0, "fail": 0, "items": []}
    summary = f"PASS {pass_n}, FAIL {fail_n}"
    return {"summary": summary, "pass": pass_n, "fail": fail_n, "items": items}
