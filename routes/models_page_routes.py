"""Routes for the Models discovery page."""

import logging

from fastapi import APIRouter, Depends, Query

from src.auth_helpers import require_user
from services.models_page import get_discover_data

logger = logging.getLogger(__name__)


def setup_models_page_routes() -> APIRouter:
    router = APIRouter(tags=["models-page"])

    @router.get("/api/models-page/discover")
    async def models_page_discover(
        fresh: bool = Query(False),
        _user: str = Depends(require_user),
    ):
        return await get_discover_data(fresh=fresh)

    return router
