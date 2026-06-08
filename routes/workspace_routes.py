"""Agent Workspace API — projects, file tree, approvals, activity."""

import logging
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.database import SessionLocal, Session, WorkspaceProject
from core.middleware import require_admin
from src.auth_helpers import get_current_user
from src.workspace_sandbox import WorkspaceContext
from src.workspace_service import (
    approve_all_pending,
    apply_change,
    bind_session_to_project,
    create_project,
    create_project_folder,
    delete_project,
    delete_project_path,
    ensure_workspace_session,
    get_file_tree,
    get_plan,
    get_project_for_owner,
    get_workspace_context,
    list_activity,
    list_pending_changes,
    list_projects,
    propose_command,
    read_project_file,
    reject_change,
    save_plan,
    write_project_file,
)

logger = logging.getLogger(__name__)


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    slug: Optional[str] = None


class ProjectBindSession(BaseModel):
    session_id: str


class ChangeReject(BaseModel):
    reason: Optional[str] = None


class PlanSave(BaseModel):
    plan: dict


class BrowserTestRequest(BaseModel):
    url: str


class ProposeCommandRequest(BaseModel):
    command: str = Field(..., min_length=1)
    summary: Optional[str] = None


class FileWriteRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=512)
    content: str = ""


class FolderCreateRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=512)


def setup_workspace_routes() -> APIRouter:
    router = APIRouter()

    def _owner(request: Request) -> str:
        require_admin(request)
        return get_current_user(request) or ""

    @router.get("/api/workspace/projects")
    async def api_list_projects(request: Request):
        owner = _owner(request)
        return {"projects": list_projects(owner)}

    @router.post("/api/workspace/projects")
    async def api_create_project(request: Request, body: ProjectCreate):
        owner = _owner(request)
        try:
            proj = create_project(owner, body.name, body.slug)
            return {"ok": True, "project": proj}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.delete("/api/workspace/projects/{project_id}")
    async def api_delete_project(request: Request, project_id: str):
        owner = _owner(request)
        if not delete_project(project_id, owner):
            raise HTTPException(404, "Project not found")
        return {"ok": True}

    @router.get("/api/workspace/projects/{project_id}/tree")
    async def api_project_tree(request: Request, project_id: str):
        owner = _owner(request)
        if not get_project_for_owner(project_id, owner):
            raise HTTPException(404, "Project not found")
        return {"tree": get_file_tree(project_id, owner)}

    @router.get("/api/workspace/projects/{project_id}/file")
    async def api_project_file(request: Request, project_id: str, path: str):
        owner = _owner(request)
        try:
            return read_project_file(project_id, owner, path)
        except FileNotFoundError:
            raise HTTPException(404, "File not found")
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.put("/api/workspace/projects/{project_id}/file")
    async def api_save_project_file(request: Request, project_id: str, body: FileWriteRequest):
        owner = _owner(request)
        try:
            return {"ok": True, **write_project_file(project_id, owner, body.path, body.content)}
        except FileNotFoundError:
            raise HTTPException(404, "Project not found")
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/api/workspace/projects/{project_id}/folder")
    async def api_create_project_folder(request: Request, project_id: str, body: FolderCreateRequest):
        owner = _owner(request)
        try:
            return {"ok": True, **create_project_folder(project_id, owner, body.path)}
        except FileNotFoundError:
            raise HTTPException(404, "Project not found")
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.delete("/api/workspace/projects/{project_id}/file")
    async def api_delete_project_file(request: Request, project_id: str, path: str):
        owner = _owner(request)
        try:
            return {"ok": True, **delete_project_path(project_id, owner, path)}
        except FileNotFoundError:
            raise HTTPException(404, "Not found")
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.get("/api/workspace/projects/{project_id}/changes")
    async def api_pending_changes(request: Request, project_id: str):
        owner = _owner(request)
        return {"changes": list_pending_changes(project_id, owner)}

    @router.post("/api/workspace/changes/{change_id}/approve")
    async def api_approve_change(request: Request, change_id: str):
        owner = _owner(request)
        try:
            return {"ok": True, "change": apply_change(change_id, owner)}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/api/workspace/changes/{change_id}/reject")
    async def api_reject_change(request: Request, change_id: str, body: ChangeReject):
        owner = _owner(request)
        try:
            return {"ok": True, "change": reject_change(change_id, owner, body.reason)}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/api/workspace/projects/{project_id}/approve-all")
    async def api_approve_all(request: Request, project_id: str):
        owner = _owner(request)
        applied = approve_all_pending(project_id, owner)
        return {"ok": True, "applied": applied}

    @router.get("/api/workspace/projects/{project_id}/activity")
    async def api_activity(request: Request, project_id: str, limit: int = 100):
        owner = _owner(request)
        return {"activity": list_activity(project_id, owner, limit=min(limit, 500))}

    @router.post("/api/workspace/projects/{project_id}/bind-session")
    async def api_bind_session(request: Request, project_id: str, body: ProjectBindSession):
        owner = _owner(request)
        proj = bind_session_to_project(body.session_id, project_id, owner)
        if not proj:
            raise HTTPException(404, "Project or session not found")
        return {"ok": True, "project": proj}

    @router.post("/api/workspace/projects/{project_id}/ensure-session")
    async def api_ensure_session(request: Request, project_id: str):
        owner = _owner(request)
        try:
            result = ensure_workspace_session(project_id, owner)
            return {"ok": True, **result}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.get("/api/workspace/projects/{project_id}/plan")
    async def api_get_plan(request: Request, project_id: str):
        owner = _owner(request)
        return {"plan": get_plan(project_id, owner)}

    @router.post("/api/workspace/projects/{project_id}/plan")
    async def api_save_plan(request: Request, project_id: str, body: PlanSave):
        owner = _owner(request)
        try:
            return {"ok": True, "project": save_plan(project_id, owner, body.plan)}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/api/workspace/projects/{project_id}/browser-test")
    async def api_browser_test(request: Request, project_id: str, body: BrowserTestRequest):
        """Phase 3: open a localhost URL via Playwright MCP when available."""
        owner = _owner(request)
        _owner(request)
        url = (body.url or "").strip()
        if not url.startswith("http://127.0.0.1") and not url.startswith("http://localhost"):
            raise HTTPException(400, "Only localhost URLs are allowed for workspace browser tests")
        try:
            from src import agent_tools
            mcp = agent_tools.get_mcp_manager()
            if not mcp:
                return {"ok": False, "error": "MCP not available — enable browser in MCP settings"}
            result = await mcp.call_tool(
                "mcp__builtin_browser__browser_navigate",
                {"url": url},
            )
            from src.workspace_service import get_project_for_owner, log_activity
            log_activity(project_id, "browser", f"Navigated to {url}", meta={"result": str(result)[:2000]})
            return {"ok": True, "result": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @router.post("/api/workspace/projects/{project_id}/propose-command")
    async def api_propose_command(request: Request, project_id: str, body: ProposeCommandRequest):
        """Queue a shell command for user approval (UI shortcuts)."""
        owner = _owner(request)
        proj = get_project_for_owner(project_id, owner)
        if not proj:
            raise HTTPException(404, "Project not found")
        ctx = get_workspace_context(proj.session_id) if proj.session_id else None
        if not ctx:
            ctx = WorkspaceContext(
                project_id=proj.id,
                owner=owner,
                root_path=Path(proj.root_path),
                project_name=proj.name or "",
                slug=proj.slug or "",
            )
        try:
            change = propose_command(ctx, body.command, body.summary)
            return {"ok": True, "change": change}
        except ValueError as e:
            raise HTTPException(400, str(e))

    return router
