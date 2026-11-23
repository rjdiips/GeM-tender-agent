"""Chat messages in; events out as server-sent events."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from gem_agent.app.auth import current_user

router = APIRouter()


class Ask(BaseModel):
    text: str
    run_id: str | None = None


async def _sse(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    async for event in events:
        yield f"data: {json.dumps(event, default=str)}\n\n"


@router.post("/api/ask")
async def ask(body: Ask, request: Request, user: str = Depends(current_user)) -> StreamingResponse:
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="empty message")
    manager = request.app.state.manager
    try:
        events = manager.ask(user, body.text.strip(), run_id=body.run_id)
    except (KeyError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail="no such run") from exc
    return StreamingResponse(_sse(events), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, user: str = Depends(current_user)) -> StreamingResponse:
    manager = request.app.state.manager
    try:
        manager.view(user, run_id)
    except (KeyError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail="no such run") from exc
    return StreamingResponse(_sse(manager.events(user, run_id)), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
