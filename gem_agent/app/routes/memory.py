"""Memory panel: list, edit and forget user facts (spec 5.5)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from gem_agent.app.auth import current_user
from gem_agent.tools.gem.models import site_now

router = APIRouter()


class Edit(BaseModel):
    text: str


@router.get("/api/memory")
def list_memory(request: Request, user: str = Depends(current_user)) -> list[dict[str, Any]]:
    facts = request.app.state.memory.facts(user)
    return [{"id": f.id, "fact": f.fact, "evidence": f.evidence, "source": f.source, "since": f.since.isoformat(),
             "kind": f.kind, "topic": f.topic}
            for f in facts if f.active and not f.removed]


@router.patch("/api/memory/{fact_id}", status_code=204)
def edit_memory(fact_id: str, body: Edit, request: Request, user: str = Depends(current_user)) -> Response:
    request.app.state.memory.correct(user, fact_id, body.text.strip(), today=site_now().date())
    return Response(status_code=204)


@router.delete("/api/memory/{fact_id}", status_code=204)
def forget_memory(fact_id: str, request: Request, user: str = Depends(current_user)) -> Response:
    request.app.state.memory.forget(user, fact_id)
    return Response(status_code=204)
