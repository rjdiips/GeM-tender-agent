"""FastAPI app factory and startup. `create_app` takes its parts; `app()` builds the
real services and resumes any run a restart interrupted."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from gem_agent.app.auth import current_user, sign_in
from gem_agent.app.routes import chat as chat_routes
from gem_agent.app.routes import downloads as download_routes
from gem_agent.app.routes import memory as memory_routes
from gem_agent.app.routes import runs as run_routes
from gem_agent.cache.docs import DocCache
from gem_agent.graph.manager import RunManager
from gem_agent.memory.user_memory import MemoryStore

log = logging.getLogger(__name__)
WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class Login(BaseModel):
    name: str


def create_app(manager: RunManager, *, memory: MemoryStore, cache: DocCache, org: str = "",
               resume_on_start: bool = False, on_start: Sequence[Callable[[], Awaitable[None]]] = ()) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        for hook in on_start:
            await hook()
        tasks = [asyncio.create_task(manager.resume(r)) for r in manager.running_runs()] if resume_on_start else []
        if tasks:
            log.info("resuming %d interrupted runs", len(tasks))
        yield

    app = FastAPI(title="GeM Tender Agent", lifespan=lifespan)
    app.state.manager, app.state.memory, app.state.cache = manager, memory, cache

    @app.post("/api/login")
    def login(body: Login, response: Response) -> dict[str, str]:
        return {"name": sign_in(response, body.name), "org": org}

    @app.get("/api/me")
    def me(user: str = Depends(current_user)) -> dict[str, str]:
        return {"name": user, "org": org}

    for module in (chat_routes, run_routes, download_routes, memory_routes):
        app.include_router(module.router)
    if WEB_DIST.exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


def app() -> FastAPI:
    """The real app: `uvicorn --factory gem_agent.app.main:app`."""
    from gem_agent.app.factory import build_services
    from gem_agent.config import get_settings

    settings = get_settings()
    services, cache, on_start = build_services(settings)
    return create_app(RunManager(services), memory=services.memory, cache=cache, org=settings.org_name,
                      resume_on_start=True, on_start=on_start)
