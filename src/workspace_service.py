"""Business logic for Agent Workspace projects, changes, and activity."""

from __future__ import annotations

import asyncio
import ast
import difflib
import logging
import os
import re
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.database import SessionLocal, Session, WorkspaceProject, WorkspaceChange, WorkspaceActivity
from src.workspace_sandbox import (
    WorkspaceContext,
    build_tree,
    make_diff_preview,
    parse_test_output,
    project_root_for,
    read_text_file,
    resolve_workspace_path,
    slugify,
    validate_slug,
    WORKSPACES_ROOT,
)

logger = logging.getLogger(__name__)

# Commands run in a background thread after approval so the HTTP handler returns
# immediately. Long-running or interactive shells would otherwise freeze the UI.
RUN_COMMAND_TIMEOUT = int(os.environ.get("NEATAI_WORKSPACE_CMD_TIMEOUT", "300"))


def get_workspace_context(session_id: Optional[str]) -> Optional[WorkspaceContext]:
    if not session_id:
        return None
    db = SessionLocal()
    try:
        sess = db.query(Session).filter(Session.id == session_id).first()
        proj = None
        if sess and sess.workspace_project_id:
            proj = db.query(WorkspaceProject).filter(
                WorkspaceProject.id == sess.workspace_project_id
            ).first()
        if not proj:
            # Reverse link: project.session_id when session row lacks workspace_project_id
            proj = db.query(WorkspaceProject).filter(
                WorkspaceProject.session_id == session_id
            ).first()
            if proj and sess and not sess.workspace_project_id:
                sess.workspace_project_id = proj.id
                try:
                    db.commit()
                except Exception:
                    db.rollback()
        if not proj:
            return None
        root = Path(proj.root_path)
        if not root.is_dir():
            return None
        plan = None
        if proj.plan_json:
            try:
                import json
                plan = json.loads(proj.plan_json) if isinstance(proj.plan_json, str) else proj.plan_json
            except Exception:
                plan = None
        return WorkspaceContext(
            project_id=proj.id,
            owner=proj.owner or "",
            root_path=root,
            session_id=session_id,
            project_name=proj.name or "",
            slug=proj.slug or "",
            plan_json=plan,
        )
    finally:
        db.close()


def get_project_for_owner(project_id: str, owner: str) -> Optional[WorkspaceProject]:
    db = SessionLocal()
    try:
        q = db.query(WorkspaceProject).filter(WorkspaceProject.id == project_id)
        if owner:
            q = q.filter(WorkspaceProject.owner == owner)
        return q.first()
    finally:
        db.close()


def list_projects(owner: str) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = (
            db.query(WorkspaceProject)
            .filter(WorkspaceProject.owner == owner)
            .order_by(WorkspaceProject.updated_at.desc())
            .all()
        )
        return [r.to_dict() for r in rows]
    finally:
        db.close()


def create_project(owner: str, name: str, slug: Optional[str] = None) -> Dict[str, Any]:
    name = (name or "Untitled Project").strip()[:120]
    base_slug = validate_slug(slug or slugify(name))
    db = SessionLocal()
    try:
        slug_candidate = base_slug
        n = 1
        while db.query(WorkspaceProject).filter(
            WorkspaceProject.owner == owner,
            WorkspaceProject.slug == slug_candidate,
        ).first():
            n += 1
            slug_candidate = f"{base_slug}-{n}"
        root = project_root_for(owner, slug_candidate)
        root.mkdir(parents=True, exist_ok=True)
        (root / "docs").mkdir(exist_ok=True)
        (root / "notes").mkdir(exist_ok=True)
        readme = root / "README.md"
        if not readme.exists():
            readme.write_text(f"# {name}\n\nCreated by NeatAi Agent Workspace.\n", encoding="utf-8")
        pid = uuid.uuid4().hex[:12]
        proj = WorkspaceProject(
            id=pid,
            owner=owner,
            name=name,
            slug=slug_candidate,
            root_path=str(root.resolve()),
        )
        db.add(proj)
        db.commit()
        db.refresh(proj)
        return proj.to_dict()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def delete_project(project_id: str, owner: str) -> bool:
    db = SessionLocal()
    try:
        proj = db.query(WorkspaceProject).filter(
            WorkspaceProject.id == project_id,
            WorkspaceProject.owner == owner,
        ).first()
        if not proj:
            return False
        db.delete(proj)
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def bind_session_to_project(session_id: str, project_id: str, owner: str) -> Optional[Dict[str, Any]]:
    db = SessionLocal()
    try:
        proj = db.query(WorkspaceProject).filter(
            WorkspaceProject.id == project_id,
            WorkspaceProject.owner == owner,
        ).first()
        sess = db.query(Session).filter(Session.id == session_id).first()
        if not proj or not sess:
            return None
        sess.workspace_project_id = project_id
        sess.mode = "chat"
        proj.session_id = session_id
        db.commit()
        return proj.to_dict()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_file_tree(project_id: str, owner: str) -> List[Dict[str, Any]]:
    proj = get_project_for_owner(project_id, owner)
    if not proj:
        return []
    return build_tree(Path(proj.root_path))


def read_project_file(project_id: str, owner: str, rel_path: str) -> Dict[str, Any]:
    proj = get_project_for_owner(project_id, owner)
    if not proj:
        raise FileNotFoundError("Project not found")
    ctx = WorkspaceContext(project_id=proj.id, owner=owner, root_path=Path(proj.root_path))
    path = resolve_workspace_path(ctx, rel_path)
    if not path.is_file():
        raise FileNotFoundError(rel_path)
    return {"path": rel_path, "content": read_text_file(path)}


def write_project_file(project_id: str, owner: str, rel_path: str, content: str) -> Dict[str, Any]:
    """Save file content directly (user editor — no approval queue)."""
    proj = get_project_for_owner(project_id, owner)
    if not proj:
        raise FileNotFoundError("Project not found")
    ctx = WorkspaceContext(project_id=proj.id, owner=owner, root_path=Path(proj.root_path))
    path = resolve_workspace_path(ctx, rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content or "", encoding="utf-8")
    rel = path.relative_to(ctx.root_path.resolve()).as_posix()
    log_activity(
        project_id,
        "edit",
        f"Saved {rel}",
        meta={"path": rel, "bytes": len(content or "")},
    )
    return {"path": rel, "content": content or "", "saved": True}


def create_project_folder(project_id: str, owner: str, rel_path: str) -> Dict[str, Any]:
    proj = get_project_for_owner(project_id, owner)
    if not proj:
        raise FileNotFoundError("Project not found")
    ctx = WorkspaceContext(project_id=proj.id, owner=owner, root_path=Path(proj.root_path))
    path = resolve_workspace_path(ctx, rel_path)
    path.mkdir(parents=True, exist_ok=True)
    rel = path.relative_to(ctx.root_path.resolve()).as_posix()
    log_activity(project_id, "edit", f"Created folder {rel}", meta={"path": rel})
    return {"path": rel, "type": "dir"}


def delete_project_path(project_id: str, owner: str, rel_path: str) -> Dict[str, Any]:
    import shutil

    proj = get_project_for_owner(project_id, owner)
    if not proj:
        raise FileNotFoundError("Project not found")
    ctx = WorkspaceContext(project_id=proj.id, owner=owner, root_path=Path(proj.root_path))
    path = resolve_workspace_path(ctx, rel_path)
    if not path.exists():
        raise FileNotFoundError(rel_path)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    log_activity(project_id, "edit", f"Deleted {rel_path}", meta={"path": rel_path})
    return {"path": rel_path, "deleted": True}


def list_pending_changes(project_id: str, owner: str) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        proj = db.query(WorkspaceProject).filter(
            WorkspaceProject.id == project_id,
            WorkspaceProject.owner == owner,
        ).first()
        if not proj:
            return []
        rows = (
            db.query(WorkspaceChange)
            .filter(WorkspaceChange.project_id == project_id, WorkspaceChange.status == "pending")
            .order_by(WorkspaceChange.created_at.asc())
            .all()
        )
        return [r.to_dict() for r in rows]
    finally:
        db.close()


def log_activity(
    project_id: str,
    kind: str,
    message: str,
    *,
    session_id: Optional[str] = None,
    meta: Optional[dict] = None,
) -> None:
    db = SessionLocal()
    try:
        db.add(WorkspaceActivity(
            id=uuid.uuid4().hex[:12],
            project_id=project_id,
            session_id=session_id,
            kind=kind,
            message=message,
            meta=meta or {},
        ))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("workspace activity log failed: %s", exc)
    finally:
        db.close()


def list_activity(project_id: str, owner: str, limit: int = 100) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        proj = db.query(WorkspaceProject).filter(
            WorkspaceProject.id == project_id,
            WorkspaceProject.owner == owner,
        ).first()
        if not proj:
            return []
        rows = (
            db.query(WorkspaceActivity)
            .filter(WorkspaceActivity.project_id == project_id)
            .order_by(WorkspaceActivity.created_at.desc())
            .limit(limit)
            .all()
        )
        return [r.to_dict() for r in reversed(rows)]
    finally:
        db.close()


def create_pending_change(
    ctx: WorkspaceContext,
    *,
    action_type: str,
    path: Optional[str],
    summary: str,
    payload: dict,
    diff_preview: Optional[str] = None,
) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        change_id = uuid.uuid4().hex[:12]
        row = WorkspaceChange(
            id=change_id,
            project_id=ctx.project_id,
            session_id=ctx.session_id,
            status="pending",
            action_type=action_type,
            path=path,
            summary=summary,
            diff_preview=diff_preview,
            payload=payload,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        log_activity(
            ctx.project_id,
            "proposal",
            summary,
            session_id=ctx.session_id,
            meta={"change_id": change_id, "action_type": action_type, "path": path},
        )
        return row.to_dict()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _read_old_content(path: Path) -> Optional[str]:
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    return None


def propose_file_change(
    ctx: WorkspaceContext,
    *,
    action: str,
    path: str,
    content: Optional[str] = None,
) -> Dict[str, Any]:
    action = (action or "modify").lower().strip()
    if action not in ("create", "modify", "delete"):
        raise ValueError("action must be create, modify, or delete")
    abs_path = resolve_workspace_path(ctx, path)
    rel = abs_path.relative_to(ctx.root_path.resolve()).as_posix()
    old = _read_old_content(abs_path)
    if action == "create" and abs_path.exists():
        action = "modify"
    if action == "delete":
        summary = f"Delete {rel}"
        diff = make_diff_preview(old, None)
        return create_pending_change(
            ctx,
            action_type="delete",
            path=rel,
            summary=summary,
            payload={"content": None},
            diff_preview=diff,
        )
    if content is None:
        raise ValueError("content required for create/modify")
    verb = "Create" if action == "create" or old is None else "Modify"
    summary = f"{verb} {rel}"
    diff = make_diff_preview(old, content)
    if old and content and old != content:
        udiff = "\n".join(difflib.unified_diff(
            old.splitlines(), content.splitlines(), fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm=""
        ))
        if udiff:
            diff = udiff[:4000]
    return create_pending_change(
        ctx,
        action_type=action if action != "create" or old is None else "create",
        path=rel,
        summary=summary,
        payload={"content": content, "old_content": old},
        diff_preview=diff,
    )


def propose_command(ctx: WorkspaceContext, command: str, summary: Optional[str] = None) -> Dict[str, Any]:
    command = (command or "").strip()
    if not command:
        raise ValueError("command required")
    short = command.split("\n")[0][:80]
    return create_pending_change(
        ctx,
        action_type="run",
        path=None,
        summary=summary or f"Run: {short}",
        payload={"command": command},
        diff_preview=command[:4000],
    )


def apply_change(change_id: str, owner: str) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        change = db.query(WorkspaceChange).filter(WorkspaceChange.id == change_id).first()
        if not change or change.status != "pending":
            raise ValueError("Change not found or not pending")
        proj = db.query(WorkspaceProject).filter(
            WorkspaceProject.id == change.project_id,
            WorkspaceProject.owner == owner,
        ).first()
        if not proj:
            raise ValueError("Project not found")
        ctx = WorkspaceContext(
            project_id=proj.id,
            owner=owner,
            root_path=Path(proj.root_path),
            session_id=change.session_id,
        )
        if change.action_type == "run":
            cmd = (change.payload or {}).get("command") or ""
            change.status = "applied"
            db.commit()
            db.refresh(change)
            log_activity(
                proj.id,
                "applied",
                change.summary or change.action_type,
                session_id=change.session_id,
                meta={"change_id": change.id, "command": cmd[:500]},
            )
            _schedule_run_command(ctx, cmd, change.id)
            return change.to_dict()
        result = _apply_change_row(change, ctx)
        change.status = "applied"
        if result.get("test_summary"):
            change.test_summary = result["test_summary"]
        db.commit()
        db.refresh(change)
        log_activity(
            proj.id,
            "applied",
            change.summary or change.action_type,
            session_id=change.session_id,
            meta={"change_id": change.id},
        )
        return change.to_dict()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _schedule_run_command(ctx: WorkspaceContext, command: str, change_id: str) -> None:
    """Run an approved shell command off the request thread."""

    def _worker() -> None:
        try:
            result = _run_command_sync(ctx, command, change_id=change_id)
            summary = result.get("test_summary")
            if not summary:
                return
            db = SessionLocal()
            try:
                row = db.query(WorkspaceChange).filter(WorkspaceChange.id == change_id).first()
                if row:
                    row.test_summary = summary
                    db.commit()
            except Exception as exc:
                db.rollback()
                logger.warning("workspace test_summary update failed: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.exception("workspace command failed for change %s", change_id)
            log_activity(
                ctx.project_id,
                "command",
                f"Command failed: {exc}",
                session_id=ctx.session_id,
                meta={"change_id": change_id, "command": command[:500]},
            )

    threading.Thread(
        target=_worker,
        daemon=True,
        name=f"ws-cmd-{change_id}",
    ).start()


def _apply_change_row(change: WorkspaceChange, ctx: WorkspaceContext) -> Dict[str, Any]:
    payload = change.payload or {}
    if change.action_type == "run":
        cmd = payload.get("command") or ""
        return _run_command_sync(ctx, cmd, change_id=change.id)
    if not change.path:
        raise ValueError("Missing path")
    path = resolve_workspace_path(ctx, change.path)
    if change.action_type == "delete":
        if path.exists():
            if path.is_dir():
                import shutil
                shutil.rmtree(path)
            else:
                path.unlink()
        return {}
    content = payload.get("content")
    if content is None:
        raise ValueError("Missing content")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {}


def _run_command_sync(ctx: WorkspaceContext, command: str, *, change_id: str = "") -> Dict[str, Any]:
    import subprocess

    env = {**os.environ, "NEATAIEUS_WORKSPACE": str(ctx.root_path), "TERM": "xterm-256color"}
    py_code = _extract_python_c_code(command)
    if py_code is not None:
        proc = subprocess.run(
            [_python_executable(), "-I", "-c", py_code],
            cwd=str(ctx.root_path),
            capture_output=True,
            text=True,
            env=env,
            timeout=RUN_COMMAND_TIMEOUT,
        )
    else:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(ctx.root_path),
            capture_output=True,
            text=True,
            env=env,
            timeout=RUN_COMMAND_TIMEOUT,
        )
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    parsed = parse_test_output(out)
    log_activity(
        ctx.project_id,
        "command",
        out[-8000:] if len(out) > 8000 else out,
        session_id=ctx.session_id,
        meta={"exit_code": proc.returncode, "change_id": change_id, "command": command[:500]},
    )
    if parsed.get("items") or parsed.get("fail"):
        log_activity(
            ctx.project_id,
            "test",
            parsed.get("summary", ""),
            session_id=ctx.session_id,
            meta=parsed,
        )
    return {
        "exit_code": proc.returncode,
        "output": out[:10000],
        "test_summary": parsed.get("summary"),
    }


async def run_command_async(ctx: WorkspaceContext, command: str) -> Dict[str, Any]:
    return await asyncio.to_thread(_run_command_sync, ctx, command)


def reject_change(change_id: str, owner: str, reason: Optional[str] = None) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        change = db.query(WorkspaceChange).filter(WorkspaceChange.id == change_id).first()
        if not change or change.status != "pending":
            raise ValueError("Change not found or not pending")
        proj = db.query(WorkspaceProject).filter(
            WorkspaceProject.id == change.project_id,
            WorkspaceProject.owner == owner,
        ).first()
        if not proj:
            raise ValueError("Project not found")
        change.status = "rejected"
        change.rejection_reason = (reason or "").strip() or None
        db.commit()
        db.refresh(change)
        log_activity(
            proj.id,
            "rejected",
            change.summary or change.action_type,
            session_id=change.session_id,
            meta={"change_id": change.id, "reason": change.rejection_reason},
        )
        return change.to_dict()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def approve_all_pending(project_id: str, owner: str) -> List[Dict[str, Any]]:
    pending = list_pending_changes(project_id, owner)
    applied = []
    errors = []
    for ch in pending:
        try:
            applied.append(apply_change(ch["id"], owner))
        except ValueError as exc:
            errors.append({"change_id": ch["id"], "error": str(exc)})
    if errors and not applied:
        raise ValueError(errors[0]["error"])
    return applied


def save_plan(project_id: str, owner: str, plan: dict) -> Dict[str, Any]:
    import json
    db = SessionLocal()
    try:
        proj = db.query(WorkspaceProject).filter(
            WorkspaceProject.id == project_id,
            WorkspaceProject.owner == owner,
        ).first()
        if not proj:
            raise ValueError("Project not found")
        proj.plan_json = json.dumps(plan)
        db.commit()
        db.refresh(proj)
        return proj.to_dict()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_plan(project_id: str, owner: str) -> Optional[dict]:
    import json
    proj = get_project_for_owner(project_id, owner)
    if not proj or not proj.plan_json:
        return None
    try:
        return json.loads(proj.plan_json)
    except json.JSONDecodeError:
        return None


def _guess_script_path(content: str) -> Optional[str]:
    """If python tool content looks like a file, return a relative path."""
    text = content.strip()
    if not text:
        return None
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("#"):
        hint = lines[0].strip()[1:].strip()
        token = (hint.split()[0] if hint else "").lstrip("./")
        if token and re.match(r"^[\w./-]+\.\w+$", token):
            return token.replace("\\", "/")
    lower = text.lower()
    if len(lines) >= 2 and any(
        ln.lstrip().startswith(("import ", "from ", "def ", "class ", "@app.", "app = "))
        for ln in lines[:12]
    ):
        if "flask" in lower or "fastapi" in lower or "@app.route" in text:
            return "app.py"
        if "requirements" in lower or re.search(r"^[\w.-]+(==|>=|<=|~=)", text, re.M):
            return "requirements.txt"
        return "main.py"
    return None


def _python_executable() -> str:
    return sys.executable or "python"


def _extract_python_c_code(command: str) -> Optional[str]:
    """Parse `python -c ...` out of a queued shell command (Windows-safe)."""
    stripped = (command or "").strip()
    if not stripped:
        return None
    m = re.match(
        r"^(?:(?:python3|python|py)|(?:[\w.:\\/ -]+[\\/]python(?:3)?(?:\.exe)?))\s+(?:-I\s+)?-c\s+(.+)$",
        stripped,
        re.I | re.S,
    )
    if not m:
        return None
    rest = m.group(1).strip()
    if not rest:
        return None
    if rest[0] in "'\"":
        try:
            return ast.literal_eval(rest)
        except (SyntaxError, ValueError):
            pass
    return rest


def intercept_python(ctx: WorkspaceContext, content: str) -> Tuple[str, Dict[str, Any]]:
    """Workspace python tool: propose files for scripts, queue short code as python -c."""
    text = content or ""
    path = _guess_script_path(text)
    if path:
        body = text
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("#"):
            hint = lines[0].strip()[1:].strip().split()[0].lstrip("./")
            if hint and path == hint.replace("\\", "/"):
                body = "\n".join(lines[1:]).lstrip("\n")
        return intercept_write_file(ctx, path, body)
    exe = _python_executable()
    wrapped = f"{exe} -I -c {repr(text)}"
    return intercept_bash(ctx, wrapped)


def intercept_write_file(ctx: WorkspaceContext, path: str, body: str) -> Tuple[str, Dict[str, Any]]:
    abs_path = resolve_workspace_path(ctx, path)
    rel = abs_path.relative_to(ctx.root_path.resolve()).as_posix()
    action = "create" if not abs_path.exists() else "modify"
    ch = propose_file_change(ctx, action=action, path=rel, content=body)
    return (
        f"proposed {action} {rel}",
        {
            "output": (
                f"Change queued for approval (id: {ch['id']}). "
                f"The user must approve it in the Agent Workspace panel before it is applied."
            ),
            "exit_code": 0,
            "pending_change_id": ch["id"],
        },
    )


def ensure_workspace_session(project_id: str, owner: str) -> Dict[str, Any]:
    """Return session_id for project, creating an agent session if needed."""
    from core.database import ModelEndpoint
    from src.settings import load_settings
    from src.auth_helpers import owner_filter

    db = SessionLocal()
    try:
        proj = db.query(WorkspaceProject).filter(
            WorkspaceProject.id == project_id,
            WorkspaceProject.owner == owner,
        ).first()
        if not proj:
            raise ValueError("Project not found")
        if proj.session_id:
            sess = db.query(Session).filter(Session.id == proj.session_id).first()
            if sess:
                sess.workspace_project_id = project_id
                sess.mode = "chat"
                db.commit()
                try:
                    from src.ai_interaction import get_session_manager
                    _sm = get_session_manager()
                    if _sm:
                        _sm.get_session(sess.id)
                except Exception as _warm_err:
                    logger.debug("workspace session warm-load: %s", _warm_err)
                return {"session_id": sess.id, "created": False, "project_id": project_id, "model": sess.model, "endpoint_url": sess.endpoint_url}
            # Stale link — session was deleted; recreate below
            proj.session_id = None

        settings = load_settings()
        ep_id = (settings.get("default_endpoint_id") or "").strip()
        model = (settings.get("default_model") or "").strip()
        if owner:
            try:
                from routes.prefs_routes import _load_for_user
                prefs = _load_for_user(owner) or {}
                ep_id = (prefs.get("default_endpoint_id") or ep_id or "").strip()
                model = (prefs.get("default_model") or model or "").strip()
            except Exception:
                pass

        ep = None
        if ep_id:
            ep_q = db.query(ModelEndpoint).filter(
                ModelEndpoint.id == ep_id,
                ModelEndpoint.is_enabled == True,  # noqa: E712
            )
            ep_q = owner_filter(ep_q, ModelEndpoint, owner)
            ep = ep_q.first()
        if not ep:
            ep_q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)  # noqa: E712
            ep_q = owner_filter(ep_q, ModelEndpoint, owner)
            ep = ep_q.first()
        if not ep:
            raise ValueError("No chat endpoint configured — add one in Settings")
        if not model and ep.cached_models:
            try:
                import json
                models = json.loads(ep.cached_models) if isinstance(ep.cached_models, str) else ep.cached_models
                if isinstance(models, list) and models:
                    model = models[0] if isinstance(models[0], str) else models[0].get("id", "")
            except Exception:
                pass
        if not model:
            model = ep.name or "default"

        sid = uuid.uuid4().hex[:12]
        sess = Session(
            id=sid,
            name=f"Workspace: {proj.name}",
            endpoint_url=ep.base_url,
            model=model,
            owner=owner,
            mode="chat",
            workspace_project_id=project_id,
        )
        db.add(sess)
        proj.session_id = sid
        db.commit()
        try:
            from src.ai_interaction import get_session_manager
            _sm = get_session_manager()
            if _sm:
                _sm.get_session(sid)
        except Exception as _warm_err:
            logger.debug("workspace session warm-load: %s", _warm_err)
        return {"session_id": sid, "created": True, "project_id": project_id, "model": model, "endpoint_url": ep.base_url}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def intercept_bash(ctx: WorkspaceContext, command: str) -> Tuple[str, Dict[str, Any]]:
    ch = propose_command(ctx, command)
    return (
        f"proposed command: {command.split(chr(10))[0][:60]}",
        {
            "output": (
                f"Command queued for approval (id: {ch['id']}). "
                f"The user must approve it in the Agent Workspace panel before it runs."
            ),
            "exit_code": 0,
            "pending_change_id": ch["id"],
        },
    )


def do_list_workspace_files(ctx: WorkspaceContext, subpath: str = "") -> Dict[str, Any]:
    root = ctx.root_path
    if subpath:
        target = resolve_workspace_path(ctx, subpath)
        if not target.is_dir():
            raise ValueError("Not a directory")
        root = target
    tree = build_tree(root, max_depth=4)
    lines = []

    def _walk(nodes, indent=0):
        for node in nodes:
            prefix = "  " * indent
            if node["type"] == "dir":
                lines.append(f"{prefix}{node['path']}/")
                _walk(node.get("children") or [], indent + 1)
            else:
                lines.append(f"{prefix}{node['path']} ({node.get('size', 0)} bytes)")

    _walk(tree)
    return {"output": "\n".join(lines) or "(empty)", "exit_code": 0}


def do_propose_file_change(ctx: WorkspaceContext, content: str) -> Dict[str, Any]:
    import json
    raw = content.strip()
    if raw.startswith("{"):
        data = json.loads(raw)
    else:
        lines = content.split("\n", 1)
        data = {"action": "modify", "path": lines[0].strip(), "content": lines[1] if len(lines) > 1 else ""}
    ch = propose_file_change(
        ctx,
        action=data.get("action", "modify"),
        path=data.get("path", ""),
        content=data.get("content"),
    )
    return {
        "output": f"Proposed change {ch['id']}: {ch['summary']}. Awaiting user approval.",
        "exit_code": 0,
        "pending_change_id": ch["id"],
    }


def do_propose_command(ctx: WorkspaceContext, content: str) -> Dict[str, Any]:
    ch = propose_command(ctx, content.strip())
    return {
        "output": f"Proposed command {ch['id']}. Awaiting user approval.",
        "exit_code": 0,
        "pending_change_id": ch["id"],
    }


def do_create_workspace_plan(ctx: WorkspaceContext, content: str) -> Dict[str, Any]:
    import json
    plan = json.loads(content) if content.strip().startswith("{") else {"phases": content.strip().split("\n")}
    save_plan(ctx.project_id, ctx.owner, plan)
    return {
        "output": "Saved workspace plan.",
        "exit_code": 0,
        "plan": plan,
    }
