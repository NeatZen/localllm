"""API routes for the Model Download Hub."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.middleware import require_admin
from services import model_hub

logger = logging.getLogger(__name__)


class ModelHubAction(BaseModel):
    model_id: str


def setup_model_hub_routes() -> APIRouter:
    router = APIRouter(tags=["model-hub"])

    @router.get("/api/model-hub/status")
    async def hub_status(_admin: None = Depends(require_admin)):
        return model_hub.get_hub_status()

    @router.get("/api/model-hub/catalog")
    async def hub_catalog(_admin: None = Depends(require_admin)):
        return {"models": model_hub.get_hub_status()["catalog"]}

    @router.post("/api/model-hub/download")
    async def hub_download(body: ModelHubAction, _admin: None = Depends(require_admin)):
        return await model_hub.start_download(body.model_id.strip())

    @router.post("/api/model-hub/activate")
    async def hub_activate(body: ModelHubAction, _admin: None = Depends(require_admin)):
        return await model_hub.activate_model(body.model_id.strip())

    return router
