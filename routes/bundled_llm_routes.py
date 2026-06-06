"""API routes for turnkey bundled local LLM setup status."""

import logging

from fastapi import APIRouter

from services import bundled_llm

logger = logging.getLogger(__name__)


def setup_bundled_llm_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/api/bundled-llm/status")
    async def bundled_llm_status():
        status = bundled_llm.get_status()
        healthy = await bundled_llm.is_server_healthy()
        status["healthy"] = healthy
        if healthy and status.get("state") != "running":
            status["state"] = "running"
            status["message"] = "Built-in AI is ready"
        return status

    @router.post("/api/bundled-llm/start")
    async def bundled_llm_start():
        if not bundled_llm.is_enabled():
            return {"ok": False, "error": "Bundled LLM is disabled"}
        ok = await bundled_llm.ensure_ready()
        return {"ok": ok, **bundled_llm.get_status()}

    return router
