"""FastAPI application entry point for the Customer Success Agent API.

Exposes the customer-facing chat endpoint, the signal detector/trigger/dashboard
endpoints, and skills introspection. Temporal and Langfuse are not required for
the app to run.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fastapi import Query

from packages.tool_system.src.registry import register_builtin_tools

from apps.agent_service.src.agent.runtime.skills import get_skill_manager
from apps.api_gateway.src.routes.chat import router as chat_router
from apps.api_gateway.src.routes.signals import router as signals_router

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm shared components on startup, dispose the DB pool on shutdown."""
    register_builtin_tools()
    logger.info("Customer Success Agent API ready")
    yield
    try:
        from packages.db.src import close_pool

        await close_pool()
    except Exception:  # pragma: no cover
        pass


app = FastAPI(
    title="Customer Success Agent API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

def _mount(router) -> None:
    """Attach a router's routes to the app.

    ``app.include_router`` drops most routes under the installed Starlette
    version, so we append the fully-formed route objects directly (the routers
    already carry absolute paths).
    """
    for route in router.routes:
        app.router.routes.append(route)


_mount(chat_router)
_mount(signals_router)


@app.get("/health")
async def health():
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/skills")
async def skills_summary(tenant_id: str = Query(...)):
    """Show currently loaded tenant skills."""
    return get_skill_manager(tenant_id).summary()


@app.post("/skills/reload")
async def reload_skills(tenant_id: str = Query(...)):
    """Hot-reload tenant skills without a restart."""
    manager = get_skill_manager(tenant_id)
    manager.reload()
    return manager.summary()


__all__ = ["app"]
